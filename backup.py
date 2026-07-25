#!/usr/bin/env python3
"""Nightly backup of everything that can't be rebuilt.

Why this exists: the stress test on 2026-07-25 found the whole business
record — orders, customers, supplier batches, the Ledger — sitting on one
VPS disk with no scheduled backup at all. The newest copy of anything was a
hand-made tarball five days old. A failed disk or one bad migration would
have taken the lot.

What it does NOT back up, deliberately: generated pages (/var/www/ops/*.html
are rebuilt from the generators in seconds) and Drop's media, which is large
and already mirrored to Google Drive. This is for the small, irreplaceable
things.

Databases go through sqlite3's own backup API rather than a file copy,
because copying a database mid-write yields a file that looks fine and
restores corrupt — the failure you find out about on the day you need it.

Stdlib only: runs on system python from a systemd timer.
"""
import datetime
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "/root/ops-dashboard"
DEST = "/root/backups"
KEEP = 14          # nightly copies; ~two weeks of "undo the thing we broke"

DATABASES = [
    os.path.join(BASE, "data", "hermes.db"),
    os.path.join(BASE, "data", "suppliers.db"),
]
# Small files that are configuration or credentials — irreplaceable, and a
# pain to reconstruct from memory.
EXTRAS = [
    os.path.join(BASE, ".env"),
    os.path.join(BASE, "access.json"),
    "/etc/oauth2-proxy/allowlist.txt",
    "/etc/nginx/conf.d/labs-access.conf",
]


# --------------------------------------------------------------- offsite copy
# A backup sitting on the disk it protects survives a bad migration but not a
# dead VPS. These push the nightly archive to the same Google Drive account
# Drop already uses, so there is a copy that doesn't depend on this machine.
GDRIVE_TOKEN = "/root/ops-dashboard/.gdrive-token.json"
OAUTH_CLIENT = "/root/oauth-client.json"
DRIVE_FOLDER = "Labs OS backups"


def _drive_token():
    """A valid access token, refreshed if needed. Mirrors drop_server's own
    refresh so both share one stored credential."""
    try:
        tok = json.load(open(GDRIVE_TOKEN))
    except (OSError, json.JSONDecodeError):
        return None
    if tok.get("exp", 0) > time.time() + 60:
        return tok.get("access_token")
    try:
        c = json.load(open(OAUTH_CLIENT))
        c = c.get("web") or c.get("installed") or {}
    except (OSError, json.JSONDecodeError):
        return None
    if not c.get("client_id") or not tok.get("refresh_token"):
        return None
    body = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c.get("client_secret"),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request("https://oauth2.googleapis.com/token",
                                       data=body), timeout=20) as r:
            fresh = json.loads(r.read())
    except Exception:
        return None
    tok["access_token"] = fresh["access_token"]
    tok["exp"] = time.time() + fresh.get("expires_in", 3500)
    try:
        with open(GDRIVE_TOKEN, "w") as f:
            json.dump(tok, f)
    except OSError:
        pass
    return tok["access_token"]


def _drive_folder_id(access):
    q = urllib.parse.quote(
        f"name='{DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder'"
        " and trashed=false")
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)",
        headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            files = json.loads(r.read()).get("files") or []
        if files:
            return files[0]["id"]
    except Exception:
        return None
    body = json.dumps({"name": DRIVE_FOLDER,
                       "mimeType": "application/vnd.google-apps.folder"}).encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files?fields=id", data=body,
        method="POST", headers={"Authorization": f"Bearer {access}",
                                "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("id")
    except Exception:
        return None


def push_offsite(archive):
    """Returns a short status string; never raises. A failed upload must not
    lose the local backup that already succeeded."""
    access = _drive_token()
    if not access:
        return "offsite: Drive not connected"
    folder = _drive_folder_id(access)
    meta = {"name": os.path.basename(archive)}
    if folder:
        meta["parents"] = [folder]
    boundary = "labs" + secrets.token_hex(12)
    try:
        data = open(archive, "rb").read()
    except OSError as e:
        return f"offsite failed: {e}"
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + json.dumps(meta).encode()
            + f"\r\n--{boundary}\r\nContent-Type: application/gzip\r\n\r\n".encode()
            + data + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {access}",
                 "Content-Type": f"multipart/related; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            json.loads(r.read())
        return f"offsite: copied to Drive/{DRIVE_FOLDER}"
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if e.code in (401, 403):
            return ("offsite failed: Drive is connected read-only — reconnect it "
                    "from Drop to allow writing")
        return f"offsite failed: {msg[:120] or e.code}"
    except Exception as e:
        return f"offsite failed: {e}"


def snapshot_db(src, outdir):
    """A consistent copy of a live sqlite database, then read it back to
    prove the copy is actually usable."""
    name = os.path.basename(src)
    dst = os.path.join(outdir, name)
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    out = sqlite3.connect(dst)
    with out:
        con.backup(out)
    out.close()
    con.close()
    check = sqlite3.connect(dst)
    ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    tables = check.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    check.close()
    if ok != "ok":
        raise RuntimeError(f"{name} snapshot failed integrity check: {ok}")
    return tables


def main():
    os.makedirs(DEST, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    archive = os.path.join(DEST, f"labs-os-{stamp}.tar.gz")
    notes = []

    with tempfile.TemporaryDirectory() as tmp:
        for db in DATABASES:
            if not os.path.isfile(db):
                notes.append(f"missing: {db}")
                continue
            tables = snapshot_db(db, tmp)
            notes.append(f"{os.path.basename(db)}: {tables} tables")
        for extra in EXTRAS:
            if os.path.isfile(extra):
                shutil.copy2(extra, os.path.join(tmp, os.path.basename(extra)))
            else:
                notes.append(f"missing: {extra}")
        with tarfile.open(archive, "w:gz") as tar:
            for f in sorted(os.listdir(tmp)):
                tar.add(os.path.join(tmp, f), arcname=f)

    # Contains .env, so it is not world-readable.
    os.chmod(archive, 0o600)

    kept = sorted(f for f in os.listdir(DEST) if f.startswith("labs-os-"))
    for old in kept[:-KEEP]:
        os.remove(os.path.join(DEST, old))

    size = os.path.getsize(archive) / 1024
    print(f"wrote {archive} ({size:.0f} KB) — " + "; ".join(notes))
    print(f"holding {len(kept[-KEEP:])} of {KEEP} nightly copies")
    offsite = push_offsite(archive)
    print(offsite)

    try:
        sys.path.insert(0, BASE)
        from agent_chat_server import hub_event
        hub_event("backup", f"{os.path.basename(archive)} ({size:.0f} KB)",
                  "system", app="key")
    except Exception:
        pass   # a backup that ran is worth more than a feed entry


if __name__ == "__main__":
    main()
