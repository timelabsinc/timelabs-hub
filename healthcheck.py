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
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from shopify_scopes import REQUESTED_SCOPES, normalized_scopes

BASE = "/root/ops-dashboard"
STATE = os.path.join(BASE, "data", ".health-state.json")

SERVICES = ["nginx", "oauth2-proxy", "ops-agent-chat", "drop",
            "ops-dashboard-refresh"]
TIMERS = ["ops-dashboard.timer", "ops-order-sync.timer", "labs-backup.timer"]
PROBES = [
    ("agent API", "http://127.0.0.1:8901/supplier/arrears",
     {"X-User-Email": "timelabs.inc@gmail.com"}, 200),
    ("drop API", "http://127.0.0.1:8903/list?path=",
     {"X-User-Email": "timelabs.inc@gmail.com"}, 200),
    ("drop anonymous denial", "http://127.0.0.1:8903/list?path=", {}, 403),
]
DATABASES = [os.path.join(BASE, "data", "hermes.db"),
             os.path.join(BASE, "data", "suppliers.db")]
DISK_MIN_PCT_FREE = 10
DISK_MIN_FREE_GB = 22
BACKUP_MAX_AGE_H = 30      # nightly at 03:40, so 30h means one was missed
SHEET_MIRROR_STATE = os.path.join(BASE, "data", ".sheet-mirror-state.json")


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


def check_timers():
    out = {}
    for unit in TIMERS:
        try:
            r = subprocess.run(["systemctl", "is-active", unit],
                               capture_output=True, text=True, timeout=10)
            ok = r.stdout.strip() == "active"
        except Exception:
            ok = False
        out[f"timer:{unit}"] = (ok, f"{unit} is not active")
    return out


def check_probes():
    out = {}
    for label, url, headers, expected in PROBES:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
            ok = status == expected
            detail = f"{label} returned {status}, expected {expected}"
        except urllib.error.HTTPError as e:
            ok = e.code == expected
            detail = f"{label} returned {e.code}, expected {expected}"
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
            photo_error = ""
            if name == "hermes.db" and ok:
                required = {
                    "webchat_sessions": {"owner_email", "visibility"},
                    "supplier_bill_items": {"quantity"},
                    "orders": {
                        "order_no", "is_stock", "supplier_visible",
                        "shopify_contact_fingerprint",
                        "shopify_contact_override",
                        "shopify_conflict_fingerprint",
                    },
                    "order_number_seq": {"id", "last_value"},
                    "reddit_posts": {"status", "started_at", "queued_at"},
                    "reddit_drafts": {"status", "started_at", "queued_at"},
                }
                for table, want in required.items():
                    have = {
                        row[1] for row in con.execute(
                            f"PRAGMA table_info({table})")}
                    missing = sorted(want - have)
                    if missing:
                        photo_error = (
                            f"{table} schema is missing {', '.join(missing)}")
                        break
                indexes = {
                    row[1] for row in con.execute("PRAGMA index_list(orders)")}
                if not photo_error and "idx_orders_order_no" not in indexes:
                    photo_error = "orders is missing its unique order-number index"
                if not photo_error:
                    stale_jobs = con.execute(
                        "SELECT "
                        "(SELECT COUNT(*) FROM reddit_posts "
                        " WHERE status='running' AND "
                        " (started_at IS NULL OR started_at < datetime('now','-2 hours'))) + "
                        "(SELECT COUNT(*) FROM reddit_drafts "
                        " WHERE status='running' AND "
                        " (started_at IS NULL OR started_at < datetime('now','-2 hours')))"
                    ).fetchone()[0]
                    if stale_jobs:
                        photo_error = (
                            f"{stale_jobs} Reddit content job(s) are stuck running")
                if not photo_error:
                    stale_queue = con.execute(
                        "SELECT "
                        "(SELECT COUNT(*) FROM reddit_posts "
                        " WHERE status='queued' AND "
                        " (queued_at IS NULL OR queued_at < datetime('now','-15 minutes'))) + "
                        "(SELECT COUNT(*) FROM reddit_drafts "
                        " WHERE status='queued' AND "
                        " (queued_at IS NULL OR queued_at < datetime('now','-15 minutes')))"
                    ).fetchone()[0]
                    if stale_queue:
                        photo_error = (
                            f"{stale_queue} Reddit content job(s) are stuck queued")
            if name == "hermes.db" and ok and not photo_error:
                for oid, raw in con.execute(
                        "SELECT id, local_photos FROM orders "
                        "WHERE local_photos IS NOT NULL AND local_photos != ''"):
                    try:
                        photos = json.loads(raw or "[]")
                    except (TypeError, json.JSONDecodeError):
                        photo_error = f"order {oid} has invalid photo metadata"
                        break
                    for photo in photos:
                        path = os.path.join(
                            BASE, "data", "order-photos", str(oid),
                            os.path.basename(str(photo)))
                        if not os.path.isfile(path):
                            photo_error = (
                                f"order {oid} references missing photo "
                                f"{os.path.basename(str(photo))}")
                            break
                    if photo_error:
                        break
            if photo_error:
                ok = False
            con.close()
            detail = photo_error or f"{name} failed its integrity check"
        except Exception as e:
            ok, detail = False, f"{name} unreadable: {e}"
        out[f"db:{name}"] = (ok, detail)
    return out


def check_disk():
    du = shutil.disk_usage("/")
    pct_free = du.free * 100.0 / du.total
    enough = pct_free >= DISK_MIN_PCT_FREE and du.free >= DISK_MIN_FREE_GB * 1e9
    return {"disk": (enough,
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
        with open(os.path.join(d, ".labs-backup-status.json")) as f:
            status = json.load(f)
        status_age_h = (time.time() - float(status.get("updated", 0))) / 3600
        archive = status.get("archive") or ""
        ok = (
            age_h <= BACKUP_MAX_AGE_H
            and status_age_h <= BACKUP_MAX_AGE_H
            and bool(status.get("local_ok"))
            and bool(status.get("offsite_ok"))
            and os.path.isfile(archive)
        )
        detail = (
            f"last verified local+offsite backup was {status_age_h:.0f}h ago"
            if ok else
            "no recent verified local+offsite backup"
            + (f": {status.get('error')}" if status.get("error") else "")
        )
        return {"backup": (ok, detail)}
    except Exception as e:
        return {"backup": (False, f"no verified backup status: {e}")}


def check_sheet_mirror():
    """Keep a failed DB-to-Sheets batch visible until a later batch succeeds."""
    try:
        with open(SHEET_MIRROR_STATE) as source:
            state = json.load(source)
    except FileNotFoundError:
        # Older installs have no status yet. The first Shopify batch or
        # controlled full mirror rebuild creates one.
        return {"orders:sheet-mirror": (True, "no Sheet mirror failure recorded")}
    except Exception as exc:
        return {"orders:sheet-mirror": (
            False, f"Sheet mirror status is unreadable: {exc}")}
    if state.get("ok") is True:
        return {"orders:sheet-mirror": (True, "Google Sheets mirror is current")}
    pending = max(0, int(state.get("pending") or 0))
    detail = str(state.get("detail") or "write failed")
    return {"orders:sheet-mirror": (
        False, f"Google Sheets mirror is behind ({pending} row(s)): {detail}")}


def check_shopify_scopes():
    """Alert when the live token exceeds the code's reviewed scope contract."""
    values = {}
    try:
        with open(os.path.join(BASE, ".env")) as source:
            for line in source:
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, value = line.rstrip("\n").split("=", 1)
                if key in ("SHOPIFY_SHOP", "SHOPIFY_ADMIN_TOKEN"):
                    values[key] = value.strip().strip('"').strip("'")
    except OSError as exc:
        return {"shopify:scopes": (False, f"Shopify configuration unreadable: {exc}")}
    shop = values.get("SHOPIFY_SHOP", "").strip().lower()
    token = values.get("SHOPIFY_ADMIN_TOKEN", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", shop) or not token:
        return {"shopify:scopes": (False, "Shopify token is not configured")}
    try:
        request = urllib.request.Request(
            f"https://{shop}/admin/oauth/access_scopes.json",
            headers={"X-Shopify-Access-Token": token, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
        granted = {
            str(item.get("handle") or "").strip()
            for item in payload.get("access_scopes") or []
            if str(item.get("handle") or "").strip()
        }
    except Exception as exc:
        return {"shopify:scopes": (
            False, f"Shopify scope audit could not run: {exc}")}
    expected = set(REQUESTED_SCOPES)
    missing = expected - normalized_scopes(granted)
    unexpected = granted - expected
    ok = not missing and not unexpected
    if missing:
        detail = f"Shopify token is missing {len(missing)} required scope(s)"
    elif unexpected:
        detail = (
            f"Shopify token has {len(unexpected)} unexpected grant(s); "
            "narrow the app scopes and reconnect")
    else:
        detail = "Shopify token matches the reviewed scope contract"
    return {"shopify:scopes": (ok, detail)}


def collect_results():
    """Run every side-effect-free check.

    Both the timer and the generated Home page consume this one definition so
    the dashboard cannot claim an obsolete service is healthy while the real
    monitor is checking something else.
    """
    results = {}
    for fn in (check_services, check_timers, check_probes, check_databases, check_disk,
               check_backup, check_sheet_mirror, check_shopify_scopes):
        try:
            results.update(fn())
        except Exception as e:
            results[f"check:{fn.__name__}"] = (
                False, f"{fn.__name__} errored: {e}")
    return results


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
        result = subprocess.run(
            ["hermes", "send", "--to", target, "--quiet", body],
            capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(
                "[health] delivery failed: "
                + (result.stderr or result.stdout or "hermes send failed")[-300:],
                flush=True)
    except Exception as e:
        print(f"[health] could not send: {e}", flush=True)


def main():
    results = collect_results()

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
