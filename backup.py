#!/usr/bin/env python3
"""Nightly backup of everything that can't be rebuilt.

Why this exists: the stress test on 2026-07-25 found the whole business
record — orders, customers, supplier batches, the Ledger — sitting on one
VPS disk with no scheduled backup at all. The newest copy of anything was a
hand-made tarball five days old. A failed disk or one bad migration would
have taken the lot.

What it does NOT back up: generated pages (/var/www/ops/*.html are rebuilt
from the generators in seconds) and the large Drop media tree. Drop needs a
separate resumable offsite mirror; this job covers the small, irreplaceable
business databases, order reference photos, durable Reddit draft media, and
current access configuration.

Databases go through sqlite3's own backup API rather than a file copy,
because copying a database mid-write yields a file that looks fine and
restores corrupt — the failure you find out about on the day you need it.
Hermes and the Ledger are copied under one attached-database writer lock, so
an acknowledged supplier bill cannot be captured without its Ledger invoice.

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
STATUS = os.path.join(DEST, ".labs-backup-status.json")
ORDER_PHOTOS = os.path.join(BASE, "data", "order-photos")
REDDIT_MEDIA = os.path.join(BASE, "data", "reddit-media")
REDDIT_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

HERMES_DB = os.path.join(BASE, "data", "hermes.db")
SUPPLIERS_DB = os.path.join(BASE, "data", "suppliers.db")
DATABASES = [HERMES_DB, SUPPLIERS_DB]
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
DRIVE_FOLDER = "Labs OS backups"


def _drive_token():
    """A valid token from the one atomic cross-process credential store."""
    sys.path.insert(0, BASE)
    import google_token_store
    return google_token_store.access_token("[backup/google]")


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
    """Return (ok, status). The caller keeps a good local archive on failure."""
    access = _drive_token()
    if not access:
        return False, "offsite: Drive not connected"
    folder = _drive_folder_id(access)
    meta = {"name": os.path.basename(archive)}
    if folder:
        meta["parents"] = [folder]
    boundary = "labs" + secrets.token_hex(12)
    try:
        data = open(archive, "rb").read()
    except OSError as e:
        return False, f"offsite failed: {e}"
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
        return True, f"offsite: copied to Drive/{DRIVE_FOLDER}"
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if e.code in (401, 403):
            return False, ("offsite failed: Drive is connected read-only — reconnect it "
                           "from Drop to allow writing")
        return False, f"offsite failed: {msg[:120] or e.code}"
    except Exception as e:
        return False, f"offsite failed: {e}"


def snapshot_db(src, outdir):
    """A consistent copy of a live sqlite database, then read it back to
    prove the copy is actually usable."""
    name = os.path.basename(src)
    dst = os.path.join(outdir, name)
    con = sqlite3.connect(_sqlite_uri(src, "ro"), uri=True, timeout=30)
    out = sqlite3.connect(dst)
    try:
        con.backup(out)
    finally:
        out.close()
        con.close()
    check = sqlite3.connect(_sqlite_uri(dst, "ro"), uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        tables = check.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        raise RuntimeError(f"{name} snapshot failed integrity check: {ok}")
    return tables


def _sqlite_uri(path, mode):
    """A SQLite file URI that cannot reinterpret path characters as options."""
    encoded = urllib.parse.quote(os.path.abspath(path), safe="/")
    return f"file:{encoded}?mode={mode}"


def _table_columns(conn, schema, table):
    return {
        row[1]
        for row in conn.execute(f"PRAGMA {schema}.table_info({table})")
    }


def validate_supplier_ledger_links(hermes_path, suppliers_path):
    """Reject a pair of snapshots whose supplier-bill/Ledger links are torn.

    The live acknowledgement path commits both databases through one attached
    SQLite transaction. A usable backup must preserve the same invariant:
    every forward link names the invoice created for that bill, and every
    supplier-queue invoice (or explicit reverse-link column in a future
    schema) points back to that same bill.
    """
    conn = sqlite3.connect(_sqlite_uri(hermes_path, "ro"), uri=True)
    try:
        conn.execute(
            "ATTACH DATABASE ? AS ledger",
            (_sqlite_uri(suppliers_path, "ro"),),
        )
        bill_columns = _table_columns(conn, "main", "supplier_bills")
        invoice_columns = _table_columns(conn, "ledger", "invoices")
        bill_required = {"id", "bill_no", "ledger_invoice_id"}
        invoice_required = {"id", "invoice_no"}
        if not bill_required.issubset(bill_columns):
            raise RuntimeError(
                "cross-database validation requires the supplier bill link schema")
        if not invoice_required.issubset(invoice_columns):
            raise RuntimeError(
                "cross-database validation requires the Ledger invoice link schema")

        forward_bad = conn.execute("""
            SELECT COUNT(*)
            FROM main.supplier_bills AS b
            LEFT JOIN ledger.invoices AS i ON i.id = b.ledger_invoice_id
            WHERE b.ledger_invoice_id IS NOT NULL
              AND (i.id IS NULL OR i.invoice_no IS NULL
                   OR i.invoice_no != b.bill_no)
        """).fetchone()[0]
        if forward_bad:
            raise RuntimeError(
                f"cross-database backup has {forward_bad} supplier bill(s) "
                "without their matching Ledger invoice")

        # Current Ledger rows created by the supplier queue carry this marker.
        # It is the reverse-side invariant until the Ledger schema gains an
        # explicit supplier_bill_id column.
        if "parsed_by" in invoice_columns:
            queue_bad = conn.execute("""
                SELECT COUNT(*)
                FROM ledger.invoices AS i
                LEFT JOIN main.supplier_bills AS b
                  ON b.ledger_invoice_id = i.id
                WHERE i.parsed_by = 'supplier-queue'
                  AND (b.id IS NULL OR i.invoice_no IS NULL
                       OR i.invoice_no != b.bill_no)
            """).fetchone()[0]
            if queue_bad:
                raise RuntimeError(
                    f"cross-database backup has {queue_bad} supplier-queue "
                    "invoice(s) without their matching supplier bill")

        # Validate a real reverse key automatically if one is added later.
        reverse_column = next(
            (name for name in
             ("supplier_bill_id", "source_supplier_bill_id", "bill_id")
             if name in invoice_columns),
            None,
        )
        if reverse_column:
            reverse_bad = conn.execute(f"""
                SELECT COUNT(*)
                FROM ledger.invoices AS i
                LEFT JOIN main.supplier_bills AS b
                  ON b.id = i.{reverse_column}
                WHERE i.{reverse_column} IS NOT NULL
                  AND (b.id IS NULL OR b.ledger_invoice_id IS NULL
                       OR b.ledger_invoice_id != i.id
                       OR i.invoice_no IS NULL OR i.invoice_no != b.bill_no)
            """).fetchone()[0]
            missing_reverse = conn.execute(f"""
                SELECT COUNT(*)
                FROM main.supplier_bills AS b
                JOIN ledger.invoices AS i ON i.id = b.ledger_invoice_id
                WHERE i.{reverse_column} IS NULL
                   OR i.{reverse_column} != b.id
            """).fetchone()[0]
            if reverse_bad or missing_reverse:
                raise RuntimeError(
                    "cross-database backup has mismatched supplier bill "
                    "reverse links")

        linked = conn.execute(
            "SELECT COUNT(*) FROM main.supplier_bills "
            "WHERE ledger_invoice_id IS NOT NULL").fetchone()[0]
        return linked
    finally:
        conn.close()


def snapshot_databases(outdir, hermes_src=HERMES_DB,
                       suppliers_src=SUPPLIERS_DB):
    """Copy both business databases while one transaction blocks all writers.

    Python's backup API cannot run on the same connection that owns an active
    write transaction. The lock connection therefore attaches both databases
    and holds BEGIN IMMEDIATE, while snapshot_db uses separate read-only
    handles. Writers to either database wait until both snapshots are done, so
    the two destination files describe one committed cross-database state.
    """
    for src in (hermes_src, suppliers_src):
        if not os.path.isfile(src):
            raise RuntimeError(f"required database is missing: {src}")

    lock = sqlite3.connect(
        _sqlite_uri(hermes_src, "rw"),
        uri=True,
        timeout=30,
        isolation_level=None,
    )
    try:
        lock.execute(
            "ATTACH DATABASE ? AS ledger",
            (_sqlite_uri(suppliers_src, "rw"),),
        )
        lock.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                os.path.basename(hermes_src): snapshot_db(hermes_src, outdir),
                os.path.basename(suppliers_src): snapshot_db(
                    suppliers_src, outdir),
            }
        finally:
            if lock.in_transaction:
                lock.rollback()
    finally:
        lock.close()

    validate_supplier_ledger_links(
        os.path.join(outdir, os.path.basename(hermes_src)),
        os.path.join(outdir, os.path.basename(suppliers_src)),
    )
    return tables


def _skip_reddit_media_name(name):
    low = name.lower()
    return name.startswith(".") or low.endswith((".tmp", ".part"))


def stage_reddit_media(outdir, required, source=REDDIT_MEDIA):
    """Stage durable Reddit draft images without transient or linked files."""
    if not os.path.lexists(source):
        return 0
    if os.path.islink(source) or not os.path.isdir(source):
        raise RuntimeError("reddit-media must be a real directory")

    destination = os.path.join(outdir, "reddit-media")
    os.makedirs(destination, mode=0o700)
    copied = 0
    for base, dirs, files in os.walk(source, followlinks=False):
        kept_dirs = []
        for dirname in dirs:
            path = os.path.join(base, dirname)
            if os.path.islink(path):
                raise RuntimeError("reddit-media contains a directory symlink")
            if _skip_reddit_media_name(dirname):
                continue
            kept_dirs.append(dirname)
            os.makedirs(
                os.path.join(destination, os.path.relpath(path, source)),
                mode=0o700,
                exist_ok=True,
            )
        dirs[:] = kept_dirs

        for filename in files:
            path = os.path.join(base, filename)
            if os.path.islink(path):
                raise RuntimeError("reddit-media contains a file symlink")
            if _skip_reddit_media_name(filename):
                continue
            if not os.path.isfile(path):
                raise RuntimeError("reddit-media contains a non-regular file")
            if (os.path.splitext(filename)[1].lower()
                    not in REDDIT_MEDIA_EXTENSIONS):
                raise RuntimeError(
                    "reddit-media contains an unexpected non-image file")
            rel = os.path.relpath(path, source)
            target = os.path.join(destination, rel)
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            shutil.copy2(path, target)
            required.append(os.path.join("reddit-media", rel))
            copied += 1
    return copied


def _reddit_media_expectations(hermes_path):
    """Return (post ids, exact archive members) from persisted draft paths."""
    conn = sqlite3.connect(_sqlite_uri(hermes_path, "ro"), uri=True)
    try:
        columns = _table_columns(conn, "main", "reddit_posts")
        if not {"id", "photos"}.issubset(columns):
            raise RuntimeError(
                "reddit-media validation requires the Reddit posts schema")
        rows = conn.execute("SELECT id, photos FROM reddit_posts").fetchall()
    finally:
        conn.close()

    post_ids = set()
    expected_files = set()
    for post_id, raw_photos in rows:
        post_id = int(post_id)
        post_ids.add(post_id)
        try:
            photos = json.loads(raw_photos or "[]")
        except (TypeError, json.JSONDecodeError):
            raise RuntimeError("reddit_posts contains invalid photos JSON")
        if not isinstance(photos, list):
            raise RuntimeError("reddit_posts photos must be a list")
        for raw_path in photos:
            if not isinstance(raw_path, str):
                raise RuntimeError("reddit_posts contains a non-text media path")
            filename = os.path.basename(raw_path)
            stem, extension = os.path.splitext(filename)
            expected_path = os.path.join(
                REDDIT_MEDIA, str(post_id), filename)
            if (raw_path != expected_path
                    or not stem.isascii()
                    or not stem.isdigit()
                    or str(int(stem)) != stem
                    or int(stem) < 1
                    or extension.lower() not in REDDIT_MEDIA_EXTENSIONS):
                raise RuntimeError(
                    "reddit_posts contains a media path outside its exact "
                    "per-post image directory")
            member = f"reddit-media/{post_id}/{filename}"
            if member in expected_files:
                raise RuntimeError(
                    "reddit_posts contains a duplicate persisted media path")
            expected_files.add(member)
    return post_ids, expected_files


def _staged_reddit_media_manifest(media_root):
    files, directories = set(), set()
    if not os.path.lexists(media_root):
        return files, directories
    if os.path.islink(media_root) or not os.path.isdir(media_root):
        raise RuntimeError("staged reddit-media must be a real directory")
    for base, dirs, names in os.walk(media_root, followlinks=False):
        for dirname in dirs:
            path = os.path.join(base, dirname)
            if os.path.islink(path):
                raise RuntimeError("staged reddit-media contains a symlink")
            rel = os.path.relpath(path, media_root)
            directories.add("reddit-media/" + rel.replace(os.sep, "/"))
        for filename in names:
            path = os.path.join(base, filename)
            if os.path.islink(path) or not os.path.isfile(path):
                raise RuntimeError(
                    "staged reddit-media contains a non-regular file")
            rel = os.path.relpath(path, media_root)
            files.add("reddit-media/" + rel.replace(os.sep, "/"))
    return files, directories


def validate_reddit_media_links(hermes_path, *, media_root=None,
                                archive_members=None):
    """Prove Reddit database paths and staged/archive media are one manifest."""
    if (media_root is None) == (archive_members is None):
        raise ValueError("provide exactly one Reddit media source")
    post_ids, expected_files = _reddit_media_expectations(hermes_path)
    if media_root is not None:
        actual_files, directories = _staged_reddit_media_manifest(media_root)
    else:
        actual_files = {
            name for name, member in archive_members.items()
            if name.startswith("reddit-media/") and member.isfile()
        }
        directories = {
            name.rstrip("/") for name, member in archive_members.items()
            if name.startswith("reddit-media/") and member.isdir()
        }

    missing = expected_files - actual_files
    unexpected = actual_files - expected_files
    if missing:
        raise RuntimeError(
            f"reddit-media backup is missing {len(missing)} persisted image(s)")
    if unexpected:
        raise RuntimeError(
            f"reddit-media backup has {len(unexpected)} unreferenced image(s)")

    for directory in directories:
        parts = directory.split("/")
        if (len(parts) != 2 or not parts[1].isascii()
                or not parts[1].isdigit()
                or str(int(parts[1])) != parts[1]):
            raise RuntimeError(
                "reddit-media contains a directory outside the per-post layout")
        if int(parts[1]) not in post_ids:
            raise RuntimeError(
                "reddit-media contains an orphan per-post directory")

    required_directories = {
        member.rsplit("/", 1)[0] for member in expected_files
    }
    if not required_directories.issubset(directories):
        raise RuntimeError(
            "reddit-media backup is missing a persisted post directory")
    return len(expected_files)


def _write_status(payload):
    os.makedirs(DEST, exist_ok=True)
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, STATUS)
    os.chmod(STATUS, 0o600)


def verify_archive(archive, required):
    """Read the finished tar back, verify its inventory and both SQLite files."""
    with tarfile.open(archive, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        unsafe = [name for name in members
                  if name.startswith("/") or ".." in name.split("/")]
        if unsafe:
            raise RuntimeError("archive contains unsafe member names")
        bad_reddit_media = []
        for name, member in members.items():
            if name != "reddit-media" and not name.startswith("reddit-media/"):
                continue
            rel_parts = name.split("/")[1:]
            if (any(_skip_reddit_media_name(part) for part in rel_parts)
                    or not (member.isdir() or member.isfile())
                    or (member.isfile() and os.path.splitext(name)[1].lower()
                        not in REDDIT_MEDIA_EXTENSIONS)):
                bad_reddit_media.append(name)
        if bad_reddit_media:
            raise RuntimeError(
                "archive contains unsafe or transient reddit-media entries")
        missing = sorted(set(required) - set(members))
        if missing:
            raise RuntimeError("archive is missing: " + ", ".join(missing[:8]))
        with tempfile.TemporaryDirectory() as checkdir:
            extracted = {}
            for name in (os.path.basename(p) for p in DATABASES):
                src = tar.extractfile(members[name])
                if src is None:
                    raise RuntimeError(f"archive member {name} is unreadable")
                dst = os.path.join(checkdir, name)
                with open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted[name] = dst

            for name, dst in extracted.items():
                con = sqlite3.connect(_sqlite_uri(dst, "ro"), uri=True)
                try:
                    result = con.execute(
                        "PRAGMA integrity_check").fetchone()[0]
                    if name == os.path.basename(HERMES_DB):
                        for oid, raw in con.execute(
                                "SELECT id, local_photos FROM orders "
                                "WHERE local_photos IS NOT NULL "
                                "AND local_photos != ''"):
                            try:
                                photos = json.loads(raw or "[]")
                            except (TypeError, json.JSONDecodeError):
                                raise RuntimeError(
                                    f"order {oid} has invalid local_photos JSON")
                            for photo in photos:
                                expected = (
                                    f"order-photos/{oid}/"
                                    f"{os.path.basename(str(photo))}")
                                if expected not in members:
                                    raise RuntimeError(
                                        f"archive is missing order {oid} photo "
                                        f"{os.path.basename(str(photo))}")
                finally:
                    con.close()
                if result != "ok":
                    raise RuntimeError(
                        f"archived {name} failed integrity check: {result}")

            validate_supplier_ledger_links(
                extracted[os.path.basename(HERMES_DB)],
                extracted[os.path.basename(SUPPLIERS_DB)],
            )
            validate_reddit_media_links(
                extracted[os.path.basename(HERMES_DB)],
                archive_members=members,
            )


def main():
    os.makedirs(DEST, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive = os.path.join(DEST, f"labs-os-{stamp}.tar.gz")
    notes = []
    status = {"updated": int(time.time()), "archive": archive,
              "local_ok": False, "offsite_ok": False}
    try:
        required = []
        with tempfile.TemporaryDirectory() as tmp:
            table_counts = snapshot_databases(tmp)
            for db in DATABASES:
                name = os.path.basename(db)
                required.append(name)
                notes.append(f"{name}: {table_counts[name]} tables")
            for extra in EXTRAS:
                if not os.path.isfile(extra):
                    raise RuntimeError(f"required configuration is missing: {extra}")
                name = os.path.basename(extra)
                shutil.copy2(extra, os.path.join(tmp, name))
                required.append(name)
            photo_dst = os.path.join(tmp, "order-photos")
            if os.path.isdir(ORDER_PHOTOS):
                shutil.copytree(ORDER_PHOTOS, photo_dst)
                for base, _dirs, files in os.walk(photo_dst):
                    for filename in files:
                        required.append(os.path.relpath(
                            os.path.join(base, filename), tmp))
            else:
                os.makedirs(photo_dst)
            reddit_count = stage_reddit_media(tmp, required)
            validate_reddit_media_links(
                os.path.join(tmp, os.path.basename(HERMES_DB)),
                media_root=os.path.join(tmp, "reddit-media"),
            )
            if reddit_count:
                notes.append(f"reddit-media: {reddit_count} files")
            with tarfile.open(archive, "w:gz") as tar:
                for f in sorted(os.listdir(tmp)):
                    tar.add(os.path.join(tmp, f), arcname=f)

        # Contains .env, so it is not world-readable.
        os.chmod(archive, 0o600)
        verify_archive(archive, required)
        status["local_ok"] = True

        kept = sorted(f for f in os.listdir(DEST)
                      if f.startswith("labs-os-") and f.endswith(".tar.gz"))
        for old in kept[:-KEEP]:
            os.remove(os.path.join(DEST, old))

        size = os.path.getsize(archive) / 1024
        print(f"wrote and verified {archive} ({size:.0f} KB) — " + "; ".join(notes))
        print(f"holding {len(kept[-KEEP:])} of {KEEP} nightly copies")
        offsite_ok, offsite = push_offsite(archive)
        status["offsite_ok"] = offsite_ok
        status["offsite"] = offsite
        status["size_bytes"] = os.path.getsize(archive)
        print(offsite)
        _write_status(status)
        if not offsite_ok:
            return 1

        # Log without importing agent_chat_server: importing it performs
        # schema migrations, which a backup must never do after snapshotting.
        try:
            conn = sqlite3.connect(DATABASES[0], timeout=5)
            conn.execute(
                "INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
                ("key", "backup", "system",
                 f"{os.path.basename(archive)} ({size:.0f} KB)"))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return 0
    except Exception as e:
        status["error"] = str(e)
        _write_status(status)
        print(f"backup failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
