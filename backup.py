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
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile

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

    try:
        sys.path.insert(0, BASE)
        from agent_chat_server import hub_event
        hub_event("backup", f"{os.path.basename(archive)} ({size:.0f} KB)",
                  "system", app="key")
    except Exception:
        pass   # a backup that ran is worth more than a feed entry


if __name__ == "__main__":
    main()
