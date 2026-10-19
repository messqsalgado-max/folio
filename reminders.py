"""
Due-date reminders: Windows notifications for missing work and things due today or tomorrow,
based on the last Classroom list Folio loaded. Each reminder shows at most once a day.
"""

import datetime as dt
import json
import os
import re
import subprocess

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], 1)}


def due_date(due, seen_on):
    """Best guess at the calendar date of a Classroom due label, e.g. 'Friday, Oct 30' or 'Thursday, 11:59 PM'."""
    t = (due or "").lower()
    if not t or "posted" in t or "no due" in t:
        return None
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(?:, (\d{4}))?", t)
    if m:
        year = int(m.group(3)) if m.group(3) else seen_on.year
        d = dt.date(year, MONTHS[m.group(1)], int(m.group(2)))
        if not m.group(3) and d < seen_on - dt.timedelta(days=180):
            d = d.replace(year=year + 1)
        return d
    if t.startswith("today"):
        return seen_on
    if t.startswith("tomorrow"):
        return seen_on + dt.timedelta(days=1)
    for i, w in enumerate(WEEKDAYS):
        if t.startswith(w):  # a bare weekday means the coming one (Classroom shows dates this close that way)
            return seen_on + dt.timedelta(days=(i - seen_on.weekday()) % 7)
    return None


def due_soon(cache, today=None):
    """[(kind, title, text)] reminders worth showing now, from the cached Classroom list."""
    today = today or dt.date.today()
    try:
        seen_on = dt.datetime.fromisoformat(cache.get("when")).date()
    except Exception:
        seen_on = today
    out = []
    missing = cache.get("missing") or []
    if missing:
        out.append(("missing", f"{len(missing)} missing assignment{'s' if len(missing) > 1 else ''}",
                    "Oldest first: " + "; ".join(m["title"] for m in missing[:3])))
    for it in cache.get("assigned") or []:
        d = due_date(it.get("due"), seen_on)
        if d == today:
            out.append(("today:" + it["title"], f"Due today: {it['title']}", it.get("course", "")))
        elif d == today + dt.timedelta(days=1):
            out.append(("tomorrow:" + it["title"], f"Due tomorrow: {it['title']}", it.get("course", "")))
    return out


def notify(title, text):
    """A Windows notification (shown by Windows PowerShell's built-in notifier). Returns True if sent."""
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    xml = (f'<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text>'
           f'<text>{esc(text)}</text><text placement="attribution">Folio</text></binding></visual></toast>')
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
          "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] > $null;"
          f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{xml}');"
          "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
          "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)")
    try:
        import base64  # an encoded command can't be broken by quotes in assignment titles
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], capture_output=True,
                           timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def check(cache_path, state_path, today=None, send=notify):
    """Show today's reminders that haven't been shown yet. Returns the titles shown."""
    today = today or dt.date.today()
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return []
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    shown_today = set(state.get(today.isoformat(), []))
    shown = []
    for key, title, text in due_soon(cache, today):
        if key in shown_today:
            continue
        if send(title, text):
            shown_today.add(key)
            shown.append(title)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({today.isoformat(): sorted(shown_today)}, f)  # only today's record is kept
    return shown
