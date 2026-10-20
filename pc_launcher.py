"""
Run Folio on this PC and put it online with a free Cloudflare tunnel, so you can open it from your
school Chromebook (or phone). Just double-click start-folio.bat.

First run makes a settings file (folio.env) next to this script. Fill in your password, save, and run
start-folio.bat again. Add your own API key in Folio Settings after signing in. Keep the window that
opens; closing it takes Folio offline.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "folio.env")
CF = os.path.join(HERE, "cloudflared.exe")
CF_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
PORT = os.environ.get("PORT") or "7860"
# cloudflared defaults to QUIC (UDP), which many home routers/ISPs throttle or drop — that shows up as
# "no recent network activity" timeouts and constant reconnects, so the link goes down (ERR_CONNECTION).
# HTTP/2 runs over TCP and stays up on those networks. Override with FOLIO_TUNNEL_PROTOCOL if needed.
TUNNEL_PROTOCOL = os.environ.get("FOLIO_TUNNEL_PROTOCOL") or "http2"

REPO = "messqsalgado-max/folio"
ZIP_URL = "https://codeload.github.com/%s/zip/refs/heads/main" % REPO
API_URL = "https://api.github.com/repos/%s/commits/main" % REPO
VERSION_FILE = os.path.join(HERE, ".version")
# your own stuff — never overwritten or deleted by an update
KEEP = {"folio.env", "folio-data", "cloudflared.exe", ".chromium-ok", ".version"}

TEMPLATE = """# Fill these in, save this file, then run start-folio.bat again.

# A password you make up. You'll type it to open Folio on the Chromebook.
APP_PASSWORD=

# Any long random text. Keeps you signed in. Just mash the keyboard.
SECRET_KEY=

# Optional: set to 1 to run Chrome with no window (use this if the PC is usually locked).
FOLIO_HEADLESS=

# Optional: set to 1 to add a personal Browser tab with its own Chrome process/profile, separate from
# Classroom sign-in. Leave blank to keep it off. Not for getting around a school device's filter.
FOLIO_BROWSER_TAB=

# Optional: your own Cloudflare domain for a fixed link (e.g. folio.gripe). Don't type it here by hand —
# run setup-domain.bat once and it fills this in and sets everything up.
FOLIO_TUNNEL_HOSTNAME=
"""


def say(*a):
    print("  " + " ".join(str(x) for x in a))


def load_env():
    if not os.path.isfile(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
        print()
        say("I made a settings file:  folio.env")
        say("Open it, fill in your password, save, then run start-folio.bat again. Add your own API key in Folio Settings after signing in.")
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
    if not env.get("APP_PASSWORD"):
        print()
        say("folio.env still needs APP_PASSWORD. Fill it in and run start-folio.bat again.")
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
    missing = False
    for mod in ("flask", "flask_sock", "playwright", "anthropic", "pptx", "docx", "reportlab", "PIL", "ddgs"):
        try:
            __import__(mod)
        except Exception:
            missing = True
            break
    if missing:
        say("Installing what Folio needs (one time, a couple of minutes)…")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        except Exception:
            pass
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "-r",
                                   os.path.join(HERE, "requirements.txt")])
        except Exception as e:
            say("Couldn't install the packages:", e)
            return False
    return ensure_browser()


def ensure_browser():
    """Classroom needs a Chrome. Windows has Edge built in; if it doesn't, fetch Playwright's Chromium."""
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if os.path.isfile(edge):
        return True
    marker = os.path.join(HERE, ".chromium-ok")
    if os.path.isfile(marker):
        os.environ["FOLIO_BROWSER_CHANNEL"] = "none"
        return True
    say("Getting a browser for Classroom (one time)…")
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        open(marker, "w").close()
        os.environ["FOLIO_BROWSER_CHANNEL"] = "none"
        return True
    except Exception as e:
        say("Couldn't set up a browser automatically:", e)
        return False


def _latest_sha(timeout=5):
    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github.sha",
                                                        "User-Agent": "folio-launcher"})
        return urllib.request.urlopen(req, timeout=timeout).read().decode().strip()
    except Exception:
        try:
            req = urllib.request.Request(API_URL, headers={"User-Agent": "folio-launcher"})
            return json.load(urllib.request.urlopen(req, timeout=timeout)).get("sha", "")
        except Exception:
            return ""


def _saved_sha():
    try:
        return open(VERSION_FILE, encoding="utf-8").read().strip()
    except Exception:
        return ""


def update(auto=False, sha=""):
    print()
    say("Checking for the latest Folio…")
    tmp = os.path.join(tempfile.gettempdir(), "folio-update.zip")
    try:
        urllib.request.urlretrieve(ZIP_URL, tmp)
    except Exception as e:
        say("Couldn't download the update:", e)
        return False
    try:
        with zipfile.ZipFile(tmp) as z:
            root = z.namelist()[0].split("/")[0] + "/"  # e.g. "folio-main/"
            n = 0
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel = info.filename[len(root):] if info.filename.startswith(root) else info.filename
                if not rel or rel.split("/")[0] in KEEP:
                    continue
                dest = os.path.join(HERE, *rel.split("/"))
                os.makedirs(os.path.dirname(dest) or HERE, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                n += 1
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(sha or _latest_sha() or "updated")
        say("Updated %d files. Your password, projects and sign-in were kept." % n)
        if not auto:
            say("Now run start-folio.bat to launch the new version.")
        return True
    except Exception as e:
        say("Update failed:", e)
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


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


def run_domain_tunnel(web, host):
    """Keep Folio running locally and restart its fixed-domain tunnel if cloudflared exits."""
    cf = None
    try:
        while web.poll() is None:
            say("Starting your tunnel…")
            # Inherit the console so Cloudflare's actual connection or error messages are visible.
            cf = subprocess.Popen([CF, "tunnel", "--no-autoupdate", "--protocol", TUNNEL_PROTOCOL, "run",
                                   "--url", "http://127.0.0.1:%s" % PORT, TUNNEL_NAME], cwd=HERE)
            while cf.poll() is None and web.poll() is None:
                time.sleep(1)
            if web.poll() is not None:
                say("Folio's web server stopped (exit code %s)." % web.returncode)
                break
            say("The Cloudflare tunnel to https://%s stopped (exit code %s)." % (host, cf.returncode))
            say("Folio is still running on this PC. Retrying the tunnel in 5 seconds; press Ctrl+C to stop.")
            cf = None  # this process has exited; the next loop starts a fresh one
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (cf, web):
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass


def set_env_value(key, value):
    """Set KEY=value in folio.env (create the file from the template if it's missing)."""
    if not os.path.isfile(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
    lines = open(ENV_FILE, encoding="utf-8").read().splitlines()
    out, found = [], False
    for ln in lines:
        if re.match(r"\s*" + re.escape(key) + r"\s*=", ln):
            out.append("%s=%s" % (key, value))
            found = True
        else:
            out.append(ln)
    if not found:
        out.append("%s=%s" % (key, value))
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


CF_HOME = os.path.join(os.path.expanduser("~"), ".cloudflared")
TUNNEL_NAME = "folio"


def _cf(*args):
    return subprocess.run([CF] + list(args), capture_output=True, text=True)


def _domain_ready():
    return os.path.isfile(os.path.join(CF_HOME, "cert.pem"))


def _ensure_tunnel_credentials(host):
    """Restore the local runner credential for an existing tunnel when only cert.pem remains."""
    result = _cf("tunnel", "list", "--output", "json")
    if result.returncode:
        say("Couldn't check the Cloudflare tunnel for %s." % host)
        say((result.stderr or result.stdout or "Cloudflare returned an error.").strip()[:300])
        return False
    try:
        tunnels = json.loads(result.stdout or "[]")
        tunnel = next(t for t in tunnels if t.get("name") == TUNNEL_NAME and not t.get("deleted_at", "").startswith("20"))
    except (ValueError, TypeError, StopIteration):
        say("Couldn't find the existing '%s' tunnel in your Cloudflare account." % TUNNEL_NAME)
        say("Run setup-domain.bat to reconnect the domain, then start Folio again.")
        return False

    tunnel_id = tunnel.get("id", "")
    credential = os.path.join(CF_HOME, tunnel_id + ".json")
    if os.path.isfile(credential) and os.path.getsize(credential) > 0:
        return True

    say("Restoring the missing local credentials for the existing '%s' tunnel…" % TUNNEL_NAME)
    result = _cf("tunnel", "token", "--cred-file", credential, tunnel_id)
    if result.returncode or not os.path.isfile(credential) or os.path.getsize(credential) == 0:
        say("Couldn't restore the tunnel credentials.")
        say((result.stderr or result.stdout or "Cloudflare returned no credentials.").strip()[:300])
        return False
    say("Tunnel credentials restored.")
    return True


def _configure_domain(host):
    """Interactive Cloudflare setup for `host`: sign in, make the tunnel, point the domain at it.
    Returns True once it's ready. Safe to call from start-folio.bat the first time."""
    if not ensure_cloudflared():
        return False
    print()
    say("A browser will open. Pick the domain '%s' and click Authorize." % host)
    _cf("tunnel", "login")
    if not _domain_ready():
        say("Cloudflare sign-in didn't finish.")
        return False
    if TUNNEL_NAME not in (_cf("tunnel", "list").stdout or ""):
        say("Creating the tunnel…")
        r = _cf("tunnel", "create", TUNNEL_NAME)
        if r.returncode:
            say(r.stderr.strip()[:300])
    say("Pointing %s at Folio…" % host)
    r = _cf("tunnel", "route", "dns", "--overwrite-dns", TUNNEL_NAME, host)
    if r.returncode and "already" not in (r.stderr or "").lower():
        say(r.stderr.strip()[:300])
    set_env_value("FOLIO_TUNNEL_HOSTNAME", host)
    return _domain_ready()


def setup_domain():
    """One-time (via setup-domain.bat): ask for the domain, then connect it."""
    env = {}
    if os.path.isfile(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    host = env.get("FOLIO_TUNNEL_HOSTNAME") or input("  Your domain (e.g. folio.gripe): ").strip().lower()
    if not host:
        say("No domain given.")
        input("Press Enter to close.")
        return
    ok = _configure_domain(host)
    print()
    if ok:
        say("Done! Folio will always be at:  https://%s" % host)
        say("Start it with start-folio.bat. (DNS can take a few minutes the very first time.)")
    else:
        say("Setup didn't finish — run setup-domain.bat again.")
    input("Press Enter to close.")


def main():
    if "--update" in sys.argv:
        update()
        input("Press Enter to close.")
        return
    if "--setup-domain" in sys.argv:
        setup_domain()
        return
    if "--enable-browser" in sys.argv or "--disable-browser" in sys.argv:
        on = "--enable-browser" in sys.argv
        set_env_value("FOLIO_BROWSER_TAB", "1" if on else "")
        print()
        say("The personal Browser tab is now %s." % ("ON" if on else "OFF"))
        say("Start Folio (start-folio.bat), or if it's already running, close that window and start it again.")
        input("Press Enter to close.")
        return

    env = load_env()
    if not env:
        input("Press Enter to close.")
        return
    if not ensure_deps() or not ensure_cloudflared():
        input("Press Enter to close.")
        return

    # Auto-update on launch: pull the latest from GitHub before starting, unless turned off
    # (set FOLIO_NO_AUTOUPDATE=1 in folio.env to skip). Offline? the check just falls through.
    skip_update = env.get("FOLIO_NO_AUTOUPDATE") == "1" or os.environ.get("FOLIO_JUST_UPDATED") == "1"
    latest = "" if skip_update else _latest_sha(3)
    if latest and _saved_sha() and latest != _saved_sha():
        say("A newer version of Folio is available — updating now…")
        if update(auto=True, sha=latest):
            say("Relaunching with the update…")
            try:
                sys.stdout.flush()
                os.environ["FOLIO_JUST_UPDATED"] = "1"  # one-shot guard so the relaunch can't loop
                os.execv(sys.executable, [sys.executable, os.path.join(HERE, "pc_launcher.py")] + sys.argv[1:])
            except Exception as e:
                say("Couldn't relaunch automatically (%s). Close this window and run start-folio.bat again." % e)
                input("Press Enter to close.")
                return
        # update failed (e.g. network dropped) → carry on with the version already on disk
    elif latest and not _saved_sha():
        try:
            open(VERSION_FILE, "w", encoding="utf-8").write(latest)  # remember where we are now
        except Exception:
            pass

    os.environ.update(env)
    os.environ.update(FOLIO_CLASSROOM="1", HOST="127.0.0.1", PORT=PORT,
                      FOLIO_DATA=os.path.join(HERE, "folio-data"))
    host = os.environ.get("FOLIO_TUNNEL_HOSTNAME", "").strip().lower()
    if host and _domain_ready() and not _ensure_tunnel_credentials(host):
        say("Folio will not start its public tunnel until the credentials are restored.")
        input("Press Enter to close.")
        return

    web = subprocess.Popen([sys.executable, os.path.join(HERE, "webapp.py")], cwd=HERE)
    say("Starting Folio…")
    if not wait_up():
        say("Folio didn't start — check the messages above.")
        web.terminate()
        input("Press Enter to close.")
        return

    if host and not _domain_ready():  # domain set but not connected yet → do the one-time setup now
        say("First time with your domain '%s' — let's connect it to Cloudflare (one time)." % host)
        _configure_domain(host)
    bar = "=" * 60
    if host and _domain_ready():
        # your own domain → a stable link that never changes
        print("\n" + bar)
        say("Folio address:  https://%s" % host)
        say("Password: the APP_PASSWORD you put in folio.env")
        print(bar)
        say("Keep this window open. Cloudflare connection messages appear below.\n")
        run_domain_tunnel(web, host)
        return

    if host and not _domain_ready():
        say("Your domain '%s' isn't set up yet — run setup-domain.bat once. Using a temporary link for now." % host)
    say("Opening the public link…")
    cf = subprocess.Popen([CF, "tunnel", "--protocol", TUNNEL_PROTOCOL, "--url",
                           "http://127.0.0.1:%s" % PORT, "--no-autoupdate"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    shown = False
    try:
        for line in cf.stdout:
            m = re.search(r"https://[-\w.]+\.trycloudflare\.com", line)
            if m and not shown:
                shown = True
                url = m.group(0)
                print("\n" + bar)
                say("Folio is online. On your Chromebook, open:")
                say("  " + url)
                say("Password: the APP_PASSWORD you put in folio.env")
                print(bar)
                say("Keep this window open. Closing it takes Folio offline.")
                say("The link changes each time (set a fixed domain with setup-domain.bat).\n")
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
