"""
API keys live in Windows Credential Manager (encrypted to this Windows account), not in
settings.json. Falls back to returning None anywhere the vault isn't available.
"""

import ctypes
import sys
from ctypes import wintypes

KEYS = ("api_key", "gemini_key", "brave_key")
PREFIX = "Folio/"

if sys.platform == "win32":
    _adv = ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    class _CRED(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    _adv.CredWriteW.argtypes = [ctypes.POINTER(_CRED), wintypes.DWORD]
    _adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CRED))]
    _adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _adv.CredFree.argtypes = [ctypes.c_void_p]
    _GENERIC, _LOCAL_MACHINE = 1, 2


def available():
    return sys.platform == "win32"


def get(name):
    if not available():
        return None
    p = ctypes.POINTER(_CRED)()
    if not _adv.CredReadW(PREFIX + name, _GENERIC, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        _adv.CredFree(p)


def put(name, value):
    """Store (or, for an empty value, delete) a secret. Returns True on success."""
    if not available():
        return False
    if not value:
        _adv.CredDeleteW(PREFIX + name, _GENERIC, 0)
        return True
    blob = value.encode("utf-16-le")
    buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
    c = _CRED(Type=_GENERIC, TargetName=PREFIX + name, CredentialBlobSize=len(blob),
              CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)), Persist=_LOCAL_MACHINE,
              UserName="Folio", Comment="Folio API key")
    return bool(_adv.CredWriteW(ctypes.byref(c), 0))
