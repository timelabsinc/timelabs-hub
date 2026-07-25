#!/usr/bin/env python3
"""Watches the things that, when they break, nobody finds out until someone
complains.

Before this, if ops-agent-chat died at 2am the first sign was Hannan not
being able to load his queue. This runs every 15 minutes and messages the
team WhatsApp when something breaks — and again when it comes back, because
"is it fixed yet?" is the next question after "it's broken".

Deliberately only speaks on a CHANGE of state. A monitor that reports "all
fine" every fifteen minutes is a monitor people mute, and a muted monitor is
worse than none: it's the appearance of cover without the cover.

Stdlib only; runs on system python from a systemd timer.
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

BASE = "/root/ops-dashboard"
STATE = os.path.join(BASE, "data", ".health-state.json")

SERVICES = ["nginx", "oauth2-proxy", "ops-agent-chat", "drop"]
PROBES = [
    ("agent API", "http://127.0.0.1:8901/supplier/arrears",
     {"X-User-Email": "timelabs.inc@gmail.com"}),
    ("drop API", "http://127.0.0.1:8903/list?path=", {}),
]
DATABASES = [os.path.join(BASE, "data", "hermes.db"),
             os.path.join(BASE, "data", "suppliers.db")]
DISK_MIN_PCT_FREE = 10
BACKUP_MAX_AGE_H = 30      # nightly at 03:40, so 30h means one was missed


def check_services():
    out = {}
    for unit in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", unit],
                               capture_output=True, text=True, timeout=10)
            ok = r.stdout.strip() == "active"
        except Exception:
            ok = False
        out[f"service:{unit}"] = (ok, f"{unit} is not running")
    return out


def check_probes():
    out = {}
    for label, url, headers in PROBES:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                ok = r.status == 200
            detail = f"{label} did not return 200"
        except Exception as e:
            ok, detail = False, f"{label} unreachable: {e}"
        out[f"probe:{label}"] = (ok, detail)
    return out


def check_databases():
    out = {}
    for db in DATABASES:
        name = os.path.basename(db)
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            con.close()
            detail = f"{name} failed its integrity check"
        except Exception as e:
            ok, detail = False, f"{name} unreadable: {e}"
        out[f"db:{name}"] = (ok, detail)
    return out


def check_disk():
    du = shutil.disk_usage("/")
    pct_free = du.free * 100.0 / du.total
    return {"disk": (pct_free >= DISK_MIN_PCT_FREE,
                     f"disk is {100 - pct_free:.0f}% full "
                     f"({du.free / 1e9:.0f} GB left)")}


def check_backup():
    """A backup job that silently stopped running is the failure this whole
    file exists to prevent, so it watches itself."""
    d = "/root/backups"
    try:
        files = [f for f in os.listdir(d) if f.startswith("labs-os-")]
        newest = max(os.path.getmtime(os.path.join(d, f)) for f in files)
        age_h = (time.time() - newest) / 3600
        return {"backup": (age_h <= BACKUP_MAX_AGE_H,
                           f"last backup was {age_h:.0f}h ago")}
    except Exception as e:
        return {"backup": (False, f"no backups found: {e}")}


def notify(lines):
    """Alerts go to their own destination, never the supplier group.

    That group is where Hannan works; "disk is 91% full" means nothing to him
    and erodes the signal of the messages that do concern him. If no alert
    destination is configured this logs and stays quiet rather than falling
    back to the supplier group — silence is better than shouting in the wrong
    room. Set HEALTH_ALERT_TARGET in .env to switch it on.
    """
    target = ""
    try:
        with open(os.path.join(BASE, ".env")) as f:
            for line in f:
                if line.startswith("HEALTH_ALERT_TARGET="):
                    target = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except OSError:
        pass
    body = "*Labs OS*\n" + "\n".join(lines)
    if not target:
        print("[health] HEALTH_ALERT_TARGET not set; logging only:\n" + body,
              flush=True)
        return
    try:
        subprocess.run(["hermes", "send", "--to", target, "--quiet", body],
                       capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"[health] could not send: {e}", flush=True)


def main():
    results = {}
    for fn in (check_services, check_probes, check_databases, check_disk,
               check_backup):
        try:
            results.update(fn())
        except Exception as e:
            print(f"[health] {fn.__name__} errored: {e}", flush=True)

    try:
        previous = json.load(open(STATE))
    except (OSError, json.JSONDecodeError):
        previous = {}

    broke, healed = [], []
    for key, (ok, detail) in results.items():
        was_ok = previous.get(key, True)     # first run assumes fine, so a
        if ok and not was_ok:                # healthy start stays quiet
            healed.append(key.split(":", 1)[-1])
        elif not ok and was_ok:
            broke.append(detail)

    lines = []
    if broke:
        lines.append("⚠️ " + ("Problem:" if len(broke) == 1 else "Problems:"))
        lines += [f"• {d}" for d in broke]
    if healed:
        lines.append("✅ Back to normal: " + ", ".join(healed))
    if lines:
        notify(lines)
        print("[health] " + " | ".join(lines).replace("\n", " "), flush=True)
    else:
        bad = [k for k, (ok, _) in results.items() if not ok]
        print(f"[health] {len(results)} checks, "
              + (f"still down: {', '.join(bad)}" if bad else "all healthy"),
              flush=True)

    json.dump({k: ok for k, (ok, _) in results.items()}, open(STATE, "w"))
    return 1 if any(not ok for ok, _ in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
