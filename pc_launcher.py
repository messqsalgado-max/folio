"""
Run Folio on this PC and put it online with a free Cloudflare tunnel, so you can open it from your
school Chromebook (or phone). Just double-click start-folio.bat.

First run makes a settings file (folio.env) next to this script. Fill in your password and Gemini key,
save, and run start-folio.bat again. Keep the window that opens; closing it takes Folio offline.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "folio.env")
CF = os.path.join(HERE, "cloudflared.exe")
CF_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
PORT = os.environ.get("PORT") or "7860"

TEMPLATE = """# Fill these in, save this file, then run start-folio.bat again.

# A password you make up. You'll type it to open Folio on the Chromebook.
APP_PASSWORD=

# Your Gemini key from aistudio.google.com (click "Get API key"). It starts with AIza...
GEMINI_API_KEY=

# Any long random text. Keeps you signed in. Just mash the keyboard.
SECRET_KEY=

# Optional: set to 1 to run Chrome with no window (use this if the PC is usually locked).
FOLIO_HEADLESS=
"""


def say(*a):
    print("  " + " ".join(str(x) for x in a))


def load_env():
    if not os.path.isfile(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
        print()
        say("I made a settings file:  folio.env")
        say("Open it, fill in your password and Gemini key, save, then run start-folio.bat again.")
        print()
        _open(ENV_FILE)
        return None
    env = {}
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                env[k.strip()] = v.strip()
    if not env.get("APP_PASSWORD") or not env.get("GEMINI_API_KEY"):
        print()
        say("folio.env still needs APP_PASSWORD and GEMINI_API_KEY. Fill them in and run start-folio.bat again.")
        print()
        _open(ENV_FILE)
        return None
    return env


def _open(path):
    try:
        os.startfile(path)  # noqa: pragma (Windows only)
    except Exception:
        pass


def ensure_deps():
    try:
        import flask  # noqa: F401
        import playwright  # noqa: F401
        return True
    except Exception:
        say("Installing what Folio needs (one time, a couple of minutes)…")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "-r",
                                   os.path.join(HERE, "requirements.txt")])
            return True
        except Exception as e:
            say("Couldn't install the packages:", e)
            return False


def ensure_cloudflared():
    if os.path.isfile(CF):
        return True
    say("Getting the tunnel program (one time)…")
    try:
        urllib.request.urlretrieve(CF_URL, CF)
        return True
    except Exception as e:
        say("Couldn't download cloudflared:", e)
        return False


def wait_up(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", int(PORT)), 1):
                return True
        except OSError:
            time.sleep(1)
    return False


def main():
    env = load_env()
    if not env:
        input("Press Enter to close.")
        return
    if not ensure_deps() or not ensure_cloudflared():
        input("Press Enter to close.")
        return

    os.environ.update(env)
    os.environ.update(FOLIO_CLASSROOM="1", HOST="127.0.0.1", PORT=PORT,
                      FOLIO_DATA=os.path.join(HERE, "folio-data"))

    web = subprocess.Popen([sys.executable, os.path.join(HERE, "webapp.py")], cwd=HERE)
    say("Starting Folio…")
    if not wait_up():
        say("Folio didn't start — check the messages above.")
        web.terminate()
        input("Press Enter to close.")
        return

    say("Opening the public link…")
    cf = subprocess.Popen([CF, "tunnel", "--url", "http://127.0.0.1:%s" % PORT, "--no-autoupdate"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    shown = False
    try:
        for line in cf.stdout:
            m = re.search(r"https://[-\w.]+\.trycloudflare\.com", line)
            if m and not shown:
                shown = True
                url = m.group(0)
                bar = "=" * 60
                print("\n" + bar)
                say("Folio is online. On your Chromebook, open:")
                say("  " + url)
                say("Password: the APP_PASSWORD you put in folio.env")
                print(bar)
                say("Keep this window open. Closing it takes Folio offline.")
                say("The link changes each time you start Folio.\n")
    except KeyboardInterrupt:
        pass
    finally:
        for p in (cf, web):
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
