#!/usr/bin/env python3
"""Chat backend for the Timelabs Agent web interface.

Runs on 127.0.0.1:8901 behind nginx (gated by the ops session login):
  GET  /history  -> last 50 messages of the shared thread
  POST /send     -> {"message": "..."} -> runs Hermes with recent context,
                    stores both sides in hermes.db, returns the reply

One Hermes run at a time (lock); a second sender gets 409 "agent is busy".
Continuity is transcript-based: the last 8 exchanges are prepended to each
prompt, so the thread is shared and persistent across devices and people.
"""
import html as html_mod
import glob
import hashlib
import hmac
import http.server
import http.cookies
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from markdown_render import md_to_html
from order_metrics import (
    ALLOCATED_ITEM_REVENUE_SQL,
    ORDER_ITEM_TOTALS_JOIN,
    SALE_ORDER_PREDICATE,
    SALE_ORDER_PREDICATE_O,
)
from shopify_oauth import canonical_hmac_message
from shopify_scopes import REQUESTED_SCOPE_STRING, normalized_scopes
import creator_interview
import writing_quality

HOST, PORT = "127.0.0.1", 8901
DB = "/root/ops-dashboard/data/hermes.db"
# The Ledger's own database — acknowledged supplier bills are written through
# to it as invoices, so build spend lands where cost/margin already lives.
SUPPLIERS_DB = "/root/ops-dashboard/data/suppliers.db"
UPLOAD_DIR = "/root/ops-dashboard/data/uploads"
# Where an order's reference photos actually live. Under /root deliberately:
# nginx can't reach it, so the only way to a photo is through a role-checked
# endpoint here, never a guessable static URL.
ORDER_PHOTOS = "/root/ops-dashboard/data/order-photos"
# Reddit draft photos outlive the 24-hour upload staging area. They are served
# only through the role-checked endpoint below and backed up with the databases.
REDDIT_MEDIA = "/root/ops-dashboard/data/reddit-media"
REDDIT_MEDIA_TRASH = "/root/ops-dashboard/data/.reddit-media-trash"
# Labs Drop's storage root — a separate service (drop_server.py, :8903) owns
# this, but it's local disk on the same box, so copying a picked photo into
# UPLOAD_DIR is a plain file copy rather than a round trip through Drop's own
# HTTP API. See _drop_safe_path, which mirrors drop_server.safe_rel exactly
# since the two processes don't share code.
DROP_ROOT = "/srv/timelabs-drop"
THEME_BACKUPS = "/root/ops-dashboard/theme-backups"
FS_ROOT = "/root/ops-dashboard"  # browser shows safe project source/docs only
FS_DENY_DIRS = {
    "data", "venv", "theme-backups", "backups", "node_modules", "__pycache__",
}
# Chat-photo cap. Modern phone photos routinely exceed the old 11 MB ceiling;
# 32 MB stays comfortably under nginx's 50m on this vhost. Large videos go
# through Drop (copyparty), not this endpoint. Formats stay jpg/png/webp — the
# only ones tesseract OCR and browser <img> render without a HEIC decoder.
MAX_UPLOAD_MB = 32
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_TTL = 24 * 60 * 60
UPLOAD_USER_LIMIT = 256 * 1024 * 1024
UPLOAD_MIN_FREE = 20 * 1024 * 1024 * 1024
UPLOAD_LOCK = threading.Lock()
LOCK = threading.Lock()
# Serializes theme apply/revert's read-modify-write specifically — LOCK above
# guards the shared Hermes agent process and would be too broad to reuse here
# (an in-flight research run would block an unrelated theme edit for no
# reason). Without this, two near-simultaneous applies both read the same
# pre-change settings and the later PUT silently wins, dropping the other.
THEME_LOCK = threading.Lock()
# A bulk Shopify preview is a frozen, short-lived proposal. The apply request
# must present its token and the exact same operation; product descriptions are
# rechecked before the first write so the reviewed diff cannot drift.
CONTENT_PREVIEW_LOCK = threading.Lock()
CONTENT_PREVIEWS = {}
CONTENT_PREVIEW_TTL = 15 * 60
LEDGER_PREVIEW_LOCK = threading.Lock()
LEDGER_PREVIEWS = {}
LEDGER_PREVIEW_TTL = 15 * 60
# Content-role AI pipelines are multi-pass and billable. One shared slot stops
# double-clicks or a compromised account from spawning an unbounded process
# fan-out on the production VPS.
REDDIT_JOB_SLOT = threading.BoundedSemaphore(1)
REDDIT_POST_WORKER_TIMEOUT = 4300
REDDIT_REPLY_WORKER_TIMEOUT = 2900
INVOICE_INBOX = "/srv/timelabs-drop/Supplier Invoices"
# Fast requests answer synchronously within the soft wait; anything longer keeps
# running in a background thread (up to the hard cap) and lands in the thread
# via history polling — long tasks are no longer killed at the HTTP boundary.
HERMES_SOFT_WAIT = 120   # seconds to hold the HTTP request open (nginx allows 300)
HERMES_HARD_TIMEOUT = 2700  # 45 min absolute cap for a single agent run
CONTEXT_TURNS = 16  # messages (8 exchanges)

# --- Key (invite/access) ----------------------------------------------------
# The allowlist IS the access-control boundary (External+Published consent
# screen). Admin-only endpoints below mutate it; oauth2-proxy hot-reloads on
# change. ADMIN_EMAILS mirrors hub_shell.ADMIN_EMAILS — keep in sync.
ALLOWLIST_PATH = "/etc/oauth2-proxy/allowlist.txt"
ADMIN_EMAILS = ("timelabs.inc@gmail.com", "schezan.m@gmail.com")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# --- Shopify OAuth (turn app key+secret into an Admin token via one approval) ---
ENV_FILE = "/root/ops-dashboard/.env"
SHOPIFY_OAUTH_CREDS = "/root/ops-dashboard/.shopify-oauth.json"
# Requested at OAuth connect. Must be a SUBSET of what the app is configured for
# in Shopify admin, or the authorize screen errors on the missing scope. Widened
# 2026-07-21 after the owner enabled the fuller set on the app; reconnect to mint
# a token carrying these. If Shopify flags one as not-allowed, trim it here.
SHOPIFY_SCOPES = REQUESTED_SCOPE_STRING
SHOPIFY_REDIRECT = "https://ops.timelabsco.in/ops/agent/api/shopify/callback"
SHOPIFY_STATE_TTL = 10 * 60
_shopify_states = {}
_shopify_states_lock = threading.Lock()


def write_env(updates):
    """Upsert keys into .env without clobbering the rest; keep it 0600."""
    try:
        lines = open(ENV_FILE).read().splitlines()
    except OSError:
        lines = []
    seen, out = set(), []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.startswith("#") else None
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    # Every other writer in this codebase (product_builder.py, reddit.py,
    # drop_server.py's upload finish, ...) writes to a temp file then
    # os.replace()s it, specifically so a crash mid-write can't leave the
    # target half-written. This is the one file where that matters most —
    # a truncated .env loses every credential the whole OS depends on, not
    # just one tool's data — and it was writing in place directly.
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_FILE)


def hub_event(kind, detail, actor="?", app="key"):
    """Shared Hub activity feed (hub_events in hermes.db) — Hermes watches this."""
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS hub_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            app TEXT NOT NULL DEFAULT 'drop',
            kind TEXT NOT NULL, actor TEXT, detail TEXT)""")
        conn.execute("INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
                     (app, kind, actor, detail[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[events] {e}", flush=True)

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ORDER_PHOTOS, exist_ok=True)
os.makedirs(REDDIT_MEDIA, exist_ok=True)
os.makedirs(THEME_BACKUPS, exist_ok=True)


def _upload_meta_path(path):
    return path + ".labs-upload.json"


def _write_upload_meta(path, owner, size):
    meta_path = _upload_meta_path(path)
    tmp = meta_path + f".{os.getpid()}-{threading.get_ident()}.tmp"
    with open(tmp, "x") as out:
        json.dump({
            "owner": (owner or "").strip().lower(),
            "size": int(size),
            "created": int(time.time()),
            "expires": int(time.time()) + UPLOAD_TTL,
        }, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, meta_path)
    os.chmod(meta_path, 0o600)


def _cleanup_uploads_locked(now=None):
    """Expire temporary user uploads and one-off generated artifacts."""
    now = now or time.time()
    try:
        names = os.listdir(UPLOAD_DIR)
    except OSError:
        return
    tracked = set()
    for name in names:
        if not name.endswith(".labs-upload.json"):
            continue
        meta_path = os.path.join(UPLOAD_DIR, name)
        data_path = meta_path[:-len(".labs-upload.json")]
        tracked.add(os.path.basename(data_path))
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            expired = float(meta.get("expires") or 0) <= now
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            expired = True
        if expired:
            for path in (data_path, meta_path):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
    # Old versions created untracked temporary images/PDFs. They are not
    # claimable by the new owner-bound flow and can be reclaimed after a day.
    for name in names:
        if (name.endswith(".labs-upload.json") or name in tracked
                or name.startswith(".")):
            continue
        path = os.path.join(UPLOAD_DIR, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > UPLOAD_TTL:
                os.remove(path)
        except OSError:
            pass


def _upload_usage_locked(owner):
    total = 0
    for name in os.listdir(UPLOAD_DIR):
        if not name.endswith(".labs-upload.json"):
            continue
        try:
            with open(os.path.join(UPLOAD_DIR, name)) as f:
                meta = json.load(f)
            if (meta.get("owner") or "").strip().lower() == owner:
                total += max(0, int(meta.get("size") or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return total


def _store_user_upload(owner, ext, data=None, source=None):
    """Admit and atomically store one owner-bound temporary image."""
    owner = (owner or "").strip().lower()
    if not owner:
        raise ValueError("an authenticated account is required for uploads")
    size = len(data) if data is not None else os.path.getsize(source)
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise ValueError(f"image must be under {MAX_UPLOAD_MB} MB")
    with UPLOAD_LOCK:
        _cleanup_uploads_locked()
        if _upload_usage_locked(owner) + size > UPLOAD_USER_LIMIT:
            raise ValueError("temporary upload limit reached; finish or wait for old uploads")
        if shutil.disk_usage(UPLOAD_DIR).free - size < UPLOAD_MIN_FREE:
            raise ValueError("upload refused to protect the system disk reserve")
        path = os.path.join(UPLOAD_DIR, secrets.token_hex(16) + ext)
        try:
            with open(path, "xb") as out:
                if data is not None:
                    out.write(data)
                else:
                    with open(source, "rb") as src:
                        shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())
            _write_upload_meta(path, owner, size)
            return path
        except Exception:
            for candidate in (path, _upload_meta_path(path)):
                try:
                    os.remove(candidate)
                except OSError:
                    pass
            raise


def _upload_owned(path, owner):
    """Resolve an active upload owned by this authenticated account."""
    real = os.path.realpath(str(path or ""))
    root = os.path.realpath(UPLOAD_DIR) + os.sep
    if not real.startswith(root) or not os.path.isfile(real):
        return None
    try:
        with open(_upload_meta_path(real)) as f:
            meta = json.load(f)
        if (meta.get("owner") or "").strip().lower() != (owner or "").strip().lower():
            return None
        if float(meta.get("expires") or 0) <= time.time():
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return real


def _forget_upload(path, remove_data=False):
    with UPLOAD_LOCK:
        if remove_data:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.remove(_upload_meta_path(path))
        except OSError:
            pass


def _persist_reddit_photos(post_id, photos):
    """Copy staged owner uploads into one durable, private post directory."""
    if not photos:
        return []
    post_dir = os.path.join(REDDIT_MEDIA, str(int(post_id)))
    os.makedirs(post_dir, mode=0o700, exist_ok=False)
    stored = []
    try:
        for index, source in enumerate(photos, 1):
            ext = os.path.splitext(source)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                raise ValueError("unsupported Reddit draft photo")
            destination = os.path.join(post_dir, f"{index}{ext}")
            with open(source, "rb") as src, open(destination, "xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            stored.append(destination)
        _fsync_directory(post_dir)
        _fsync_directory(REDDIT_MEDIA)
        _fsync_directory(os.path.dirname(REDDIT_MEDIA))
        return stored
    except Exception:
        shutil.rmtree(post_dir, ignore_errors=True)
        try:
            _fsync_directory(REDDIT_MEDIA)
        except OSError:
            pass
        raise


def _fsync_directory(path):
    """Persist directory entries, not only the bytes inside their files."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _quarantine_reddit_media(post_id):
    """Atomically move one post directory out of the backup-visible tree."""
    post_id = int(post_id)
    post_dir = os.path.join(REDDIT_MEDIA, str(post_id))
    if not os.path.lexists(post_dir):
        return None
    if os.path.islink(post_dir) or not os.path.isdir(post_dir):
        raise OSError("Reddit media directory is not a regular directory")
    os.makedirs(REDDIT_MEDIA_TRASH, mode=0o700, exist_ok=True)
    if os.path.islink(REDDIT_MEDIA_TRASH) or not os.path.isdir(REDDIT_MEDIA_TRASH):
        raise OSError("Reddit media quarantine is not a regular directory")
    target = os.path.join(
        REDDIT_MEDIA_TRASH, f"{post_id}-{secrets.token_hex(8)}")
    os.replace(post_dir, target)
    try:
        _fsync_directory(REDDIT_MEDIA)
        _fsync_directory(REDDIT_MEDIA_TRASH)
        _fsync_directory(os.path.dirname(REDDIT_MEDIA_TRASH))
    except OSError:
        # The database still references the canonical path at this point.
        # Put it back before reporting failure.
        os.replace(target, post_dir)
        _fsync_directory(REDDIT_MEDIA)
        _fsync_directory(REDDIT_MEDIA_TRASH)
        raise
    return target


def _restore_reddit_quarantine(post_id, quarantine):
    """Restore a pre-commit media move after a rolled-back/crashed delete."""
    if not quarantine or not os.path.isdir(quarantine):
        return
    destination = os.path.join(REDDIT_MEDIA, str(int(post_id)))
    os.makedirs(REDDIT_MEDIA, mode=0o700, exist_ok=True)
    if os.path.lexists(destination):
        raise OSError("cannot restore Reddit media over an existing directory")
    os.replace(quarantine, destination)
    _fsync_directory(REDDIT_MEDIA_TRASH)
    _fsync_directory(REDDIT_MEDIA)
    _fsync_directory(os.path.dirname(REDDIT_MEDIA))


def _purge_reddit_quarantine(quarantine):
    """Best-effort final removal after the database no longer references it."""
    try:
        if quarantine and os.path.isdir(quarantine):
            shutil.rmtree(quarantine)
            _fsync_directory(REDDIT_MEDIA_TRASH)
    except OSError as exc:
        # Startup recovery removes a quarantine only when the post row is
        # absent, so a transient filesystem error cannot break future backups.
        print(f"[reddit-media] quarantine cleanup deferred: {exc}", flush=True)


def _recover_reddit_media_quarantine():
    """Resolve crash leftovers by consulting the authoritative post row."""
    if not os.path.isdir(REDDIT_MEDIA_TRASH) or os.path.islink(REDDIT_MEDIA_TRASH):
        return
    conn = db()
    try:
        for name in os.listdir(REDDIT_MEDIA_TRASH):
            match = re.fullmatch(r"([1-9]\d*)-([0-9a-f]{16})", name)
            if not match:
                continue
            post_id = int(match.group(1))
            quarantine = os.path.join(REDDIT_MEDIA_TRASH, name)
            if os.path.islink(quarantine) or not os.path.isdir(quarantine):
                continue
            exists = conn.execute(
                "SELECT 1 FROM reddit_posts WHERE id=?", (post_id,)).fetchone()
            destination = os.path.join(REDDIT_MEDIA, str(post_id))
            if exists and not os.path.lexists(destination):
                try:
                    _restore_reddit_quarantine(post_id, quarantine)
                except OSError as exc:
                    print(f"[reddit-media] restore {post_id} deferred: {exc}",
                          flush=True)
            elif not exists:
                _purge_reddit_quarantine(quarantine)
    finally:
        conn.close()


with UPLOAD_LOCK:
    _cleanup_uploads_locked()


def _order_photo_revision(names):
    """A non-secret cache key that changes whenever an order's photo list does.

    Photo endpoints deliberately address an image by list index rather than by
    its private filename.  That keeps filenames out of the supplier response,
    but it also means ``n=0`` can point at a different file after an edit.  The
    revision gives both UIs a fresh URL whenever that mapping changes.
    """
    if not isinstance(names, list):
        names = []
    safe = [os.path.basename(n) for n in names if isinstance(n, str)]
    if not safe:
        return ""
    packed = json.dumps(safe, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()[:16]


def _effective_order_total_paise(price_inr=None, quantity=1,
                                 sale_total_paise=None):
    """Return the admin-recorded sale total, falling back to price × qty."""
    if sale_total_paise is not None:
        return int(sale_total_paise)
    if price_inr is None:
        return None
    return int((Decimal(str(price_inr)) * int(quantity or 1) * 100)
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _customer_payment_revision(receipts, price_inr=None, quantity=1,
                               sale_total_paise=None):
    """Optimistic-lock key for receipts and the total they are allocated to."""
    packed = [{
        "price_inr": None if price_inr is None else str(price_inr),
        "quantity": int(quantity or 1),
        "sale_total_paise": (None if sale_total_paise is None
                             else int(sale_total_paise)),
    }]
    for kind in ("advance", "balance"):
        receipt = (receipts or {}).get(kind)
        if not receipt:
            continue
        packed.append((kind, int(receipt["amount_paise"]), str(receipt["account"])))
    raw = json.dumps(packed, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _ensure_orders_schema():
    """orders predates the Order form (it was WhatsApp-capture only); the form
    adds the shipping fields staff paste in, plus Drive photo links."""
    want = {"drive_link": "TEXT", "photo_links": "TEXT", "customer_email": "TEXT",
            "address": "TEXT", "pincode": "TEXT", "city": "TEXT", "state": "TEXT",
            "source": "TEXT", "case_style": "TEXT", "dial_colour": "TEXT",
            "dial_style": "TEXT", "case_colour": "TEXT", "movement": "TEXT",
            "watch_size": "TEXT",
            # Shopify linkage — lets a storefront order sync into this same
            # table (see shopify_order_sync.py) instead of only living in a
            # live API call, so it gets one unified order number like every
            # other channel and shows up in the same lists, totals and sheet.
            "shopify_order_id": "TEXT", "shopify_name": "TEXT",
            "financial_status": "TEXT",
            # Contact edits made in Labs must not be overwritten every five
            # minutes by the same unchanged Shopify snapshot. The sync stores
            # the last remote fingerprint and treats a local edit as an
            # explicit override until Shopify is made to match it.
            "shopify_contact_fingerprint": "TEXT",
            "shopify_contact_override": "INTEGER DEFAULT 0",
            "shopify_conflict_fingerprint": "TEXT",
            # Same idea, for the build itself. A staff edit to product/quantity
            # on an order not yet committed to the supplier used to be
            # silently reverted by the next sync (it treats "not committed"
            # as "still take Shopify's word for it"). Once this is set, the
            # sync leaves product/quantity alone — permanently, there is no
            # auto-clear the way contact has, since a corrected build spec
            # has no Shopify-side value to eventually "catch up" to.
            "shopify_build_override": "INTEGER DEFAULT 0",
            # A staff-requested removal of a website/messaging order that the
            # permanent-record rule (below, in _handle_orders_delete) would
            # otherwise refuse outright. The row, and critically its
            # shopify_order_id, are kept — hiding is the only way to grant
            # "delete" without letting the next sync treat the gone row as a
            # new order and resurrect it under a new number.
            "local_hidden": "INTEGER DEFAULT 0",
            # Every channel lands in the unified order log first. A person
            # explicitly chooses which orders enter the supplier queue.
            "supplier_visible": "INTEGER DEFAULT 0",
            # Courier/tracking reference, entered by whoever has it — usually
            # the supplier once a build ships.
            "tracking_code": "TEXT",
            # The original order number a build already had before Labs OS —
            # the number in the WhatsApp caption when these were tracked by
            # hand. Kept so the supplier still recognises "order 101" rather
            # than our internal auto-id, and so backfilled history lines up
            # with whatever was written down at the time.
            "ref_code": "TEXT",
            # Photos used to survive ONLY as Google Drive URLs, so with Google
            # disconnected an attached photo was written to the temp upload
            # dir and then referenced by nothing — silently lost. They now
            # live on our own disk (data/order-photos/<id>/) with Drive kept
            # as a mirror, which is also what lets the supplier queue show
            # them: the reference photo is the whole brief for a build.
            "local_photos": "TEXT",
            # Once staff or the supplier changes the photo list, that local
            # choice is authoritative.  Shopify sync must not interpret an
            # intentionally-empty list as "missing" and re-import it later.
            "photo_override": "INTEGER DEFAULT 0",
            # Optional whole-order selling value recorded alongside customer
            # receipts. It deliberately does not replace Shopify/unit pricing:
            # when NULL, payment math continues to use price × quantity; when
            # set, Shopify sync can refresh its own financials without erasing
            # the amount staff explicitly agreed with the customer.
            "sale_total_paise": "INTEGER",
            # The number a person sees — "#61". Kept apart from the DB id
            # (which photos, the sheet join and Shopify links all key off, so
            # it can't be renumbered) precisely so the visible number CAN be a
            # stable sequence. Existing rows are seeded to their own id so
            # nothing they've already been called by changes; the durable
            # sequence never reuses a number after a permitted hard delete.
            "order_no": "INTEGER"}
    try:
        conn = sqlite3.connect(DB, timeout=5)
        # Serialize the read-before-ALTER sequence with the independent
        # Shopify timer, which self-ensures a subset of these columns too.
        conn.execute("BEGIN IMMEDIATE")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        if cols:
            for name, typ in want.items():
                if name not in cols:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {typ}")
            # NULLs never collide in a UNIQUE index, so form/WhatsApp orders
            # (no Shopify id) are unaffected — this only guards against
            # double-inserting the same storefront order.
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_shopify_id "
                        "ON orders(shopify_order_id)")
            # Seed the display number for anything that predates the column.
            conn.execute("UPDATE orders SET order_no=id WHERE order_no IS NULL")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_order_no "
                         "ON orders(order_no)")
            # Customer money received by us is neither Shopify's checkout
            # status nor a supplier payment.  Store the two business stages
            # as integer paise, with one current row per stage.  That avoids
            # floating point drift and lets advance/final receipts land in
            # different internal accounts without adding four loosely-bound
            # columns to every order.
            conn.execute("""CREATE TABLE IF NOT EXISTS order_customer_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('advance','balance')),
                amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
                account_code TEXT NOT NULL
                    CHECK(account_code IN ('SM','MK','TL','TM')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT,
                updated_by TEXT,
                UNIQUE(order_id, kind),
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE RESTRICT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_receipts_order "
                         "ON order_customer_receipts(order_id)")
            import order_numbers
            order_numbers.ensure(conn)

            # Collapse the old 8-status pipeline onto the 5 stages — ONCE.
            # This can't be value-based-idempotent: old "shipped" (to the
            # customer, = done) must become "delivered", but "shipped" is also
            # a NEW stage key (Shipped from China), so a guard keyed on the
            # value can't tell a not-yet-migrated old row from a correct new
            # one. So it's gated on PRAGMA user_version and runs exactly once,
            # when every value is still guaranteed to be old-vocabulary.
            import order_stages
            SCHEMA_VERSION = 1
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver < SCHEMA_VERSION:
                whens = " ".join(f"WHEN '{old}' THEN '{new}'"
                                 for old, new in order_stages.OLD_TO_NEW.items())
                conn.execute(f"UPDATE orders SET status = CASE status {whens} "
                             f"ELSE status END")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # Belt-and-braces for a NULL or genuinely unrecognised status
            # (idempotent, unambiguous — none of these collide with a new key).
            valid = ",".join(f"'{s}'" for s in order_stages.STATUSES)
            conn.execute(f"UPDATE orders SET status='pending' "
                         f"WHERE status IS NULL OR status NOT IN ({valid})")

            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[orders] schema check: {e}", flush=True)
        raise


def _ensure_order_items_schema():
    """One row per product line, across every channel — the SQL a sortable,
    editable 'what's selling' and Hermes' `labs sales` both read. A logged
    order (order form / WhatsApp) has exactly one line, mirroring its single
    product/quantity/price fields; a Shopify order gets one line per line
    item, so a two-watch order attributes units correctly instead of both
    landing on whichever title happened to print first.

    canonical_product starts NULL (falls back to the raw product text) and is
    only set when someone renames a line from the What's Selling tab — that
    rename is retroactive for every row sharing the old text, which is the
    whole point: messy free-typed names ("dj arabic lightblue") converge on
    one clean product name without touching history.
    """
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product TEXT NOT NULL,
            canonical_product TEXT,
            quantity INTEGER DEFAULT 1,
            price_inr REAL,
            line_total REAL,
            updated_at TEXT DEFAULT (datetime('now')))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order "
                     "ON order_items(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_product "
                     "ON order_items(product)")
        # Self-healing backfill: any order (the pre-existing WhatsApp capture,
        # or one saved while this code was mid-deploy) that has no line yet
        # gets one synthesized from its own product/quantity/price — so
        # analytics never silently drops an order because the write path
        # missed it.
        conn.execute("""
            INSERT INTO order_items (order_id, product, quantity, price_inr, line_total)
            SELECT id, product, COALESCE(quantity,1), price_inr,
                   COALESCE(price_inr,0) * COALESCE(quantity,1)
            FROM orders
            WHERE product IS NOT NULL AND TRIM(product) != ''
              AND id NOT IN (SELECT DISTINCT order_id FROM order_items)
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[order_items] schema check: {e}", flush=True)
        raise


def _ensure_order_events_schema():
    """Per-order timeline: who moved it to which stage, and when.

    Replacing a WhatsApp group with a dashboard loses something real if it
    isn't recorded — the group at least had timestamps and someone saying
    "dial is out of stock". This is that history: every status change from
    either side, plus notes the supplier writes back, so "when did we pay
    for #147" and "why is #152 stuck" have answers."""
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT, kind TEXT NOT NULL, detail TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order "
                     "ON order_events(order_id, id)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[order_events] schema check: {e}", flush=True)
        raise


def order_event(conn, order_id, kind, detail, actor):
    """Append to an order's timeline on an existing connection (so it commits
    with whatever change it describes, never separately from it)."""
    try:
        conn.execute("INSERT INTO order_events (order_id, actor, kind, detail) "
                     "VALUES (?,?,?,?)", (order_id, actor, kind, (detail or "")[:400]))
    except Exception as e:
        print(f"[order_events] {e}", flush=True)


def _ensure_shipments_schema():
    """A shipment is one physical consignment carrying several builds.

    Cost is held as one number for the whole consignment and divided evenly
    across the orders in it, which is what the owner asked for: one figure to
    type per shipment instead of a per-item breakdown. It is worth being
    honest that even splitting charges a cheap strap the same freight as an
    expensive build, so per-unit cost here is an allocation for visibility,
    not a precise landed cost — the Ledger remains the place where real
    invoice-level costs live.
    """
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            code TEXT, carrier TEXT, total_cost REAL, currency TEXT DEFAULT 'USD',
            notes TEXT, status TEXT DEFAULT 'open')""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shipments_code "
                     "ON shipments(code)")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        if cols and "shipment_id" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN shipment_id INTEGER")
        # Payments toward what's owed to the supplier. shipment_id is nullable
        # on purpose: a payment can be recorded against one consignment
        # ("this $500 settles shipment 125678") or left unallocated ("sent
        # $500, haven't said which shipment yet") — either way it counts
        # against the running total owed, which is what "arrears" means here.
        # Partial payments are the reason this is its own table rather than a
        # paid/unpaid flag: a shipment can be paid down over several transfers.
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            shipment_id INTEGER REFERENCES shipments(id),
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            note TEXT, actor TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_payments_shipment "
                     "ON supplier_payments(shipment_id)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[shipments] schema check: {e}", flush=True)
        raise


def _ensure_reddit_schema():
    """The Reddit Listener: read-only threads surfaced from the target
    subreddits, ranked/tagged for review, plus a drafts table for phase-2
    (a suggested reply a human approves before it ever reaches Reddit — see
    reference_reddit_timelabs memory for the full phased plan). Both tables
    exist now even though reddit_api.py can't populate them yet, so the UI,
    endpoints and schema are all ready the moment the OAuth app is approved."""
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS reddit_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            subreddit TEXT NOT NULL,
            title TEXT, permalink TEXT, author TEXT,
            created_utc INTEGER, score INTEGER, num_comments INTEGER,
            tag TEXT, opportunity_score REAL DEFAULT 0,
            status TEXT DEFAULT 'new',
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reddit_threads_tid "
                     "ON reddit_threads(thread_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reddit_threads_status "
                     "ON reddit_threads(status)")
        conn.execute("""CREATE TABLE IF NOT EXISTS reddit_scan_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            next_index INTEGER NOT NULL DEFAULT 0)""")
        import reddit_api
        reddit_api.ensure_thread_columns(conn)
        reddit_api.reclassify_recent_threads(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS reddit_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES reddit_threads(id),
            draft_text TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reddit_drafts_thread "
                     "ON reddit_drafts(thread_id)")
        dcols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_drafts)")}
        for col, decl in (("stage", "TEXT"), ("passes", "TEXT"),
                          ("question", "TEXT"), ("answer", "TEXT"),
                          ("error", "TEXT"), ("finished_at", "TEXT"),
                          ("posted_at", "TEXT"), ("started_at", "TEXT"),
                          ("queued_at", "TEXT")):
            if col not in dcols:
                conn.execute(f"ALTER TABLE reddit_drafts ADD COLUMN {col} {decl}")
        # Posts for our own subreddit (r/IndiaWatchMods). A post is built by
        # a multi-pass pipeline rather than one generation, so `passes` keeps
        # each stage's output — the research it used, what the audit objected
        # to, what humanising changed. That trail is the point: a draft you
        # can't inspect is a draft you can't trust enough to publish.
        conn.execute("""CREATE TABLE IF NOT EXISTS reddit_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT,
            subreddit TEXT NOT NULL DEFAULT 'IndiaWatchMods',
            kind TEXT NOT NULL DEFAULT 'showcase',
            brief TEXT,
            photos TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT,
            title TEXT,
            body TEXT,
            passes TEXT,
            error TEXT,
            finished_at TEXT,
            posted_at TEXT,
            posted_url TEXT,
            question TEXT,
            answer TEXT,
            rounds INTEGER DEFAULT 0)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reddit_posts_status "
                     "ON reddit_posts(status)")
        # Rules, sidebar copy and flair for our own sub. One row per piece,
        # overwritten on redraft, because there is only ever one live version
        # of a subreddit's rules and keeping history here helps nobody.
        conn.execute("""CREATE TABLE IF NOT EXISTS reddit_setup (
            kind TEXT PRIMARY KEY,
            text TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_posts)")}
        for col, decl in (("question", "TEXT"), ("answer", "TEXT"),
                          ("rounds", "INTEGER DEFAULT 0"),
                          ("started_at", "TEXT"),
                          ("queued_at", "TEXT"),
                          # Several people posting to one sub needs a rota, or
                          # you get two posts on Tuesday and nothing until Friday.
                          ("slot_date", "TEXT"), ("assigned_to", "TEXT")):
            if col not in pcols:
                conn.execute(f"ALTER TABLE reddit_posts ADD COLUMN {col} {decl}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[reddit] schema check: {e}", flush=True)
        raise


def _ensure_stock_schema():
    """A build we are making for ourselves, with no buyer yet.

    These already existed in practice: bulk intake creates orders with no
    customer, and so does anyone queuing a build speculatively. What was
    missing was a way to say so on purpose and then find them again. Without
    it, stock and unsold-but-real orders look identical, so "how many orders
    this week" quietly counts watches nobody has bought.
    """
    try:
        conn = sqlite3.connect(DB, timeout=5)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        if cols and "is_stock" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN is_stock INTEGER DEFAULT 0")
            # The owner already invented this convention by hand: builds for
            # stock were being logged with the customer typed as "Self". The
            # backfill matches what he actually did, plus genuinely blank
            # rows, rather than a convention nobody used.
            conn.execute("UPDATE orders SET is_stock=1 WHERE "
                         "LOWER(TRIM(COALESCE(customer_name,''))) IN "
                         "('self','stock','') AND price_inr IS NULL")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[stock] schema check: {e}", flush=True)
        raise


def _ensure_billing_schema():
    """What the supplier charges us, per watch, and the bills that collect it.

    Two things worth being explicit about, because they look similar and are
    not: orders.price_inr is what the CUSTOMER pays and must never reach the
    supplier role; orders.supplier_cost is what the supplier charges US for
    that build, which they enter themselves. Different numbers, different
    direction, different audience.

    Bill line items carry their own copy of the cost rather than reading
    through to the order. An acknowledged bill is a record of what was agreed
    at that moment — if someone later corrects a build's cost, history must
    not silently rewrite itself. Freight lives on the bill, not the build
    (the owner's call), which also matches how suppliers.db invoices already
    separate subtotal from shipping_cost.
    """
    conn = sqlite3.connect(DB, timeout=5)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        if cols:
            if "supplier_cost" not in cols:
                conn.execute("ALTER TABLE orders ADD COLUMN supplier_cost REAL")
            if "supplier_cost_ccy" not in cols:
                conn.execute("ALTER TABLE orders ADD COLUMN supplier_cost_ccy TEXT DEFAULT 'USD'")
            if "bill_id" not in cols:
                conn.execute("ALTER TABLE orders ADD COLUMN bill_id INTEGER")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            currency TEXT DEFAULT 'USD',
            subtotal REAL DEFAULT 0,
            shipping_cost REAL DEFAULT 0,
            total REAL DEFAULT 0,
            shipment_id INTEGER REFERENCES shipments(id),
            notes TEXT,
            acknowledged_at TEXT,
            acknowledged_by TEXT,
            ledger_invoice_id INTEGER)""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_supplier_bills_no "
                     "ON supplier_bills(bill_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_bills_status "
                     "ON supplier_bills(status)")
        conn.execute("""CREATE TABLE IF NOT EXISTS supplier_bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL REFERENCES supplier_bills(id),
            order_id INTEGER,
            description TEXT,
            ref_code TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            cost REAL NOT NULL DEFAULT 0)""")
        icols = {r[1] for r in conn.execute(
            "PRAGMA table_info(supplier_bill_items)")}
        if icols and "quantity" not in icols:
            # Agreed history had no quantity snapshot, so preserve it as one
            # unit. New bills snapshot the order quantity below.
            conn.execute(
                "ALTER TABLE supplier_bill_items "
                "ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_bill_items_bill "
                     "ON supplier_bill_items(bill_id)")
        # A payment can now settle a bill as well as a shipment (or neither,
        # if it's just money sent on account).
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(supplier_payments)")}
        if pcols and "bill_id" not in pcols:
            conn.execute("ALTER TABLE supplier_payments ADD COLUMN bill_id INTEGER")
        # One courier reference for the whole batch. Per-build tracking was
        # dropped as more fiddly than useful — the batch travels as one box,
        # so one number describes it.
        bcols = {r[1] for r in conn.execute("PRAGMA table_info(supplier_bills)")}
        if bcols and "tracking_code" not in bcols:
            conn.execute("ALTER TABLE supplier_bills ADD COLUMN tracking_code TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_webchat_ownership():
    """Private-by-default conversations for non-admin team accounts."""
    conn = sqlite3.connect(DB, timeout=5)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(webchat_sessions)")}
        if not cols:
            raise RuntimeError("webchat_sessions table is missing")
        if "owner_email" not in cols:
            conn.execute("ALTER TABLE webchat_sessions ADD COLUMN owner_email TEXT")
        if "visibility" not in cols:
            conn.execute(
                "ALTER TABLE webchat_sessions ADD COLUMN visibility TEXT "
                "NOT NULL DEFAULT 'private'")
        conn.execute(
            "UPDATE webchat_sessions SET owner_email=? "
            "WHERE owner_email IS NULL OR owner_email=''",
            (ADMIN_EMAILS[0],),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webchat_sessions_owner "
            "ON webchat_sessions(owner_email, updated_at)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_atomic_cross_db_journals():
    """Attached-db bill/Ledger writes need SQLite's rollback journal.

    SQLite provides a super-journal for atomic commits across attached files
    only when neither database uses WAL. Both files live on the same disk, so
    DELETE mode gives bill acknowledgement/reversal one crash-safe commit.
    """
    for path in (DB, SUPPLIERS_DB):
        conn = sqlite3.connect(path, timeout=10)
        try:
            mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise RuntimeError(
                    f"{os.path.basename(path)} would not leave WAL mode")
        finally:
            conn.close()


def _fail_reddit_job(table, job_id, error):
    """Best-effort terminal state for a job already claimed by the worker."""
    conn = None
    try:
        conn = db()
        conn.execute(
            f"UPDATE {table} SET status='failed', stage='error', error=?, "
            "finished_at=datetime('now') WHERE id=? AND status='running'",
            (str(error)[:500], job_id))
        conn.commit()
    except Exception as exc:
        print(f"[reddit_draft] could not fail {table} #{job_id}: {exc}",
              flush=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _send_owner_alert(body):
    """Private completion/health channel; never fall back to supplier chat."""
    target = ""
    try:
        with open(ENV_FILE) as source:
            for line in source:
                if line.startswith("HEALTH_ALERT_TARGET="):
                    target = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except OSError:
        pass
    if not target:
        print(f"[alert] no HEALTH_ALERT_TARGET set; would have sent: {body}",
              flush=True)
        return
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", target, "--quiet", body],
            capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(
                "[alert] delivery failed: "
                + (result.stderr or result.stdout or "hermes send failed")[-300:],
                flush=True)
    except Exception as exc:
        print(f"[alert] {exc}", flush=True)


def _reddit_post_worker(alert=None):
    """Drain durable queued post and reply jobs, one at a time.

    The queue is the database, not the request thread. A service restart turns
    interrupted ``running`` rows back into ``queued`` below, and this worker
    resumes both content types when the actual agent service starts.
    """
    claim_failures = 0
    try:
        while True:
            conn = None
            job_type = table = actor = None
            job_id = None
            try:
                conn = db()
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT job_type, id, created_at, created_by FROM ("
                    "SELECT 'post' AS job_type, id, created_at, created_by "
                    "FROM reddit_posts WHERE status='queued' "
                    "UNION ALL "
                    "SELECT 'reply' AS job_type, id, created_at, created_by "
                    "FROM reddit_drafts WHERE status='queued'"
                    ") ORDER BY created_at ASC, id ASC LIMIT 1").fetchone()
                if not row:
                    conn.rollback()
                    break
                job_type = row["job_type"]
                job_id = row["id"]
                actor = row["created_by"] or "reddit-worker"
                table = "reddit_posts" if job_type == "post" else "reddit_drafts"
                changed = conn.execute(
                    f"UPDATE {table} SET status='running', "
                    "stage=COALESCE(NULLIF(stage,''),'starting'), error=NULL, "
                    "started_at=datetime('now') "
                    "WHERE id=? AND status='queued'", (job_id,))
                if changed.rowcount != 1:
                    conn.rollback()
                    continue
                conn.commit()
                claim_failures = 0
            except Exception as exc:
                claim_failures += 1
                if conn is not None and conn.in_transaction:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                if job_id is not None and table is not None:
                    _fail_reddit_job(
                        table, job_id,
                        f"queue claim failed after selecting this job: {exc}")
                print(f"[reddit_draft] queue claim failed: {exc}", flush=True)
                if claim_failures >= 6:
                    break
                time.sleep(min(0.25 * (2 ** (claim_failures - 1)), 4.0))
                continue
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception as exc:
                        print(f"[reddit_draft] queue connection close: {exc}",
                              flush=True)

            failure = None
            try:
                script = (
                    "/root/ops-dashboard/reddit_draft.py"
                    if job_type == "post"
                    else "/root/ops-dashboard/reddit_reply.py"
                )
                result = subprocess.run(
                    ["python3", script, str(job_id)],
                    capture_output=True, text=True,
                    timeout=(
                        REDDIT_POST_WORKER_TIMEOUT
                        if job_type == "post"
                        else REDDIT_REPLY_WORKER_TIMEOUT))
                if result.returncode != 0:
                    failure = (
                        result.stderr
                        or f"{job_type} draft worker failed"
                    )[-500:]
            except Exception as e:
                failure = str(e)[:500]
                print(f"[reddit_draft] {job_type} worker {job_id}: {e}",
                      flush=True)

            try:
                conn = db()
                if failure:
                    conn.execute(
                        f"UPDATE {table} SET status='failed', stage='error', error=?, "
                        "finished_at=datetime('now') WHERE id=?",
                        (failure, job_id))
                    conn.commit()
                if job_type == "post":
                    final = conn.execute(
                        "SELECT status, title, question, error "
                        "FROM reddit_posts WHERE id=?", (job_id,)).fetchone()
                else:
                    final = conn.execute(
                        "SELECT status, NULL AS title, question, error "
                        "FROM reddit_drafts WHERE id=?", (job_id,)).fetchone()
                if final and final["status"] == "running":
                    failure = "draft process exited without a terminal state"
                    conn.execute(
                        f"UPDATE {table} SET status='failed', stage='error', error=?, "
                        "finished_at=datetime('now') WHERE id=?",
                        (failure, job_id))
                    conn.commit()
                    if job_type == "post":
                        final = conn.execute(
                            "SELECT status, title, question, error "
                            "FROM reddit_posts WHERE id=?", (job_id,)).fetchone()
                    else:
                        final = conn.execute(
                            "SELECT status, NULL AS title, question, error "
                            "FROM reddit_drafts WHERE id=?", (job_id,)).fetchone()
            except Exception as exc:
                if conn is not None and conn.in_transaction:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                _fail_reddit_job(
                    table, job_id,
                    f"worker finalization failed: {exc}")
                print(
                    f"[reddit_draft] {job_type} #{job_id} finalization failed: {exc}",
                    flush=True)
                continue
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            status = final["status"] if final else "failed"
            try:
                hub_event(
                    "reddit_post" if job_type == "post" else "reddit_reply",
                    f"{job_type} draft #{job_id} {status}", actor, "agent")
            except Exception as exc:
                print(f"[reddit_draft] activity log skipped: {exc}", flush=True)
            if alert:
                try:
                    if status == "ready":
                        title = f"\n{final['title']}" if final["title"] else ""
                        alert(f"*Reddit {job_type} draft ready*{title}\n\n"
                              "Review it at ops.timelabsco.in/ops/reddit.html")
                    elif status == "needs_input":
                        alert(f"*Reddit {job_type} draft needs one answer*\n"
                              f"{final['question']}\n\n"
                              "Open ops.timelabsco.in/ops/reddit.html")
                    elif status == "failed":
                        alert(f"Reddit {job_type} draft #{job_id} failed to build. "
                              "It can be retried.")
                except Exception as exc:
                    print(f"[reddit_draft] completion alert skipped: {exc}",
                          flush=True)
    finally:
        REDDIT_JOB_SLOT.release()


def _start_reddit_post_worker(alert=None, slot_held=False):
    """Start the durable queue drainer; return False only if no worker started."""
    if not slot_held and not REDDIT_JOB_SLOT.acquire(blocking=False):
        return False
    try:
        threading.Thread(
            target=_reddit_post_worker, args=(alert,), daemon=True,
            name="reddit-post-worker").start()
        return True
    except Exception:
        REDDIT_JOB_SLOT.release()
        return False


def _recover_reddit_post_jobs():
    """Resume durable jobs interrupted by a prior service stop."""
    _recover_reddit_media_quarantine()
    conn = db()
    quarantines = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        abandoned = [row[0] for row in conn.execute(
            "SELECT id FROM reddit_posts WHERE status='staging'")]
        for post_id in abandoned:
            quarantine = _quarantine_reddit_media(post_id)
            if quarantine:
                quarantines.append((post_id, quarantine))
        conn.execute("DELETE FROM reddit_posts WHERE status='staging'")
        conn.execute(
            "UPDATE reddit_posts SET status='queued', stage='recovering', "
            "error='Resuming after a service restart', started_at=NULL "
            ", queued_at=datetime('now') WHERE status='running'")
        conn.execute(
            "UPDATE reddit_drafts SET status='queued', stage='recovering', "
            "error='Resuming after a service restart', started_at=NULL "
            ", queued_at=datetime('now') WHERE status='running'")
        queued = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM reddit_posts WHERE status='queued') + "
            "(SELECT COUNT(*) FROM reddit_drafts WHERE status='queued')").fetchone()[0]
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        for post_id, quarantine in quarantines:
            try:
                _restore_reddit_quarantine(post_id, quarantine)
            except OSError as exc:
                print(f"[reddit-media] staging restore {post_id} deferred: {exc}",
                      flush=True)
        raise
    finally:
        conn.close()
    for _post_id, quarantine in quarantines:
        _purge_reddit_quarantine(quarantine)
    if queued and not _start_reddit_post_worker(_send_owner_alert):
        print("[reddit_draft] queued jobs are waiting for the shared content worker",
              flush=True)


_ensure_atomic_cross_db_journals()
_ensure_orders_schema()
_ensure_order_items_schema()
_ensure_order_events_schema()
_ensure_shipments_schema()
_ensure_reddit_schema()
_ensure_billing_schema()
_ensure_stock_schema()
_ensure_webchat_ownership()


def _fs_resolve(p):
    """Resolve a safe source/doc path; secrets, runtime data and links fail closed."""
    rp = os.path.realpath(p or FS_ROOT)
    if rp != FS_ROOT and not rp.startswith(FS_ROOT + os.sep):
        return None
    rel = os.path.relpath(rp, FS_ROOT)
    parts = [] if rel == "." else rel.split(os.sep)
    if any(part.startswith(".") or part in FS_DENY_DIRS for part in parts):
        return None
    return rp


def _fs_protected(path):
    """True if a file's CONTENTS must be withheld from the web browser because it
    holds credentials. Listing/metadata is still allowed; download is blocked too.
    Deliberately conservative — better to over-protect than leak a token."""
    base = os.path.basename(path).lower()
    low = path.lower()
    if base == ".env" or base.startswith(".env."):
        return True
    if base in ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".htpasswd", ".netrc"):
        return True
    if "/.ssh/" in low and not low.endswith(".pub"):
        return True
    if base.endswith((".pem", ".key", ".ppk", ".p12", ".pfx", ".keystore")):
        return True
    if any(w in base for w in ("secret", "oauth", "credential", "password", "token", "apikey", "api_key")):
        return True
    return False


def _extract_json_obj(text):
    """Pull the first JSON object out of a model reply (handles fences/prose)."""
    text = (text or "").strip()
    for cand in (text,):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except (json.JSONDecodeError, TypeError):
            pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            v = json.loads(m.group(1))
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(text[start:i + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        break
    return None


def theme_prompt(request, flat_settings):
    """Prompt the model to translate a plain-language request into a settings patch."""
    return (
        "You are configuring a Shopify storefront theme. Here are its current editable "
        "settings as JSON (key: value):\n\n" + json.dumps(flat_settings, ensure_ascii=False)
        + "\n\nThe store owner wants this change: \"" + request + "\"\n\n"
        "Reply with ONLY a JSON object mapping the setting keys that should change to their "
        "new values. Rules:\n"
        "- Use ONLY keys that appear above, spelled exactly.\n"
        "- Keep each value's type: numbers stay numbers, booleans stay true/false, colours "
        "are hex strings like \"#1a2a6c\".\n"
        "- Colours live under color_schemes.<scheme>.settings.<name>; for a consistent look "
        "change the same colour across all schemes unless the owner names one.\n"
        "- Fonts use Shopify font handles shaped like the current values "
        "(e.g. \"playfair_display_n4\", \"open_sans_n4\").\n"
        "- Change as few keys as needed. No prose, no markdown fences — only the JSON object."
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A URL that resolves to a safe host on the first request could still
    redirect somewhere internal on the hop — a real fetch happens either way,
    so the host check below has to hold for every request actually made, not
    just the one the caller typed. Simplest correct answer: don't follow
    redirects at all. Every real caller (Drop /raw links, a pasted product
    image URL) works fine without one."""
    def redirect_request(self, *a, **k):
        return None


def _safe_image_host(url):
    """Reject a URL whose host resolves to loopback/private/link-local/
    reserved space, so an admin-authenticated /shopify/product/ai-draft call
    can't be pointed at internal-only services on this box or a cloud
    metadata endpoint. Was previously scheme-only (any http(s) URL, no host
    check at all)."""
    import ipaddress
    import socket
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return False
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True


def _download_images(urls, limit=4):
    """Fetch a few image URLs (Drop /raw links or external) to local temp files
    so the vision model can see them. Returns [(path, url)], skipping failures."""
    opener = urllib.request.build_opener(_NoRedirect)
    saved = []
    for u in urls[:limit]:
        u = str(u).strip()
        if not u.startswith(("http://", "https://")) or not _safe_image_host(u):
            continue
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "LabsOS/1.0"})
            with opener.open(req, timeout=25) as r:
                ctype = (r.headers.get("Content-Type") or "").lower()
                data = r.read(9 * 1024 * 1024)  # cap ~9MB per image
        except Exception:
            continue
        if not data:
            continue
        ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
        path = os.path.join(UPLOAD_DIR, f"aidraft_{secrets.token_hex(6)}{ext}")
        try:
            with open(path, "wb") as f:
                f.write(data)
            saved.append((path, u))
        except OSError:
            pass
    return saved


def _drop_safe_path(rel, name):
    """Resolve a Drop folder-path + filename to an absolute path inside
    DROP_ROOT, or None if it's missing or tries to escape. Mirrors
    drop_server.py's safe_rel/safe_name — duplicated rather than imported
    since Drop runs as its own process on :8903."""
    rel = (rel or "").strip().strip("/")
    name = (name or "").strip()
    if (not name or len(name) > 200 or "/" in name or "\\" in name
            or name.startswith(".") or any(ord(c) < 0x20 for c in name)):
        return None
    parts = rel.split("/") if rel else []
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None
    path = os.path.realpath(os.path.join(DROP_ROOT, *parts, name))
    if not path.startswith(DROP_ROOT + os.sep):
        return None
    return path


def ai_product_prompt(saved, hint=""):
    """Vision prompt: turn product photos into a Shopify listing (JSON)."""
    lines = ["You are writing a Shopify product listing for Timelabs Co, an India-based brand "
             "selling Seiko watch-mod parts and complete builds (cases, dials, movements, hands, "
             "bezels, straps, crystals)."]
    for i, (path, _) in enumerate(saved, 1):
        lines.append(f"Product photo {i} is saved at {path} — analyze it with your vision tool: "
                     "identify the part type, materials, colour, finish, movement/case if visible, "
                     "and any printed branding or specs.")
    if hint:
        lines.append(f"The owner's rough idea / context: {hint}")
    lines.append(
        "Write the listing in the Timelabs brand voice: confident, concrete, no hype, no emoji. "
        "Reply with ONLY a JSON object with these keys:\n"
        '- "title": concise and specific, under 70 characters.\n'
        '- "type": exactly one of Case, Dial, Hands, Movement, Bezel, Bezel insert, Crystal, '
        'Chapter ring, Crown, Strap, Bracelet, Gasket, Case back, Complete build, Tool, Accessory.\n'
        '- "tags": array of 4-8 lowercase tags (e.g. "seiko-mod", "nh35", "sapphire").\n'
        '- "description": HTML — a 2-3 sentence opening (design/build + one distinctive detail), '
        "then a <ul> specs list (material, size/fit, compatible movement or case where visible).\n"
        '- "price": a suggested price as a number in INR, or "" if genuinely unsure.\n'
        "No text outside the JSON object."
    )
    lines.append("Mandatory product-copy standard:\n" + writing_quality.prompt_brief("product"))
    return "\n".join(lines)


def apply_content_op(desc, op, block="", name="", find="", replace=""):
    """Pure HTML transform for the Content updater. Blocks are wrapped in
    <!--labs:slug--> … <!--/labs:slug--> so re-running a block op updates it in
    place instead of stacking duplicates. Returns the new HTML."""
    desc = desc or ""
    if op in ("append_block", "prepend_block"):
        slug = re.sub(r"[^a-z0-9_-]", "", (name or "block").lower().replace(" ", "-")) or "block"
        wrapped = f"<!--labs:{slug}-->\n{block}\n<!--/labs:{slug}-->"
        pat = re.compile(r"<!--labs:%s-->.*?<!--/labs:%s-->" % (slug, slug), re.S)
        if pat.search(desc):
            return pat.sub(lambda m: wrapped, desc)   # update existing block
        if not desc.strip():
            return wrapped
        return (desc.rstrip() + "\n" + wrapped) if op == "append_block" else (wrapped + "\n" + desc)
    if op == "replace":
        return desc.replace(find, replace) if find else desc
    return desc


PREAMBLE = (
    "You are Timelabs Co's agent, chatting via Labs Chat inside Labs OS — the "
    "company's self-hosted command center at ops.timelabsco.in — with the owner "
    "(Schezan) or his partner. Be helpful and direct. Your replies "
    "render as rich Markdown — use headers, **bold**, bullet lists, and tables where "
    "they make the answer clearer, and the reader can export your reply as a PDF, "
    "so structure longer answers like a clean document. You have your usual tools "
    "(terminal, files, web, vision) and the business database at "
    "/root/ops-dashboard/data/hermes.db (orders, products, action_items, "
    "memory_facts, content_library, and hub_events — a live feed of everything "
    "happening in Labs OS: file uploads, public share links, photo organizing, "
    "product creates/updates, and access changes; check it when asked what's new). "
    "Labs OS is a family of tools (Home dashboard, Drop files, Ledger costs, this "
    "Chat, plus a Tools launcher with a Blog builder, a Quick product updater, and "
    "a Product builder that pushes new products live to Shopify). You have a "
    "knowledge base about the OS itself in memory_facts (category 'labs_os') — "
        "and you can ACT on the store yourself with the `labs` terminal command "
        "(labs orders / products / costs / theme, and labs product-create, article-create, "
        "theme-set … which dry-run unless you add --execute; run `labs --help`). It reuses "
        "the existing credentials, so never ask for new Shopify or Google keys. "
    "consult it when asked how the system works or how to do a store task, and "
    "point the owner at the right tool URL. Team files live in Drop at "
    "/srv/timelabs-drop (browse it directly). Sourcing costs and margins are in "
    "/root/ops-dashboard/data/suppliers.db, surfaced at /ops/ledger.html. "
    "When a conversation surfaces something durable, save it to memory_facts. "
    "For anything CURRENT — today's order counts, what is owed, what is stuck, "
    "which services are up, who has access — read memory_facts category 'state' "
    "FIRST. Those rows are rewritten every 6 hours by context_sync.py, and when "
    "one contradicts an older fact the 'state' row is the true one. Never edit "
    "or save into 'state' by hand: it is overwritten on the next run. "
    "ORDERS NOW LIVE IN ONE PLACE — this changed recently, don't fall back on the old "
    "assumption that sqlite misses the storefront: hermes.db's orders + order_items "
    "tables hold every channel (order form, WhatsApp, AND the storefront), because "
    "Shopify orders now sync in automatically every 5 minutes (shopify_order_sync.py, "
    "ops-order-sync.timer). Querying sqlite directly is correct and fast — for sales, "
    "revenue or best-seller questions run `labs sales` (reads order_items, the exact "
    "table the order form's What's Selling tab uses, so your answer and what the "
    "owner sees on screen always match) or query hermes.db directly with SQL. For "
    "finding one specific order use `labs orders --query <text>`, which also checks "
    "Shopify live — useful because a storefront order can be up to ~5 minutes old "
    "before the local sync picks it up, so for anything from the last few minutes "
    "prefer that live path over sqlite. "
    "HOW TO OPERATE — you are an operator, not a narrator. For anything that touches "
    "live data or the store: (1) work out the few steps, (2) actually RUN them with the "
    "`labs` command and your terminal, (3) check the result rather than assuming it "
    "worked, (4) report what you did and the real numbers. Never answer from memory when "
    "a command could give the true figure, never invent a number, and never tell the "
    "owner to go look somewhere you could have looked yourself. For multi-step jobs keep "
    "a todo list and work through it. Writes via `labs` are dry-run until --execute, so "
    "show the plan first, then apply. Check memory_facts category 'preference' for how "
    "he wants things written and built — voice, taste, and what he has ruled out. "
    "If something genuinely blocks you, say so in one line and name what would unblock it. "
    "LEARN PROACTIVELY: when this conversation produces a decision, preference, "
    "correction, or new task, write it to the database yourself (action_items for "
    "tasks, memory_facts for knowledge, content_library for approved copy) and "
    "mention in one short clause that you saved it — don't wait to be asked. "
    "All marketing copy follows the Timelabs brand voice from your identity. "
    "Recent conversation:\n"
)

NONADMIN_PREAMBLE = (
    "You are the Timelabs Co assistant inside Labs Command, helping an authorized "
    "team operator. Be helpful, direct, and honest about the access available in "
    "this session. You may research the public web, ask clarifying questions, and "
    "analyze images attached to this conversation. You do not have terminal or "
    "filesystem access, Shopify/store write access, owner conversations, global "
    "Hermes memory, credentials, private company databases, or other operators' "
    "sessions. Never claim that you inspected or changed those systems. If the "
    "operator requests a privileged or live-data action, prepare a concise plan "
    "and say that an admin must review and run it. Replies render as Markdown, so "
    "use short headings, bullets, and tables only when they improve clarity. "
    "Recent conversation:\n"
)

CONTENT_HANDOFF = (
    "Creator handoff format: put text that can be pasted into the destination "
    "inside one or more fenced code blocks. Put a short plain heading immediately "
    "before each block so the creator knows where it belongs. Use separate blocks "
    "for separate fields or choices, such as Email subject, Email body, Story frame "
    "or Caption option. Never put explanations, citations, internal notes or review "
    "instructions inside a paste block. Never leave bracketed placeholders in a "
    "paste block. If an essential fact is missing, ask one short question instead of "
    "guessing; otherwise omit the unknown detail. After the paste blocks, add a "
    "'Final checks' section only when useful, with no more than three brief items the "
    "creator must confirm before publishing. Do not narrate that you followed these rules."
)

# Content routes: a slash command expands into a structured brief and selects
# the channel-specific writing standard. These create drafts only; authorization
# and any external action remain separate.
TEMPLATES = {
    "/caption": ("instagram",
        "Write an Instagram caption in the Timelabs brand voice. Structure: hook line "
        "(no 'introducing'), 2-3 short lines of concrete detail (movement, build, delivery), "
        "one natural next step, and no more than five relevant hashtags if they genuinely help. "
        "Give 2 variants. Brief: "
    ),
    "/story": ("instagram",
        "Write an Instagram Story sequence of 3-5 frames. Each frame needs one short on-screen "
        "line and, only where useful, a poll/question/link-sticker suggestion. Add what the image "
        "cannot show; do not describe the picture back to the viewer. Brief: "
    ),
    "/ad": ("meta_ad",
        "Write Meta ad copy in the Timelabs brand voice: 3 primary-text variants "
        "(under 125 chars each), 3 headlines (under 40 chars), 1 description (under 30 chars). "
        "Use only supplied proof and no fake urgency. Keep primary text, headlines and "
        "description in clearly labelled paste blocks, not a table. Brief: "
    ),
    "/product": ("product",
        "Write a Shopify product description in the Timelabs brand voice: 2-3 sentence "
        "opening (design story, movement, one distinctive detail), then a specs list "
        "using only supplied facts. Never infer water resistance, delivery, material or compatibility. "
        "No emoji or superlatives without evidence. Brief: "
    ),
    "/email": ("email",
        "Write a customer email in the Timelabs brand voice: subject line (under 45 chars, "
        "no clickbait), founder-personal body under 120 words, one soft CTA. Give 2 subject "
        "variants. Brief: "
    ),
    "/post": ("whatsapp",
        "Write this week's WhatsApp community post per the playbook calendar in the Timelabs "
        "brand voice: under 80 words, one concrete detail and one relevant next step. Brief: "
    ),
    "/whatsapp": ("whatsapp",
        "Write a WhatsApp message in the Timelabs voice. Answer the actual situation early, keep "
        "it natural on a phone, and use one clear next step. Use only the facts in the brief. Brief: "
    ),
    "/sales": ("sales",
        "Write a one-to-one sales reply for a watch buyer. Answer their question first, use only "
        "the supplied price/specification/availability/delivery facts, and close with one helpful "
        "next step. No pressure, fake scarcity or scripted sales language. Brief: "
    ),
    "/reddit": ("reddit",
        "Write a Reddit draft as a useful watch-community participant. Choose the route stated in "
        "the brief (showcase, build diary, honest review, educational answer, comparison, founder "
        "note or question), disclose the brand relationship where relevant, and do not promote. Brief: "
    ),
    "/blog": ("blog",
        "Write a useful blog draft that answers the stated question early and uses only the supplied "
        "first-party evidence and openable sources. Delete padding and do not invent citations. Brief: "
    ),
    "/youtube": ("youtube",
        "Write a YouTube script for speech and footage, opening on the object, conflict or result. "
        "Mark what should be shown, avoid repeated previews/recaps, and preserve every fact. Brief: "
    ),
    "/linkedin": ("linkedin",
        "Write a LinkedIn draft from a real decision, mistake, customer moment or number. No false "
        "vulnerability, lesson list, engagement bait or generic founder moral. Brief: "
    ),
    "/founder": ("founder",
        "Write in the approved TimeLabs founder voice from a real decision, mistake, customer moment "
        "or number. Preserve the specific tension and do not manufacture a moral. Brief: "
    ),
}


def expand_template(message):
    for cmd, (channel, brief) in TEMPLATES.items():
        if message.lower().startswith(cmd):
            return brief + message[len(cmd):].strip(), channel
    channel = writing_quality.infer_channel(message)
    return message, channel


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def store(session_id, role, text):
    conn = db()
    cur = conn.execute(
        "INSERT INTO webchat_messages (session_id, role, text) VALUES (?, ?, ?)",
        (session_id, role, text),
    )
    message_id = cur.lastrowid
    conn.execute(
        "UPDATE webchat_sessions SET updated_at=datetime('now') WHERE id=?", (session_id,)
    )
    # auto-title from the first user message
    if role == "user":
        row = conn.execute(
            "SELECT title FROM webchat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row and row["title"] in ("New chat", ""):
            title = text.strip().replace("\n", " ")[:48]
            conn.execute("UPDATE webchat_sessions SET title=? WHERE id=?", (title, session_id))
    conn.commit()
    conn.close()
    return message_id


def ensure_session(session_id, email=None):
    conn = db()
    row = conn.execute(
        "SELECT id, owner_email FROM webchat_sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    email = (email or "").strip().lower()
    return bool(row and (email in ADMIN_EMAILS or row["owner_email"] == email))


def recent(session_id, limit):
    conn = db()
    rows = conn.execute(
        "SELECT role, text FROM webchat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed(rows))


def build_prompt(session_id, message, images=None, admin=True, content_channel=None):
    """images: list of (path, name, ocr_text) — supports multi-photo messages."""
    lines = [PREAMBLE if admin else NONADMIN_PREAMBLE]
    lines.append(
        "When this request creates or rewrites public/customer-facing content, "
        "apply this mandatory standard before drafting:\n"
        + writing_quality.prompt_brief(content_channel or "general"))
    if content_channel:
        lines.append(CONTENT_HANDOFF)
    if not admin:
        lines.append(
            "Approved public TimeLabs context for this team account: TimeLabs Co is an "
            "Indian independent maker of hand-built custom watches powered by authentic "
            "Seiko movements. Complete builds can use aftermarket/custom components and "
            "must never be described as factory Seiko models or as affiliated with Seiko. "
            "The voice is confident, concrete, collector-aware and calm. Only use a price, "
            "movement, material, water-resistance, compatibility, delivery or warranty fact "
            "when the team member supplied it in this conversation or it is clearly visible "
            "in an attached source. Ask for a required missing fact; otherwise omit it.")
    user_label = "Owner" if admin else "Team member"
    for row in recent(session_id, CONTEXT_TURNS):
        speaker = user_label if row["role"] == "user" else "You"
        lines.append(f"{speaker}: {row['text']}")
    for i, (path, name, ocr_text) in enumerate(images or [], 1):
        n = f" {i} of {len(images)}" if len(images) > 1 else ""
        lines.append(
            f"\nThe {user_label.lower()} attached a photo{n} ({name or 'image'}), saved at "
            f"{path} — analyze it with your vision tool as part of answering."
        )
        if ocr_text:
            lines.append(
                "\nOCR text extracted from this photo (verbatim, may contain recognition "
                f"errors — trust your vision reading over this where they differ):\n{ocr_text}"
            )
    lines.append(f"\n{user_label}'s new message: {message}\n\nYour reply:")
    return "\n".join(lines)


PDF_CSS = """
@page { size: A4; margin: 2.2cm 2cm; @bottom-right { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; } }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt; line-height: 1.55; color: #1a1f1b; }
.doc-head { border-bottom: 2px solid #8a6a2c; padding-bottom: 8pt; margin-bottom: 14pt; }
.doc-head .mark { font-family: Helvetica, Arial, sans-serif; font-size: 7pt; letter-spacing: 3pt; text-transform: uppercase; color: #8a6a2c; }
.doc-head h1 { font-size: 16pt; margin: 3pt 0 0; }
.doc-head .stamp { font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt; color: #777; margin-top: 3pt; }
h1 { font-size: 14pt; margin: 14pt 0 5pt; } h2 { font-size: 12pt; margin: 12pt 0 4pt; } h3 { font-size: 11pt; margin: 10pt 0 3pt; }
p { margin: 5pt 0; } ul, ol { margin: 5pt 0; padding-left: 16pt; } li { margin: 2pt 0; }
code { font-family: 'Courier New', monospace; font-size: 9pt; background: #f2f0e8; padding: 1pt 3pt; }
pre { background: #f2f0e8; border: 1pt solid #ddd; padding: 7pt 9pt; font-size: 8.5pt; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 7pt 0; font-size: 9.5pt; width: 100%; }
th, td { border: 1pt solid #ccc; padding: 4pt 7pt; text-align: left; } th { background: #efece1; }
blockquote { border-left: 3pt solid #8a6a2c; margin: 7pt 0; padding: 2pt 10pt; color: #555; }
a { color: #8a6a2c; } hr { border: none; border-top: 1pt solid #ccc; margin: 10pt 0; }
"""


def make_pdf(markdown, title):
    from weasyprint import HTML
    import datetime
    body = md_to_html(markdown)
    stamp = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    doc = (
        f"<html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head><body>"
        f"<div class='doc-head'><div class='mark'>Timelabs Co</div>"
        f"<h1>{html_mod.escape(title)}</h1>"
        f"<div class='stamp'>Generated by the Timelabs agent · {stamp}</div></div>"
        f"{body}</body></html>"
    )
    return HTML(string=doc).write_pdf()


SAFE_WEB_TOOLSET = "web,memory,skills,session_search,context_engine,clarify,vision,image_gen,tts,todo"
# Full-role operators get useful research and image-reading, but no shared
# Hermes state. `memory`, `session_search`, `skills`, and even generic context
# plugins are global to this VPS rather than scoped to webchat ownership.
# Exposing them would let one operator retrieve or persist data in the
# owner's CLI/WhatsApp sessions despite the SQL session boundary below.
NONADMIN_TOOLSET = "web,clarify,vision"
CREATOR_PREVIEW_TOOLSET = NONADMIN_TOOLSET

# Admins additionally get `terminal`, which is what lets the agent actually DO
# things via the `labs` CLI instead of only describing them. Granted by role,
# never globally: /send is reachable by any signed-in user, so a blanket grant
# would let a restricted role (e.g. intake) ask the agent to run commands as
# root. Admins already hold root SSH, so for them this adds no new privilege.
ADMIN_TOOLSET = SAFE_WEB_TOOLSET + ",terminal"


def toolset_for(email):
    return ADMIN_TOOLSET if (email or "").strip().lower() in ADMIN_EMAILS else NONADMIN_TOOLSET


def run_hermes(prompt, model=None, provider=None, toolset=None, ignore_rules=False,
               timeout=None):
    cmd = ["hermes"]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    if ignore_rules:
        # One-shot mode otherwise injects the owner's shared memory, rules,
        # and preloaded skills even when their callable tools are absent.
        cmd.append("--ignore-rules")
    cmd += ["-t", toolset or SAFE_WEB_TOOLSET]
    cmd += ["-z", prompt]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout or HERMES_HARD_TIMEOUT,
    )
    reply = result.stdout.strip()
    if result.returncode != 0 or not reply:
        raise RuntimeError(result.stderr[-300:] or "empty reply")
    return reply


def ocr_image(path):
    """Extract text from an image with tesseract. Returns '' when nothing usable."""
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "--psm", "3", "-l", "eng"],
            capture_output=True, text=True, timeout=25,
        )
        text = re.sub(r"\n{3,}", "\n\n", (r.stdout or "")).strip()
        return text[:4000]
    except Exception:
        return ""


# --- per-session model override (set via the /model chat command) -----------
MODEL_ALIASES = {
    "claude": ("claude-sonnet-4-6", "anthropic"),
    "minimax": ("MiniMax-M3", "minimax-oauth"),
    "free": ("stepfun/step-3.7-flash:free", "nous"),
}

# Choices exposed by the Command UI. Exact free-form model names remain
# available to admins through the existing /model command, but the shared UI
# only offers routes we deliberately support and can describe accurately.
SESSION_MODEL_CHOICES = {
    "auto": (None, None),
    "claude": MODEL_ALIASES["claude"],
    "minimax": MODEL_ALIASES["minimax"],
}


def _init_prefs():
    conn = db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS webchat_model_pref ("
        "session_id INTEGER PRIMARY KEY, model TEXT, provider TEXT, "
        "last_model TEXT, last_provider TEXT, last_used_at TEXT)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(webchat_model_pref)")}
    for column in ("last_model", "last_provider", "last_used_at"):
        if column not in columns:
            conn.execute(f"ALTER TABLE webchat_model_pref ADD COLUMN {column} TEXT")
    conn.commit()
    conn.close()


_init_prefs()


def get_model_pref(session_id):
    conn = db()
    row = conn.execute(
        "SELECT model, provider FROM webchat_model_pref WHERE session_id=?", (session_id,)
    ).fetchone()
    conn.close()
    return (row["model"], row["provider"]) if row else (None, None)


def record_model_use(session_id, model, provider):
    """Remember the route that actually produced the latest answer."""
    conn = db()
    conn.execute(
        "INSERT INTO webchat_model_pref "
        "(session_id, model, provider, last_model, last_provider, last_used_at) "
        "VALUES (?, NULL, NULL, ?, ?, datetime('now')) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "last_model=excluded.last_model, last_provider=excluded.last_provider, "
        "last_used_at=excluded.last_used_at",
        (session_id, model or "hermes-default", provider),
    )
    conn.commit()
    conn.close()


VISION_PREFIXES = ("claude",)  # models that can actually see images
VISION_MODEL = ("claude-sonnet-4-6", "anthropic")
FAILOVER_CHAIN = [
    ("claude-sonnet-4-6", "anthropic"),
    ("MiniMax-M3", "minimax-oauth"),
    ("stepfun/step-3.7-flash:free", "nous"),
]


def is_vision(model):
    return model is None or model.lower().startswith(VISION_PREFIXES)


def route_models(pref_model, pref_provider, has_image):
    """Return ordered (model, provider, note) candidates: preferred first, then failovers.
    A None model means the currently configured Hermes default routing."""
    chain, seen = [], set()

    def add(m, p, note=None):
        key = m or "__default__"
        if key not in seen:
            seen.add(key)
            chain.append((m, p, note))

    if has_image and pref_model and not is_vision(pref_model):
        add(*VISION_MODEL, note=f"auto-switched from {pref_model} for image analysis")
        add(pref_model, pref_provider)
    else:
        add(pref_model, pref_provider)
    for m, p in FAILOVER_CHAIN:
        add(m, p, note=f"fallback — earlier model unavailable")
    return chain


def handle_model_command(session_id, message):
    """Handle '/model ...' without invoking hermes. Returns the reply text."""
    # tolerate '--provider X' / 'provider=X' forms
    msg = message.replace("--provider", " ").replace("provider=", " ")
    parts = msg.split()
    if len(parts) == 1:
        model, provider = get_model_pref(session_id)
        cur = f"`{model}`" + (f" (provider `{provider}`)" if provider else "") if model \
            else "Hermes Auto (the currently configured default with failover)"
        return (f"Current model for this chat: {cur}\n\n"
                "Switch with `/model claude`, `/model minimax`, `/model free`, "
                "`/model <exact-model-name> [provider]`, or `/model default` to reset.")
    conn = db()
    if parts[1].lower() in ("default", "reset", "auto"):
        conn.execute(
            "INSERT INTO webchat_model_pref (session_id, model, provider) "
            "VALUES (?, NULL, NULL) ON CONFLICT(session_id) DO UPDATE SET "
            "model=NULL, provider=NULL", (session_id,))
        conn.commit(); conn.close()
        return "Model reset — Hermes Auto will choose the configured default and failovers."
    alias = MODEL_ALIASES.get(parts[1].lower())
    model, provider = alias if alias else (parts[1], parts[2] if len(parts) > 2 else None)
    conn.execute(
        "INSERT INTO webchat_model_pref (session_id, model, provider) VALUES (?,?,?) "
        "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model, provider=excluded.provider",
        (session_id, model, provider),
    )
    conn.commit(); conn.close()
    extra = " ⚠️ Note: this model may not support images — attached photos will still be OCR'd to text for it." \
        if model and not model.lower().startswith("claude") else ""
    return f"Model for this chat set to `{model}`" + (f" (provider `{provider}`)" if provider else "") + f".{extra}"


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _oauth_email(self, uri):
        """Validate the browser session with oauth2-proxy.

        This deliberately happens in the same loopback request as the Labs
        role check. nginx auth_request subrequests cannot themselves run
        another auth_request, so trying to compose the two in nginx silently
        skipped OAuth and left the role gate with an empty identity.
        """
        cookie = self.headers.get("Cookie") or ""
        if not cookie:
            return None
        req = urllib.request.Request(
            "http://127.0.0.1:4180/oauth2/auth",
            headers={
                "Cookie": cookie,
                "Host": self.headers.get("Host") or "ops.timelabsco.in",
                "X-Real-IP": self.headers.get("X-Real-IP") or "127.0.0.1",
                "X-Scheme": self.headers.get("X-Scheme") or "https",
                "X-Auth-Request-Redirect": uri or "/ops/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                email = response.headers.get("X-Auth-Request-Email") or ""
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            return None
        return email.strip().lower() or None

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/access/gate":
            uri = self.headers.get("X-Original-URI") or ""
            email = self._oauth_email(uri)
            if not email:
                self.send_response(401)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            import access_store
            if not access_store.can_open_path(email, uri):
                self.send_response(403)
                self.send_header("X-Labs-Home", access_store.home_for(email))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("X-Labs-Email", email)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/access/home":
            email = (self.headers.get("X-User-Email") or "").strip().lower()
            import access_store
            self.send_response(302)
            self.send_header("Location", access_store.home_for(email))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/allowlist":
            self._handle_allowlist_get()
            return
        if path == "/whoami":
            email = (self.headers.get("X-User-Email") or "").strip().lower()
            self._json(200, {"email": email, "admin": email in ADMIN_EMAILS})
            return
        if path == "/shopify/connect":
            self._handle_shopify_connect()
            return
        if path == "/shopify/callback":
            self._handle_shopify_callback(query)
            return
        if path == "/shopify/products":
            self._handle_shopify_products(query)
            return
        if path == "/shopify/product/detail":
            self._handle_shopify_detail(query)
            return
        if path == "/orders/list":
            self._handle_orders_list()
            return
        if path == "/orders/meta":
            self._handle_orders_meta()
            return
        if path == "/orders/products":
            self._handle_orders_products()
            return
        if path == "/supplier/orders":
            self._handle_supplier_orders()
            return
        if path == "/supplier/photo":
            self._handle_supplier_photo(query)
            return
        if path == "/supplier/card":
            self._handle_supplier_card(query)
            return
        if path == "/supplier/ledger":
            self._handle_supplier_ledger(query)
            return
        if path == "/supplier/shipments":
            self._handle_supplier_shipments()
            return
        if path == "/supplier/arrears":
            self._handle_supplier_arrears()
            return
        if path == "/supplier/bill":
            self._handle_supplier_bill(query)
            return
        if path == "/supplier/bills":
            self._handle_supplier_bills()
            return
        if path == "/reddit/setup":
            self._handle_reddit_setup()
            return
        if path == "/reddit/replies":
            self._handle_reddit_replies()
            return
        if path == "/reddit/posts":
            self._handle_reddit_posts()
            return
        if path == "/reddit/post/photo":
            self._handle_reddit_post_photo(query)
            return
        if path == "/reddit/threads":
            self._handle_reddit_threads(query)
            return
        if path == "/orders/photo":
            self._handle_order_photo(query)
            return
        if path == "/customers/list":
            self._handle_customers_list()
            return
        if path == "/access/me":
            self._handle_access_me(query)
            return
        if path == "/access/list":
            self._handle_access_list()
            return
        if path == "/fs/list":
            self._handle_fs_list(query)
            return
        if path == "/fs/read":
            self._handle_fs_read(query)
            return
        if path == "/fs/download":
            self._handle_fs_download(query)
            return
        if path == "/shopify/theme/settings":
            self._handle_shopify_theme_settings()
            return
        if path == "/shopify/theme/status":
            if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
                self._json(403, {"error": "admins only"})
            else:
                import shopify_api
                self._json(200, shopify_api.theme_status() if shopify_api.configured()
                           else {"available": False, "reason": "Shopify isn't connected."})
            return
        if path == "/shopify/blogs":
            self._handle_shopify_blogs()
            return
        if path == "/shopify/articles":
            self._handle_shopify_articles(query)
            return
        if path == "/shopify/article/detail":
            self._handle_shopify_article_detail(query)
            return
        if path == "/shopify/pages":
            self._handle_shopify_pages()
            return
        if path == "/shopify/page/detail":
            self._handle_shopify_page_detail(query)
            return
        if path == "/events":
            # Command is available to the Creator role, but the global Hub
            # activity feed can include access, order and store events. Only a
            # role that can already open Home receives that cross-business feed.
            if not self._has_tool("face"):
                self._json(403, {"error": "not available for this account"})
                return
            conn = db()
            try:
                rows = conn.execute(
                    "SELECT created_at, app, kind, actor, detail FROM hub_events "
                    "ORDER BY id DESC LIMIT 30"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []   # table not created until the first event
            conn.close()
            self._json(200, {"events": [dict(r) for r in rows]})
            return
        if path == "/sessions":
            # Every chat session ever had, across everyone — fine when every
            # signed-in person was an equally-trusted admin, not once a
            # deliberately-restricted outside role (supplier) can sign in too.
            if not self._has_tool("chat"):
                self._json(403, {"error": "not available for this account"})
                return
            email = (self.headers.get("X-User-Email") or "").strip().lower()
            conn = db()
            sql = (
                "SELECT s.id, s.title, s.updated_at, "
                "CASE WHEN s.visibility='archived' THEN 1 ELSE 0 END AS archived, "
                "p.model AS preferred_model, p.provider AS preferred_provider, "
                "p.last_model, p.last_provider, p.last_used_at, "
                "(SELECT COUNT(*) FROM webchat_messages m WHERE m.session_id = s.id) AS n "
                "FROM webchat_sessions s LEFT JOIN webchat_model_pref p ON p.session_id=s.id"
            )
            args = ()
            if email not in ADMIN_EMAILS:
                sql += " WHERE s.owner_email=?"
                args = (email,)
            rows = conn.execute(
                sql + " ORDER BY archived ASC, s.updated_at DESC", args).fetchall()
            conn.close()
            self._json(200, {"sessions": [dict(r) for r in rows]})
            return
        if path == "/history":
            if not self._has_tool("chat"):
                self._json(403, {"error": "not available for this account"})
                return
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            try:
                session_id = int(params.get("session", 1))
            except ValueError:
                session_id = 1
            email = (self.headers.get("X-User-Email") or "").strip().lower()
            if not ensure_session(session_id, email):
                self._json(404, {"error": "no such conversation"})
                return
            conn = db()
            rows = conn.execute(
                "SELECT id, role, text, created_at FROM webchat_messages "
                "WHERE session_id=? ORDER BY id DESC LIMIT 80",
                (session_id,),
            ).fetchall()
            conn.close()
            self._json(200, {"messages": [dict(r) for r in reversed(rows)]})
            return
        self._json(404, {"error": "not found"})

    # --- Key: allowlist management (admin-only) -----------------------------
    def _admin_email(self):
        """The authenticated caller's email if they're an admin, else None.
        nginx sets X-User-Email from oauth2-proxy's auth_request on every
        /ops/agent/api/ request; the port is loopback-only otherwise."""
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        return email if email in ADMIN_EMAILS else None

    @staticmethod
    def _read_allowlist():
        try:
            with open(ALLOWLIST_PATH) as f:
                seen, out = set(), []
                for line in f:
                    e = line.strip().lower()
                    if e and e not in seen:
                        seen.add(e)
                        out.append(e)
                return out
        except OSError:
            return []

    @staticmethod
    def _write_allowlist(emails):
        """Timestamped backup, then atomic replace (oauth2-proxy hot-reloads)."""
        import shutil, time as _t
        shutil.copy2(ALLOWLIST_PATH, f"{ALLOWLIST_PATH}.bak.{int(_t.time())}")
        tmp = ALLOWLIST_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(emails) + "\n")
        os.replace(tmp, ALLOWLIST_PATH)

    def _handle_allowlist_get(self):
        admin = self._admin_email()
        if not admin:
            self._json(403, {"error": "admins only"})
            return
        members = self._read_allowlist()
        self._json(200, {"members": [
            {"email": e, "admin": e in ADMIN_EMAILS, "you": e == admin}
            for e in members
        ]})

    def _handle_allowlist_change(self, action):
        admin = self._admin_email()
        if not admin:
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            email = str(payload.get("email", "")).strip().lower()
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        if not EMAIL_RE.match(email):
            self._json(400, {"error": "that doesn't look like an email address"})
            return
        members = self._read_allowlist()
        if action == "add":
            # The legacy Key dialog grants Full access. Do this through the
            # same atomic path as People & Access so an allowlisted person
            # never exists briefly without an explicit role.
            try:
                import access_store
                _, note, existed = access_store.invite(email, "full")
            except (ValueError, RuntimeError, OSError) as e:
                self._json(500, {"error": f"could not invite member: {e}"})
                return
            members = self._read_allowlist()
            print(f"[key] {admin} added {email}", flush=True)
            hub_event("member_add", email, admin)
            self._json(200, {
                "ok": True, "note": "already a member" if existed else note,
                "members": members,
            })
            return
        else:  # remove
            if email == admin:
                self._json(400, {"error": "you can't remove yourself — ask the other admin"})
                return
            remaining_admins = [e for e in members if e in ADMIN_EMAILS and e != email]
            if email in ADMIN_EMAILS and not remaining_admins:
                self._json(400, {"error": "refusing to remove the last admin"})
                return
            if email not in members:
                self._json(404, {"error": "not a member"})
                return
            members = [e for e in members if e != email]
        try:
            self._write_allowlist(members)
        except OSError as e:
            self._json(500, {"error": f"could not write allowlist: {e}"})
            return
        print(f"[key] {admin} {action}ed {email}", flush=True)
        hub_event(f"member_{action}", email, admin)
        try:                                    # keep the tool-access gate in step
            import access_store
            access_store.forget(email) if action == "remove" else access_store.sync_nginx()
        except Exception as _e:
            print(f"[key] gate sync skipped: {_e}", flush=True)
        self._json(200, {"ok": True, "members": members})

    # --- Shopify connect (OAuth) -------------------------------------------
    @staticmethod
    def _shopify_creds():
        try:
            return json.load(open(SHOPIFY_OAUTH_CREDS))
        except (OSError, json.JSONDecodeError):
            return None

    def _redirect(self, location, shopify_cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if shopify_cookie is not None:
            if shopify_cookie:
                self.send_header(
                    "Set-Cookie",
                    "__Host-labs_shopify_state=" + shopify_cookie
                    + f"; Path=/; Max-Age={SHOPIFY_STATE_TTL}; "
                      "Secure; HttpOnly; SameSite=Lax",
                )
            else:
                self.send_header(
                    "Set-Cookie",
                    "__Host-labs_shopify_state=; Path=/; Max-Age=0; "
                    "Secure; HttpOnly; SameSite=Lax",
                )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_shopify_connect(self):
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        if email not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        creds = self._shopify_creds()
        if not creds:
            self._json(500, {"error": "no Shopify app credentials stored"})
            return
        shop = self._normal_shop(creds.get("shop"))
        if (not shop or not creds.get("client_id") or not creds.get("client_secret")
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", shop)):
            self._json(500, {"error": "stored Shopify OAuth credentials are invalid"})
            return
        state = secrets.token_urlsafe(16)
        with _shopify_states_lock:
            now = time.time()
            for old, record in list(_shopify_states.items()):
                if record["expires"] < now:
                    _shopify_states.pop(old, None)
            _shopify_states[state] = {
                "actor": email,
                "expires": now + SHOPIFY_STATE_TTL,
                "shop": shop,
            }
        state_sig = hmac.new(
            str(creds["client_secret"]).encode(), state.encode(), hashlib.sha256
        ).hexdigest()
        from urllib.parse import urlencode
        url = f"https://{shop}/admin/oauth/authorize?" + urlencode({
            "client_id": creds["client_id"], "scope": SHOPIFY_SCOPES,
            "redirect_uri": SHOPIFY_REDIRECT, "state": state,
        })
        self._redirect(url, f"{state}.{state_sig}")

    @staticmethod
    def _normal_shop(value):
        shop = str(value or "").strip().lower().rstrip(".")
        if "://" in shop:
            shop = (urllib.parse.urlparse(shop).hostname or "").lower().rstrip(".")
        return shop

    def _handle_shopify_callback(self, query):
        creds = self._shopify_creds()
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        params = dict(pairs)
        code, state = params.get("code"), params.get("state")
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        with _shopify_states_lock:
            state_record = _shopify_states.pop(state, None)
        shop = self._normal_shop(params.get("shop"))
        configured_shop = self._normal_shop((creds or {}).get("shop"))
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie") or "")
            state_cookie = cookies.get("__Host-labs_shopify_state")
            state_cookie = state_cookie.value if state_cookie else ""
        except http.cookies.CookieError:
            state_cookie = ""
        cookie_state, dot, cookie_sig = state_cookie.partition(".")
        expected_cookie_sig = hmac.new(
            str((creds or {}).get("client_secret") or "").encode(),
            cookie_state.encode(),
            hashlib.sha256,
        ).hexdigest()
        valid_state_cookie = (
            bool(dot) and cookie_state == state
            and hmac.compare_digest(cookie_sig.lower(), expected_cookie_sig)
        )
        duplicate_keys = len(params) != len(pairs)
        supplied_hmac = params.pop("hmac", "")
        # Shopify's legacy serializer also excludes `signature`. Values must
        # be percent-encoded after parsing; joining decoded strings rejects
        # valid callbacks containing spaces, +, /, =, or encoded host data.
        params.pop("signature", None)
        message = canonical_hmac_message(params)
        expected_hmac = hmac.new(
            str((creds or {}).get("client_secret") or "").encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        valid_hmac = bool(supplied_hmac) and hmac.compare_digest(
            supplied_hmac.lower(), expected_hmac)
        if (not creds or not code or not state_record or duplicate_keys
                or state_record["expires"] < time.time()
                or email not in ADMIN_EMAILS or state_record["actor"] != email
                or not shop or shop != configured_shop or state_record["shop"] != shop
                or not valid_hmac or not valid_state_cookie):
            self._redirect("/ops/tools.html?shopify=failed", "")
            return
        from urllib.parse import urlencode
        body = urlencode({"client_id": creds["client_id"],
                          "client_secret": creds["client_secret"], "code": code}).encode()
        try:
            req = urllib.request.Request(f"https://{shop}/admin/oauth/access_token", data=body)
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
            access = tok.get("access_token")
            granted = {s.strip() for s in str(tok.get("scope") or "").split(",") if s.strip()}
            required = {s.strip() for s in SHOPIFY_SCOPES.split(",") if s.strip()}
        except Exception as e:
            print(f"[shopify] token exchange failed: {e}", flush=True)
            self._redirect("/ops/tools.html?shopify=failed", "")
            return
        if not access or not required.issubset(normalized_scopes(granted)):
            self._redirect("/ops/tools.html?shopify=failed", "")
            return
        write_env({"SHOPIFY_SHOP": shop, "SHOPIFY_ADMIN_TOKEN": access})
        print(f"[shopify] connected by {email}", flush=True)
        hub_event("shopify_connected", "Shopify store connected", email, app="shopify")
        self._redirect("/ops/tools.html?shopify=connected", "")

    # --- Quick product updater (Shopify) -----------------------------------
    def _handle_shopify_products(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        from urllib.parse import parse_qsl
        params = dict(parse_qsl(query))
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected — open Tools and connect it."})
            return
        try:
            data = shopify_api.list_products(search=params.get("q", ""), first=25,
                                             after=params.get("after") or None)
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})
            return
        self._json(200, data)

    def _handle_shopify_detail(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        from urllib.parse import parse_qsl
        pid = dict(parse_qsl(query)).get("id")
        import shopify_api
        if not pid:
            self._json(400, {"error": "missing product"})
            return
        try:
            self._json(200, shopify_api.get_detail(pid))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_media(self, action):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        import shopify_api
        pid = p.get("product_id")
        if not pid:
            self._json(400, {"error": "missing product"})
            return
        try:
            if action == "add":
                url = str(p.get("url", "")).strip()
                if not url.startswith(("http://", "https://")):
                    self._json(400, {"error": "give a public image URL (https://…)"})
                    return
                shopify_api.add_media_url(pid, url)
            else:
                shopify_api.remove_media(pid, p.get("media_id"))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})
            return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("product_media", f"{action} image on {p.get('title', pid.split('/')[-1])}", actor, app="shopify")
        self._json(200, {"ok": True})

    def _handle_shopify_product_update(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        import shopify_api
        pid, vid = p.get("product_id"), p.get("variant_id")
        if not pid:
            self._json(400, {"error": "missing product"})
            return
        changed = {}
        try:
            if p.get("status") in ("ACTIVE", "DRAFT", "ARCHIVED"):
                shopify_api.set_status(pid, p["status"])
                changed["status"] = p["status"]
            if p.get("price") is not None and vid:
                price = f"{float(p['price']):.2f}"
                shopify_api.set_price(pid, vid, price)
                changed["price"] = price
            fields = {}
            if "title" in p and str(p["title"] or "").strip():
                fields["title"] = str(p["title"]).strip()
            if "type" in p:
                fields["productType"] = str(p["type"]).strip()
            if "description" in p:
                fields["descriptionHtml"] = str(p["description"])
            if "tags" in p and isinstance(p["tags"], list):
                fields["tags"] = [str(t).strip() for t in p["tags"] if str(t).strip()]
            if fields:
                shopify_api.update_fields(pid, fields)
                changed.update({k: (v if not isinstance(v, list) else ",".join(v)) for k, v in fields.items()})
        except (shopify_api.ShopifyError, ValueError) as e:
            self._json(502, {"error": str(e)})
            return
        if changed:
            actor = (self.headers.get("X-User-Email") or "?").strip().lower()
            hub_event("product_update", f"{p.get('title', pid.split('/')[-1])}: "
                      + ", ".join(f"{k}={v}" for k, v in changed.items()), actor, app="shopify")
        self._json(200, {"ok": True, "changed": changed})

    # --- Tool access: who may use what -------------------------------------
    def _handle_access_me(self, query=""):
        """Any signed-in user: their own role + allowed tools. Drives nav/tile
        filtering, so a page never offers a tool the person can't open."""
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        import access_store
        params = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
        self._json(200, access_store.presentation_for(email, params.get("view_as")))

    def _handle_access_list(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        import access_store
        self._json(200, {"people": access_store.everyone(),
                         "roles": [{"key": k, "label": v["label"], "blurb": v["blurb"],
                                    "home": v["home"]} for k, v in access_store.ROLES.items()]})

    def _handle_access_set(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import access_store
        email, role = str(p.get("email", "")).strip().lower(), str(p.get("role", "")).strip()
        if not email or role not in access_store.ROLES or (
                role == "admin" and email not in ADMIN_EMAILS):
            self._json(400, {
                "error": "need an email and a valid non-admin role"}); return
        try:
            access_store.set_role(email, role)
        except ValueError as e:
            self._json(400, {"error": str(e)}); return
        except (RuntimeError, OSError) as e:
            self._json(500, {"error": f"access gate was not changed: {e}"}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("access_change", f"{email} -> {role}", actor, app="key")
        self._json(200, {"ok": True, "email": email, "role": role,
                         "gate": "reloaded", "gate_ok": True})

    def _handle_access_invite(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import access_store
        email = str(p.get("email", "")).strip().lower()
        role = str(p.get("role", "")).strip()
        if not EMAIL_RE.match(email) or role not in access_store.ROLES or role == "admin":
            self._json(400, {"error": "need a valid email and non-admin role"}); return
        try:
            _, note, existed = access_store.invite(email, role)
        except ValueError as e:
            self._json(400, {"error": str(e)}); return
        except (RuntimeError, OSError) as e:
            self._json(500, {"error": f"invite was rolled back: {e}"}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("member_update" if existed else "member_add",
                  f"{email} -> {role}", actor, app="key")
        self._json(200, {
            "ok": True, "email": email, "role": role, "gate": note,
            "gate_ok": True, "existing": existed,
        })

    # --- System files browser (admin, read-only, confined to FS_ROOT) --------
    def _handle_fs_list(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        params = dict(parse_qsl(query))
        show_hidden = params.get("hidden") == "1"
        d = _fs_resolve(params.get("path") or FS_ROOT)
        if d is None or not os.path.isdir(d):
            self._json(404, {"error": "no such folder"}); return
        entries = []
        try:
            with os.scandir(d) as it:
                for e in it:
                    hidden = e.name.startswith(".")
                    if hidden and not show_hidden:
                        continue
                    if _fs_resolve(e.path) is None:
                        continue
                    try:
                        st = e.stat(follow_symlinks=False)
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    entries.append({
                        "name": e.name, "path": os.path.join(d, e.name),
                        "is_dir": is_dir, "size": None if is_dir else st.st_size,
                        "mtime": int(st.st_mtime), "hidden": hidden,
                        "link": e.is_symlink(),
                        "protected": (not is_dir) and _fs_protected(os.path.join(d, e.name)),
                    })
        except OSError as ex:
            self._json(500, {"error": str(ex)}); return
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        self._json(200, {"path": d, "root": FS_ROOT,
                         "parent": (os.path.dirname(d) if d != FS_ROOT else None),
                         "entries": entries})

    def _handle_fs_read(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        path = _fs_resolve(dict(parse_qsl(query)).get("path", ""))
        if path is None or not os.path.isfile(path):
            self._json(404, {"error": "no such file"}); return
        if _fs_protected(path):
            self._json(200, {"protected": True, "name": os.path.basename(path),
                             "reason": "This file holds credentials, so it's withheld from the "
                                       "web browser. View it over SSH if you truly need it."}); return
        size = os.path.getsize(path)
        if size > 512 * 1024:
            self._json(200, {"too_large": True, "size": size, "name": os.path.basename(path)}); return
        try:
            data = open(path, "rb").read()
        except OSError as ex:
            self._json(500, {"error": str(ex)}); return
        try:
            self._json(200, {"content": data.decode("utf-8"), "size": size,
                             "name": os.path.basename(path)})
        except UnicodeDecodeError:
            self._json(200, {"binary": True, "size": size, "name": os.path.basename(path)})

    def _handle_fs_download(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        path = _fs_resolve(dict(parse_qsl(query)).get("path", ""))
        if path is None or not os.path.isfile(path):
            self._json(404, {"error": "no such file"}); return
        if _fs_protected(path):
            self._json(403, {"error": "this file is protected (credentials)"}); return
        size = os.path.getsize(path)
        name = os.path.basename(path).replace('"', "")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

    def _handle_shopify_theme_settings(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected."}); return
        try:
            theme, _, data = shopify_api.get_settings_data()
            flat = shopify_api.flatten_theme_settings(data["current"])
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        n_backups = len([f for f in os.listdir(THEME_BACKUPS) if f.startswith(theme["id"] + "-")])
        self._json(200, {"theme": theme["name"], "count": len(flat),
                         "settings": flat, "can_revert": n_backups > 0})

    def _handle_shopify_theme_suggest(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        request = str(p.get("request", "")).strip()
        if not request:
            self._json(400, {"error": "describe the change you want"}); return
        import shopify_api
        try:
            _, _, data = shopify_api.get_settings_data()
            flat = shopify_api.flatten_theme_settings(data["current"])
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "The agent is busy with another task — try again in a moment."}); return
        patch = None
        try:
            prompt = theme_prompt(request, flat)
            for model, provider in (("claude-sonnet-4-6", "anthropic"), (None, None)):
                try:
                    reply = run_hermes(prompt, model, provider)
                except Exception:
                    continue
                patch = _extract_json_obj(reply)
                if isinstance(patch, dict):
                    break
        finally:
            LOCK.release()
        if not isinstance(patch, dict):
            self._json(502, {"error": "Couldn't turn that into settings — try being more specific, "
                                      "or the model may be busy. Give it another go."}); return
        _, applied, skipped = shopify_api.apply_theme_patch(data["current"], patch)
        changes = [{"key": k, "old": v["old"], "new": v["new"]} for k, v in applied.items()]
        self._json(200, {"changes": changes, "unmatched": len(skipped)})

    def _handle_shopify_theme_apply(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        patch = p.get("patch")
        if not isinstance(patch, dict) or not patch:
            self._json(400, {"error": "no changes to apply"}); return
        import shopify_api
        try:
            with THEME_LOCK:
                theme, raw, data = shopify_api.get_settings_data()
                new_current, applied, skipped = shopify_api.apply_theme_patch(data["current"], patch)
                if not applied:
                    self._json(200, {"ok": True, "applied": 0, "skipped": len(skipped)}); return
                # token_hex suffix, not just the second-resolution timestamp:
                # two applies landing in the same second used to collide and
                # silently clobber each other's backup file.
                ts = int(time.time())
                fname = f"{theme['id']}-{ts}-{secrets.token_hex(4)}.json"
                with open(os.path.join(THEME_BACKUPS, fname), "w") as f:
                    f.write(raw)  # full snapshot for one-click revert
                data["current"] = new_current
                shopify_api.put_settings_data(theme["id"], json.dumps(data, ensure_ascii=False))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("theme_update", f"{len(applied)} setting"
                  + ("s" if len(applied) != 1 else "") + f" on {theme['name']}", actor, app="shopify")
        self._json(200, {"ok": True, "applied": len(applied), "skipped": len(skipped)})

    def _handle_shopify_theme_revert(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        import shopify_api
        try:
            theme = shopify_api.theme_main()
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        try:
            with THEME_LOCK:
                backups = sorted(f for f in os.listdir(THEME_BACKUPS)
                                 if f.startswith(theme["id"] + "-"))
                if not backups:
                    self._json(404, {"error": "nothing to revert"}); return
                newest = os.path.join(THEME_BACKUPS, backups[-1])
                with open(newest) as f:
                    raw = f.read()
                shopify_api.put_settings_data(theme["id"], raw)
                os.remove(newest)   # consume the undo step
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("theme_revert", f"reverted last change on {theme['name']}", actor, app="shopify")
        self._json(200, {"ok": True, "remaining": len(backups) - 1})

    # --- Blog: write posts straight into the store ---------------------------
    def _handle_shopify_blogs(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected."}); return
        try:
            self._json(200, {"blogs": shopify_api.list_blogs()})
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_articles(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        blog = dict(parse_qsl(query)).get("blog") or None
        import shopify_api
        try:
            self._json(200, {"articles": shopify_api.list_articles(blog)})
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_article_detail(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        aid = dict(parse_qsl(query)).get("id")
        import shopify_api
        if not aid:
            self._json(400, {"error": "missing article"}); return
        try:
            self._json(200, shopify_api.get_article(aid))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_article_save(self):
        """Create or update a post. `id` present = update, absent = create."""
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 1048576)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected."}); return
        tags = p.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        common = dict(title=p.get("title"), body=p.get("body"),
                      summary=p.get("summary"), tags=tags or [],
                      author=p.get("author", ""), published=bool(p.get("published")),
                      image_url=p.get("image", ""))
        try:
            if p.get("id"):
                art = shopify_api.update_article(p["id"], **common)
                action = "updated"
            else:
                art = shopify_api.create_article(p.get("blog_id"), **common)
                action = "created"
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        state = "published" if common["published"] else "draft"
        hub_event("blog_post", f"{action} {state}: {str(p.get('title',''))[:60]}", actor, app="shopify")
        self._json(200, {"ok": True, "action": action, "article": art})

    def _handle_shopify_pages(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected — open Tools and connect it."}); return
        try:
            self._json(200, {"pages": shopify_api.list_pages()})
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_page_detail(self, query):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        from urllib.parse import parse_qsl
        pid = dict(parse_qsl(query)).get("id")
        import shopify_api
        if not pid:
            self._json(400, {"error": "missing page"}); return
        try:
            self._json(200, shopify_api.get_page(pid))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})

    def _handle_shopify_page_update(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 262144)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import shopify_api
        pid = p.get("id")
        if not pid:
            self._json(400, {"error": "missing page"}); return
        try:
            shopify_api.update_page(pid, title=p.get("title"), body=p.get("body"))
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("page_update", f"edited page {p.get('title', pid.split('/')[-1])}", actor, app="shopify")
        self._json(200, {"ok": True})

    def _handle_shopify_bulk_content(self):
        actor = (self.headers.get("X-User-Email") or "").strip().lower()
        if actor not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 262144)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected — open Tools and connect it."}); return
        ids = list(dict.fromkeys(i for i in (p.get("ids") or []) if i))[:100]
        op = p.get("op")
        if op not in ("append_block", "prepend_block", "replace"):
            self._json(400, {"error": "unknown operation"}); return
        if op == "replace" and not str(p.get("find", "")).strip():
            self._json(400, {"error": "give the text to find"}); return
        if op in ("append_block", "prepend_block") and not str(p.get("block", "")).strip():
            self._json(400, {"error": "the block is empty"}); return
        if not ids:
            self._json(400, {"error": "select at least one product"}); return
        proposal = {
            "ids": ids, "op": op, "block": p.get("block", ""),
            "name": p.get("name", ""), "find": p.get("find", ""),
            "replace": p.get("replace", ""),
        }
        canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
        dry = bool(p.get("dry_run"))
        results, sample = [], None

        if dry:
            frozen = {}
            for pid in ids:
                try:
                    d = shopify_api.get_detail(pid)
                    before = d["description"]
                    after = apply_content_op(
                        before, op, block=proposal["block"], name=proposal["name"],
                        find=proposal["find"], replace=proposal["replace"])
                    changed = after != before
                    frozen[pid] = {
                        "title": d["title"], "before": before, "after": after,
                        "changed": changed,
                    }
                    results.append({
                        "id": pid, "title": d["title"], "changed": changed,
                        "delta": len(after) - len(before),
                    })
                    if changed and sample is None:
                        sample = {
                            "title": d["title"], "before": before[:1400],
                            "after": after[:1400],
                        }
                except shopify_api.ShopifyError as e:
                    results.append({
                        "id": pid, "title": pid.split("/")[-1], "error": str(e)})
            n_changed = sum(1 for r in results if r.get("changed"))
            token = secrets.token_urlsafe(24)
            now = time.time()
            with CONTENT_PREVIEW_LOCK:
                for old_token, record in list(CONTENT_PREVIEWS.items()):
                    if record["expires"] <= now:
                        CONTENT_PREVIEWS.pop(old_token, None)
                CONTENT_PREVIEWS[token] = {
                    "actor": actor, "expires": now + CONTENT_PREVIEW_TTL,
                    "canonical": canonical, "frozen": frozen,
                }
            self._json(200, {
                "results": results, "changed": n_changed, "total": len(results),
                "dry_run": True, "sample": sample, "preview_token": token,
            })
            return

        token = str(p.get("preview_token") or "")
        with CONTENT_PREVIEW_LOCK:
            record = CONTENT_PREVIEWS.pop(token, None)
        if (not record or record["expires"] <= time.time()
                or record["actor"] != actor or record["canonical"] != canonical):
            self._json(409, {
                "error": "That preview is missing, expired, or no longer matches. "
                         "Preview the exact change again before applying it."})
            return

        # Validate every source description before the first write. If even one
        # changed since preview, nothing from this proposal is applied.
        current = {}
        try:
            for pid in ids:
                d = shopify_api.get_detail(pid)
                current[pid] = d
                expected = record["frozen"].get(pid)
                if not expected or d["description"] != expected["before"]:
                    self._json(409, {
                        "error": "At least one product changed after the preview. "
                                 "Nothing was applied; preview again."})
                    return
        except shopify_api.ShopifyError as e:
            self._json(502, {
                "error": f"Could not recheck every product, so nothing was applied: {e}"})
            return

        for pid in ids:
            expected = record["frozen"][pid]
            changed = expected["changed"]
            try:
                if changed:
                    shopify_api.update_fields(
                        pid, {"descriptionHtml": expected["after"]})
                results.append({
                    "id": pid, "title": expected["title"], "changed": changed,
                    "delta": len(expected["after"]) - len(expected["before"]),
                })
            except shopify_api.ShopifyError as e:
                results.append({
                    "id": pid, "title": expected["title"], "error": str(e)})
        n_changed = sum(1 for r in results if r.get("changed") and not r.get("error"))
        if n_changed:
            label = {"append_block": "appended a block to", "prepend_block": "prepended a block to",
                     "replace": "find/replaced in"}[op]
            hub_event("bulk_content", f"{label} {n_changed} product"
                      + ("s" if n_changed != 1 else ""), actor, app="shopify")
        self._json(200, {"results": results, "changed": n_changed,
                         "total": len(results), "dry_run": False, "sample": None})

    def _handle_shopify_ai_draft(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        images = [u for u in (p.get("images") or []) if str(u).strip()]
        hint = str(p.get("hint", "")).strip()[:500]
        if not images and not hint:
            self._json(400, {"error": "add a photo, or type a rough idea to draft from"}); return
        saved = _download_images(images)
        if images and not saved:
            self._json(502, {"error": "couldn't read those photos — try different images"}); return
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "The agent is busy — try again in a moment."}); return
        draft = None
        try:
            prompt = ai_product_prompt(saved, hint)
            for model, provider in (("claude-sonnet-4-6", "anthropic"), (None, None)):
                try:
                    reply = run_hermes(prompt, model, provider)
                except Exception:
                    continue
                draft = _extract_json_obj(reply)
                if isinstance(draft, dict):
                    break
        finally:
            LOCK.release()
            for path, _ in saved:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if not isinstance(draft, dict):
            self._json(502, {"error": "AI couldn't draft this one — try again, or add a hint."}); return
        tags = draft.get("tags")
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()][:20]
        else:
            tags = [t.strip() for t in str(tags or "").split(",") if t.strip()]
        price = draft.get("price", draft.get("price_suggestion", ""))
        self._json(200, {
            "title": str(draft.get("title", "")).strip()[:255],
            "type": str(draft.get("type", "")).strip()[:100],
            "tags": tags,
            "description": str(draft.get("description", "")),
            "price": re.sub(r"[^0-9.]", "", str(price)) if price not in (None, "") else "",
        })

    def _handle_shopify_product_create(self):
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected — open Tools and connect it."})
            return
        title = str(p.get("title", "")).strip()
        if not title:
            self._json(400, {"error": "give the product a title"})
            return
        tags = p.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        images = [u for u in (p.get("images") or []) if str(u).strip()]
        try:
            out = shopify_api.create_product(
                title=title,
                description=p.get("description", ""),
                product_type=p.get("type", ""),
                tags=tags or [],
                status=p.get("status", "DRAFT"),
                vendor=p.get("vendor", ""),
                price=p.get("price"),
                image_urls=images,
            )
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)})
            return
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("product_create",
                  f"{title} ({out['status'].lower()}, {len(images)} image"
                  + ("s" if len(images) != 1 else "") + ")", actor, app="shopify")
        self._json(200, {"ok": True, **out})

    # ------------------------------------------------------------- order form
    def _order_user(self):
        """The caller's email, or '' if SSO didn't populate the header. Only
        an identity — pair with _has_tool() wherever the endpoint returns
        anything a restricted role (intake, supplier) shouldn't see."""
        return (self.headers.get("X-User-Email") or "").strip().lower()

    def _has_tool(self, tool_key):
        """Role-gate an API call directly, rather than trusting that nginx's
        page-level redirect was the only door to it. It wasn't: nginx only
        gates loading /ops/*.html — /ops/agent/api/ has its own location
        block that just checks 'signed in', so before this, an intake or
        supplier account could call any endpoint here by URL alone, PII
        included, without ever touching a page it's blocked from. Every
        signed-in person used to be a fully-trusted admin in practice, which
        is the only reason that never mattered until a genuinely restricted
        outside role (supplier) existed."""
        email = self._order_user()
        if not email:
            return False
        import access_store
        return access_store.can_use(email, tool_key)

    def _handle_order_create(self):
        actor = self._order_user()
        if not (self._has_tool("orders") or self._has_tool("intake")):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        customer = str(p.get("customer_name", "")).strip()[:120]
        product = str(p.get("product", "")).strip()[:200]
        # Customer is optional: a build can be queued to the supplier before
        # it's sold (that's what bulk intake creates), and the buyer gets
        # attached later. Product is the one thing an order can't lack.
        if not product:
            self._json(400, {"error": "a product is needed"})
            return
        # "For stock" can be said explicitly, or inferred from the convention
        # already in the data, where the customer was typed as Self.
        raw_stock = p.get("is_stock")
        if (raw_stock is not None
                and (not isinstance(raw_stock, (bool, int))
                     or raw_stock not in (0, 1, False, True))):
            self._json(400, {"error": "stock must be on or off"})
            return
        phone = str(p.get("customer_phone", "")).strip()[:40]
        email = str(p.get("customer_email", "")).strip()[:200]
        # A buyer-less build is inventory by definition. Treating an omitted
        # checkbox as a sale creates phantom zero-value orders in every KPI.
        is_stock = 1 if (
            bool(raw_stock)
            or customer.strip().lower() in ("self", "stock")
            or not (customer or phone or email)
        ) else 0
        address = str(p.get("address", "")).strip()[:600]
        pincode = str(p.get("pincode", "")).strip()[:20]
        notes = str(p.get("notes", "")).strip()[:1000]
        import order_stages
        status = str(p.get("status", "")).strip()[:40] or order_stages.DEFAULT_STAGE
        if status not in order_stages.STATUSES:
            self._json(400, {"error": "unrecognised order status"})
            return
        if status != order_stages.DEFAULT_STAGE:
            self._json(400, {"error": "new orders start Pending; move it after saving"})
            return
        source = str(p.get("source", "")).strip()[:40]

        # City/state and product attributes come from the browser (where staff
        # can correct them); recompute anything missing so an API caller or an
        # untouched field still lands structured.
        import india_places
        import order_taxonomy
        city = str(p.get("city", "")).strip()[:80]
        state = str(p.get("state", "")).strip()[:80]
        if address and not (city and state):
            gc, gs = india_places.parse(address, pincode)
            city = city or (gc or "")
            state = state or (gs or "")
        attrs = {k: str(p.get(k, "")).strip()[:60] for k in order_taxonomy.FIELDS}
        if not any(attrs.values()):
            attrs.update(order_taxonomy.extract(product + " " + notes))
        try:
            qty = int(p.get("quantity") or 1)
        except (TypeError, ValueError):
            self._json(400, {"error": "quantity must be a whole number"})
            return
        if qty < 1 or qty > 1000:
            self._json(400, {"error": "quantity must be between 1 and 1000"})
            return
        price = None
        if str(p.get("price_inr", "")).strip():
            try:
                price = float(p.get("price_inr"))
            except (TypeError, ValueError):
                self._json(400, {"error": "price must be a number"})
                return
            if not math.isfinite(price) or price < 0 or price > 100000000:
                self._json(400, {"error": "price is outside the allowed range"})
                return

        # Photos are whatever /upload just wrote — confine each to UPLOAD_DIR so a
        # crafted path can't push an arbitrary server file to Drive.
        raw_photos = p.get("photo_paths") or ([p["photo_path"]] if p.get("photo_path") else [])
        photos = []
        for cand in raw_photos[:10]:
            rp = _upload_owned(cand, actor)
            if not rp:
                self._json(400, {"error": "a photo upload expired — attach it again"})
                return
            photos.append(rp)

        import google_api
        access = google_api.access_token()
        warnings = []
        links = []
        if photos and access:
            for i, fp in enumerate(photos):
                try:
                    links.append(google_api.upload_photo(
                        access, f"order-{int(time.time())}-{i+1}-{os.path.basename(fp)}", fp))
                except Exception as e:
                    warnings.append(f"A photo didn't reach Drive: {e}")
        drive_link = links[0] if links else None

        conn = db()
        stored, created_photo_paths = [], []
        dest_dir = ""
        try:
            # Serialize the display-number allocation with the insert. Merely
            # doing MAX()+1 on two ThreadingHTTPServer connections can hand two
            # simultaneous orders the same number.
            conn.execute("BEGIN IMMEDIATE")
            import order_numbers
            order_no = order_numbers.next_number(conn)
            cur = conn.execute(
                "INSERT INTO orders (customer_name, customer_phone, customer_email, address, "
                "pincode, city, state, source, product, price_inr, quantity, notes, status, "
                "has_image, drive_link, photo_links, case_style, dial_colour, dial_style, "
                "case_colour, movement, watch_size, is_stock, order_no, supplier_visible) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (customer, phone, email, address, pincode, city, state, source, product,
                 price, qty, notes, status, 1 if photos else 0, drive_link,
                 json.dumps(links) if links else None,
                 attrs.get("case_style"), attrs.get("dial_colour"), attrs.get("dial_style"),
                 attrs.get("case_colour"), attrs.get("movement"), attrs.get("watch_size"),
                 is_stock, order_no))
            oid = cur.lastrowid

            # Copy each upload first, but keep its source until the database is
            # committed. A failed order remains retryable and cannot leave a
            # private photo directory with no matching record.
            if photos:
                dest_dir = os.path.join(ORDER_PHOTOS, str(oid))
                os.makedirs(dest_dir, exist_ok=True)
                for i, fp in enumerate(photos):
                    fn = f"{i + 1}{os.path.splitext(fp)[1].lower()}"
                    dest_path = os.path.join(dest_dir, fn)
                    with open(fp, "rb") as src, open(dest_path, "xb") as dst:
                        created_photo_paths.append(dest_path)
                        shutil.copyfileobj(src, dst)
                    stored.append(fn)
                conn.execute("UPDATE orders SET local_photos=? WHERE id=?",
                             (json.dumps(stored), oid))

            conn.execute(
                "INSERT INTO order_items (order_id, product, quantity, price_inr, line_total) "
                "VALUES (?,?,?,?,?)",
                (oid, product, qty, price, (price or 0) * qty))
            order_event(conn, oid, "created",
                        f"logged via {source or 'order form'} with status {status}", actor)
            row = conn.execute(
                "SELECT received_at FROM orders WHERE id=?", (oid,)).fetchone()
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            for path in created_photo_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if dest_dir:
                try:
                    os.rmdir(dest_dir)
                except OSError:
                    pass
            print(f"[orders-create] atomic save failed: {e}", flush=True)
            self._json(500, {
                "error": "The order was not saved. Its uploads are still available; "
                         "try Save again."})
            return

        for fp in photos:
            # The committed order owns its permanent copy.
            _forget_upload(fp, remove_data=True)
        if not access:
            warnings.append(
                "Google isn't connected, so this order didn't reach the sheet"
                + (" (the photos are saved here)" if stored else "")
                + ". Open Drop and reconnect Google.")
        logged = row["received_at"] if row else ""

        # Customer roll-up recomputes from orders, so it stays correct even if
        # an order is later edited or removed.
        import customers as customers_mod
        cust = None
        # An unsold build has nobody to roll up. Without this guard every
        # customer-less order would collide on the same empty key and appear
        # as one phantom buyer accumulating all of their spend.
        if customer or phone or email:
            try:
                cust = customers_mod.upsert_from_order(conn, {
                    "customer_name": customer, "customer_phone": phone,
                    "customer_email": email, "address": address, "pincode": pincode,
                    "city": city, "state": state, "source": source})
            except Exception as e:
                warnings.append(f"Customer list not updated: {e}")
        conn.close()

        # The DB is the source of truth; a failed mirror must never lose an order.
        try:
            if access:
                line_total = (price or 0) * qty
                google_api.append_order_row(access, google_api.row_for(google_api.SHEET_HEADERS, {
                    "Order #": order_no, "Logged": logged,
                    "Status": order_stages.label(status), "Source": source,
                    "Customer": customer, "Phone": phone, "Email": email, "Address": address,
                    "City": city, "State": state, "Pincode": pincode, "Product": product,
                    "Case style": attrs.get("case_style"), "Dial colour": attrs.get("dial_colour"),
                    "Dial style": attrs.get("dial_style"), "Case colour": attrs.get("case_colour"),
                    "Movement": attrs.get("movement"), "Size": attrs.get("watch_size"),
                    "Qty": qty, "Price (INR)": "" if price is None else price,
                    "Line total (INR)": "" if price is None else line_total,
                    "Notes": notes, "Photos": "\n".join(links)}))
                if cust:
                    google_api.upsert_customer_row(
                        access, customers_mod.HEADERS, customers_mod.sheet_row(cust))
        except Exception as e:
            warnings.append(f"Sheet not updated: {e}")

        hub_event("order_created", f"#{order_no} {product} ×{qty} for {customer}",
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "order_no": order_no, "drive_link": drive_link,
                         "attributes": {k: v for k, v in attrs.items() if v},
                         "customer": {"orders": cust["orders_count"],
                                      "tags": customers_mod.all_tags(cust)} if cust else None,
                         "warnings": warnings})

    def _handle_orders_list(self):
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        can_manage_payments = bool(self._admin_email())
        conn = db()
        try:
            rows = conn.execute(
                "SELECT id, order_no, received_at, customer_name, customer_phone, "
                "customer_email, address, pincode, city, state, source, product, quantity, "
                "price_inr, notes, status, drive_link, photo_links, local_photos, case_style, "
                "dial_colour, dial_style, case_colour, movement, watch_size, shopify_name, "
                "ref_code, is_stock, financial_status, supplier_visible, shipment_id, bill_id, "
                "shopify_order_id, sale_total_paise "
                "FROM orders WHERE COALESCE(local_hidden,0)=0 "
                "ORDER BY id DESC LIMIT 100").fetchall()
            receipts = {}
            receipt_totals = {}
            if rows:
                marks = ",".join("?" for _ in rows)
                for receipt in conn.execute(
                        "SELECT order_id, kind, amount_paise, account_code "
                        f"FROM order_customer_receipts WHERE order_id IN ({marks})",
                        tuple(r["id"] for r in rows)):
                    receipt_totals[receipt["order_id"]] = (
                        receipt_totals.get(receipt["order_id"], 0)
                        + receipt["amount_paise"])
                    if can_manage_payments:
                        receipts.setdefault(receipt["order_id"], {})[receipt["kind"]] = {
                            "amount_paise": receipt["amount_paise"],
                            "account": receipt["account_code"],
                        }
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        import google_api
        import order_stages
        out = []
        for row in rows:
            order = dict(row)
            try:
                photo_names = json.loads(order.get("local_photos") or "[]")
            except (json.JSONDecodeError, TypeError):
                photo_names = []
            if not isinstance(photo_names, list):
                photo_names = []
            order["photo_rev"] = _order_photo_revision(photo_names)
            order["customer_payment_recorded"] = bool(
                receipt_totals.get(order["id"], 0))
            if can_manage_payments:
                payment_rows = receipts.get(order["id"], {})
                order["customer_payments"] = payment_rows
                order["customer_payment_rev"] = _customer_payment_revision(
                    payment_rows, order.get("price_inr"), order.get("quantity") or 1,
                    order.get("sale_total_paise"))
            out.append(order)
        self._json(200, {"orders": out,
                         "can_manage_payments": can_manage_payments,
                         "labels": order_stages.LABELS,
                         "sheet_url": google_api.sheet_url()})

    def _handle_orders_parse(self):
        """Live helper for the form: address → city/state, product → attributes.
        Keeps the vocabulary server-side so there's one implementation of it.
        Pure computation on whatever the caller sends — no existing record is
        read — so intake gets this too, same as /orders/create."""
        if not (self._has_tool("orders") or self._has_tool("intake")):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        import india_places
        import order_taxonomy
        city, state = india_places.parse(str(p.get("address", ""))[:600],
                                         str(p.get("pincode", ""))[:20])
        attrs = order_taxonomy.extract(
            (str(p.get("product", "")) + " " + str(p.get("notes", "")))[:600])
        self._json(200, {"city": city or "", "state": state or "", "attributes": attrs,
                         "labels": order_taxonomy.FIELD_LABELS})

    # order_items.line_total inherits the parent order's exclusions (cancelled,
    # refunded, voided) via this join — every analytics query below reuses it
    # so "what sells" and "what we banked" can never quietly disagree about
    # which orders count.
    _LIVE_ITEMS_JOIN = (
        "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        + ORDER_ITEM_TOTALS_JOIN
        + f"WHERE {SALE_ORDER_PREDICATE_O}")

    def _handle_orders_meta(self):
        """Sources, the what's-selling roll-up, and per-product analytics —
        all local SQL now that Shopify orders sync into order_items instead
        of being re-fetched live on every page load. Same tables `labs sales`
        and Hermes query directly, so the dashboard, the form and chat can
        never show three different numbers for the same question."""
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        import order_taxonomy
        conn = db()
        try:
            used = [r[0] for r in conn.execute(
                "SELECT DISTINCT source FROM orders WHERE source IS NOT NULL "
                "AND source != '' ORDER BY source")]
            sold = {}
            for field in ("case_style", "dial_colour", "dial_style", "movement"):
                sold[field] = [dict(r) for r in conn.execute(
                    f"SELECT {field} AS name, COUNT(*) AS orders, "
                    "SUM(COALESCE(quantity,1)) AS units FROM orders "
                    f"WHERE {field} IS NOT NULL AND {field} != '' "
                    f"AND {SALE_ORDER_PREDICATE} "
                    f"GROUP BY {field} ORDER BY units DESC LIMIT 8")]
            # Stock builds are counted separately, not folded into "orders".
            # A watch we made for ourselves is not a sale, and letting it sit
            # in the same number quietly inflates how the week looks.
            totals = conn.execute(
                "SELECT COUNT(*) n, "
                "COALESCE(SUM(COALESCE(sale_total_paise/100.0, "
                "COALESCE(price_inr,0)*COALESCE(quantity,1))),0) revenue, "
                "SUM(CASE WHEN source='website' THEN 1 ELSE 0 END) website "
                f"FROM orders WHERE {SALE_ORDER_PREDICATE}").fetchone()
            stock = conn.execute(
                "SELECT COUNT(*) n FROM orders WHERE COALESCE(is_stock,0)=1 "
                "AND status!='cancelled' AND (financial_status IS NULL OR "
                "LOWER(financial_status) NOT IN ('refunded','voided')) "
                "AND COALESCE(local_hidden,0)=0").fetchone()["n"]
            ncust = conn.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
            top_products = [dict(r) for r in conn.execute(
                "SELECT COALESCE(NULLIF(oi.canonical_product,''), oi.product) AS name, "
                "SUM(oi.quantity) AS units, COUNT(DISTINCT oi.order_id) AS orders, "
                f"ROUND(SUM({ALLOCATED_ITEM_REVENUE_SQL}), 2) AS revenue, "
                "MAX(o.received_at) AS last_sold "
                + self._LIVE_ITEMS_JOIN +
                " GROUP BY 1 ORDER BY units DESC LIMIT 30")]
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        defaults = ["CC", "TLC", "Offkicks"]
        sources = defaults + [s for s in used if s not in defaults]
        website = totals["website"] or 0
        self._json(200, {"sources": sources, "selling": sold,
                         "vocab": order_taxonomy.vocab(), "top_products": top_products,
                         "channels": {"logged": totals["n"] - website, "website": website},
                         "totals": {"orders": totals["n"] or 0,
                                    "stock": stock or 0,
                                    "revenue": totals["revenue"] or 0,
                                    "customers": ncust}})

    def _handle_orders_update(self):
        """Patch an existing order.

        Details shown in the order list stay editable until the customer
        payment state is exactly ``paid``. Workflow fields remain writable
        after payment because fulfilment and courier references still have to
        move forward. The server enforces the paid lock; the browser's
        hidden Edit button is only the first line of defence.
        """
        actor = self._order_user()
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        # Always editable: moving fulfilment along and correcting a courier
        # reference are things you do *after* payment as often as before it.
        always = {"status": 40, "tracking_code": 80}
        # Everything a person edits from the Recent Orders list. Once the
        # customer payment state is "paid", these values are the receipt and
        # must not silently shift underneath it.
        lockable = {"customer_name": 120, "customer_phone": 40, "customer_email": 200,
                    "address": 600, "city": 80, "state": 80, "pincode": 20,
                    "product": 200, "case_style": 60, "dial_colour": 60,
                    "dial_style": 60, "case_colour": 60, "movement": 60,
                    "watch_size": 60, "source": 40, "notes": 1000,
                    "quantity": None, "price_inr": None, "is_stock": None}
        conn = db()
        # Lock before reading payment/build state. A supplier action that
        # commits between validation and this write must never let a stale edit
        # change a newly committed build.
        conn.execute("BEGIN IMMEDIATE")
        before = conn.execute(
            "SELECT order_no, status, financial_status, supplier_visible, shipment_id, "
            "bill_id, customer_name, customer_phone, customer_email, address, city, "
            "state, pincode, product, quantity, price_inr, source, is_stock, notes, "
            "case_style, dial_colour, dial_style, case_colour, movement, watch_size, "
            "tracking_code, shopify_order_id, sale_total_paise "
            "FROM orders WHERE id=?", (oid,)).fetchone()
        if not before:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        from order_form import STATUSES
        import order_stages
        cur_status = before["status"] or order_stages.DEFAULT_STAGE
        # financial_status='paid' is Shopify's own checkout signal, not a fact
        # about our build — a storefront order is typically "paid" within
        # seconds of being placed, long before it's even been looked at, let
        # alone sent to the supplier. Applying the same receipt-freeze rule
        # to it as to a manually-logged order (where "paid" IS a meaningful
        # late milestone) locked every real Shopify order from birth. Money
        # fields don't need this protection either way: price_inr and
        # financial_status are already re-synced from Shopify on every pass
        # regardless of commitment, so a local edit to them would be
        # overwritten on the next sync whether or not this lock exists.
        # Website orders are governed purely by build_locked instead, same as
        # everything else about them.
        is_website = bool(before["shopify_order_id"])
        paid_locked = (not is_website
                      and (before["financial_status"] or "").strip().lower() == "paid")
        build_locked = order_stages.is_committed(
            cur_status, before["supplier_visible"], before["shipment_id"], before["bill_id"])
        has_customer_receipt = bool(conn.execute(
            "SELECT 1 FROM order_customer_receipts WHERE order_id=? LIMIT 1",
            (oid,)).fetchone())
        receipt_locked = has_customer_receipt and not self._admin_email()
        build_fields = {
            "product", "case_style", "dial_colour", "dial_style", "case_colour",
            "movement", "watch_size", "quantity", "is_stock",
        }
        receipt_fields = set(lockable) - {"notes"}
        blocked_fields = set(lockable) if paid_locked else set()
        if not paid_locked and build_locked:
            blocked_fields.update(build_fields)
        if not paid_locked and receipt_locked:
            blocked_fields.update(receipt_fields)
        blocked = [k for k in blocked_fields if k in p]
        if blocked:
            conn.close()
            self._json(409, {
                "error": (
                    "This order is marked Paid, so its receipt details are locked."
                    if paid_locked else
                    "This order has a customer payment recorded. An admin must correct "
                    "its receipt details; notes and tracking remain editable."
                    if receipt_locked and any(k in receipt_fields for k in blocked) else
                    "This build is already ordered, shipped, or billed, so its build "
                    "specification and quantity are locked. Customer/contact details "
                    "and tracking can still be corrected."
                ),
                "locked_fields": blocked})
            return
        if "status" in p and p["status"] not in STATUSES:
            conn.close()
            self._json(400, {"error": f"'{p['status']}' isn't a real status"})
            return
        # Staff can force a stage past the normal forward-only rule (the
        # supplier fat-fingered a tap, or a batch needs correcting) but never
        # past a shipment or bill — those are financial/logistics facts a
        # stage reset must not silently contradict. Bypasses
        # staff_transition_allowed only; every other lock in this handler
        # (paid, build spec once committed, multi-item Shopify orders) is
        # untouched by this flag.
        force_reset = False
        status_committed = bool(before["supplier_visible"]) or build_locked
        if ("status" in p and p["status"] != cur_status
                and not order_stages.staff_transition_allowed(
                    cur_status, p["status"], status_committed)):
            hard_linked = before["shipment_id"] is not None or before["bill_id"] is not None
            if not p.get("force_status"):
                conn.close()
                self._json(409, {
                    "error": "A committed supplier build cannot be cancelled or moved "
                             "backwards. Correct the supplier, shipment, or bill linkage "
                             "first.",
                    "resettable": status_committed and not hard_linked})
                return
            if hard_linked:
                conn.close()
                self._json(409, {
                    "error": "This build has a shipment or bill attached, so its stage "
                             "can't be force-reset — remove that link first.",
                    "resettable": False})
                return
            force_reset = True
        supplier_change = None
        if "supplier_visible" in p:
            raw = p["supplier_visible"]
            if not isinstance(raw, (bool, int)) or raw not in (0, 1, False, True):
                conn.close()
                self._json(400, {"error": "supplier selection must be on or off"})
                return
            supplier_change = 1 if raw else 0
            if supplier_change and cur_status == "cancelled":
                conn.close()
                self._json(409, {"error": "A cancelled order can't be sent to the supplier"})
                return
            if (not supplier_change and before["supplier_visible"]
                    and (cur_status != order_stages.DEFAULT_STAGE
                         or before["shipment_id"] is not None
                         or before["bill_id"] is not None)):
                conn.close()
                self._json(409, {
                    "error": "This build is already in progress, shipped, or billed, "
                             "so it can't be removed from the supplier queue."})
                return
        item_fields = {"product", "quantity", "price_inr"}
        contact_fields = {
            "customer_name", "customer_phone", "customer_email", "address",
            "city", "state", "pincode",
        }
        updatable = dict(always)
        updatable.update({k: v for k, v in lockable.items() if k not in blocked_fields})
        sets, vals, normalized = [], [], {}
        for k, maxlen in updatable.items():
            if k not in p:
                continue
            raw = p[k]
            if k == "quantity":
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    conn.close()
                    self._json(400, {"error": "quantity must be a whole number"})
                    return
                if value < 1 or value > 1000:
                    conn.close()
                    self._json(400, {"error": "quantity must be between 1 and 1000"})
                    return
            elif k == "price_inr":
                if str(raw).strip() == "":
                    value = None
                else:
                    try:
                        value = float(raw)
                    except (TypeError, ValueError):
                        conn.close()
                        self._json(400, {"error": "price must be a number"})
                        return
                    if (not math.isfinite(value)
                            or value < 0 or value > 100000000):
                        conn.close()
                        self._json(400, {"error": "price is outside the allowed range"})
                        return
            elif k == "is_stock":
                if not isinstance(raw, (bool, int)) or raw not in (0, 1, False, True):
                    conn.close()
                    self._json(400, {"error": "stock must be on or off"})
                    return
                value = 1 if raw else 0
            else:
                value = str(raw).strip()[:maxlen]
                if k == "product" and not value:
                    conn.close()
                    self._json(400, {"error": "a product is needed"})
                    return
            old = before[k]
            if k == "quantity":
                unchanged = value == int(old or 1)
            elif k == "price_inr":
                unchanged = value == (float(old) if old is not None else None)
            elif k == "is_stock":
                unchanged = value == int(old or 0)
            else:
                unchanged = value == str(old or "")
            if unchanged:
                continue
            sets.append(f"{k}=?")
            vals.append(value)
            normalized[k] = value
        if item_fields.intersection(normalized):
            item_count = conn.execute(
                "SELECT COUNT(*) FROM order_items WHERE order_id=?", (oid,)).fetchone()[0]
            if item_count > 1:
                conn.close()
                self._json(409, {
                    "error": "This storefront order contains multiple products. "
                             "Edit its product lines in Shopify; customer and shipping "
                             "details can still be changed here."})
                return
        if supplier_change is not None:
            if supplier_change == int(before["supplier_visible"] or 0):
                supplier_change = None
            else:
                sets.append("supplier_visible=?")
                vals.append(supplier_change)
        if before["shopify_order_id"] and contact_fields.intersection(normalized):
            sets.append("shopify_contact_override=1")
        # Mirrors the contact override immediately above: reaching here with a
        # product/quantity change means build_locked was false (those fields
        # are in blocked_fields and rejected earlier once committed), so this
        # is specifically the "still pending, edited before it was ever sent
        # to the supplier" case the sync would otherwise stomp on next run.
        if before["shopify_order_id"] and {"product", "quantity"}.intersection(normalized):
            sets.append("shopify_build_override=1")
        if not sets:
            conn.close()
            self._json(200, {"ok": True, "id": oid, "unchanged": True, "warnings": []})
            return
        vals.append(oid)
        conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", vals)

        # Keep the one-line analytics record in step with list edits. A
        # multi-product Shopify order was rejected above rather than flattened
        # into one synthetic line.
        if item_fields.intersection(normalized):
            product = normalized.get("product", before["product"]) or ""
            qty = normalized.get("quantity", before["quantity"]) or 1
            price = normalized.get("price_inr", before["price_inr"])
            item = conn.execute(
                "SELECT id FROM order_items WHERE order_id=? ORDER BY id LIMIT 1",
                (oid,)).fetchone()
            if item:
                conn.execute(
                    "UPDATE order_items SET product=?, quantity=?, price_inr=?, "
                    "line_total=?, canonical_product=CASE WHEN ? THEN NULL "
                    "ELSE canonical_product END, updated_at=datetime('now') WHERE id=?",
                    (product, qty, price, (price or 0) * qty,
                     1 if "product" in normalized else 0, item["id"]))
            else:
                conn.execute(
                    "INSERT INTO order_items "
                    "(order_id, product, quantity, price_inr, line_total) "
                    "VALUES (?,?,?,?,?)",
                    (oid, product, qty, price, (price or 0) * qty))

        # A status move goes on the order's own timeline. Flagged distinctly
        # when forced, so "why did this jump backward" is always answerable
        # from the history alone.
        if "status" in p and str(p["status"]).strip() != (before["status"] or ""):
            order_event(conn, oid, "status",
                       f'{before["status"] or "new"} -> {str(p["status"]).strip()}'
                       + (" (reset by staff)" if force_reset else ""), actor)
        if (supplier_change is not None
                and supplier_change != int(before["supplier_visible"] or 0)):
            order_event(
                conn, oid, "supplier",
                "sent to supplier queue" if supplier_change else "removed from supplier queue",
                actor)
        detail_changes = [k for k in normalized if k not in ("status", "tracking_code")]
        if detail_changes:
            order_event(conn, oid, "edited",
                        "updated " + ", ".join(detail_changes), actor)
        conn.commit()

        # Customer metrics are derived from orders. Recount the previous
        # identity and then upsert the edited identity so name/contact changes
        # and price changes cannot leave lifetime spend or VIP tags stale.
        if set(normalized).intersection({
                "customer_name", "customer_phone", "customer_email", "address",
                "city", "state", "pincode", "source", "price_inr", "quantity",
                "status", "is_stock"}):
            import customers as customers_mod
            old_key = customers_mod.key_for(
                before["customer_phone"], before["customer_email"], before["customer_name"])
            try:
                customers_mod.recount(conn, old_key)
                current = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
                current = dict(current)
                if (current.get("customer_name") or current.get("customer_phone")
                        or current.get("customer_email")):
                    customers_mod.upsert_from_order(conn, current)
            except Exception as e:
                print(f"[orders-update] customer repair skipped: {e}", flush=True)
        conn.close()
        changed = list(normalized)
        if supplier_change is not None:
            changed.append("sent to supplier" if supplier_change else "removed from supplier")

        # The local database is authoritative; sheet mirroring is best-effort.
        # Update only columns that changed rather than appending a second row.
        warnings = []
        try:
            import google_api
            access = google_api.access_token()
            if access and before["order_no"] is not None:
                sheet_fields = {
                    "status": "Status", "source": "Source",
                    "customer_name": "Customer", "customer_phone": "Phone",
                    "customer_email": "Email", "address": "Address",
                    "city": "City", "state": "State", "pincode": "Pincode",
                    "product": "Product", "quantity": "Qty", "price_inr": "Price (INR)",
                    "notes": "Notes", "case_style": "Case style",
                    "dial_colour": "Dial colour", "dial_style": "Dial style",
                    "case_colour": "Case colour", "movement": "Movement",
                    "watch_size": "Size"}
                for key in changed:
                    if key not in sheet_fields:
                        continue
                    value = normalized.get(key)
                    if key == "status":
                        value = order_stages.label(value)
                    google_api.update_order_field(
                        access, before["order_no"], sheet_fields[key],
                        "" if value is None else value)
                if item_fields.intersection(normalized):
                    line_total_paise = _effective_order_total_paise(
                        normalized.get("price_inr", before["price_inr"]),
                        normalized.get("quantity", before["quantity"]) or 1,
                        before["sale_total_paise"])
                    google_api.update_order_field(
                        access, before["order_no"], "Line total (INR)",
                        "" if line_total_paise is None else line_total_paise / 100.0)
        except Exception as e:
            warnings.append(f"Sheet not updated: {e}")
        hub_event("order_updated",
                 f"#{before['order_no'] or oid}: " + ", ".join(changed),
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "warnings": warnings})

    def _handle_order_customer_payment(self):
        """Create, correct, or clear the two customer receipt stages.

        This is intentionally admin-only.  The internal account allocation
        does not belong in the broader Orders role, and it must never be
        confused with either Shopify's ``financial_status`` or supplier
        settlement events.  Zero clears a stage; positive amounts are stored
        as integer paise with an explicit receiving account.
        """
        actor = self._admin_email()
        if not actor:
            self._json(403, {"error": "admin access is required"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
            oid = int(p.get("id"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return

        def amount_paise(key):
            raw = p.get(key)
            if raw is None or str(raw).strip() == "":
                return 0
            try:
                amount = Decimal(str(raw).strip())
                if not amount.is_finite() or amount < 0:
                    raise ValueError("Payment amounts must be non-negative numbers.")
                rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                raise ValueError("Enter a valid payment amount.")
            if amount != rounded:
                raise ValueError("Payment amounts can have at most two decimal places.")
            if rounded > Decimal("100000000"):
                raise ValueError("Payment amount is too large.")
            return int(rounded * 100)

        try:
            amounts = {
                "advance": amount_paise("advance_amount"),
                "balance": amount_paise("balance_amount"),
            }
        except ValueError as e:
            self._json(400, {"error": str(e)})
            return

        raw_sale_total = p.get("sale_total_amount")
        # Blank means "leave it alone". Clearing an override is explicit in
        # the UI: its reset button submits the calculated price × quantity,
        # which is canonicalized back to NULL below.
        sale_total_supplied = (
            "sale_total_amount" in p
            and raw_sale_total is not None
            and bool(str(raw_sale_total).strip()))
        requested_sale_total = None
        if sale_total_supplied:
            try:
                total = Decimal(str(raw_sale_total).strip())
                if not total.is_finite() or total < 0:
                    raise ValueError(
                        "Total sale amount must be a non-negative number.")
                rounded_total = total.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                self._json(400, {"error": "Enter a valid total sale amount."})
                return
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            if total != rounded_total:
                self._json(400, {
                    "error": "Total sale amount can have at most two decimal places."})
                return
            if rounded_total > Decimal("100000000"):
                self._json(400, {"error": "Total sale amount is too large."})
                return
            requested_sale_total = int(rounded_total * 100)
        allowed_accounts = {"SM", "MK", "TL", "TM"}
        accounts = {
            "advance": str(p.get("advance_account") or "").strip().upper(),
            "balance": str(p.get("balance_account") or "").strip().upper(),
        }
        expected_revision = str(p.get("expected_revision") or "").strip()
        for kind in ("advance", "balance"):
            if not amounts[kind]:
                accounts[kind] = ""
            elif accounts[kind] not in allowed_accounts:
                label = "advance" if kind == "advance" else "remaining payment"
                self._json(400, {
                    "error": f"Select SM, MK, TL, or TM for the {label}."})
                return

        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute(
                "SELECT * FROM orders "
                "WHERE id=? AND COALESCE(local_hidden,0)=0", (oid,)).fetchone()
            if not order:
                conn.rollback()
                conn.close()
                self._json(404, {"error": "no such order"})
                return
            before = {
                r["kind"]: {
                    "amount_paise": r["amount_paise"],
                    "account": r["account_code"],
                }
                for r in conn.execute(
                    "SELECT kind, amount_paise, account_code "
                    "FROM order_customer_receipts WHERE order_id=?", (oid,))
            }
            after = {
                kind: {"amount_paise": amounts[kind], "account": accounts[kind]}
                for kind in ("advance", "balance") if amounts[kind]
            }
            current_revision = _customer_payment_revision(
                before, order["price_inr"], order["quantity"] or 1,
                order["sale_total_paise"])
            if expected_revision != current_revision:
                conn.rollback()
                conn.close()
                self._json(409, {
                    "error": "Payment changed in another view. Reopen it and try again.",
                    "payment_rev": current_revision,
                })
                return
            calculated_total = _effective_order_total_paise(
                order["price_inr"], order["quantity"] or 1)
            next_sale_total = order["sale_total_paise"]
            if sale_total_supplied:
                # Storing the fallback again would turn an unchanged value
                # into an unnecessary override. Equality also powers the
                # modal's "Use calculated total" reset action.
                next_sale_total = (
                    None if requested_sale_total == calculated_total
                    else requested_sale_total)
            total_changed = next_sale_total != order["sale_total_paise"]
            receipt_changed = before != after
            if total_changed:
                conn.execute(
                    "UPDATE orders SET sale_total_paise=? WHERE id=?",
                    (next_sale_total, oid))
            if receipt_changed:
                for kind in ("advance", "balance"):
                    if amounts[kind]:
                        conn.execute(
                            "INSERT INTO order_customer_receipts "
                            "(order_id, kind, amount_paise, account_code, created_by, updated_by) "
                            "VALUES (?,?,?,?,?,?) "
                            "ON CONFLICT(order_id, kind) DO UPDATE SET "
                            "amount_paise=excluded.amount_paise, "
                            "account_code=excluded.account_code, "
                            "updated_at=datetime('now'), updated_by=excluded.updated_by",
                            (oid, kind, amounts[kind], accounts[kind], actor, actor))
                    else:
                        conn.execute(
                            "DELETE FROM order_customer_receipts "
                            "WHERE order_id=? AND kind=?", (oid, kind))
                def shown(kind):
                    if not amounts[kind]:
                        return "cleared"
                    rupees = Decimal(amounts[kind]) / Decimal(100)
                    return f"INR {rupees:.2f} to {accounts[kind]}"
            if total_changed or receipt_changed:
                details = []
                if total_changed:
                    old_total = _effective_order_total_paise(
                        order["price_inr"], order["quantity"] or 1,
                        order["sale_total_paise"])
                    new_total = _effective_order_total_paise(
                        order["price_inr"], order["quantity"] or 1,
                        next_sale_total)

                    def total_shown(value):
                        return ("not set" if value is None else
                                f"INR {Decimal(value) / Decimal(100):.2f}")

                    details.append(
                        f"sale total {total_shown(old_total)} -> "
                        f"{total_shown(new_total)}")
                if receipt_changed:
                    details.append(
                        f"advance {shown('advance')}; remaining {shown('balance')}")
                order_event(conn, oid, "customer_receipt", "; ".join(details), actor)
            conn.commit()
        except sqlite3.Error as e:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            print(f"[customer-payment] update failed for {oid}: {e}", flush=True)
            self._json(500, {"error": "Payment was not changed. Try again."})
            return
        conn.close()

        qty = order["quantity"] or 1
        total_paise = _effective_order_total_paise(
            order["price_inr"], qty, next_sale_total)
        received_paise = amounts["advance"] + amounts["balance"]

        # Customer lifetime value and the shared Sheet both describe what the
        # customer paid for the order, so a deliberate sale-total override
        # must reach them too. The local transaction above is authoritative;
        # these mirrors are best-effort and never roll the receipt back.
        warnings = []
        if sale_total_supplied:
            customer_row = None
            try:
                import customers as customers_mod
                repair = db()
                current = dict(repair.execute(
                    "SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
                if (current.get("customer_name") or current.get("customer_phone")
                        or current.get("customer_email")):
                    customer_row = customers_mod.upsert_from_order(repair, current)
                repair.close()
            except Exception as e:
                try:
                    repair.close()
                except Exception:
                    pass
                warnings.append(f"Customer totals not refreshed: {e}")
                print(f"[customer-payment] customer repair skipped: {e}", flush=True)
            try:
                import google_api
                access = google_api.access_token()
                if access and order["order_no"] is not None:
                    google_api.update_order_field(
                        access, order["order_no"], "Line total (INR)",
                        "" if total_paise is None else total_paise / 100.0)
                    if customer_row:
                        google_api.upsert_customer_row(
                            access, customers_mod.HEADERS,
                            customers_mod.sheet_row(customer_row))
            except Exception as e:
                warnings.append(f"Sheet not updated: {e}")
        if total_changed or receipt_changed:
            hub_event("order_customer_payment",
                      f"#{order['order_no'] or oid} sale total or receipt updated",
                      actor, app="orders")
        self._json(200, {
            "ok": True, "id": oid,
            "received_paise": received_paise,
            "total_paise": total_paise,
            "due_paise": (max(total_paise - received_paise, 0)
                          if total_paise is not None else None),
            "overpaid_paise": (max(received_paise - total_paise, 0)
                               if total_paise is not None else 0),
            "sale_total_paise": next_sale_total,
            "warnings": warnings,
            "payment_rev": _customer_payment_revision(
                after, order["price_inr"], order["quantity"] or 1,
                next_sale_total),
        })

    def _handle_orders_bulk(self):
        """One build per screenshot. Send a batch of watch photos and each
        becomes its own order in the unified log — no customer yet, that gets
        attached when it sells. Staff decide which ones enter the supplier
        queue after reviewing the batch.

        Each photo is filed against its own order id, so the queue shows the
        right reference against the right build rather than a shared album
        nobody can match up. Partial success is reported rather than rolled
        back: if the seventh photo is corrupt, the first six are still real
        orders and should not be thrown away."""
        actor = self._order_user()
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        items = p.get("items") or []
        if not isinstance(items, list) or not items:
            self._json(400, {"error": "nothing to create"})
            return
        source = str(p.get("source", "")).strip()[:40]
        import order_taxonomy
        conn = db()
        created, failed = [], []
        for i, it in enumerate(items[:60]):
            photo_dest = ""
            photo_dir = ""
            photo_dest_created = False
            try:
                product = str((it or {}).get("product", "")).strip()[:200]
                photo = str((it or {}).get("photo_path", "")).strip()
                ref = str((it or {}).get("ref_code", "")).strip()[:60]
                rp = _upload_owned(photo, actor) if photo else ""
                if photo and not rp:
                    failed.append({"i": i, "error": "photo upload expired"})
                    continue
                # A backfilled build is a photo plus its old order number; the
                # picture IS the spec, so a product name isn't required. Fall
                # back to the ref, then a placeholder, so the row is never
                # nameless on the queue. But a row with neither a photo nor any
                # text is empty — skip it rather than create a blank order.
                if not product and not rp and not ref:
                    failed.append({"i": i, "error": "nothing to add"})
                    continue
                if not product:
                    # With a photo the picture IS the spec, and the card
                    # already shows the order number in its header — so don't
                    # repeat the number as the product name too.
                    product = ("See reference photo" if rp
                               else (f"Order {ref}" if ref else "Build"))
                notes = str((it or {}).get("notes", "")).strip()[:1000]
                try:
                    qty = int((it or {}).get("quantity") or 1)
                except (TypeError, ValueError):
                    raise ValueError("quantity must be a whole number")
                if qty < 1 or qty > 1000:
                    raise ValueError("quantity must be between 1 and 1000")
                price = None
                if str((it or {}).get("price_inr", "")).strip():
                    try:
                        price = float(it["price_inr"])
                    except (TypeError, ValueError):
                        raise ValueError("price must be a number")
                    if (not math.isfinite(price)
                            or price < 0 or price > 100000000):
                        raise ValueError("price is outside the allowed range")
                attrs = order_taxonomy.extract(product + " " + notes)
                # Keep each row atomic.  The handler deliberately permits a
                # partially successful batch, but a failed row must never
                # leave uncommitted inserts that a later row then commits.
                # BEGIN IMMEDIATE also serializes order_no allocation across
                # simultaneous intake requests.
                conn.execute("BEGIN IMMEDIATE")
                import order_numbers
                order_no = order_numbers.next_number(conn)
                cur = conn.execute(
                    "INSERT INTO orders (customer_name, source, product, price_inr, "
                    "quantity, notes, status, has_image, ref_code, case_style, dial_colour, "
                    "dial_style, case_colour, movement, watch_size, order_no, supplier_visible) "
                    "VALUES ('',?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,0)",
                    (source, product, price, qty, notes, 1 if rp else 0, ref or None,
                     attrs.get("case_style"), attrs.get("dial_colour"),
                     attrs.get("dial_style"), attrs.get("case_colour"),
                     attrs.get("movement"), attrs.get("watch_size"), order_no))
                oid = cur.lastrowid
                # Bulk/photo intake is explicitly unsold build inventory.
                conn.execute("UPDATE orders SET is_stock=1 WHERE id=?", (oid,))
                if rp:
                    photo_dir = os.path.join(ORDER_PHOTOS, str(oid))
                    os.makedirs(photo_dir, exist_ok=True)
                    fn = "1" + os.path.splitext(rp)[1].lower()
                    photo_dest = os.path.join(photo_dir, fn)
                    # Copy first and remove the upload only after the database
                    # commit.  If anything below fails, rollback can leave the
                    # source upload available for a clean retry.
                    with open(rp, "rb") as src, open(photo_dest, "xb") as dst:
                        photo_dest_created = True
                        shutil.copyfileobj(src, dst)
                    conn.execute("UPDATE orders SET local_photos=? WHERE id=?",
                                (json.dumps([fn]), oid))
                conn.execute(
                    "INSERT INTO order_items (order_id, product, quantity, price_inr, "
                    "line_total) VALUES (?,?,?,?,?)",
                    (oid, product, qty, price, (price or 0) * qty))
                order_event(conn, oid, "created",
                           f"backfilled in a bulk batch{f' (order {ref})' if ref else ''}", actor)
                conn.commit()
                if rp:
                    _forget_upload(rp, remove_data=True)
                created.append({"id": oid, "product": product, "ref_code": ref})
            except Exception as e:
                if conn.in_transaction:
                    conn.rollback()
                if photo_dest_created:
                    try:
                        os.remove(photo_dest)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
                if photo_dir:
                    try:
                        os.rmdir(photo_dir)
                    except OSError:
                        pass
                failed.append({"i": i, "error": str(e)[:120]})
        conn.close()
        if created:
            hub_event("orders_bulk", f"{len(created)} order(s) logged from a photo batch",
                     actor, app="orders")
        self._json(200, {"ok": True, "created": created, "failed": failed})

    def _handle_orders_delete(self):
        """Remove an order — hard-deleted (row, line items, timeline, photos:
        all gone) for a manually-created Test/Mistake/Draft record, or hidden
        (row and history kept, just flagged off every list/total) for a
        website order, since a hard delete there would free its
        shopify_order_id and let the very next sync recreate it as a
        "new" order under a fresh number. Messaging-captured orders (a
        transcribed customer conversation) get neither path — they stay
        exactly as permanent as before.

        Eligibility is nearly identical either way: untouched Pending, never
        sent to the supplier, no shipment/bill/tracking, no STAFF history
        beyond its own creation. The one deliberate difference: neither the
        Shopify "paid" checkout signal nor the sync's own routine
        reconciliation events count against a website order, since hiding it
        destroys nothing (Shopify's own record is the real permanent one
        either way) — counting them made this path unusable, since almost
        every real order is paid and reconciled within minutes of existing.
        The derived customer is repaired afterwards — recomputed from what's
        left, and removed entirely if nothing remains — so neither path can
        leave a phantom buyer with inflated lifetime spend."""
        actor = self._order_user()
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 2048)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT order_no, customer_name, customer_phone, customer_email, product, "
            "financial_status, status, supplier_visible, shipment_id, bill_id, tracking_code, source, "
            "shopify_order_id, chat_id, sender_number, raw_message "
            "FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        is_website = bool(row["shopify_order_id"])
        # Paid blocks a TRUE hard delete outright — that destroys the row,
        # its items and its history, and a paid order is a real financial
        # record. It does not need to block hiding a website order: nothing
        # is destroyed (row, items, financial_status, the Shopify link all
        # survive; /orders/unhide reverses it), and financial_status='paid'
        # is Shopify's own checkout signal — a storefront order is normally
        # "paid" within seconds of being placed, so leaving this unscoped
        # meant almost no real website order could ever be removed from the
        # working queue, paid or not.
        if not is_website and (row["financial_status"] or "").strip().lower() == "paid":
            conn.close()
            self._json(409, {
                "error": "This order is marked Paid and cannot be deleted."})
            return
        import order_stages
        if (row["status"] or order_stages.DEFAULT_STAGE) != order_stages.DEFAULT_STAGE:
            conn.close()
            self._json(409, {
                "error": "Only an untouched Pending order can be deleted. Cancel or "
                         "correct the workflow instead."})
            return
        if (row["supplier_visible"] or row["shipment_id"] is not None
                or row["bill_id"] is not None or row["tracking_code"]):
            conn.close()
            self._json(409, {
                "error": "This order has already been shared, shipped, billed, or tracked "
                         "and cannot be removed."})
            return
        if row["chat_id"] or row["sender_number"] or row["raw_message"]:
            conn.close()
            self._json(409, {
                "error": "Messaging-captured orders are a transcript of what a customer "
                         "actually sent and are permanent records. Cancel the order instead."})
            return
        if not is_website and (row["source"] or "").strip().lower() not in (
                "test", "mistake", "draft"):
            conn.close()
            self._json(409, {
                "error": "Only a record explicitly marked Test, Mistake, or Draft can be "
                         "hard-deleted. Cancel this order to preserve its audit history."})
            return
        # shopify_sync/shopify_conflict are the timer talking to itself, not a
        # person doing anything — routine financial reconciliation fires on
        # nearly every real order, so counting it here would have made this
        # check as unconditionally blocking as the paid check above just was.
        # (shopify_conflict specifically can only be logged once a build is
        # committed, which the supplier_visible/shipment/bill checks above
        # already gate on their own — excluding it from this count for a
        # website order can never let an actually-committed one slip through.)
        # Genuine staff actions (edited, note, cost, billed, tracking...)
        # still count for every order type.
        ignored_kinds = (
            "shopify_sync", "shopify_conflict", "customer_receipt"
        ) if is_website else ()
        marks = ",".join("?" for _ in ignored_kinds)
        extra_events = conn.execute(
            f"SELECT COUNT(*) FROM order_events WHERE order_id=? AND kind!='created'"
            f"{f' AND kind NOT IN ({marks})' if ignored_kinds else ''}",
            (oid, *ignored_kinds)).fetchone()[0]
        bill_links = conn.execute(
            "SELECT COUNT(*) FROM supplier_bill_items WHERE order_id=?", (oid,)).fetchone()[0]
        receipt_links = conn.execute(
            "SELECT COUNT(*) FROM order_customer_receipts WHERE order_id=?",
            (oid,)).fetchone()[0]
        if receipt_links and not is_website:
            conn.close()
            self._json(409, {
                "error": "This order has customer payments recorded and is a permanent "
                         "financial record. Cancel it instead."})
            return
        if extra_events or bill_links:
            conn.close()
            self._json(409, {
                "error": "This record has workflow history or billing links and cannot "
                         "be removed."})
            return

        if is_website:
            # Hide, don't destroy: shopify_order_id stays on the row, so the
            # UNIQUE index still blocks the next sync from re-inserting this
            # order as if it were new. Nothing else about the record changes —
            # it's recoverable via /orders/unhide, unlike a hard delete.
            conn.execute("UPDATE orders SET local_hidden=1 WHERE id=?", (oid,))
            order_event(conn, oid, "hidden", "removed from Labs OS (Shopify record untouched)",
                       actor)
        else:
            conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
            conn.execute("DELETE FROM order_events WHERE order_id=?", (oid,))
            conn.execute("DELETE FROM orders WHERE id=?", (oid,))
        conn.commit()

        import customers as customers_mod
        ckey = customers_mod.key_for(row["customer_phone"], row["customer_email"],
                                     row["customer_name"])
        try:
            remaining = customers_mod.recount(conn, ckey)
        except Exception as e:
            remaining = None
            print(f"[orders-delete] customer repair skipped: {e}", flush=True)
        conn.close()

        # The Sheet is a mirror, not authority, but leave an explicit tombstone
        # instead of a live-looking row. The durable local sequence guarantees
        # this number is never allocated again even if the mirror is offline.
        try:
            import google_api
            access = google_api.access_token()
            if access and row["order_no"] is not None:
                google_api.update_order_field(
                    access, row["order_no"], "Status",
                    "Removed locally (Shopify order)" if is_website
                    else "Deleted — test/mistake")
        except Exception as e:
            print(f"[orders-delete] sheet tombstone skipped: {e}", flush=True)

        if not is_website:
            photo_dir = os.path.join(ORDER_PHOTOS, str(oid))
            if os.path.isdir(photo_dir):
                try:
                    for fn in os.listdir(photo_dir):
                        os.remove(os.path.join(photo_dir, fn))
                    os.rmdir(photo_dir)
                except OSError as e:
                    print(f"[orders-delete] photo cleanup: {e}", flush=True)
        hub_event("order_hidden" if is_website else "order_deleted",
                 f'#{row["order_no"] or oid} {row["product"] or ""} '
                 f'({row["customer_name"] or "?"})',
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "hidden": is_website,
                         "customer_orders_left": remaining})

    def _handle_orders_unhide(self):
        """Undo a website order's local hide. Admin/orders only, matching the
        delete endpoint it reverses — nothing was destroyed, so this is a
        plain flag flip plus the same customer recompute delete does."""
        actor = self._order_user()
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 2048)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        conn = db()
        row = conn.execute(
            "SELECT order_no, customer_name, customer_phone, customer_email, "
            "local_hidden FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        if not row["local_hidden"]:
            conn.close()
            self._json(200, {"ok": True, "id": oid, "unchanged": True})
            return
        conn.execute("UPDATE orders SET local_hidden=0 WHERE id=?", (oid,))
        order_event(conn, oid, "hidden", "restored to Labs OS", actor)
        conn.commit()
        current = dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
        try:
            import customers as customers_mod
            customers_mod.upsert_from_order(conn, current)
        except Exception as e:
            print(f"[orders-unhide] customer repair skipped: {e}", flush=True)
        conn.close()
        try:
            import google_api
            import order_stages
            access = google_api.access_token()
            if access and row["order_no"] is not None:
                google_api.update_order_field(
                    access, row["order_no"], "Status",
                    order_stages.label(current.get("status")))
        except Exception as e:
            print(f"[orders-unhide] sheet restore skipped: {e}", flush=True)
        hub_event("order_unhidden", f"#{row['order_no'] or oid} restored", actor, app="orders")
        self._json(200, {"ok": True, "id": oid})

    def _handle_order_items_relabel(self):
        """Rename a product for analytics — retroactive across every order
        that used the old text, so a messy free-typed name only needs fixing
        once. Never touches the order itself or what it says was charged,
        only the label What's Selling groups by."""
        actor = self._order_user()
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        old = str(p.get("old", "")).strip()[:200]
        new = str(p.get("new", "")).strip()[:200]
        if not old or not new:
            self._json(400, {"error": "both the current and new name are needed"})
            return
        conn = db()
        cur = conn.execute(
            "UPDATE order_items SET canonical_product=?, updated_at=datetime('now') "
            "WHERE COALESCE(NULLIF(canonical_product,''), product) = ?", (new, old))
        n = cur.rowcount
        conn.commit()
        conn.close()
        hub_event("product_relabelled",
                 f'"{old}" -> "{new}" ({n} line item{"s" if n != 1 else ""})',
                 actor, app="orders")
        self._json(200, {"ok": True, "updated": n})

    def _handle_orders_products(self):
        """Names for the product field's type-ahead: everything already sold
        (logged or synced) plus the live Shopify catalogue, merged once per
        page load rather than queried per keystroke — the list is small
        enough that client-side filtering is instant and this keeps the
        combobox usable even if Shopify is briefly unreachable. Product names
        aren't PII, so intake gets this too, same as /orders/create."""
        if not (self._has_tool("orders") or self._has_tool("intake")):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT DISTINCT COALESCE(NULLIF(canonical_product,''), product) "
                "FROM order_items WHERE product IS NOT NULL AND product != ''")]
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        catalog = []
        try:
            import shopify_api
            if shopify_api.configured():
                catalog = [pr["title"] for pr in
                          shopify_api.list_products(first=100)["products"] if pr.get("title")]
        except Exception as e:
            print(f"[orders-products] shopify catalog skipped: {e}", flush=True)
        merged = sorted({n.strip() for n in (names + catalog) if n and n.strip()},
                        key=str.lower)
        self._json(200, {"products": merged})

    # --- Order photos ------------------------------------------------------
    def _photo_names(self, conn, order_id, supplier_only=False):
        """The stored filenames for an order, or None if it isn't visible to
        this caller. supplier_only additionally requires the order to have
        been shared with the supplier queue."""
        q = "SELECT local_photos FROM orders WHERE id=?"
        args = [order_id]
        if supplier_only:
            q += " AND supplier_visible=1"
        row = conn.execute(q, args).fetchone()
        if not row:
            return None
        try:
            names = json.loads(row["local_photos"] or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(names, list):
            return []
        return [n for n in names if isinstance(n, str)]

    def _serve_order_photo(self, query, supplier_only):
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            oid = int(params.get("id", ""))
            idx = int(params.get("n", "0"))
        except ValueError:
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        names = self._photo_names(conn, oid, supplier_only)
        conn.close()
        if names is None:
            self._json(404, {"error": "no such order"})
            return
        if not (0 <= idx < len(names)):
            self._json(404, {"error": "no such photo"})
            return
        # Rebuild the path from the order id and an index into the stored
        # list — the client never supplies a filename, so there's nothing
        # here to traverse out of.
        path = os.path.join(ORDER_PHOTOS, str(oid), os.path.basename(names[idx]))
        if not os.path.isfile(path):
            self._json(404, {"error": "photo missing"})
            return
        revision = _order_photo_revision(names)
        etag = f'"order-photo-{oid}-{idx}-{revision}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache, max-age=0, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return
        ext = os.path.splitext(path)[1].lower()
        ctype = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._json(404, {"error": "photo unreadable"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Index-based URLs used to stay fresh for an hour even when removing
        # photo zero shifted a different file into zero.  Revalidate every
        # use; the filename-list ETag makes unchanged redraws a cheap 304.
        # The UIs also append the revision, immediately bypassing any response
        # cached under the old pre-fix URL.
        self.send_header("Cache-Control", "private, no-cache, max-age=0, must-revalidate")
        self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _handle_order_photo(self, query):
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        self._serve_order_photo(query, supplier_only=False)

    def _handle_supplier_photo(self, query):
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        self._serve_order_photo(query, supplier_only=True)

    def _handle_order_photos_update(self, supplier_only=False):
        """Add uploaded images and/or remove existing images from an order.
        Supplier callers may only touch orders explicitly shared with them;
        staff may touch any logged order. Filenames never come from the
        browser, and removals are expressed as stable list indexes."""
        if supplier_only:
            allowed = self._supplier_ok()
        else:
            allowed = self._has_tool("orders")
        if not allowed:
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
            oid = int(p.get("id"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        incoming = p.get("photo_paths") or []
        if not isinstance(incoming, list):
            incoming = []
        incoming = incoming[:10]
        remove = p.get("remove") or []
        if not isinstance(remove, list):
            remove = []
        remove = {int(i) for i in remove if str(i).isdigit()}
        expected_revision = str(p.get("expected_revision") or "").strip()
        actor = self._order_user()
        validated = []
        for candidate in incoming:
            path = _upload_owned(candidate, actor)
            if not path:
                self._json(400, {"error": "a photo upload expired — attach it again"})
                return
            validated.append(path)

        conn = db()
        order_dir = os.path.join(ORDER_PHOTOS, str(oid))
        created, removed_names = [], []
        try:
            conn.execute("BEGIN IMMEDIATE")
            q = "SELECT local_photos, order_no FROM orders WHERE id=?"
            if supplier_only:
                q += " AND supplier_visible=1"
            row = conn.execute(q, (oid,)).fetchone()
            if not row:
                conn.rollback()
                conn.close()
                self._json(404, {"error": "no such order"})
                return
            try:
                stored_photos = json.loads(row["local_photos"] or "[]")
                if not isinstance(stored_photos, list):
                    stored_photos = []
                current = [
                    os.path.basename(n)
                    for n in stored_photos
                    if isinstance(n, str)
                ]
            except (json.JSONDecodeError, TypeError):
                current = []
            current_revision = _order_photo_revision(current)
            # A removal is index-addressed.  Never apply an index selected
            # against an older list: another browser may have added/removed a
            # photo since the thumbnails were drawn, making that index point
            # at the wrong image.  Updated clients send this for additions as
            # well, so simultaneous photo managers get an explicit refresh.
            if (remove and expected_revision != current_revision) or (
                    expected_revision and expected_revision != current_revision):
                conn.rollback()
                conn.close()
                self._json(409, {
                    "error": "Images changed in another view. Refresh and try again.",
                    "photo_rev": current_revision,
                })
                return
            os.makedirs(order_dir, mode=0o750, exist_ok=True)
            kept = []
            for idx, name in enumerate(current):
                if idx in remove:
                    removed_names.append(name)
                else:
                    kept.append(name)
            for path in validated:
                ext = os.path.splitext(path)[1].lower()
                name = secrets.token_hex(16) + ext
                final = os.path.join(order_dir, name)
                stage = os.path.join(
                    order_dir, ".stage-" + secrets.token_hex(16) + ext)
                try:
                    with open(path, "rb") as src, open(stage, "xb") as dst:
                        shutil.copyfileobj(src, dst)
                        dst.flush()
                        os.fsync(dst.fileno())
                    os.replace(stage, final)
                finally:
                    try:
                        os.remove(stage)
                    except FileNotFoundError:
                        pass
                created.append(final)
                kept.append(name)
            # SQLite must never commit a filename whose directory entry is
            # still only in the kernel's cache. The parent fsync also makes a
            # newly-created per-order directory durable.
            _fsync_directory(order_dir)
            _fsync_directory(ORDER_PHOTOS)
            conn.execute(
                "UPDATE orders SET local_photos=?, has_image=?, photo_override=1 "
                "WHERE id=?",
                (json.dumps(kept), 1 if kept else 0, oid))
            order_event(
                conn, oid, "photos",
                f"reference photos updated ({len(kept)} total)", actor)
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            for path in created:
                try:
                    os.remove(path)
                except OSError:
                    pass
            try:
                _fsync_directory(order_dir)
            except OSError:
                pass
            print(f"[order-photos] atomic update failed for {oid}: {e}", flush=True)
            self._json(500, {
                "error": "Photos were not changed. Your new uploads remain available."})
            return
        conn.close()
        for name in removed_names:
            try:
                os.remove(os.path.join(order_dir, name))
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f"[order-photos] old photo cleanup for {oid}: {e}", flush=True)
        if removed_names:
            try:
                _fsync_directory(order_dir)
            except OSError as e:
                print(
                    f"[order-photos] cleanup sync for {oid} deferred: {e}",
                    flush=True)
        for path in validated:
            _forget_upload(path, remove_data=True)
        self._json(200, {"ok": True, "id": oid, "photos": len(kept),
                         "photo_rev": _order_photo_revision(kept)})

    # --- Supplier build queue: what to build, never who for -----------------
    def _supplier_ok(self):
        """admin or supplier role — anyone else, including a signed-in person
        with no role record at all, is refused."""
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        if not email:
            return False
        import access_store
        return access_store.get_role(email) in ("admin", "supplier")

    def _handle_supplier_orders(self):
        """The build queue: order number, what to build, quantity, status.
        The SELECT never names a customer-identity or price column (name,
        phone, email, address, price_inr), so there is no code path here
        that could leak one even by accident.

        `notes` (owner's 2026-07-30 call, reversing the earlier default of
        withholding it) IS included: staff's own sizing/deadline shorthand is
        useful to the person actually building the watch. It is still
        free-typed staff text, occasionally containing a stray customer
        detail if someone pastes carelessly — the supplier's own running
        note box (the 'note' timeline kind below) is the channel for
        anything they add themselves.

        No separate "paid" flag: `status` already has a paid stage, and it
        means "we've paid the supplier" — Shopify's financial_status means
        "the customer paid us", a fact about a different relationship
        entirely that has no bearing on building the watch, so it's excluded
        rather than surfaced under a confusingly-similar name."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        import order_stages
        from order_form import STATUSES
        conn = db()
        try:
            rows = conn.execute(
                # supplier_cost is the supplier's OWN price to us — safe to
                # return here, unlike price_inr (what the customer pays),
                # which stays absent from this SELECT entirely.
                "SELECT id, order_no, received_at, product, quantity, status, notes, "
                "case_style, dial_colour, dial_style, case_colour, movement, "
                "watch_size, local_photos, tracking_code, ref_code, "
                "supplier_cost, supplier_cost_ccy, bill_id FROM orders "
                "WHERE supplier_visible=1 AND COALESCE(local_hidden,0)=0 "
                "ORDER BY id DESC").fetchall()
            events = {}
            safe_event_kinds = (
                "created", "status", "supplier", "photos", "cost", "billed",
                "bill_acknowledged", "bill_deleted", "tracking", "payment",
                "shipment", "note",
            )
            marks = ",".join("?" for _ in safe_event_kinds)
            for e in conn.execute(
                    "SELECT order_id, created_at, actor, kind, detail FROM order_events "
                    "WHERE order_id IN (SELECT id FROM orders WHERE supplier_visible=1) "
                    f"AND kind IN ({marks}) ORDER BY id ASC", safe_event_kinds):
                events.setdefault(e["order_id"], []).append({
                    "at": e["created_at"], "kind": e["kind"], "detail": e["detail"],
                    # Who acted matters (did we pay, or did they mark it?) but
                    # a full email address doesn't need to travel here.
                    "by": (e["actor"] or "").split("@")[0]})
            locked_bills = {b["id"] for b in conn.execute(
                "SELECT id FROM supplier_bills WHERE status='acknowledged'")}
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                photo_names = json.loads(d.pop("local_photos") or "[]")
            except (json.JSONDecodeError, TypeError):
                photo_names = []
            if not isinstance(photo_names, list):
                photo_names = []
            d["photos"] = len(photo_names)
            d["photo_rev"] = _order_photo_revision(photo_names)
            d["timeline"] = events.get(d["id"], [])
            # Once the batch carrying this build has been acknowledged, its
            # cost is settled history and the UI shows it read-only.
            d["locked"] = d.get("bill_id") in locked_bills
            # Shown greyed on the card so the rate that will be applied is
            # visible before it's applied, never a surprise on the bill.
            dc, dl = self._default_cost(d.get("product"), d.get("movement"))
            d["default_cost"], d["default_label"] = dc, dl
            out.append(d)
        self._json(200, {"orders": out, "statuses": STATUSES,
                         "pipeline": order_stages.PIPELINE,
                         "labels": order_stages.LABELS})

    def _handle_supplier_status(self):
        """Status-only, and only on an order actually shared with this role —
        the ownership check is server-side, not left to the UI only offering
        shared orders."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        from order_form import STATUSES
        import order_stages
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 2048)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        status = str(p.get("status", "")).strip()[:40]
        if not status or status not in STATUSES:
            self._json(400, {"error": "unrecognised status"})
            return
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM orders WHERE id=? AND supplier_visible=1", (oid,)).fetchone()
        if not row:
            conn.rollback()
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        was = row["status"] or order_stages.DEFAULT_STAGE
        if was == status:
            conn.rollback()
            conn.close()
            self._json(200, {"ok": True, "id": oid, "status": status, "unchanged": True})
            return
        if not order_stages.supplier_can_advance(was, status):
            conn.rollback()
            conn.close()
            expected = order_stages.next_of(was)
            self._json(409, {
                "error": (
                    f"This build can only move forward to {order_stages.label(expected)}."
                    if expected else "This build is already at its final stage."
                )})
            return
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        order_event(conn, oid, "status", f"{was} -> {status}", actor)
        onum = conn.execute("SELECT order_no FROM orders WHERE id=?", (oid,)).fetchone()["order_no"]
        conn.commit()
        conn.close()
        import order_stages
        import google_api
        # Keep the sheet's Status column in step — best-effort, never fatal.
        try:
            access = google_api.access_token()
            if access and onum is not None:
                google_api.update_order_field(access, onum, "Status",
                                              order_stages.label(status))
        except Exception:
            pass
        hub_event("order_updated", f"#{onum or oid}: status -> {status}", actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "status": status})

    # Same effective INR/USD rate the Ledger and Home dashboard already use —
    # bank charges and transfer fees included, not the market rate — so a
    # rupee figure here means the same thing as the same figure shown
    # anywhere else in the OS.
    _USD_INR = 100.0
    _MONEY_LIMIT = 1_000_000_000.0
    _MONEY_CURRENCIES = frozenset(("INR", "USD"))

    def _money_currency(self, value, default="INR"):
        """Return a supported accounting currency or None.

        Keeping this narrow is deliberate: silently treating a typo as INR
        corrupts both the bill and the Ledger conversion later.
        """
        currency = str(value or default).strip().upper()
        return currency if currency in self._MONEY_CURRENCIES else None

    def _money_amount(self, value, *, positive=False, optional=False):
        """Parse a finite, bounded monetary input.

        JSON accepts NaN/Infinity in Python's decoder, and float("nan") also
        evades ordinary `< 0` checks. Reject those before they can reach
        SQLite or poison every aggregate that touches the row.
        """
        if optional and (value is None or str(value).strip() == ""):
            return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        floor = 0.0 if not positive else 0.0000001
        if not math.isfinite(amount) or amount < floor or amount > self._MONEY_LIMIT:
            return None
        return amount

    def _to_inr(self, amount, currency):
        if not amount:
            return 0.0
        return float(amount) * (self._USD_INR if (currency or "USD").upper() == "USD" else 1.0)

    def _convert_money(self, amount, source_currency, target_currency):
        inr = self._to_inr(amount, source_currency)
        return inr / self._USD_INR if target_currency == "USD" else inr

    # ------------------------------------------------- per-watch supplier cost
    # What one watch costs if nobody says otherwise. The supplier can always
    # type a real number; this is the floor so a batch can be raised without
    # pricing every line by hand.
    #
    # Movement is read from the product text as well as the movement column,
    # because the column is mostly empty in practice (2 of 7 builds had one
    # when this was written) while the name almost always says "vk63" or
    # "nh35". VK63 is checked first: it's a meca-quartz chronograph, so a
    # build naming both it and "automatic" is a VK63 with an automatic-style
    # dial, not an NH35.
    _DEFAULT_COSTS = (
        ("VK63", 6500.0, ("vk63", "vk 63")),
        ("NH35", 9000.0, ("nh35", "nh 35", "automatic")),
    )
    _FALLBACK_COST = 5000.0

    def _default_cost(self, product, movement):
        hay = f"{movement or ''} {product or ''}".lower()
        for label, amount, keys in self._DEFAULT_COSTS:
            if any(k in hay for k in keys):
                return amount, label
        return self._FALLBACK_COST, "unidentified"

    def _cost_editable(self, conn, order_id):
        """A build's cost is editable until the bill carrying it is
        acknowledged. After that the number is settled history — the check
        lives here rather than in the UI so it holds for a direct API call."""
        row = conn.execute(
            "SELECT o.bill_id, o.status AS order_status, o.supplier_visible, "
            "b.status AS bill_status FROM orders o "
            "LEFT JOIN supplier_bills b ON b.id = o.bill_id "
            "WHERE o.id=?", (order_id,)).fetchone()
        if not row:
            return False, "no such build"
        if not row["supplier_visible"]:
            return False, "this build is not in the supplier queue"
        if str(row["order_status"] or "").strip().lower() in (
                "cancelled", "canceled", "refunded", "voided"):
            return False, "cancelled builds cannot be repriced or billed"
        if row["bill_status"] == "acknowledged":
            return False, "this build is on a bill you've already acknowledged"
        return True, ""

    def _set_costs(self, ids, cost, currency, actor):
        """Shared by the single and bulk paths — one build or forty, the
        validation and the audit trail are identical, so they're not two
        different code paths that can drift apart."""
        conn = db()
        done, skipped = [], []
        try:
            conn.execute("BEGIN IMMEDIATE")
            for oid in ids:
                ok, why = self._cost_editable(conn, oid)
                if not ok:
                    skipped.append({"id": oid, "reason": why})
                    continue
                cur = conn.execute(
                    "UPDATE orders SET supplier_cost=?, supplier_cost_ccy=? "
                    "WHERE id=? AND supplier_visible=1 "
                    "AND LOWER(TRIM(COALESCE(status,''))) NOT IN "
                    "('cancelled','canceled','refunded','voided')",
                    (cost, currency, oid))
                if cur.rowcount != 1:
                    skipped.append(
                        {"id": oid, "reason": "build changed; refresh and try again"})
                    continue
                order_event(conn, oid, "cost",
                            f"supplier cost set to {currency} {cost:,.2f}", actor)
                done.append(oid)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return done, skipped

    def _handle_supplier_cost(self):
        """Set what the supplier charges for one build. Supplier and admin
        both, per the owner's call — he wants to be able to fix a typo
        without going back to Hannan, but neither side can touch it once the
        bill is acknowledged."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("order_id") or 0)
        except (TypeError, ValueError):
            oid = 0
        cost = self._money_amount(p.get("cost"))
        currency = self._money_currency(p.get("currency") or "INR")
        if oid <= 0 or cost is None:
            self._json(400, {"error": "a build and a cost are needed"})
            return
        if not currency:
            self._json(400, {"error": "currency must be INR or USD"})
            return
        done, skipped = self._set_costs([oid], cost, currency, actor)
        if not done:
            self._json(400, {"error": skipped[0]["reason"] if skipped else "couldn't save"})
            return
        self._json(200, {"ok": True, "order_id": oid, "cost": cost, "currency": currency})

    def _handle_supplier_cost_bulk(self):
        """Same cost across a selection — the common case when a batch of
        identical builds is quoted at one price."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        ids = list(dict.fromkeys(
            int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()
        ))[:200]
        cost = self._money_amount(p.get("cost"))
        currency = self._money_currency(p.get("currency") or "INR")
        if not ids or cost is None:
            self._json(400, {"error": "pick at least one build and a cost"})
            return
        if not currency:
            self._json(400, {"error": "currency must be INR or USD"})
            return
        done, skipped = self._set_costs(ids, cost, currency, actor)
        hub_event("supplier_cost", f"cost set on {len(done)} build(s)", actor, "supplier")
        self._json(200, {"ok": True, "updated": len(done), "skipped": skipped})

    # ------------------------------------------------------------------- bills
    def _next_bill_no(self, conn):
        stamp = time.strftime("%Y%m")
        n = conn.execute("SELECT COUNT(*) FROM supplier_bills "
                         "WHERE bill_no LIKE ?", (f"TLB-{stamp}-%",)).fetchone()[0]
        # Collisions are possible if a bill was deleted, so step past any
        # number already taken rather than trusting the count alone.
        while True:
            n += 1
            candidate = f"TLB-{stamp}-{n:03d}"
            if not conn.execute("SELECT 1 FROM supplier_bills WHERE bill_no=?",
                                (candidate,)).fetchone():
                return candidate

    def _handle_supplier_bills(self):
        """Every bill, newest first, with its lines. Both roles see this —
        it's the supplier's own pricing, not ours."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        bills = [dict(b) for b in conn.execute(
            "SELECT id, bill_no, created_at, status, currency, subtotal, "
            "shipping_cost, total, notes, acknowledged_at, acknowledged_by, "
            "tracking_code FROM supplier_bills ORDER BY id DESC LIMIT 100")]
        items = {}
        for it in conn.execute(
                "SELECT bi.id, bi.bill_id, bi.order_id, bi.description, bi.ref_code, "
                "bi.quantity, bi.cost, COALESCE(o.order_no, bi.order_id) AS order_no "
                "FROM supplier_bill_items bi LEFT JOIN orders o ON o.id=bi.order_id "
                "ORDER BY bi.id ASC"):
            items.setdefault(it["bill_id"], []).append(dict(it))
        conn.close()
        for b in bills:
            # The UI only needs a human label, never a staff email address.
            b["acknowledged_by"] = (b.get("acknowledged_by") or "").split("@", 1)[0]
            b["items"] = items.get(b["id"], [])
            b["total_inr"] = round(self._to_inr(b["total"], b["currency"]), 2)
        self._json(200, {"bills": bills, "can_acknowledge": self._has_tool("orders")})

    def _handle_supplier_bill_create(self):
        """Turn a selection of costed builds into a bill. Freight is one line
        on the bill rather than smeared across the builds, so per-watch cost
        stays the true unit cost."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        ids = [int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()][:200]
        if not ids:
            self._json(400, {"error": "pick at least one build"})
            return
        shipping = self._money_amount(p.get("shipping_cost") or 0)
        currency = self._money_currency(p.get("currency") or "INR")
        if shipping is None:
            self._json(400, {"error": "shipping needs to be a non-negative amount"})
            return
        if not currency:
            self._json(400, {"error": "currency must be INR or USD"})
            return
        notes = str(p.get("notes") or "").strip()[:400]

        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, product, movement, ref_code, quantity, status, "
            "supplier_cost, supplier_cost_ccy, bill_id "
            "FROM orders WHERE id IN ({}) AND supplier_visible=1".format(
                ",".join("?" * len(ids))), ids).fetchall()
        if len(rows) != len(ids):
            conn.close()
            self._json(409, {
                "error": "one or more selected builds left the supplier queue; refresh and try again"})
            return
        cancelled = [r["id"] for r in rows
                     if str(r["status"] or "").strip().lower() in
                     ("cancelled", "canceled", "refunded", "voided")]
        if cancelled:
            conn.close()
            self._json(409, {
                "error": "cancelled builds cannot be billed: "
                         + ", ".join(f"#{i}" for i in cancelled)})
            return
        already = [r["id"] for r in rows if r["bill_id"]]
        if already:
            conn.close()
            self._json(400, {"error": "already in a batch: "
                                      + ", ".join(f"#{i}" for i in already)})
            return

        # A build with no price set falls to its movement's default rather
        # than blocking the batch. The rate is written onto the line as a
        # real number, so a batch reads the same whether it was priced by
        # hand or by rule.
        priced, defaulted = [], 0
        for r in rows:
            cost = r["supplier_cost"]
            cost_currency = self._money_currency(r["supplier_cost_ccy"] or "INR")
            if cost is None:
                cost, label = self._default_cost(r["product"], r["movement"])
                cost_currency = "INR"
                defaulted += 1
            parsed_cost = self._money_amount(cost)
            if parsed_cost is None or not cost_currency:
                conn.close()
                self._json(409, {"error": f"build #{r['id']} has an invalid saved cost"})
                return
            if cost_currency != currency:
                conn.close()
                self._json(409, {
                    "error": (
                        f"build #{r['id']} is priced in {cost_currency}; "
                        f"set every selected build to {currency} before creating this bill"
                    )})
                return
            quantity = max(1, int(r["quantity"] or 1))
            priced.append((
                r, quantity, parsed_cost,
                label if r["supplier_cost"] is None else None,
            ))

        raw_shipment_id = p.get("shipment_id")
        shipment_id = None
        if raw_shipment_id not in (None, "", 0):
            try:
                shipment_id = int(raw_shipment_id)
            except (TypeError, ValueError):
                conn.close()
                self._json(400, {"error": "bad shipment"})
                return
            if not conn.execute("SELECT 1 FROM shipments WHERE id=?",
                                (shipment_id,)).fetchone():
                conn.close()
                self._json(404, {"error": "no such shipment"})
                return

        bill_no = self._next_bill_no(conn)
        for r, _, cost, default_label in priced:
            if default_label:
                conn.execute("UPDATE orders SET supplier_cost=?, supplier_cost_ccy='INR' "
                             "WHERE id=?", (cost, r["id"]))
                order_event(conn, r["id"], "cost",
                            f"default {default_label} rate applied: INR {cost:,.0f}", actor)

        subtotal = sum(quantity * cost for _, quantity, cost, _ in priced)
        total = subtotal + shipping
        cur = conn.execute(
            "INSERT INTO supplier_bills (bill_no, created_by, status, currency, "
            "subtotal, shipping_cost, total, shipment_id, notes) "
            "VALUES (?,?,'draft',?,?,?,?,?,?)",
            (bill_no, actor, currency, subtotal, shipping, total,
             shipment_id, notes))
        bill_id = cur.lastrowid
        for r, quantity, cost, _ in priced:
            conn.execute(
                "INSERT INTO supplier_bill_items (bill_id, order_id, description, "
                "ref_code, quantity, cost) VALUES (?,?,?,?,?,?)",
                (bill_id, r["id"], r["product"], r["ref_code"], quantity, cost))
            conn.execute("UPDATE orders SET bill_id=? WHERE id=?", (bill_id, r["id"]))
            order_event(conn, r["id"], "billed", f"added to batch {bill_no}", actor)
        conn.commit()
        conn.close()
        hub_event("supplier_bill", f"{bill_no} drafted — {currency} {total:,.2f} "
                                   f"across {len(rows)} build(s)", actor, "supplier")
        self._json(200, {"ok": True, "bill_id": bill_id, "bill_no": bill_no,
                         "subtotal": subtotal, "shipping_cost": shipping,
                         "total": total, "defaulted": defaulted})

    def _handle_supplier_bill_update(self):
        """Edit a draft bill until staff acknowledge it or any payment clears
        — the same envelope `_handle_supplier_bill_create` requires to make
        one in the first place. Re-pricing lines updates the corresponding
        order costs as well, so the queue and bill can never show two
        different agreed figures.

        Membership is editable too, not just price: `remove_order_ids` takes
        a build off the batch (it returns to the unbilled queue, free to
        join another), `add_ids` puts one on (same eligibility as creating a
        batch — supplier-visible, not cancelled, not already on a bill, and
        priced in this bill's own currency or defaulted the same way create
        does). A batch can't be edited down to zero items; delete the whole
        batch for that."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
            bid = int(p.get("id"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        shipping = self._money_amount(p.get("shipping_cost") or 0)
        if shipping is None:
            self._json(400, {"error": "shipping needs to be a non-negative amount"})
            return
        notes = str(p.get("notes") or "").strip()[:400]
        costs = p.get("costs") or {}
        if not isinstance(costs, dict):
            costs = {}
        remove_ids = [int(i) for i in (p.get("remove_order_ids") or [])
                     if str(i).strip().lstrip("-").isdigit()]
        add_ids = [int(i) for i in (p.get("add_ids") or [])
                  if str(i).strip().isdigit()][:200]
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        bill = conn.execute("SELECT status, currency, bill_no FROM supplier_bills WHERE id=?",
                            (bid,)).fetchone()
        if not bill:
            conn.rollback()
            conn.close()
            self._json(404, {"error": "no such batch"})
            return
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE bill_id=?",
            (bid,)).fetchone()[0]
        if bill["status"] != "draft" or float(paid or 0) > 0:
            conn.rollback()
            conn.close()
            self._json(409, {"error": "accepted or paid batches cannot be edited"})
            return

        items = list(conn.execute(
            "SELECT id, order_id, quantity, cost FROM supplier_bill_items WHERE bill_id=?",
            (bid,)).fetchall())

        if remove_ids:
            kept = [it for it in items if it["order_id"] not in remove_ids]
            if not kept and not add_ids:
                conn.rollback()
                conn.close()
                self._json(409, {
                    "error": "A batch needs at least one build — delete the whole "
                             "batch instead of removing its last item."})
                return
            for it in items:
                if it["order_id"] in remove_ids:
                    conn.execute("DELETE FROM supplier_bill_items WHERE id=?", (it["id"],))
                    conn.execute("UPDATE orders SET bill_id=NULL WHERE id=?", (it["order_id"],))
                    order_event(conn, it["order_id"], "billed",
                               f"removed from batch {bill['bill_no']}", actor)
            items = kept

        if add_ids:
            new_rows = conn.execute(
                "SELECT id, product, movement, ref_code, quantity, status, "
                "supplier_cost, supplier_cost_ccy, bill_id FROM orders "
                "WHERE id IN ({}) AND supplier_visible=1".format(
                    ",".join("?" * len(add_ids))), add_ids).fetchall()
            if len(new_rows) != len(add_ids):
                conn.rollback()
                conn.close()
                self._json(409, {
                    "error": "one or more builds to add left the supplier queue; "
                             "refresh and try again"})
                return
            already = [r["id"] for r in new_rows if r["bill_id"] and r["bill_id"] != bid]
            if already:
                conn.rollback()
                conn.close()
                self._json(400, {"error": "already in another batch: "
                                          + ", ".join(f"#{i}" for i in already)})
                return
            cancelled = [r["id"] for r in new_rows
                        if str(r["status"] or "").strip().lower() in
                        ("cancelled", "canceled", "refunded", "voided")]
            if cancelled:
                conn.rollback()
                conn.close()
                self._json(409, {"error": "cancelled builds cannot be billed: "
                                          + ", ".join(f"#{i}" for i in cancelled)})
                return
            for r in new_rows:
                if r["bill_id"] == bid:
                    continue   # already on this exact batch — a harmless re-add
                cost = r["supplier_cost"]
                cost_currency = self._money_currency(r["supplier_cost_ccy"] or "INR")
                default_label = None
                if cost is None:
                    cost, default_label = self._default_cost(r["product"], r["movement"])
                    cost_currency = "INR"
                parsed_cost = self._money_amount(cost)
                if parsed_cost is None or not cost_currency:
                    conn.rollback()
                    conn.close()
                    self._json(409, {"error": f"build #{r['id']} has an invalid saved cost"})
                    return
                if cost_currency != bill["currency"]:
                    conn.rollback()
                    conn.close()
                    self._json(409, {
                        "error": f"build #{r['id']} is priced in {cost_currency}; "
                                 f"this batch is in {bill['currency']}"})
                    return
                if default_label:
                    conn.execute("UPDATE orders SET supplier_cost=?, supplier_cost_ccy='INR' "
                                "WHERE id=?", (parsed_cost, r["id"]))
                    order_event(conn, r["id"], "cost",
                               f"default {default_label} rate applied: INR "
                               f"{parsed_cost:,.0f}", actor)
                quantity = max(1, int(r["quantity"] or 1))
                conn.execute(
                    "INSERT INTO supplier_bill_items (bill_id, order_id, description, "
                    "ref_code, quantity, cost) VALUES (?,?,?,?,?,?)",
                    (bid, r["id"], r["product"], r["ref_code"], quantity, parsed_cost))
                conn.execute("UPDATE orders SET bill_id=? WHERE id=?", (bid, r["id"]))
                order_event(conn, r["id"], "billed", f"added to batch {bill['bill_no']}", actor)
            items = list(conn.execute(
                "SELECT id, order_id, quantity, cost FROM supplier_bill_items WHERE bill_id=?",
                (bid,)).fetchall())

        subtotal = 0.0
        prepared = []
        for item in items:
            raw = costs.get(str(item["id"]), item["cost"])
            cost = self._money_amount(raw)
            if cost is None:
                conn.rollback()
                conn.close()
                self._json(400, {"error": "every line needs a valid cost"})
                return
            subtotal += cost * max(1, int(item["quantity"] or 1))
            prepared.append((item, cost))
        for item, cost in prepared:
            if cost == item["cost"]:
                continue
            conn.execute("UPDATE supplier_bill_items SET cost=? WHERE id=?",
                         (cost, item["id"]))
            conn.execute("UPDATE orders SET supplier_cost=?, supplier_cost_ccy=? WHERE id=?",
                         (cost, bill["currency"], item["order_id"]))
            order_event(conn, item["order_id"], "cost",
                        f"bill line updated to {bill['currency']} {cost:,.2f}", actor)
        total = subtotal + shipping
        conn.execute("UPDATE supplier_bills SET subtotal=?, shipping_cost=?, total=?, notes=? "
                     "WHERE id=?", (subtotal, shipping, total, notes, bid))
        conn.commit()
        conn.close()
        if remove_ids or add_ids:
            hub_event("supplier_bill",
                     f"{bill['bill_no']} membership changed"
                     f"{' +' + str(len(add_ids)) if add_ids else ''}"
                     f"{' -' + str(len(remove_ids)) if remove_ids else ''}",
                     actor, "supplier")
        self._json(200, {"ok": True, "id": bid, "subtotal": subtotal, "total": total,
                         "items": len(items)})

    def _handle_supplier_bill_whatsapp(self):
        """Post a batch's bill straight into the team WhatsApp, PDF attached.

        This is how a batch actually reaches the other side: the supplier
        raises it, taps send, and it lands in the group everyone already
        watches. Sending happens on a background thread for the same reason
        the status share does — `hermes send` with an attachment takes long
        enough that holding the response open makes the button look dead."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            bid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        from_name = str(p.get("from") or "Supplier").strip()[:80]

        target = ""
        try:
            with open("/root/ops-dashboard/.env") as f:
                for line in f:
                    if line.startswith("WHATSAPP_ORDER_TARGET="):
                        target = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
        if not target:
            self._json(400, {"error": "No WhatsApp destination is set up yet."})
            return

        conn = db()
        bill = conn.execute("SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        n = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM supplier_bill_items WHERE bill_id=?",
            (bid,)).fetchone()[0]
        conn.close()
        if not bill:
            self._json(404, {"error": "no such batch"})
            return

        ccy = "\u20b9" if (bill["currency"] or "INR").upper() == "INR" else \
            (bill["currency"] or "") + " "
        text = (f"*{bill['bill_no']}* — {n} build(s)\n"
                f"Total {ccy}{bill['total']:,.2f}\n"
                f"Status: {'agreed' if bill['status'] == 'acknowledged' else 'awaiting your OK'}")

        def _deliver():
            body = text
            try:
                pdf = self._bill_pdf_bytes(bid, from_name)
                if pdf:
                    path = os.path.join(UPLOAD_DIR, f"{bill['bill_no']}.pdf")
                    with open(path, "wb") as f:
                        f.write(pdf)
                    body = f"MEDIA:{path}\n{text}"
            except Exception as e:
                print(f"[batch-wa] PDF skipped: {e}", flush=True)
            try:
                r = subprocess.run(["hermes", "send", "--to", target, "--quiet", body],
                                   capture_output=True, text=True, timeout=180)
                ok = r.returncode == 0
            except Exception as e:
                ok, r = False, None
                print(f"[batch-wa] {e}", flush=True)
            hub_event("supplier_bill" if ok else "supplier_bill_failed",
                      f"{bill['bill_no']} sent to WhatsApp" if ok
                      else f"{bill['bill_no']} WhatsApp send failed", actor, "supplier")

        threading.Thread(target=_deliver, daemon=True).start()
        self._json(200, {"ok": True, "queued": True})

    def _handle_supplier_bill_tracking(self):
        """One courier reference for a whole batch. Editable after
        acknowledgement on purpose — the money is settled at that point but
        the box may not have shipped yet, and a tracking number is a fact
        about the parcel, not about what's owed."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            bid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        code = str(p.get("tracking_code") or "").strip()[:80]
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT bill_no FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        if not row:
            conn.rollback()
            conn.close()
            self._json(404, {"error": "no such batch"})
            return
        conn.execute("UPDATE supplier_bills SET tracking_code=? WHERE id=?",
                     (code or None, bid))
        for oid in [r["order_id"] for r in conn.execute(
                "SELECT order_id FROM supplier_bill_items WHERE bill_id=?", (bid,))
                if r["order_id"]]:
            order_event(conn, oid, "tracking",
                        f"batch {row['bill_no']} tracking: {code or 'cleared'}", actor)
        conn.commit()
        conn.close()
        self._json(200, {"ok": True})

    def _ledger_supplier_id(self, lconn, schema="main"):
        """Hannan builds the watches; the 31 invoices already in the Ledger
        are parts from a different company in China. Keeping him as his own
        supplier row is what stops build spend and parts spend from being
        averaged into one meaningless per-supplier number."""
        prefix = "ledger." if schema == "ledger" else ""
        row = lconn.execute(f"SELECT id FROM {prefix}suppliers WHERE name=?",
                            ("TimeLabsCo x Sunesra",)).fetchone()
        if row:
            return row[0]
        cur = lconn.execute(
            f"INSERT INTO {prefix}suppliers "
            "(name, country, platform, default_currency, notes) "
            "VALUES (?,?,?,?,?)",
            ("TimeLabsCo x Sunesra", "India", "whatsapp", "USD",
             "Build partner (Hannan) — assembles the watches. Invoices here are "
             "written through from acknowledged bills in the supplier build queue."))
        return cur.lastrowid

    def _handle_supplier_bill_acknowledge(self):
        """Acknowledging is the moment a bill becomes real: the costs freeze,
        the Ledger gets an invoice, and this is what counts toward what we
        owe. Admin/orders only — the supplier can't sign off their own bill,
        the same boundary that already applies to recording a payment."""
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            bid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        conn = db()
        invoice_id = None
        try:
            conn.execute("ATTACH DATABASE ? AS ledger", (SUPPLIERS_DB,))
            conn.execute("BEGIN IMMEDIATE")
            bill = conn.execute(
                "SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
            if not bill:
                conn.rollback()
                conn.close()
                self._json(404, {"error": "no such bill"})
                return
            if bill["status"] == "acknowledged":
                conn.rollback()
                conn.close()
                self._json(400, {"error": "already acknowledged"})
                return
            items = conn.execute(
                "SELECT * FROM supplier_bill_items WHERE bill_id=?",
                (bid,)).fetchall()
            rate = (
                self._USD_INR
                if (bill["currency"] or "USD").upper() == "USD"
                else 1.0
            )
            sup_id = self._ledger_supplier_id(conn, "ledger")
            cur = conn.execute(
                "INSERT INTO ledger.invoices "
                "(supplier_id, invoice_no, order_date, currency, subtotal, "
                "shipping_cost, discount, total, exchange_rate, total_inr, "
                "payment_status, parsed_by, notes) "
                "VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?)",
                (sup_id, bill["bill_no"], time.strftime("%Y-%m-%d"),
                 bill["currency"], bill["subtotal"], bill["shipping_cost"],
                 bill["total"], rate, bill["total"] * rate, "unpaid",
                 "supplier-queue", f"Acknowledged by {actor}"))
            invoice_id = cur.lastrowid
            for it in items:
                quantity = max(1, int(it["quantity"] or 1))
                conn.execute(
                    "INSERT INTO ledger.invoice_items "
                    "(invoice_id, description_raw, description_en, part_category, "
                    "quantity, unit_price, line_total, unit_price_inr) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (invoice_id, it["description"], it["description"], "build",
                     quantity, it["cost"], (it["cost"] or 0) * quantity,
                     (it["cost"] or 0) * rate))
            conn.execute("UPDATE supplier_bills SET status='acknowledged', "
                         "acknowledged_at=datetime('now'), acknowledged_by=?, "
                         "ledger_invoice_id=? WHERE id=?", (actor, invoice_id, bid))
            for it in items:
                if it["order_id"]:
                    order_event(conn, it["order_id"], "bill_acknowledged",
                                f"bill {bill['bill_no']} acknowledged — cost locked", actor)
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            print(f"[supplier-bill] atomic acknowledge failed for bill {bid}: {e}",
                  flush=True)
            self._json(500, {
                "error": "The bill and Ledger were not changed. Try acknowledge again."})
            return
        conn.close()
        hub_event("supplier_bill", f"{bill['bill_no']} acknowledged — "
                                   f"{bill['currency']} {bill['total']:,.2f} to Ledger",
                  actor, "supplier")
        self._json(200, {"ok": True, "ledger_invoice_id": invoice_id})

    def _handle_supplier_bill_delete(self):
        """The escape hatch the owner asked for. A bill raised in error has to
        be removable even after acknowledgement, which means undoing all three
        of its effects — the Ledger invoice, the lock on the builds, and the
        debt — not just hiding the row."""
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            bid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        conn = db()
        try:
            conn.execute("ATTACH DATABASE ? AS ledger", (SUPPLIERS_DB,))
            conn.execute("BEGIN IMMEDIATE")
            bill = conn.execute(
                "SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
            if not bill:
                conn.rollback()
                conn.close()
                self._json(404, {"error": "no such bill"})
                return
            if bill["ledger_invoice_id"]:
                conn.execute(
                    "DELETE FROM ledger.invoice_items WHERE invoice_id=?",
                    (bill["ledger_invoice_id"],))
                conn.execute(
                    "DELETE FROM ledger.invoices WHERE id=?",
                    (bill["ledger_invoice_id"],))
            items = conn.execute(
                "SELECT order_id FROM supplier_bill_items WHERE bill_id=?",
                (bid,)).fetchall()
            for it in items:
                if it["order_id"]:
                    conn.execute(
                        "UPDATE orders SET bill_id=NULL WHERE id=?",
                        (it["order_id"],))
                    order_event(
                        conn, it["order_id"], "bill_deleted",
                        f"bill {bill['bill_no']} deleted — cost editable again",
                        actor)
            # Payments pointed at this bill go back to being general credit
            # rather than vanishing with it — the money was still sent.
            conn.execute("UPDATE supplier_payments SET bill_id=NULL WHERE bill_id=?", (bid,))
            conn.execute("DELETE FROM supplier_bill_items WHERE bill_id=?", (bid,))
            conn.execute("DELETE FROM supplier_bills WHERE id=?", (bid,))
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            print(f"[supplier-bill] atomic delete failed for bill {bid}: {e}",
                  flush=True)
            self._json(500, {
                "error": "The bill and Ledger were not changed. Try delete again."})
            return
        conn.close()
        hub_event("supplier_bill", f"{bill['bill_no']} deleted and reversed", actor, "supplier")
        self._json(200, {"ok": True})

    def _handle_supplier_arrears(self):
        """What's currently owed to the supplier, and the bill-by-bill
        breakdown behind it. 'Owed' = every acknowledged bill minus every
        payment recorded against it, whether tied to one bill or left
        general — a running balance, not a paid/unpaid flag, because partial
        payments are real here. Draft bills deliberately don't count: nothing
        is owed until it's been agreed."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        try:
            bills = conn.execute(
                "SELECT id, bill_no, currency, total, acknowledged_at FROM supplier_bills "
                "WHERE status='acknowledged' ORDER BY id DESC").fetchall()
            pays = conn.execute(
                "SELECT bill_id, amount, currency FROM supplier_payments").fetchall()
            draft_rows = conn.execute(
                "SELECT total, currency FROM supplier_bills "
                "WHERE status='draft'").fetchall()
            # What's been costed but not yet put on any bill at all — the
            # gap between work priced and money actually claimed. Summed
            # through _to_inr rather than in SQL, because a plain SUM() would
            # silently add dollars to rupees.
            unbilled_rows = conn.execute(
                "SELECT supplier_cost, supplier_cost_ccy, quantity FROM orders "
                "WHERE supplier_visible=1 AND bill_id IS NULL "
                "AND supplier_cost IS NOT NULL "
                "AND LOWER(TRIM(COALESCE(status,''))) NOT IN "
                "('cancelled','canceled','refunded','voided')").fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()

        unbilled_inr = sum(
            self._to_inr(
                r["supplier_cost"] * max(1, int(r["quantity"] or 1)),
                r["supplier_cost_ccy"])
            for r in unbilled_rows)
        draft_total_inr = sum(
            self._to_inr(r["total"], r["currency"]) for r in draft_rows)
        paid_by_bill, unallocated_inr = {}, 0.0
        for p in pays:
            inr = self._to_inr(p["amount"], p["currency"])
            if p["bill_id"] is None:
                unallocated_inr += inr
            else:
                paid_by_bill[p["bill_id"]] = paid_by_bill.get(p["bill_id"], 0.0) + inr

        out_bills, total_cost_inr, total_paid_inr = [], 0.0, 0.0
        for b in bills:
            cost_inr = self._to_inr(b["total"], b["currency"])
            paid_inr = paid_by_bill.get(b["id"], 0.0)
            total_cost_inr += cost_inr
            total_paid_inr += paid_inr
            out_bills.append({
                "id": b["id"], "bill_no": b["bill_no"], "total": b["total"],
                "currency": b["currency"], "acknowledged_at": b["acknowledged_at"],
                "cost_inr": round(cost_inr, 2), "paid_inr": round(paid_inr, 2),
                "balance_inr": round(cost_inr - paid_inr, 2)})
        total_paid_inr += unallocated_inr
        self._json(200, {
            "owed_inr": round(total_cost_inr - total_paid_inr, 2),
            "total_cost_inr": round(total_cost_inr, 2),
            "total_paid_inr": round(total_paid_inr, 2),
            "unallocated_paid_inr": round(unallocated_inr, 2),
            "draft_total": round(draft_total_inr, 2),
            "draft_count": len(draft_rows),
            "unbilled_cost_inr": round(unbilled_inr, 2),
            "bills": out_bills})

    def _handle_supplier_payment_record(self):
        """Log a payment toward what's owed. Admin/orders-role only — the
        supplier shouldn't be the one who can mark their own bill paid; they
        can see the balance and generate the bill, but recording that money
        actually moved is the business's side to confirm."""
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        amount = self._money_amount(p.get("amount"), positive=True)
        if amount is None:
            self._json(400, {"error": "a payment amount is needed"})
            return
        currency = self._money_currency(p.get("currency") or "INR")
        if not currency:
            self._json(400, {"error": "currency must be INR or USD"})
            return
        note = str(p.get("note", "")).strip()[:300]
        # Payments settle a bill now that bills are what create the debt.
        # Leaving it unset is still valid — money sent on account, before
        # anyone has agreed which bill it belongs to.
        raw_bid = p.get("bill_id")
        bill_id, code = None, None
        if raw_bid not in (None, "", 0):
            try:
                bill_id = int(raw_bid)
            except (TypeError, ValueError):
                self._json(400, {"error": "bad bill"})
                return
        conn = db()
        try:
            # Bill deletion also takes an immediate writer lock. Hold the same
            # lock from bill validation through the payment and audit events,
            # otherwise a concurrent delete can leave this money pointing at
            # a bill that no longer exists (foreign keys are legacy-off here).
            conn.execute("BEGIN IMMEDIATE")
            if bill_id is not None:
                row = conn.execute(
                    "SELECT bill_no FROM supplier_bills WHERE id=?",
                    (bill_id,)).fetchone()
                if not row:
                    conn.rollback()
                    self._json(404, {"error": "no such bill"})
                    return
                code = row["bill_no"]
            cur = conn.execute(
                "INSERT INTO supplier_payments (bill_id, amount, currency, note, actor) "
                "VALUES (?,?,?,?,?)", (bill_id, amount, currency, note, actor))
            pid = cur.lastrowid
            if bill_id:
                for oid in [r["order_id"] for r in conn.execute(
                        "SELECT order_id FROM supplier_bill_items WHERE bill_id=?",
                        (bill_id,)) if r["order_id"]]:
                    order_event(
                        conn, oid, "payment",
                        f"{currency} {amount:.2f} recorded against {code}", actor)
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            print(f"[supplier-payment] record failed: {e}", flush=True)
            self._json(500, {
                "error": "The payment was not recorded. Try again."})
            return
        finally:
            conn.close()
        hub_event("supplier_payment", f"{currency} {amount:.2f}" +
                 (f" -> bill {code}" if bill_id else " (on account)"),
                 actor, app="orders")
        self._json(200, {"ok": True, "id": pid})

    def _handle_supplier_tracking(self):
        """Courier reference for a build. Lives on the order rather than in a
        note so it can be shown as a copyable field and, later, looked up —
        a tracking number buried in free text is findable by a human and by
        nothing else."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 2048)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        code = str(p.get("tracking_code", "")).strip()[:80]
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT tracking_code, order_no FROM orders "
            "WHERE id=? AND supplier_visible=1",
                          (oid,)).fetchone()
        if not row:
            conn.rollback()
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        was = row["tracking_code"] or ""
        conn.execute("UPDATE orders SET tracking_code=? WHERE id=?", (code or None, oid))
        if code != was:
            order_event(conn, oid, "tracking",
                       (f"tracking {code}" if code else "tracking cleared"), actor)
        conn.commit()
        conn.close()
        hub_event("order_tracking", f"#{row['order_no'] or oid}: {code or 'cleared'}",
                  actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "tracking_code": code})

    def _status_card(self, conn, oid):
        """The shareable summary of one build. Same shape whether it ends up
        as copied text, a WhatsApp message or a PDF, so the three can't drift
        into saying different things about the same order."""
        o = conn.execute(
            "SELECT id, order_no, received_at, product, quantity, status, tracking_code, "
            "ref_code, "
            "case_style, dial_colour, dial_style, case_colour, movement, watch_size "
            "FROM orders WHERE id=? AND supplier_visible=1", (oid,)).fetchone()
        if not o:
            return None, None
        spec = " · ".join(str(o[k]) for k in
                          ("case_style", "dial_colour", "dial_style", "case_colour",
                           "movement", "watch_size") if o[k])
        safe_event_kinds = (
            "created", "status", "supplier", "photos", "cost", "billed",
            "bill_acknowledged", "bill_deleted", "tracking", "payment",
            "shipment", "note",
        )
        marks = ",".join("?" for _ in safe_event_kinds)
        events = conn.execute(
            "SELECT created_at, kind, detail FROM order_events "
            f"WHERE order_id=? AND kind IN ({marks}) ORDER BY id ASC",
            (oid, *safe_event_kinds)).fetchall()
        # Lead with the original order number when there is one — that's the
        # number the supplier already knows the build by.
        labs_num = o["order_no"] or o["id"]
        head_num = (
            f"Order {o['ref_code']} · Labs #{labs_num}"
            if o["ref_code"] else f"Order #{labs_num}"
        )
        lines = [f"{head_num} — {o['product'] or ''}"]
        if o["quantity"] and o["quantity"] > 1:
            lines.append(f"Quantity: {o['quantity']}")
        if spec:
            lines.append(f"Spec: {spec}")
        lines.append(f"Status: {o['status']}")
        if o["tracking_code"]:
            lines.append(f"Tracking: {o['tracking_code']}")
        hist = [f"  {e['created_at'][:16]} — " +
                (e["detail"] if e["kind"] != "note" else f"note: {e['detail']}")
                for e in events]
        if hist:
            lines.append("History:")
            lines.extend(hist)
        return dict(o), "\n".join(lines)

    def _photo_data_uri(self, oid, max_px=560):
        """First reference photo as an inline data: URI, downscaled.

        Embedding beats linking here: the PDF gets forwarded on — to a
        supplier's own supplier — and a link back to this server would 404 for
        anyone without an account, which is the one context where the image
        matters most. Downscaled because a 4MB phone photo per row makes a
        document nobody can email."""
        conn = db()
        names = self._photo_names(conn, oid, supplier_only=True)
        conn.close()
        if not names:
            return None
        path = os.path.join(ORDER_PHOTOS, str(oid), os.path.basename(names[0]))
        if not os.path.isfile(path):
            return None
        import base64
        try:
            # Pillow is present on the interpreter this server runs on, so no
            # subprocess is needed. (An earlier version shelled out to the
            # wrong venv, which has no PIL — it failed silently and produced
            # PDFs with no photo in them, which is why this now falls back
            # rather than returning None on any error.)
            import io
            from PIL import Image
            im = Image.open(path)
            im.thumbnail((max_px, max_px))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            print(f"[pdf-photo] resize skipped ({e}) — embedding the original", flush=True)
        try:
            # Worst case, embed the file as-is: a heavier PDF still beats one
            # with the reference photo missing.
            with open(path, "rb") as f:
                data = f.read(6 * 1024 * 1024)
            ext = os.path.splitext(path)[1].lower()
            mime = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
            return f"data:{mime};base64," + base64.b64encode(data).decode()
        except OSError as e:
            print(f"[pdf-photo] {e}", flush=True)
            return None

    def _handle_supplier_bill(self, query):
        """One bill as a PDF, for the supplier to send us and for our own
        records. Deliberately plain — no GST/GSTIN/HSN, no tax breakdown;
        this is a working-arrangement payment request, not a compliance
        document (the owner's explicit call).

        Figures come from the bill's own stored line items rather than being
        recomputed from the builds, so a PDF printed today and one printed
        after an acknowledged bill's underlying order was edited say exactly
        the same thing.
        """
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        from_name = urllib.parse.unquote(params.get("from", "")).strip()[:80] or "Supplier"
        try:
            bid = int(params.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        conn = db()
        exists = conn.execute("SELECT bill_no FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        conn.close()
        if not exists:
            self._json(404, {"error": "no such batch"})
            return
        try:
            pdf = self._bill_pdf_bytes(bid, from_name)
        except Exception as e:
            self._json(500, {"error": f"couldn't build the PDF: {e}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{exists["bill_no"]}.pdf"')
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    def _bill_pdf_bytes(self, bid, from_name="Supplier"):
        """The bill as PDF bytes. Shared by the download and the WhatsApp
        send so the document a supplier posts to the group is byte-for-byte
        the one we'd print ourselves."""
        conn = db()
        bill = conn.execute("SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        if not bill:
            conn.close()
            return None
        items = conn.execute(
            "SELECT bi.*, COALESCE(o.order_no, bi.order_id) AS order_no "
            "FROM supplier_bill_items bi LEFT JOIN orders o ON o.id=bi.order_id "
            "WHERE bi.bill_id=? ORDER BY bi.id ASC", (bid,)).fetchall()
        payments = conn.execute(
            "SELECT amount, currency FROM supplier_payments WHERE bill_id=?",
            (bid,)).fetchall()
        conn.close()

        raw_ccy = (bill["currency"] or "INR").upper()
        paid = sum(self._convert_money(p["amount"], p["currency"], raw_ccy)
                   for p in payments)
        # A rupee sign reads as money; "INR 12,500.00" reads as a database row.
        ccy = "\u20b9" if raw_ccy == "INR" else html_mod.escape(raw_ccy) + " "
        rows = ""
        for it in items:
            quantity = max(1, int(it["quantity"] or 1))
            line_total = (it["cost"] or 0) * quantity
            label = it["ref_code"] or (f"#{it['order_no']}" if it["order_no"] else "")
            # The photo is the spec on these builds — a line that just says
            # "White RM mod" doesn't tell anyone which watch was billed, and
            # this PDF gets forwarded to people who never saw the queue.
            shot = self._photo_data_uri(it["order_id"], 200) if it["order_id"] else None
            cell = ('<td class="shot">'
                    + (f'<img src="{shot}">' if shot else '<span class="noshot"></span>')
                    + '</td>')
            rows += ('<tr>' + cell + '<td><b>' + html_mod.escape(str(label)) + '</b>'
                     + (f'<br><span class="sub">{html_mod.escape(it["description"] or "")}</span>'
                        if it["description"] else "")
                     + (f'<br><span class="sub">{quantity} watches</span>'
                        if quantity != 1 else "")
                     + '</td>'
                     + f'<td class="n">{ccy}{it["cost"]:,.2f}'
                     + (f'<br><b>{ccy}{line_total:,.2f}</b>' if quantity != 1 else "")
                     + '</td></tr>')
        if bill["shipping_cost"]:
            rows += ('<tr><td class="shot"></td><td>Shipping</td>'
                     f'<td class="n">{ccy}{bill["shipping_cost"]:,.2f}</td></tr>')
        paid_row = ""
        if paid:
            paid_row = (f'<tr class="credit"><td class="shot"></td><td>Already paid</td>'
                        f'<td class="n">-{ccy}{paid:,.2f}</td></tr>')
        due = (bill["total"] or 0) - paid

        import datetime as _dt
        ack = ""
        if bill["status"] == "acknowledged":
            ack = (f'<div class="ack">Acknowledged {(bill["acknowledged_at"] or "")[:10]}'
                   f' by {html_mod.escape((bill["acknowledged_by"] or "").split("@")[0])}</div>')
        doc = (
            "<html><head><meta charset='utf-8'><style>"
            "@page{size:A4;margin:1.9cm 1.7cm;}"
            "body{font-family:Helvetica,Arial,sans-serif;font-size:10.5pt;color:#1a1f1b;}"
            ".mark{font-size:7.5pt;letter-spacing:2.5pt;text-transform:uppercase;color:#8a6a2c;}"
            "h1{font-size:17pt;margin:3pt 0 2pt;}"
            ".meta{color:#777;font-size:9pt;margin-bottom:22pt;}"
            ".ack{display:inline-block;font-size:8.5pt;color:#3f7d4f;border:1pt solid #cfe3d4;"
            "background:#f2f9f4;padding:3pt 8pt;border-radius:3pt;margin-bottom:14pt;}"
            "table{width:100%;border-collapse:collapse;margin-top:6pt;}"
            "th{text-align:left;font-size:8pt;text-transform:uppercase;letter-spacing:.5pt;"
            "color:#777;border-bottom:1pt solid #999;padding:0 8pt 6pt;}"
            "td{padding:9pt 8pt;border-bottom:1pt solid #eee;vertical-align:top;}"
            "td.n{text-align:right;font-variant-numeric:tabular-nums;}"
            "td.shot,th.shot{width:56pt;padding-right:0;}"
            "td.shot img{width:48pt;height:48pt;object-fit:cover;border-radius:3pt;display:block;}"
            ".sub{color:#888;font-size:8.5pt;}"
            "tr.credit td{color:#3f7d4f;font-style:italic;}"
            ".total-row td{border-top:1.5pt solid #1a1f1b;border-bottom:none;padding-top:12pt;"
            "font-size:13pt;font-weight:bold;}"
            ".stamp{color:#888;font-size:8pt;margin-top:26pt;border-top:1pt solid #ddd;padding-top:7pt;}"
            "</style></head><body>"
            f"<div class='mark'>{html_mod.escape(from_name)}</div>"
            f"<h1>Bill {html_mod.escape(bill['bill_no'])}</h1>"
            f"<div class='meta'>To Timelabs Co &middot; "
            f"{_dt.datetime.now().strftime('%d %b %Y')} &middot; "
            f"{len(items)} build(s)</div>"
            + ack +
            "<table><thead><tr><th class='shot'></th><th>Build</th>"
            "<th class='n'>Cost</th></tr></thead><tbody>"
            + rows + paid_row
            + f"<tr class='total-row'><td class='shot'></td>"
              f"<td>{'Balance due' if paid else 'Total due'}</td>"
              f"<td class='n'>{ccy}{due:,.2f}</td></tr>"
            "</tbody></table>"
            + (f"<p class='sub'>{html_mod.escape(bill['notes'])}</p>" if bill["notes"] else "")
            + "<div class='stamp'>Generated from the Timelabs build queue &middot; "
            f"{_dt.datetime.now().strftime('%d %b %Y, %H:%M')}</div>"
            "</body></html>")
        from weasyprint import HTML
        return HTML(string=doc).write_pdf()

    # ------------------------------------------------------------- reddit
    def _handle_reddit_threads(self, query):
        """Read-only list of threads the Listener has already fetched, plus
        which source is currently live — 'rss' still means the Listener
        works (Reddit's public feed, no score/comment counts), 'oauth' means
        the full API is connected. Only an actual fetch failure should read
        as broken, not the RSS fallback being active."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p) if query else {}
        tag = urllib.parse.unquote(params.get("tag", "")).strip()
        group_sql = ("CASE WHEN lower(subreddit)='indiawatchmods' THEN 'home' "
                     "WHEN lower(subreddit) IN ('watchesindia','watchenthusiastindia',"
                     "'watchcollectorsindia','indiawatchexchange') THEN 'india' "
                     "ELSE 'global' END")
        sql = ("WITH candidates AS (SELECT *, " + group_sql + " AS group_key, "
               "(opportunity_score - "
               "MAX(0,(strftime('%s','now')-created_utc)/86400.0)*0.035) AS rank_score "
               "FROM reddit_threads WHERE created_utc >= "
               "CAST(strftime('%s','now','-14 days') AS INTEGER) AND "
               "(opportunity_score >= 0.55 OR created_utc >= "
               "CAST(strftime('%s','now','-2 days') AS INTEGER) OR "
               "lower(subreddit) IN ('indiawatchmods','watchesindia',"
               "'watchenthusiastindia','watchcollectorsindia','indiawatchexchange'))")
        args = []
        if tag and tag != "all":
            sql += " AND tag=?"
            args.append(tag)
        # Reserve space for our own and India-specific communities instead of
        # letting high-volume r/Watches crowd them out. Within each group,
        # useful matches decay with age; the multi-year archive never enters.
        sql += (") , ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY group_key "
                "ORDER BY rank_score DESC, created_utc DESC) AS group_rank "
                "FROM candidates) SELECT * FROM ranked WHERE "
                "(group_key='home' AND group_rank<=50) OR "
                "(group_key='india' AND group_rank<=120) OR "
                "(group_key='global' AND group_rank<=120) "
                "ORDER BY CASE group_key WHEN 'home' THEN 0 WHEN 'india' THEN 1 ELSE 2 END, "
                "rank_score DESC, created_utc DESC")
        conn = db()
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
        import reddit_api
        self._json(200, {"mode": reddit_api.mode(), "window_days": 14,
                         "selection": "fresh, India-relevant, or strong reply fit",
                         "threads": rows})

    def _handle_reddit_posts(self):
        """Drafts for our own subreddit, newest first, with pipeline state."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM reddit_posts ORDER BY id DESC LIMIT 50")]
        conn.close()
        for r in rows:
            try:
                photos = json.loads(r.get("photos") or "[]")
            except (json.JSONDecodeError, TypeError):
                photos = []
            # Never expose local filesystem paths to the browser. Each URL is
            # served through the same content-role check as the drafts list.
            r["photos"] = [
                f"/ops/agent/api/reddit/post/photo?id={r['id']}&n={index}"
                for index, _ in enumerate(photos)
            ]
            try:
                r["passes"] = json.loads(r.get("passes") or "{}")
            except (json.JSONDecodeError, TypeError):
                r["passes"] = {}
        self._json(200, {"posts": rows, "subreddit": "IndiaWatchMods"})

    def _handle_reddit_post_photo(self, query):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        params = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
        try:
            post_id = int(params.get("id") or 0)
            index = int(params.get("n") or 0)
        except (TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        row = conn.execute(
            "SELECT photos FROM reddit_posts WHERE id=?", (post_id,)).fetchone()
        conn.close()
        if not row:
            self._json(404, {"error": "no such draft"})
            return
        try:
            photos = json.loads(row["photos"] or "[]")
        except (json.JSONDecodeError, TypeError):
            photos = []
        if not (0 <= index < len(photos)):
            self._json(404, {"error": "no such photo"})
            return
        path = os.path.realpath(str(photos[index]))
        post_root = os.path.realpath(
            os.path.join(REDDIT_MEDIA, str(post_id))) + os.sep
        if (not path.startswith(post_root) or not os.path.isfile(path)
                or os.path.splitext(path)[1].lower()
                not in (".jpg", ".jpeg", ".png", ".webp")):
            self._json(404, {"error": "photo missing"})
            return
        try:
            with open(path, "rb") as source:
                data = source.read()
        except OSError:
            self._json(404, {"error": "photo unreadable"})
            return
        ext = os.path.splitext(path)[1].lower()
        content_type = {
            ".png": "image/png", ".webp": "image/webp",
        }.get(ext, "image/jpeg")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if params.get("download") == "1":
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="reddit-draft-{post_id}-{index + 1}{ext}"')
        self.end_headers()
        self.wfile.write(data)

    def _handle_reddit_drop_photo(self):
        """Copy a photo the owner picked from Labs Drop into the same upload
        area a phone photo lands in, so the compose flow can pull an existing
        build/order photo instead of only a fresh device upload. Returns the
        same {"path": ...} shape as /upload, so the frontend treats both
        sources identically once picked."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        src = _drop_safe_path(p.get("path", ""), p.get("name", ""))
        if not src or not os.path.isfile(src):
            self._json(404, {"error": "photo not found"})
            return
        ext = os.path.splitext(src)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            self._json(400, {"error": "only jpg, png, or webp images"})
            return
        try:
            dst = _store_user_upload(
                self._order_user(), ext, source=src)
        except ValueError as e:
            self._json(409, {"error": str(e)})
            return
        except OSError:
            self._json(507, {"error": "the server could not stage that photo"})
            return
        self._json(200, {"path": dst})

    def _handle_reddit_post_create(self):
        """Queue a draft and run the five-pass pipeline in the background.

        Detached rather than awaited: the passes take a minute or two on
        Hermes and the owner asked to be told when it's ready, not to sit and
        watch a spinner."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 262144)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        brief = str(p.get("brief") or "").strip()[:2000]
        kind = str(p.get("kind") or "showcase").strip()[:40]
        raw_photos = p.get("photos") or []
        photos = []
        for candidate in raw_photos[:12]:
            owned = _upload_owned(candidate, actor)
            if not owned:
                self._json(400, {
                    "error": "A draft photo expired or belongs to another account. "
                             "Attach it again."})
                return
            photos.append(owned)
        if not brief and not photos:
            self._json(400, {"error": "add a photo or say something about the build"})
            return
        if not REDDIT_JOB_SLOT.acquire(blocking=False):
            self._json(409, {"error": "another Reddit draft is still being prepared"})
            return
        try:
            conn = db()
        except Exception as exc:
            REDDIT_JOB_SLOT.release()
            print(f"[reddit_draft] could not open queue database: {exc}", flush=True)
            self._json(500, {"error": "The draft was not queued. Try again."})
            return
        durable_photos = []
        pid = None
        queued = False
        queue_error = None
        try:
            cur = conn.execute(
                "INSERT INTO reddit_posts (created_by, kind, brief, photos, status) "
                "VALUES (?,?,?,?, 'staging')",
                (actor, kind, brief, "[]"))
            pid = cur.lastrowid
            conn.commit()
            conn.close()
            conn = None

            # Do not hold SQLite's only writer slot while copying up to twelve
            # large phone photos. The shared content-worker slot prevents this
            # staging row from being picked up before it is complete.
            durable_photos = _persist_reddit_photos(pid, photos)
            conn = db()
            conn.execute("BEGIN IMMEDIATE")
            staged = conn.execute(
                "UPDATE reddit_posts SET photos=?, status='queued', "
                "queued_at=datetime('now') "
                "WHERE id=? AND status='staging'",
                (json.dumps(durable_photos), pid))
            if staged.rowcount != 1:
                raise RuntimeError("draft staging row disappeared")
            conn.commit()
            queued = True
        except Exception as e:
            if conn and conn.in_transaction:
                conn.rollback()
            queue_error = e
        finally:
            close_ok = False
            try:
                if conn:
                    conn.close()
                close_ok = True
            finally:
                if not queued or not close_ok:
                    # Cleanup below is deliberately best-effort. Never let a
                    # disk or second database error strand the content slot.
                    REDDIT_JOB_SLOT.release()
        if queue_error is not None:
            if pid is not None:
                quarantine = None
                cleanup = None
                try:
                    quarantine = _quarantine_reddit_media(pid)
                    cleanup = db()
                    cleanup.execute("BEGIN IMMEDIATE")
                    cleanup.execute(
                        "DELETE FROM reddit_posts WHERE id=? AND status='staging'",
                        (pid,))
                    cleanup.commit()
                    _purge_reddit_quarantine(quarantine)
                except Exception as cleanup_error:
                    if cleanup and cleanup.in_transaction:
                        cleanup.rollback()
                    if quarantine:
                        try:
                            _restore_reddit_quarantine(pid, quarantine)
                        except OSError as restore_error:
                            print(
                                "[reddit-media] failed staging restore "
                                f"{pid}: {restore_error}", flush=True)
                    print(
                        f"[reddit_draft] staging cleanup {pid}: {cleanup_error}",
                        flush=True)
                finally:
                    if cleanup:
                        cleanup.close()
            print(f"[reddit_draft] could not queue draft: {queue_error}", flush=True)
            self._json(500, {
                "error": "The draft was not queued. Its staged photos are still available; "
                         "try again."})
            return
        if not _start_reddit_post_worker(self._alert, slot_held=True):
            conn = db()
            try:
                conn.execute(
                    "UPDATE reddit_posts SET status='failed', stage='error', "
                    "error='The durable worker could not start; retry this draft' "
                    "WHERE id=?", (pid,))
                conn.commit()
            finally:
                conn.close()
            self._json(500, {"error": "The draft was saved but its worker did not start. "
                                      "Use Retry on the draft."})
            return
        for photo in photos:
            _forget_upload(photo, remove_data=True)
        self._json(200, {"ok": True, "id": pid, "status": "queued"})

    def _alert(self, body):
        """Tell the owner something finished. Uses the health alert
        destination, never the supplier group — Hannan has no reason to see
        Reddit drafts."""
        _send_owner_alert(body)

    def _handle_reddit_post_answer(self):
        """Answer the question a pass stopped on, and let the run continue."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            pid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        answer = str(p.get("answer") or "").strip()[:2000]
        if not answer:
            self._json(400, {"error": "an answer is needed"})
            return
        if not REDDIT_JOB_SLOT.acquire(blocking=False):
            self._json(409, {"error": "another Reddit draft is still being prepared"})
            return
        queued = False
        try:
            conn = db()
        except Exception as exc:
            REDDIT_JOB_SLOT.release()
            print(f"[reddit_draft] answer database unavailable: {exc}", flush=True)
            self._json(500, {"error": "The answer was not queued. Try again."})
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM reddit_posts WHERE id=?", (pid,)).fetchone()
            if not row:
                conn.rollback()
                self._json(404, {"error": "no such draft"})
                return
            if row["status"] != "needs_input":
                conn.rollback()
                self._json(409, {"error": "this draft is not waiting for an answer"})
                return
            conn.execute("UPDATE reddit_posts SET answer=?, question=NULL, "
                         "status='queued', started_at=NULL, "
                         "queued_at=datetime('now'), "
                         "rounds=COALESCE(rounds,0)+1 WHERE id=?",
                         (answer, pid))
            conn.commit()
            queued = True
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            print(f"[reddit_draft] answer could not queue: {exc}", flush=True)
            self._json(500, {"error": "The answer was not queued. Try again."})
            return
        finally:
            close_ok = False
            try:
                conn.close()
                close_ok = True
            finally:
                if not queued or not close_ok:
                    REDDIT_JOB_SLOT.release()
        if not _start_reddit_post_worker(self._alert, slot_held=True):
            conn = db()
            try:
                conn.execute(
                    "UPDATE reddit_posts SET status='failed', stage='error', "
                    "error='The durable worker could not restart; retry this draft' "
                    "WHERE id=?", (pid,))
                conn.commit()
            finally:
                conn.close()
            self._json(500, {"error": "The answer was saved but the worker did not start. "
                                      "Use Retry on the draft."})
            return
        self._json(200, {"ok": True})

    def _handle_reddit_post_retry(self):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            post_id = int(payload.get("id") or 0)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        if not REDDIT_JOB_SLOT.acquire(blocking=False):
            self._json(409, {"error": "another Reddit draft is still being prepared"})
            return
        queued = False
        try:
            conn = db()
        except Exception as exc:
            REDDIT_JOB_SLOT.release()
            print(f"[reddit_draft] retry database unavailable: {exc}", flush=True)
            self._json(500, {"error": "The retry was not queued. Try again."})
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM reddit_posts WHERE id=?", (post_id,)).fetchone()
            if not row:
                conn.rollback()
                self._json(404, {"error": "no such draft"})
                return
            if row["status"] != "failed":
                conn.rollback()
                self._json(409, {"error": "only a failed draft can be retried"})
                return
            conn.execute(
                "UPDATE reddit_posts SET status='queued', stage='retrying', "
                "error=NULL, finished_at=NULL, started_at=NULL, "
                "queued_at=datetime('now') WHERE id=?",
                (post_id,))
            conn.commit()
            queued = True
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            print(f"[reddit_draft] retry could not queue: {exc}", flush=True)
            self._json(500, {"error": "The retry was not queued. Try again."})
            return
        finally:
            close_ok = False
            try:
                conn.close()
                close_ok = True
            finally:
                if not queued or not close_ok:
                    REDDIT_JOB_SLOT.release()
        if not _start_reddit_post_worker(self._alert, slot_held=True):
            conn = db()
            try:
                conn.execute(
                    "UPDATE reddit_posts SET status='failed', stage='error', "
                    "error='The durable worker could not restart; retry again' WHERE id=?",
                    (post_id,))
                conn.commit()
            finally:
                conn.close()
            self._json(500, {"error": "The worker did not start; retry again."})
            return
        self._json(200, {"ok": True, "id": post_id})

    def _handle_reddit_post_update(self):
        """Edit a draft before it goes out, or mark it as posted."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            pid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        conn = db()
        quarantine = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, status, photos FROM reddit_posts WHERE id=?",
                (pid,)).fetchone()
            if not row:
                conn.rollback()
                self._json(404, {"error": "no such draft"})
                return
            active = row["status"] in ("queued", "running", "staging")
            scheduling = (
                p.get("slot_date") is not None
                or p.get("assigned_to") is not None)
            if active and not scheduling:
                conn.rollback()
                self._json(
                    409, {"error": "wait for the active draft run before editing it"})
                return
            if scheduling:
                conn.execute(
                    "UPDATE reddit_posts SET slot_date=?, assigned_to=? WHERE id=?",
                    (str(p.get("slot_date") or "")[:10] or None,
                     str(p.get("assigned_to") or "")[:80] or None, pid))
            elif p.get("posted"):
                conn.execute(
                    "UPDATE reddit_posts SET status='posted', "
                    "posted_at=datetime('now'), posted_url=? WHERE id=?",
                    (str(p.get("url") or "")[:400], pid))
            elif p.get("discard"):
                try:
                    photos = json.loads(row["photos"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    photos = [True]
                quarantine = _quarantine_reddit_media(pid)
                if photos and not quarantine:
                    conn.rollback()
                    self._json(
                        409,
                        {"error": "The draft's stored photos are missing. "
                                  "Repair or restore them before discarding."})
                    return
                conn.execute("DELETE FROM reddit_posts WHERE id=?", (pid,))
            else:
                conn.execute(
                    "UPDATE reddit_posts SET title=?, body=? WHERE id=?",
                    (str(p.get("title") or "")[:300],
                     str(p.get("body") or "")[:20000], pid))
            conn.commit()
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            if quarantine:
                try:
                    _restore_reddit_quarantine(pid, quarantine)
                except OSError as restore_exc:
                    print(f"[reddit-media] restore after failed discard: {restore_exc}",
                          flush=True)
            print(f"[reddit] draft update failed: {exc}", flush=True)
            self._json(500, {"error": "The draft was not changed. Try again."})
            return
        finally:
            conn.close()
        if p.get("discard") and quarantine:
            _purge_reddit_quarantine(quarantine)
        self._json(200, {"ok": True})

    def _handle_reddit_setup(self):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        rows = {r["kind"]: {"text": r["text"], "updated_at": r["updated_at"]}
                for r in conn.execute("SELECT * FROM reddit_setup")}
        conn.close()
        self._json(200, {"setup": rows})

    def _handle_reddit_setup_draft(self):
        """Draft the sub's rules, sidebar and flair.

        A brand-new subreddit with no rules and an empty sidebar reads as
        abandoned to anyone who lands on it, which is the opposite of what
        daily posting is meant to achieve."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            p = {}
        kind = str(p.get("kind") or "").strip()
        if kind not in ("rules", "sidebar", "flair"):
            self._json(400, {"error": "unknown piece"})
            return
        if not REDDIT_JOB_SLOT.acquire(blocking=False):
            self._json(409, {"error": "another Reddit draft is still being prepared"})
            return

        def _work():
            ok = False
            try:
                r = subprocess.run(
                    ["python3", "/root/ops-dashboard/reddit_setup.py", kind],
                    capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    print(f"[reddit_setup] {kind}: {r.stderr[:200]}", flush=True)
                else:
                    ok = True
            except Exception as e:
                print(f"[reddit_setup] {kind}: {e}", flush=True)
            finally:
                REDDIT_JOB_SLOT.release()
            hub_event("reddit_setup", f"{kind} " + ("drafted" if ok else "failed"),
                      actor, "agent")

        try:
            threading.Thread(target=_work, daemon=True).start()
        except Exception:
            REDDIT_JOB_SLOT.release()
            raise
        self._json(200, {"ok": True, "kind": kind})

    def _handle_reddit_setup_save(self):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        kind = str(p.get("kind") or "").strip()
        if kind not in ("rules", "sidebar", "flair"):
            self._json(400, {"error": "unknown piece"})
            return
        conn = db()
        conn.execute("INSERT INTO reddit_setup (kind, text, updated_at) "
                     "VALUES (?,?,datetime('now')) ON CONFLICT(kind) DO UPDATE "
                     "SET text=excluded.text, updated_at=datetime('now')",
                     (kind, str(p.get("text") or "")[:20000]))
        conn.commit()
        conn.close()
        self._json(200, {"ok": True})

    def _handle_reddit_replies(self):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        rows = [dict(r) for r in conn.execute(
            "SELECT d.*, t.title, t.subreddit, t.author, t.permalink "
            "FROM reddit_drafts d LEFT JOIN reddit_threads t ON t.id=d.thread_id "
            "ORDER BY d.id DESC LIMIT 50")]
        conn.close()
        for r in rows:
            try:
                r["passes"] = json.loads(r.get("passes") or "{}")
            except (json.JSONDecodeError, TypeError):
                r["passes"] = {}
        self._json(200, {"replies": rows})

    def _handle_reddit_reply_create(self):
        """Draft a reply to somebody else's thread. On a small subreddit this
        matters more than another post of our own."""
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            tid = int(p.get("thread_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        note = str(p.get("note") or "").strip()[:1000]
        conn = db()
        t = conn.execute(
            "SELECT id, body, post_type FROM reddit_threads WHERE id=?", (tid,)
        ).fetchone()
        if not t:
            conn.close()
            self._json(404, {"error": "no such thread"})
            return
        if not (t["body"] or "").strip() and not note:
            conn.close()
            self._json(400, {"error":
                "This is a title-only image/link post. Open it first, then add the "
                "relevant visual or discussion detail so Hermes does not guess."})
            return
        if not REDDIT_JOB_SLOT.acquire(blocking=False):
            conn.close()
            self._json(409, {"error": "another Reddit draft is still being prepared"})
            return
        committed = False
        try:
            cur = conn.execute(
                "INSERT INTO reddit_drafts "
                "(thread_id, draft_text, status, created_by, answer, queued_at) "
                "VALUES (?,'','queued',?,?,datetime('now'))",
                (tid, actor, note))
            did = cur.lastrowid
            conn.commit()
            committed = True
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            print(f"[reddit_reply] could not queue reply: {exc}", flush=True)
            self._json(500, {"error": "The reply was not queued. Try again."})
            return
        finally:
            close_ok = False
            try:
                conn.close()
                close_ok = True
            finally:
                if not committed or not close_ok:
                    REDDIT_JOB_SLOT.release()

        if not _start_reddit_post_worker(self._alert, slot_held=True):
            c = db()
            try:
                c.execute(
                    "UPDATE reddit_drafts SET status='failed', stage='error', "
                    "error='The durable worker could not start; retry this reply', "
                    "finished_at=datetime('now') WHERE id=?", (did,))
                c.commit()
            finally:
                c.close()
            self._json(500, {
                "error": "The reply was saved but its worker did not start. Use Retry."})
            return
        self._json(200, {"ok": True, "id": did})

    def _handle_reddit_reply_update(self):
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 65536)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            did = int(p.get("id") or 0)
        except (TypeError, ValueError):
            did = 0
        queue_mode = (
            "answer" if p.get("answer") is not None
            else "retry" if p.get("retry") else None
        )
        try:
            conn = db()
        except Exception as exc:
            print(f"[reddit_reply] update database unavailable: {exc}", flush=True)
            self._json(500, {"error": "The reply was not changed. Try again."})
            return
        slot_held = False
        if queue_mode:
            if not REDDIT_JOB_SLOT.acquire(blocking=False):
                conn.close()
                self._json(409, {"error": "another Reddit draft is still being prepared"})
                return
            slot_held = True
        queued = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, status FROM reddit_drafts WHERE id=?", (did,)).fetchone()
            if not row:
                conn.rollback()
                self._json(404, {"error": "no such reply"})
                return
            if row["status"] in ("queued", "running") and not queue_mode:
                conn.rollback()
                self._json(
                    409, {"error": "wait for the active reply run before changing it"})
                return
            if p.get("discard"):
                conn.execute("DELETE FROM reddit_drafts WHERE id=?", (did,))
            elif p.get("posted"):
                conn.execute(
                    "UPDATE reddit_drafts SET status='posted', "
                    "posted_at=datetime('now') WHERE id=?", (did,))
            elif queue_mode == "answer":
                if row["status"] != "needs_input":
                    conn.rollback()
                    self._json(409, {
                        "error": "this reply is not waiting for an answer"})
                    return
                answer = str(p.get("answer") or "").strip()[:1000]
                if not answer:
                    conn.rollback()
                    self._json(400, {"error": "type an answer first"})
                    return
                conn.execute(
                    "UPDATE reddit_drafts SET answer=?, question=NULL, "
                    "status='queued', stage='resuming', error=NULL, "
                    "started_at=NULL, queued_at=datetime('now') WHERE id=?",
                    (answer, did))
            elif queue_mode == "retry":
                if row["status"] != "failed":
                    conn.rollback()
                    self._json(409, {
                        "error": "only a failed reply can be retried"})
                    return
                conn.execute(
                    "UPDATE reddit_drafts SET status='queued', stage='retrying', "
                    "error=NULL, finished_at=NULL, started_at=NULL, "
                    "queued_at=datetime('now') WHERE id=?",
                    (did,))
            else:
                if row["status"] in ("queued", "running"):
                    conn.rollback()
                    self._json(
                        409, {"error": "wait for the active reply run before editing"})
                    return
                conn.execute(
                    "UPDATE reddit_drafts SET draft_text=? WHERE id=?",
                    (str(p.get("text") or "")[:20000], did))
            conn.commit()
            queued = bool(queue_mode)
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            print(f"[reddit_reply] update failed: {exc}", flush=True)
            self._json(500, {"error": "The reply was not changed. Try again."})
            return
        finally:
            close_ok = False
            try:
                conn.close()
                close_ok = True
            finally:
                if slot_held and (not queued or not close_ok):
                    REDDIT_JOB_SLOT.release()
        if queued:
            slot_held = False  # worker start owns/releases the acquired slot
            if not _start_reddit_post_worker(self._alert, slot_held=True):
                c = db()
                try:
                    c.execute(
                        "UPDATE reddit_drafts SET status='failed', stage='error', "
                        "error='The durable worker could not restart; retry this reply', "
                        "finished_at=datetime('now') WHERE id=?", (did,))
                    c.commit()
                finally:
                    c.close()
                self._json(500, {
                    "error": "The reply was saved but the worker did not start. "
                             "Use Retry."})
                return
        self._json(200, {"ok": True})

    def _handle_reddit_sync(self):
        """Pull fresh threads from the target subreddits and upsert them.
        Read-only against Reddit itself — this never posts or comments,
        it only fills the Listener's inbox for a human to review."""
        actor = self._order_user()
        if not self._has_tool("reddit"):
            self._json(403, {"error": "not available for this account"})
            return
        import reddit_api
        state = db()
        sub = reddit_api.claim_next_scan_sub(state)
        state.commit()
        state.close()
        try:
            threads = reddit_api.fetch_new_threads(subs=[sub], limit=25)
        except reddit_api.RedditError as e:
            self._json(400, {"error": str(e)})
            return
        conn = db()
        added = reddit_api.upsert_threads(conn, threads)
        conn.commit()
        conn.close()
        hub_event("reddit_sync", f"{added} new thread(s) from r/{sub}",
                  actor, "agent")
        self._json(200, {"ok": True, "subreddit": sub,
                         "fetched": len(threads), "added": added})

    def _handle_supplier_ledger(self, query):
        """An order-request sheet for a batch of builds, so the supplier can
        forward the job to their own supplier. Photo, spec and quantity only —
        deliberately no prices, since this document leaves the business and a
        manufacturing brief has no reason to carry cost."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        raw = urllib.parse.unquote(params.get("ids", ""))
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()][:60]
        if not ids:
            self._json(400, {"error": "select some orders first"})
            return
        conn = db()
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, order_no, product, quantity, case_style, dial_colour, dial_style, "
            f"case_colour, movement, watch_size FROM orders "
            f"WHERE id IN ({marks}) AND supplier_visible=1 ORDER BY id ASC", ids).fetchall()
        conn.close()
        if not rows:
            self._json(404, {"error": "none of those orders are in your queue"})
            return
        cards = []
        for r in rows:
            spec = " · ".join(str(r[k]) for k in
                              ("case_style", "dial_colour", "dial_style", "case_colour",
                               "movement", "watch_size") if r[k])
            img = self._photo_data_uri(r["id"], 420)
            cards.append(
                '<tr>'
                f'<td class="imgcell">{f"<img src=\"{img}\">" if img else "&nbsp;"}</td>'
                f'<td><b>#{r["order_no"] or r["id"]}</b><br>'
                f'{html_mod.escape(r["product"] or "")}'
                + (f'<br><span class="spec">{html_mod.escape(spec)}</span>' if spec else "")
                + f'</td><td class="qty">{r["quantity"] or 1}</td></tr>')
        import datetime as _dt
        doc = (
            "<html><head><meta charset='utf-8'><style>"
            "@page{size:A4;margin:1.6cm 1.4cm;}"
            "body{font-family:Helvetica,Arial,sans-serif;font-size:10pt;color:#1a1f1b;}"
            "h1{font-size:15pt;margin:0 0 2pt;}"
            ".sub{color:#777;font-size:8.5pt;margin-bottom:14pt;}"
            "table{width:100%;border-collapse:collapse;}"
            "th{text-align:left;font-size:8pt;text-transform:uppercase;letter-spacing:.5pt;"
            "color:#777;border-bottom:1pt solid #ccc;padding:0 6pt 5pt;}"
            "td{border-bottom:1pt solid #eee;padding:8pt 6pt;vertical-align:top;}"
            ".imgcell{width:110pt;}.imgcell img{width:100pt;height:100pt;object-fit:cover;"
            "border-radius:5pt;border:1pt solid #ddd;}"
            ".spec{color:#666;font-size:8.5pt;}.qty{text-align:right;font-weight:bold;width:40pt;}"
            "</style></head><body>"
            f"<h1>Build request &mdash; {len(rows)} watch{'es' if len(rows) != 1 else ''}</h1>"
            f"<div class='sub'>Timelabs Co &middot; {_dt.datetime.now().strftime('%d %b %Y')}"
            "</div><table><thead><tr><th>Reference</th><th>Build</th><th>Qty</th></tr></thead>"
            "<tbody>" + "".join(cards) + "</tbody></table></body></html>")
        try:
            from weasyprint import HTML
            pdf = HTML(string=doc).write_pdf()
        except Exception as e:
            self._json(500, {"error": f"couldn't build the PDF: {e}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                        f'attachment; filename="build-request-{len(rows)}-items.pdf"')
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    def _handle_supplier_card(self, query):
        """text=1 returns the summary as plain text (for copy / a wa.me link);
        otherwise a PDF, using the same generator the agent's exports use."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            oid = int(params.get("id", ""))
        except ValueError:
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        o, text = self._status_card(conn, oid)
        conn.close()
        if not o:
            self._json(404, {"error": "no such order"})
            return
        if params.get("text") == "1":
            self._json(200, {"id": oid, "text": text})
            return
        # Built as HTML directly rather than through make_pdf(), which escapes
        # its input before converting — correct for untrusted markdown, but it
        # would render the photo tag as literal text.
        img = self._photo_data_uri(oid, 560)
        lines = text.split("\n")
        head, body = lines[0], lines[1:]
        rows = "".join(
            f"<div class='ln'>{html_mod.escape(l.strip())}</div>" if not l.startswith("  ")
            else f"<div class='ev'>{html_mod.escape(l.strip())}</div>"
            for l in body if l.strip())
        import datetime as _dt
        doc = (
            "<html><head><meta charset='utf-8'><style>"
            "@page{size:A4;margin:1.8cm 1.6cm;}"
            "body{font-family:Helvetica,Arial,sans-serif;font-size:10.5pt;color:#1a1f1b;}"
            ".mark{font-size:7.5pt;letter-spacing:2.5pt;text-transform:uppercase;color:#8a6a2c;}"
            "h1{font-size:14pt;margin:3pt 0 10pt;}"
            "img{width:7cm;height:7cm;object-fit:cover;border-radius:6pt;"
            "border:1pt solid #ddd;margin-bottom:12pt;}"
            ".ln{font-size:11pt;margin:3pt 0;}"
            ".ev{font-size:9pt;color:#666;margin:2pt 0 2pt 10pt;}"
            ".stamp{color:#888;font-size:8pt;margin-top:16pt;border-top:1pt solid #ddd;"
            "padding-top:6pt;}"
            "</style></head><body>"
            "<div class='mark'>Timelabs Co</div>"
            f"<h1>{html_mod.escape(head)}</h1>"
            + (f"<img src='{img}'>" if img else "")
            + rows
            + f"<div class='stamp'>Generated {_dt.datetime.now().strftime('%d %b %Y, %H:%M')}</div>"
            "</body></html>")
        try:
            from weasyprint import HTML
            pdf = HTML(string=doc).write_pdf()
        except Exception as e:
            self._json(500, {"error": f"couldn't build the PDF: {e}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                        f'attachment; filename="order-{oid}-status.pdf"')
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    def _handle_supplier_whatsapp(self):
        """Post a build's status straight into WhatsApp from the button, using
        the gateway Hermes already has paired — so the supplier never leaves
        the queue, and the update lands where the team already looks.

        `hermes send` can carry an attachment via MEDIA:<path>, so the PDF
        goes with the text rather than the text pointing at a file nobody
        opens. Target lives in .env (WHATSAPP_ORDER_TARGET) rather than being
        hardcoded: a group id is not something to bury in source, and it
        changes if the group is ever remade."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 2048)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return

        target = ""
        try:
            with open("/root/ops-dashboard/.env") as f:
                for line in f:
                    if line.startswith("WHATSAPP_ORDER_TARGET="):
                        target = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
        if not target:
            self._json(400, {"error": "No WhatsApp destination is set up yet — an admin "
                                      "needs to add WHATSAPP_ORDER_TARGET to .env."})
            return

        conn = db()
        o, text = self._status_card(conn, oid)
        conn.close()
        if not o:
            self._json(404, {"error": "no such order"})
            return

        body = text
        if p.get("with_pdf"):
            try:
                display_no = o.get("ref_code") or f"#{o.get('order_no') or oid}"
                pdf = make_pdf(
                    text.replace("\n", "\n\n"), f"Order {display_no} — status")
                path = os.path.join(UPLOAD_DIR, f"order-{oid}-status.pdf")
                with open(path, "wb") as f:
                    f.write(pdf)
                body = f"MEDIA:{path}\n{text}"
            except Exception as e:
                print(f"[whatsapp] PDF skipped: {e}", flush=True)

        # The send goes to a background thread and the request returns now.
        # `hermes send` takes ~4.5s for plain text and longer with a PDF
        # attached, and holding the HTTP response open for it made the button
        # look broken — the supplier taps it, nothing happens for five
        # seconds, so he taps it again. The outcome lands on the order's
        # timeline either way, which is where he'd look to confirm it went.
        def _deliver():
            try:
                r = subprocess.run(["hermes", "send", "--to", target, "--quiet", body],
                                   capture_output=True, text=True, timeout=120)
                ok = r.returncode == 0
                detail = ("status sent to configured WhatsApp destination" if ok else
                          "WhatsApp send failed: " +
                          (r.stderr or r.stdout or "unknown error").strip()[:160])
            except Exception as e:
                ok, detail = False, f"WhatsApp send failed: {e}"[:200]
            try:
                c = db()
                order_event(c, oid, "shared" if ok else "share_failed", detail, actor)
                c.commit()
                c.close()
            except Exception as e:
                print(f"[whatsapp] could not log outcome: {e}", flush=True)
            hub_event("order_shared" if ok else "order_share_failed",
                      f"#{o.get('order_no') or oid} status -> WhatsApp"
                      if ok else f"#{o.get('order_no') or oid} WhatsApp failed",
                      actor, app="orders")

        threading.Thread(target=_deliver, daemon=True).start()
        self._json(200, {"ok": True, "id": oid, "queued": True})

    def _handle_supplier_shipments(self):
        """Shipments with the builds they carry and what each one costs.

        Per-watch cost is the consignment total divided evenly by how many
        physical units are in it, so it moves as builds are added or removed — it's a
        live view of the split, not a number frozen at entry time that goes
        stale the moment the shipment changes."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        try:
            ships = [dict(r) for r in conn.execute(
                "SELECT id, created_at, code, carrier, total_cost, currency, notes, status "
                "FROM shipments ORDER BY id DESC")]
            rows = conn.execute(
                "SELECT id, shipment_id, product, quantity, status, local_photos "
                "FROM orders WHERE shipment_id IS NOT NULL AND supplier_visible=1 "
                "ORDER BY id ASC").fetchall()
            unassigned = [dict(r) for r in conn.execute(
                "SELECT id, product, quantity, status FROM orders "
                "WHERE shipment_id IS NULL AND supplier_visible=1 "
                "AND status NOT IN ('cancelled','delivered') ORDER BY id ASC")]
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        by_ship = {}
        for r in rows:
            d = dict(r)
            try:
                d["photos"] = len(json.loads(d.pop("local_photos") or "[]"))
            except (json.JSONDecodeError, TypeError):
                d["photos"] = 0
            by_ship.setdefault(d["shipment_id"], []).append(d)
        for s in ships:
            items = by_ship.get(s["id"], [])
            s["orders"] = items
            n = len(items)
            units = sum(max(1, int(it.get("quantity") or 1)) for it in items)
            s["order_count"] = n
            s["unit_count"] = units
            s["per_watch"] = round((s["total_cost"] or 0) / units, 2) if units else None
        self._json(200, {"shipments": ships, "unassigned": unassigned})

    def _handle_supplier_shipment_save(self):
        """Create or update a consignment. Matched on code so re-entering the
        same shipment number updates it rather than making a duplicate."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        code = str(p.get("code", "")).strip()[:80]
        if not code:
            self._json(400, {"error": "a shipment number is needed"})
            return
        carrier = str(p.get("carrier", "")).strip()[:60]
        notes = str(p.get("notes", "")).strip()[:400]
        currency = self._money_currency(p.get("currency") or "INR")
        if not currency:
            self._json(400, {"error": "currency must be INR or USD"})
            return
        total = self._money_amount(p.get("total_cost"), optional=True)
        if str(p.get("total_cost", "")).strip() and total is None:
            self._json(400, {"error": "the total cost should be a number"})
            return
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM shipments WHERE code=?", (code,)).fetchone()
        if row:
            sid = row["id"]
            conn.execute("UPDATE shipments SET carrier=?, total_cost=?, currency=?, "
                        "notes=? WHERE id=?", (carrier, total, currency, notes, sid))
        else:
            cur = conn.execute(
                "INSERT INTO shipments (code, carrier, total_cost, currency, notes) "
                "VALUES (?,?,?,?,?)", (code, carrier, total, currency, notes))
            sid = cur.lastrowid
        conn.commit()
        conn.close()
        hub_event("shipment_saved", f"{code} ({currency} {total or 0})", actor, app="orders")
        self._json(200, {"ok": True, "id": sid, "code": code})

    def _handle_supplier_shipment_assign(self):
        """Put builds into a consignment (or pull them out with shipment=null).
        Also stamps each order's tracking with the shipment code, so an order
        looked at on its own still says how it travelled."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        ids = list(dict.fromkeys(
            int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()
        ))[:200]
        if not ids:
            self._json(400, {"error": "no orders selected"})
            return
        sid = p.get("shipment_id")
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        code = None
        if sid in (None, "", 0):
            sid = None
        else:
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                conn.close()
                self._json(400, {"error": "bad shipment"})
                return
            row = conn.execute("SELECT code FROM shipments WHERE id=?", (sid,)).fetchone()
            if not row:
                conn.close()
                self._json(404, {"error": "no such shipment"})
                return
            code = row["code"]
        marks = ",".join("?" for _ in ids)
        eligible = [r["id"] for r in conn.execute(
            f"SELECT id FROM orders WHERE id IN ({marks}) "
            "AND supplier_visible=1 AND status NOT IN ('cancelled','delivered')",
            ids).fetchall()]
        if not eligible:
            conn.close()
            self._json(404, {"error": "none of those active builds are in the queue"})
            return
        eligible_set = set(eligible)
        skipped = [oid for oid in ids if oid not in eligible_set]
        marks = ",".join("?" for _ in eligible)
        conn.execute(f"UPDATE orders SET shipment_id=? WHERE id IN ({marks}) "
                     f"AND supplier_visible=1", [sid] + eligible)
        if code:
            conn.execute(f"UPDATE orders SET tracking_code=? WHERE id IN ({marks}) "
                        f"AND supplier_visible=1 AND (tracking_code IS NULL OR tracking_code='')",
                        [code] + eligible)
        for oid in eligible:
            order_event(conn, oid, "shipment",
                       f"added to shipment {code}" if code else "removed from its shipment",
                       actor)
        conn.commit()
        conn.close()
        hub_event("shipment_assign",
                 f"{len(eligible)} order(s) -> {code or 'no shipment'}", actor, app="orders")
        self._json(200, {"ok": True, "count": len(eligible), "skipped": skipped,
                         "shipment_id": sid})

    def _handle_supplier_bulk(self):
        """One action across many builds — the point of the checkboxes. Status
        and tracking only: the things a supplier legitimately changes for a
        whole batch at once ("these six all shipped today")."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        from order_form import STATUSES
        import order_stages
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        ids = list(dict.fromkeys(
            int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()
        ))[:200]
        if not ids:
            self._json(400, {"error": "no orders selected"})
            return
        status = str(p.get("status", "")).strip()[:40]
        tracking = p.get("tracking_code")
        if status and status not in STATUSES:
            self._json(400, {"error": "unrecognised status"})
            return
        if not status and tracking is None:
            self._json(400, {"error": "nothing to change"})
            return
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        marks = ",".join("?" for _ in ids)
        owned_rows = list(conn.execute(
            f"SELECT id, status FROM orders WHERE id IN ({marks}) "
            f"AND supplier_visible=1", ids))
        owned = [r["id"] for r in owned_rows]
        if not owned:
            conn.close()
            self._json(404, {"error": "none of those orders are in your queue"})
            return
        m2 = ",".join("?" for _ in owned)
        if status:
            blocked = [
                r for r in owned_rows
                if not order_stages.supplier_can_advance(
                    r["status"] or order_stages.DEFAULT_STAGE, status)
            ]
            if blocked:
                conn.close()
                self._json(409, {
                    "error": "Bulk status updates must move every selected build exactly "
                             "one stage forward. Split selections at different stages."})
                return
            conn.execute(f"UPDATE orders SET status=? WHERE id IN ({m2})", [status] + owned)
            for oid in owned:
                order_event(conn, oid, "status", f"-> {status} (bulk)", actor)
        if tracking is not None:
            code = str(tracking).strip()[:80]
            conn.execute(f"UPDATE orders SET tracking_code=? WHERE id IN ({m2})",
                        [code or None] + owned)
            for oid in owned:
                order_event(conn, oid, "tracking",
                           f"tracking {code}" if code else "tracking cleared", actor)
        conn.commit()
        conn.close()
        what = " and ".join(x for x in [f"status {status}" if status else "",
                                        "tracking" if tracking is not None else ""] if x)
        hub_event("orders_bulk_update", f"{len(owned)} order(s): {what}", actor, app="orders")
        self._json(200, {"ok": True, "count": len(owned)})

    def _handle_supplier_note(self):
        """Let the supplier say something back — 'dial is out of stock', 'sent
        today'. WhatsApp had this and a status dropdown alone doesn't; without
        it the dashboard is strictly worse than the group it replaces for
        anything that isn't a clean state change."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 4096)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        try:
            oid = int(p.get("id"))
        except (TypeError, ValueError):
            self._json(400, {"error": "missing order id"})
            return
        note = str(p.get("note", "")).strip()[:400]
        if not note:
            self._json(400, {"error": "write something first"})
            return
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        note_order = conn.execute(
            "SELECT order_no FROM orders WHERE id=? AND supplier_visible=1",
            (oid,)).fetchone()
        if not note_order:
            conn.rollback()
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        order_event(conn, oid, "note", note, actor)
        conn.commit()
        conn.close()
        hub_event("supplier_note", f"#{note_order['order_no'] or oid}: {note[:120]}",
                  actor, app="orders")
        self._json(200, {"ok": True, "id": oid})

    def _handle_customers_list(self):
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        import customers as customers_mod
        conn = db()
        customers_mod.ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM customers ORDER BY total_spent DESC, orders_count DESC "
            "LIMIT 200").fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = customers_mod.all_tags(d)
            out.append(d)
        self._json(200, {"customers": out})

    def _handle_ledger_import(self):
        """Preview (default) or commit new supplier invoices from Drop into
        suppliers.db. Review-gated: the UI shows the parse before committing."""
        if not self._has_tool("ledger"):
            self._json(403, {"error": "not available for this account"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode()) if length else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        commit = bool(payload.get("commit"))
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()

        def manifest():
            conn = sqlite3.connect(SUPPLIERS_DB, timeout=5)
            try:
                have = {r[0] for r in conn.execute(
                    "SELECT source_file FROM invoices WHERE source_file IS NOT NULL")}
            finally:
                conn.close()
            rows = []
            for path in sorted(glob.glob(os.path.join(INVOICE_INBOX, "*.xlsx"))):
                name = os.path.basename(path)
                if name in have:
                    continue
                digest = hashlib.sha256()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                st = os.stat(path)
                rows.append((name, st.st_size, st.st_mtime_ns, digest.hexdigest()))
            return rows

        with LEDGER_PREVIEW_LOCK:
            now = time.time()
            for old_token, rec in list(LEDGER_PREVIEWS.items()):
                if rec["expires"] < now:
                    LEDGER_PREVIEWS.pop(old_token, None)
            if commit:
                token = str(payload.get("preview_token") or "")
                rec = LEDGER_PREVIEWS.pop(token, None)
                if not rec or rec["actor"] != actor or rec["expires"] < now:
                    self._json(409, {
                        "error": "That invoice preview expired. Scan and review it again."})
                    return
                try:
                    current_manifest = manifest()
                except Exception as e:
                    self._json(500, {"error": f"couldn't recheck invoice files: {e}"})
                    return
                if current_manifest != rec["manifest"]:
                    self._json(409, {
                        "error": "The invoice folder changed after preview. Scan it again."})
                    return
            else:
                try:
                    current_manifest = manifest()
                except Exception as e:
                    self._json(500, {"error": f"couldn't inspect invoice files: {e}"})
                    return

        # run via the venv python (has openpyxl; this server runs on system python)
        cmd = ["/root/ops-dashboard/venv/bin/python", "/root/ops-dashboard/invoice_import.py", "--json"]
        if commit:
            cmd.append("--commit")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if out.returncode != 0:
                raise RuntimeError(out.stderr[-300:] or f"parser exited {out.returncode}")
            data = json.loads(out.stdout.strip() or "{}")
            results = data.get("results", [])
        except Exception as e:
            self._json(500, {"error": f"invoice parse failed: {e} · {out.stderr[-160:] if 'out' in dir() else ''}"})
            return
        preview_token = None
        if commit:
            imported = [r for r in results if not r.get("error")]
            subprocess.Popen(["/root/ops-dashboard/venv/bin/python3", "/root/ops-dashboard/ledger.py"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if imported:
                hub_event("invoice_import",
                          f"{len(imported)} invoice(s): " + ", ".join(r["file"] for r in imported),
                          actor, app="ledger")
        else:
            try:
                after_manifest = manifest()
            except Exception as e:
                self._json(500, {"error": f"invoice folder changed while scanning: {e}"})
                return
            if after_manifest != current_manifest:
                self._json(409, {
                    "error": "The invoice folder changed while scanning. Try again."})
                return
            preview_token = secrets.token_urlsafe(24)
            with LEDGER_PREVIEW_LOCK:
                LEDGER_PREVIEWS[preview_token] = {
                    "actor": actor, "expires": time.time() + LEDGER_PREVIEW_TTL,
                    "manifest": current_manifest,
                }
        self._json(200, {"committed": commit, "results": results,
                         "preview_token": preview_token})

    def _handle_new_session(self):
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        conn = db()
        cur = conn.execute(
            "INSERT INTO webchat_sessions (title, owner_email, visibility) "
            "VALUES ('New chat', ?, 'private')", (email,))
        sid = cur.lastrowid
        # Claude Sonnet is the strongest connected writing route and therefore
        # the deliberate default for new Command/Creator conversations. Older
        # chats retain their existing preference until someone changes it.
        conn.execute(
            "INSERT INTO webchat_model_pref (session_id, model, provider) VALUES (?,?,?)",
            (sid, *SESSION_MODEL_CHOICES["claude"]),
        )
        conn.commit()
        conn.close()
        self._json(200, {"id": sid})

    def _handle_session_rename(self):
        """Rename one conversation without allowing cross-account changes."""
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            session_id = int(payload.get("id"))
            title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()[:80]
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        if not title:
            self._json(400, {"error": "chat name is required"})
            return
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_email FROM webchat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row or (email not in ADMIN_EMAILS and row["owner_email"] != email):
                conn.rollback()
                self._json(404, {"error": "no such conversation"})
                return
            conn.execute(
                "UPDATE webchat_sessions SET title=?, updated_at=datetime('now') WHERE id=?",
                (title, session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._json(200, {"id": session_id, "title": title})

    def _handle_session_model(self):
        """Set one reviewed per-chat inference route for the next message."""
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            session_id = int(payload.get("id"))
            engine = str(payload.get("engine") or "").strip().lower()
            model, provider = SESSION_MODEL_CHOICES[engine]
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError):
            self._json(400, {"error": "unsupported writing engine"})
            return
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_email FROM webchat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row or (email not in ADMIN_EMAILS and row["owner_email"] != email):
                conn.rollback()
                self._json(404, {"error": "no such conversation"})
                return
            conn.execute(
                "INSERT INTO webchat_model_pref (session_id, model, provider) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model, "
                "provider=excluded.provider",
                (session_id, model, provider),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._json(200, {
            "id": session_id, "engine": engine, "model": model, "provider": provider,
        })

    def _handle_session_archive(self):
        """Archive or restore one conversation the caller is allowed to open."""
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            session_id = int(payload.get("id"))
            archived = bool(payload.get("archived", True))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_email FROM webchat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not row or (email not in ADMIN_EMAILS and row["owner_email"] != email):
                conn.rollback()
                self._json(404, {"error": "no such conversation"})
                return
            conn.execute(
                "UPDATE webchat_sessions SET visibility=? WHERE id=?",
                ("archived" if archived else "private", session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._json(200, {"id": session_id, "archived": archived})

    def _handle_session_delete(self):
        """Permanently remove one owned conversation and its local preferences."""
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        # A completing worker still needs its session row. Refuse deletion
        # conservatively while any Hermes task owns the shared process lock.
        if LOCK.locked():
            self._json(409, {"error": "Wait for Hermes to finish before deleting a chat."})
            return
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            session_id = int(payload.get("id"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_email FROM webchat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not row or (email not in ADMIN_EMAILS and row["owner_email"] != email):
                conn.rollback()
                self._json(404, {"error": "no such conversation"})
                return
            conn.execute("DELETE FROM webchat_model_pref WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM webchat_messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM webchat_sessions WHERE id=?", (session_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._json(200, {"id": session_id, "deleted": True})

    def _handle_creator_interview(self):
        """Choose one bounded next question without polluting chat history.

        The model is an optional planner, not the controller: its JSON is
        validated and every failure falls back to the channel question set.
        The restricted one-shot invocation cannot reach terminal, memory, or
        another operator's conversation.
        """
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        caller_email = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 32768)).decode())
            session_id = int(payload.get("session_id") or 1)
            channel = str(payload.get("channel") or "").strip().lower()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        if channel not in creator_interview.CHANNELS:
            self._json(400, {"error": "choose a supported destination"})
            return
        if not ensure_session(session_id, caller_email):
            self._json(404, {"error": "no such conversation"})
            return
        answers = creator_interview.clean_answers(payload.get("answers"))
        raw_images = payload.get("images") if isinstance(payload.get("images"), list) else []
        media_names = []
        for image in raw_images[:8]:
            if not isinstance(image, dict):
                continue
            owned = _upload_owned(str(image.get("path") or ""), caller_email)
            if owned:
                media_names.append(str(image.get("name") or os.path.basename(owned))[:160])
        fallback = creator_interview.next_fallback(channel, answers)
        # The source question itself is deterministic and should appear
        # instantly. AI judgment starts only after real material exists.
        if not any(answer["id"] == "source" for answer in answers):
            self._json(200, {**fallback, "planned_by": "fallback"})
            return
        # Subreddit and post-route defaults are operating data, not a creative
        # planning decision. Keep these stored choices stable and make our own
        # r/IndiaWatchMods relationship unambiguous on every new Reddit brief.
        if channel == "reddit" and fallback.get("id") in ("community", "reddit_route"):
            self._json(200, {**fallback, "planned_by": "defaults"})
            return
        if fallback.get("status") == "ready":
            self._json(200, {**fallback, "planned_by": "fallback"})
            return
        if not LOCK.acquire(blocking=False):
            self._json(200, {**fallback, "planned_by": "fallback",
                             "notice": "Hermes is busy, so the guided flow kept moving."})
            return
        try:
            prompt = creator_interview.planner_prompt(channel, answers, media_names)
            reply = run_hermes(
                prompt, model="claude-sonnet-4-6", provider="anthropic",
                toolset=NONADMIN_TOOLSET, ignore_rules=True, timeout=90,
            )
            planned = creator_interview.normalize_planner_reply(
                _extract_json_obj(reply), channel, answers)
            self._json(200, {**planned, "planned_by": "hermes"})
        except Exception as exc:
            print(f"[creator-interview] planner fallback: {exc}", flush=True)
            self._json(200, {**fallback, "planned_by": "fallback",
                             "notice": "The guided flow used its safe fallback question."})
        finally:
            LOCK.release()

    def _handle_send(self):
        # Shell access is granted per role, and now so is chat access at
        # all — the PREAMBLE hands every caller a fair amount of business
        # context regardless of tool access, which a deliberately-restricted
        # role (supplier, intake) has no reason to receive.
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        caller_email = (self.headers.get("X-User-Email") or "").strip().lower()
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 32768)).decode())
            message = str(payload.get("message", "")).strip()
            session_id = int(payload.get("session_id") or 1)
            preview_role = str(payload.get("preview_role") or "").strip().lower()
            # images: [{path, name}] — legacy single image_path/image_name still accepted
            raw_images = payload.get("images") or []
            if payload.get("image_path"):
                raw_images.append({"path": payload["image_path"], "name": payload.get("image_name")})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        preview_nonadmin = (caller_email in ADMIN_EMAILS
                            and preview_role in ("full", "creator"))
        tools_for_caller = (CREATOR_PREVIEW_TOOLSET if preview_nonadmin
                            else toolset_for(caller_email))
        isolate_caller = caller_email not in ADMIN_EMAILS or preview_nonadmin
        email = caller_email
        if not ensure_session(session_id, email):
            self._json(404, {"error": "no such conversation"})
            return
        # only accept paths our own /upload handed out (cap 8 per message)
        images = []
        for im in raw_images[:8]:
            raw_path = str((im or {}).get("path") or "")
            p = _upload_owned(raw_path, caller_email)
            if p:
                images.append((p, (im.get("name") or os.path.basename(p)), None))
        if not message and not images:
            self._json(400, {"error": "empty message"})
            return
        # /model is handled locally — instant, no agent run, no lock needed
        if message.startswith("/model"):
            store(session_id, "user", message)
            reply = handle_model_command(session_id, message)
            message_id = store(session_id, "agent", reply)
            self._json(200, {"reply": reply, "message_id": message_id})
            return
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "Still working on the previous task — its result will "
                                      "appear in the thread when done. Send this again after."})
            return
        # From here the LOCK belongs to the worker thread; it releases it when the
        # agent run finishes (which may be long after this HTTP request returned).
        try:
            shown = message or ("(photo)" if len(images) == 1 else f"({len(images)} photos)")
            for _, name, _ in images:
                shown += f"  [attached photo: {name}]"
            if message:
                expanded, content_channel = expand_template(message)
            else:
                expanded = ("Please look at the attached photo." if len(images) == 1
                            else f"Please look at the {len(images)} attached photos.")
                content_channel = None
            images = [(p, n, ocr_image(p)) for p, n, _ in images]
            prompt = build_prompt(
                session_id, expanded, images, admin=not isolate_caller,
                content_channel=content_channel)
            # Build from the existing history, then store this turn. Storing first
            # makes recent() include the message and build_prompt() append it again,
            # which can cause Hermes to interpret one action request twice.
            store(session_id, "user", shown)
            pref_model, pref_provider = get_model_pref(session_id)
            candidates = route_models(pref_model, pref_provider, bool(images))
        except Exception:
            LOCK.release()
            raise

        done = threading.Event()
        outcome = {}

        def worker():
            try:
                last_err = None
                for model, provider, note in candidates:
                    try:
                        print(f"[route] session={session_id} trying model={model or 'hermes-default'}"
                              + (f" ({note})" if note else ""), flush=True)
                        reply = run_hermes(
                            prompt, model, provider, tools_for_caller,
                            ignore_rules=isolate_caller)
                        if content_channel:
                            flags = writing_quality.lint(reply, content_channel)
                            if flags:
                                notes = "\n".join(
                                    f"- {flag['code']}: {flag['message']}"
                                    for flag in flags[:10])
                                editor_prompt = (
                                    prompt + "\n\nYour draft was:\n" + reply
                                    + "\n\nRun one final editorial pass using these flags:\n"
                                    + notes
                                    + "\nPreserve every supplied or verified fact exactly. "
                                      "Do not add claims, numbers or sources. Return the complete "
                                      "revised draft only.")
                                revised = run_hermes(
                                    editor_prompt, model, provider, tools_for_caller,
                                    ignore_rules=isolate_caller)
                                if len(writing_quality.lint(revised, content_channel)) <= len(flags):
                                    reply = revised
                        record_model_use(session_id, model, provider)
                        if note and "auto-switched" in note:
                            reply += f"\n\n*({note} — set `/model claude` to keep it, or `/model default`)*"
                        elif note and model != (pref_model or FAILOVER_CHAIN[0][0]):
                            reply += f"\n\n*(answered by `{model}` — the preferred model was unavailable)*"
                        outcome["reply"] = reply
                        break
                    except subprocess.TimeoutExpired:
                        # genuinely long-running — the model worked, don't burn another 45 min
                        outcome["reply"] = ("I worked on this for 45 minutes and had to stop — parts "
                                            "of it may have completed. Ask me what got done, or break "
                                            "the request into smaller steps.")
                        break
                    except Exception as e:
                        last_err = e
                        continue
                if "reply" not in outcome:
                    outcome["reply"] = f"All models failed — last error: {last_err}"
            finally:
                try:
                    outcome["message_id"] = store(
                        session_id, "agent", outcome.get("reply") or "(no reply)")
                finally:
                    for image_path, _name, _ocr in images:
                        _forget_upload(image_path, remove_data=True)
                    done.set()
                    LOCK.release()

        threading.Thread(target=worker, daemon=True).start()
        if done.wait(HERMES_SOFT_WAIT):
            self._json(200, {
                "reply": outcome.get("reply"),
                "message_id": outcome.get("message_id"),
            })
        else:
            self._json(200, {"pending": True, "reply": None})

    def _handle_upload(self):
        # This endpoint backs role-visible image features in Orders, Intake,
        # Supplier, Command, and Reddit. The follow-up endpoint still decides
        # which records the caller is allowed to attach the upload to.
        if not (self._supplier_ok() or any(self._has_tool(tool)
                   for tool in ("orders", "intake", "chat", "reddit"))):
            self._json(403, {"error": "not available for this account"})
            return
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        length = int(self.headers.get("Content-Length", 0))
        if ("multipart/form-data" not in ctype or not m
                or length <= 0 or length > MAX_UPLOAD_BYTES):
            self._json(400, {"error": f"expected a multipart image (jpg, png, or webp) under {MAX_UPLOAD_MB} MB"})
            return
        body = self.rfile.read(length)
        boundary = ("--" + m.group(1)).encode()
        for part in body.split(boundary):
            if b"Content-Disposition" not in part or b'name="image"' not in part:
                continue
            header_blob, _, data = part.partition(b"\r\n\r\n")
            # The multipart delimiter contributes one CRLF after the payload.
            # rstrip(b"\\r\\n-") corrupts valid images whose last byte happens
            # to be a dash or newline.
            if data.endswith(b"\r\n"):
                data = data[:-2]
            fn = re.search(rb'filename="([^"]*)"', header_blob)
            orig = fn.group(1).decode(errors="replace") if fn else "image"
            ext = os.path.splitext(orig)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                self._json(400, {"error": "only jpg, png, or webp images"})
                return
            # magic-byte check: refuse files that aren't really images
            if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
                    or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
                self._json(400, {"error": "file does not look like an image"})
                return
            try:
                path = _store_user_upload(self._order_user(), ext, data=data)
            except ValueError as e:
                self._json(409, {"error": str(e)})
                return
            except OSError:
                self._json(507, {"error": "the server could not store that upload"})
                return
            self._json(200, {"path": path})
            return
        self._json(400, {"error": "no image field found"})

    def _handle_export_pdf(self):
        if not self._admin_email():
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 512 * 1024)).decode())
            markdown = str(payload.get("markdown", "")).strip()
            title = str(payload.get("title", "Timelabs export")).strip()[:120] or "Timelabs export"
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        if not markdown:
            self._json(400, {"error": "nothing to export"})
            return
        try:
            pdf = make_pdf(markdown, title)
        except Exception as e:
            self._json(500, {"error": f"pdf generation failed: {e}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf)))
        self.send_header("Content-Disposition", 'attachment; filename="export.pdf"')
        self.end_headers()
        self.wfile.write(pdf)

    def _handle_plan_toggle(self):
        if not self._admin_email():
            self._json(403, {"error": "admins only"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode())
            item_id = int(payload.get("id"))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        conn = db()
        row = conn.execute("SELECT status FROM action_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such item"})
            return
        if row["status"] == "done":
            conn.execute("UPDATE action_items SET status='todo', done_at=NULL WHERE id=?", (item_id,))
            new_status = "todo"
        else:
            conn.execute(
                "UPDATE action_items SET status='done', done_at=datetime('now') WHERE id=?", (item_id,)
            )
            new_status = "done"
        conn.commit()
        conn.close()
        # re-render the dashboard so the change shows on next load
        subprocess.Popen(
            ["/root/ops-dashboard/venv/bin/python3", "/root/ops-dashboard/generate.py"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._json(200, {"id": item_id, "status": new_status})

    RESEARCH_PROMPT = (
        "You are Timelabs Co's marketing manager. Run a FRESH competitor research pass using "
        "your web search tools (do real searches now, do not answer from memory). Cover: direct "
        "India Seiko-mod sellers (India Mod Watches, Moddy's, AG Watch Studio, and any new "
        "entrants you find), international standard-setters (Circa Watch Labs, namokiMODS), and "
        "adjacent alternatives a Rs20k Indian watch buyer considers. Compare on price, delivery, "
        "warranty, COD, configurator, Instagram scale, weaknesses. Timelabs context: Rs17k-27k, "
        "5-10 day delivery edge, branded lines, WhatsApp-close sales.\n\n"
        "Return ONLY a Markdown report in exactly this structure: '# Competition Research — "
        "<date>' / '## Verdict' (3 sentences) / '## Direct competitors' (comparison table) / "
        "'## What standards internationals set' / '## Adjacent alternatives buyers weigh' / "
        "'## Market signals' / '## Recommended moves' (max 5, numbered). Under 1100 words. "
        "Cite sources as inline URLs. No preamble, no closing note — the report only."
    )

    def _handle_research_run(self):
        if not self._admin_email():
            self._json(403, {"error": "admins only"})
            return
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "agent is busy — try again shortly"})
            return
        try:
            try:
                result = subprocess.run(
                    ["hermes", "-t", SAFE_WEB_TOOLSET, "-z", self.RESEARCH_PROMPT],
                    capture_output=True, text=True, timeout=280,
                )
                report = result.stdout.strip()
                if result.returncode != 0 or len(report) < 300 or not report.startswith("#"):
                    raise RuntimeError(result.stderr[-200:] or "report too short or malformed")
            except subprocess.TimeoutExpired:
                self._json(504, {"error": "research timed out — try again"})
                return
            except Exception as e:
                self._json(500, {"error": f"research failed: {e}"})
                return
            conn = db()
            conn.execute(
                "INSERT INTO competitor_research (source, report_md) VALUES ('hermes', ?)",
                (report,),
            )
            conn.commit()
            conn.close()
            subprocess.Popen(
                ["/root/ops-dashboard/venv/bin/python3", "/root/ops-dashboard/generate.py"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._json(200, {"ok": True, "chars": len(report)})
        finally:
            LOCK.release()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/send":
            self._handle_send()
        elif path == "/research/run":
            self._handle_research_run()
        elif path == "/plan/toggle":
            self._handle_plan_toggle()
        elif path == "/session/new":
            self._handle_new_session()
        elif path == "/session/rename":
            self._handle_session_rename()
        elif path == "/session/model":
            self._handle_session_model()
        elif path == "/session/archive":
            self._handle_session_archive()
        elif path == "/session/delete":
            self._handle_session_delete()
        elif path == "/creator/interview":
            self._handle_creator_interview()
        elif path == "/upload":
            self._handle_upload()
        elif path == "/export/pdf":
            self._handle_export_pdf()
        elif path == "/allowlist/add":
            self._handle_allowlist_change("add")
        elif path == "/allowlist/remove":
            self._handle_allowlist_change("remove")
        elif path == "/ledger/import":
            self._handle_ledger_import()
        elif path == "/orders/create":
            self._handle_order_create()
        elif path == "/orders/parse":
            self._handle_orders_parse()
        elif path == "/orders/update":
            self._handle_orders_update()
        elif path == "/orders/customer-payment":
            self._handle_order_customer_payment()
        elif path == "/orders/photos/update":
            self._handle_order_photos_update()
        elif path == "/orders/items/relabel":
            self._handle_order_items_relabel()
        elif path == "/supplier/status":
            self._handle_supplier_status()
        elif path == "/supplier/photos/update":
            self._handle_order_photos_update(supplier_only=True)
        elif path == "/supplier/note":
            self._handle_supplier_note()
        elif path == "/supplier/tracking":
            self._handle_supplier_tracking()
        elif path == "/supplier/whatsapp":
            self._handle_supplier_whatsapp()
        elif path == "/supplier/bulk":
            self._handle_supplier_bulk()
        elif path == "/supplier/shipment/save":
            self._handle_supplier_shipment_save()
        elif path == "/supplier/shipment/assign":
            self._handle_supplier_shipment_assign()
        elif path == "/supplier/cost":
            self._handle_supplier_cost()
        elif path == "/supplier/cost/bulk":
            self._handle_supplier_cost_bulk()
        elif path == "/supplier/bill/create":
            self._handle_supplier_bill_create()
        elif path == "/supplier/bill/update":
            self._handle_supplier_bill_update()
        elif path == "/supplier/bill/acknowledge":
            self._handle_supplier_bill_acknowledge()
        elif path == "/supplier/bill/delete":
            self._handle_supplier_bill_delete()
        elif path == "/supplier/bill/whatsapp":
            self._handle_supplier_bill_whatsapp()
        elif path == "/supplier/bill/tracking":
            self._handle_supplier_bill_tracking()
        elif path == "/reddit/setup/draft":
            self._handle_reddit_setup_draft()
        elif path == "/reddit/setup/save":
            self._handle_reddit_setup_save()
        elif path == "/reddit/reply/create":
            self._handle_reddit_reply_create()
        elif path == "/reddit/reply/update":
            self._handle_reddit_reply_update()
        elif path == "/reddit/drop-photo":
            self._handle_reddit_drop_photo()
        elif path == "/reddit/post/create":
            self._handle_reddit_post_create()
        elif path == "/reddit/post/answer":
            self._handle_reddit_post_answer()
        elif path == "/reddit/post/retry":
            self._handle_reddit_post_retry()
        elif path == "/reddit/post/update":
            self._handle_reddit_post_update()
        elif path == "/reddit/sync":
            self._handle_reddit_sync()
        elif path == "/supplier/payment/record":
            self._handle_supplier_payment_record()
        elif path == "/orders/delete":
            self._handle_orders_delete()
        elif path == "/orders/unhide":
            self._handle_orders_unhide()
        elif path == "/orders/bulk":
            self._handle_orders_bulk()
        elif path == "/access/set":
            self._handle_access_set()
        elif path == "/access/invite":
            self._handle_access_invite()
        elif path == "/shopify/product/ai-draft":
            self._handle_shopify_ai_draft()
        elif path == "/shopify/product/create":
            self._handle_shopify_product_create()
        elif path == "/shopify/theme/suggest":
            self._handle_shopify_theme_suggest()
        elif path == "/shopify/theme/apply":
            self._handle_shopify_theme_apply()
        elif path == "/shopify/theme/revert":
            self._handle_shopify_theme_revert()
        elif path == "/shopify/article/save":
            self._handle_shopify_article_save()
        elif path == "/shopify/page/update":
            self._handle_shopify_page_update()
        elif path == "/shopify/products/bulk-content":
            self._handle_shopify_bulk_content()
        elif path == "/shopify/product/update":
            self._handle_shopify_product_update()
        elif path == "/shopify/product/media/add":
            self._handle_shopify_media("add")
        elif path == "/shopify/product/media/remove":
            self._handle_shopify_media("remove")
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    # Bind first. A second/manual invocation must fail on the occupied port
    # before it can reset the live process's running Reddit jobs to queued.
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    _recover_reddit_post_jobs()
    server.serve_forever()
