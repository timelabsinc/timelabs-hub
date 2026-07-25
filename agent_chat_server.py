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
import http.server
import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request

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
THEME_BACKUPS = "/root/ops-dashboard/theme-backups"
FS_ROOT = "/root"   # System-files browser is confined to the Hermes home
# Chat-photo cap. Modern phone photos routinely exceed the old 11 MB ceiling;
# 32 MB stays comfortably under nginx's 50m on this vhost. Large videos go
# through Drop (copyparty), not this endpoint. Formats stay jpg/png/webp — the
# only ones tesseract OCR and browser <img> render without a HEIC decoder.
MAX_UPLOAD_MB = 32
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
LOCK = threading.Lock()
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
SHOPIFY_SCOPES = (
    "read_products,write_products,"
    "read_content,write_content,"
    "read_online_store_pages,write_online_store_pages,"
    "read_online_store_navigation,write_online_store_navigation,"
    "read_themes,write_themes,"
    "read_orders,"
    "read_inventory,write_inventory,"
    "read_customers"
)
SHOPIFY_REDIRECT = "https://ops.timelabsco.in/ops/agent/api/shopify/callback"
_shopify_states = set()


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
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(ENV_FILE, 0o600)


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
os.makedirs(THEME_BACKUPS, exist_ok=True)


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
            # Off by default so the supplier queue starts empty rather than
            # dumping every historical order on day one; every new order
            # (either write path) turns this on for itself at creation.
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
            "local_photos": "TEXT"}
    try:
        conn = sqlite3.connect(DB, timeout=5)
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
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[orders] schema check: {e}", flush=True)


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
                          ("posted_at", "TEXT")):
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
                          # Several people posting to one sub needs a rota, or
                          # you get two posts on Tuesday and nothing until Friday.
                          ("slot_date", "TEXT"), ("assigned_to", "TEXT")):
            if col not in pcols:
                conn.execute(f"ALTER TABLE reddit_posts ADD COLUMN {col} {decl}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[reddit] schema check: {e}", flush=True)


def _ensure_billing_schema():
    """What the supplier charges us, per build, and the bills that collect it.

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
    try:
        conn = sqlite3.connect(DB, timeout=5)
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
            cost REAL NOT NULL DEFAULT 0)""")
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
        conn.close()
    except Exception as e:
        print(f"[billing] schema check: {e}", flush=True)


_ensure_orders_schema()
_ensure_order_items_schema()
_ensure_order_events_schema()
_ensure_shipments_schema()
_ensure_reddit_schema()
_ensure_billing_schema()


def _fs_resolve(p):
    """Resolve a client path, confined to FS_ROOT (blocks symlink escape/..)."""
    rp = os.path.realpath(p or FS_ROOT)
    if rp != FS_ROOT and not rp.startswith(FS_ROOT + os.sep):
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


def _download_images(urls, limit=4):
    """Fetch a few image URLs (Drop /raw links or external) to local temp files
    so the vision model can see them. Returns [(path, url)], skipping failures."""
    saved = []
    for u in urls[:limit]:
        u = str(u).strip()
        if not u.startswith(("http://", "https://")):
            continue
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "LabsOS/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
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

# Jasper-style templates: a slash command expands into a structured brief.
TEMPLATES = {
    "/caption": (
        "Write an Instagram caption in the Timelabs brand voice. Structure: hook line "
        "(no 'introducing'), 2-3 short lines of concrete detail (movement, build, delivery), "
        "soft CTA, then 8-12 niche hashtags on the last line. Give 2 variants. Brief: "
    ),
    "/ad": (
        "Write Meta ad copy in the Timelabs brand voice: 3 primary-text variants "
        "(under 125 chars each), 3 headlines (under 40 chars), 1 description (under 30 chars). "
        "Lead with a concrete trust anchor, soft CTA. Present as a table. Brief: "
    ),
    "/product": (
        "Write a Shopify product description in the Timelabs brand voice: 2-3 sentence "
        "opening (design story, movement, one distinctive detail), then a specs list "
        "(movement, case size, glass, strap, water resistance, delivery 5-10 days insured), "
        "no emoji, no superlatives without evidence. Brief: "
    ),
    "/email": (
        "Write a customer email in the Timelabs brand voice: subject line (under 45 chars, "
        "no clickbait), founder-personal body under 120 words, one soft CTA. Give 2 subject "
        "variants. Brief: "
    ),
    "/post": (
        "Write this week's WhatsApp community post per the playbook calendar in the Timelabs "
        "brand voice: under 80 words, one concrete detail, ends with the soft handle. Check "
        "content_library for the last community-post to avoid repeating. Brief: "
    ),
}


def expand_template(message):
    for cmd, brief in TEMPLATES.items():
        if message.lower().startswith(cmd):
            return brief + message[len(cmd):].strip()
    return message


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def store(session_id, role, text):
    conn = db()
    conn.execute(
        "INSERT INTO webchat_messages (session_id, role, text) VALUES (?, ?, ?)",
        (session_id, role, text),
    )
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


def ensure_session(session_id):
    conn = db()
    row = conn.execute("SELECT id FROM webchat_sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    return bool(row)


def recent(session_id, limit):
    conn = db()
    rows = conn.execute(
        "SELECT role, text FROM webchat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed(rows))


def build_prompt(session_id, message, images=None):
    """images: list of (path, name, ocr_text) — supports multi-photo messages."""
    lines = [PREAMBLE]
    for row in recent(session_id, CONTEXT_TURNS):
        speaker = "Owner" if row["role"] == "user" else "You"
        lines.append(f"{speaker}: {row['text']}")
    for i, (path, name, ocr_text) in enumerate(images or [], 1):
        n = f" {i} of {len(images)}" if len(images) > 1 else ""
        lines.append(
            f"\nThe owner attached a photo{n} ({name or 'image'}), saved at "
            f"{path} — analyze it with your vision tool as part of answering."
        )
        if ocr_text:
            lines.append(
                "\nOCR text extracted from this photo (verbatim, may contain recognition "
                f"errors — trust your vision reading over this where they differ):\n{ocr_text}"
            )
    lines.append(f"\nOwner's new message: {message}\n\nYour reply:")
    return "\n".join(lines)


# ---------------------------------------------------------------------- PDF export
def md_to_html(md):
    """Small, safe markdown -> HTML for PDF export (escape first, then transform)."""
    out, lines, i = [], html_mod.escape(md).split("\n"), 0
    def inline(s):
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<em>\2</em>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?:[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        return s
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
        elif re.match(r"^#{1,3}\s", l):
            n = len(re.match(r"^#+", l).group())
            out.append(f"<h{n}>" + inline(re.sub(r"^#+\s*", "", l)) + f"</h{n}>")
            i += 1
        elif re.match(r"^(-{3,}|\*{3,})\s*$", l):
            out.append("<hr>"); i += 1
        elif re.match(r"^\s*&gt;\s?", l):
            buf = []
            while i < len(lines) and re.match(r"^\s*&gt;\s?", lines[i]):
                buf.append(re.sub(r"^\s*&gt;\s?", "", lines[i])); i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
        elif re.match(r"^\s*([-*]|\d+\.)\s+", l):
            tag = "ol" if re.match(r"^\s*\d+\.", l) else "ul"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])) + "</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
        elif "|" in l and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            def row(r):
                return [inline(c.strip()) for c in r.strip().strip("|").split("|")]
            head = row(l); i += 2
            body = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body.append(row(lines[i])); i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
        elif not l.strip():
            i += 1
        else:
            buf = [l]; i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,3}\s|```|\s*([-*]|\d+\.)\s)", lines[i]) and "|" not in lines[i]:
                buf.append(lines[i]); i += 1
            out.append("<p>" + inline("<br>".join(buf)) + "</p>")
    return "".join(out)


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

# Admins additionally get `terminal`, which is what lets the agent actually DO
# things via the `labs` CLI instead of only describing them. Granted by role,
# never globally: /send is reachable by any signed-in user, so a blanket grant
# would let a restricted role (e.g. intake) ask the agent to run commands as
# root. Admins already hold root SSH, so for them this adds no new privilege.
ADMIN_TOOLSET = SAFE_WEB_TOOLSET + ",terminal"


def toolset_for(email):
    return ADMIN_TOOLSET if (email or "").strip().lower() in ADMIN_EMAILS else SAFE_WEB_TOOLSET


def run_hermes(prompt, model=None, provider=None, toolset=None):
    cmd = ["hermes"]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    cmd += ["-t", toolset or SAFE_WEB_TOOLSET]
    cmd += ["-z", prompt]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=HERMES_HARD_TIMEOUT,
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


def _init_prefs():
    conn = db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS webchat_model_pref ("
        "session_id INTEGER PRIMARY KEY, model TEXT, provider TEXT)"
    )
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
    A None model means 'hermes default routing' (Claude-primary per SOUL)."""
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
            else "Hermes default (Claude-primary routing per its identity)"
        return (f"Current model for this chat: {cur}\n\n"
                "Switch with `/model claude`, `/model minimax`, `/model free`, "
                "`/model <exact-model-name> [provider]`, or `/model default` to reset.")
    conn = db()
    if parts[1].lower() in ("default", "reset", "auto"):
        conn.execute("DELETE FROM webchat_model_pref WHERE session_id=?", (session_id,))
        conn.commit(); conn.close()
        return "Model reset — Hermes decides (Claude-primary routing)."
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

    def do_GET(self):
        path, _, query = self.path.partition("?")
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
            self._handle_access_me()
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
            conn = db()
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, "
                "(SELECT COUNT(*) FROM webchat_messages m WHERE m.session_id = s.id) AS n "
                "FROM webchat_sessions s ORDER BY s.updated_at DESC"
            ).fetchall()
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
            conn = db()
            rows = conn.execute(
                "SELECT role, text, created_at FROM webchat_messages "
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
            if email in members:
                self._json(200, {"ok": True, "note": "already a member", "members": members})
                return
            members.append(email)
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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
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
        state = secrets.token_urlsafe(16)
        _shopify_states.add(state)
        from urllib.parse import urlencode
        url = f"https://{creds['shop']}/admin/oauth/authorize?" + urlencode({
            "client_id": creds["client_id"], "scope": SHOPIFY_SCOPES,
            "redirect_uri": SHOPIFY_REDIRECT, "state": state,
        })
        self._redirect(url)

    def _handle_shopify_callback(self, query):
        from urllib.parse import parse_qsl
        params = dict(parse_qsl(query))
        creds = self._shopify_creds()
        code, state = params.get("code"), params.get("state")
        shop = params.get("shop") or (creds or {}).get("shop", "")
        if not creds or not code or state not in _shopify_states:
            self._redirect("/ops/tools.html?shopify=failed")
            return
        _shopify_states.discard(state)
        import urllib.request
        from urllib.parse import urlencode
        body = urlencode({"client_id": creds["client_id"],
                          "client_secret": creds["client_secret"], "code": code}).encode()
        try:
            req = urllib.request.Request(f"https://{shop}/admin/oauth/access_token", data=body)
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
            access = tok.get("access_token")
        except Exception as e:
            print(f"[shopify] token exchange failed: {e}", flush=True)
            self._redirect("/ops/tools.html?shopify=failed")
            return
        if not access:
            self._redirect("/ops/tools.html?shopify=failed")
            return
        write_env({"SHOPIFY_SHOP": shop, "SHOPIFY_ADMIN_TOKEN": access})
        email = (self.headers.get("X-User-Email") or "?").strip().lower()
        print(f"[shopify] connected by {email}", flush=True)
        hub_event("shopify_connected", "Shopify store connected", email, app="shopify")
        self._redirect("/ops/tools.html?shopify=connected")

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
    def _handle_access_me(self):
        """Any signed-in user: their own role + allowed tools. Drives nav/tile
        filtering, so a page never offers a tool the person can't open."""
        email = (self.headers.get("X-User-Email") or "").strip().lower()
        import access_store
        role = access_store.get_role(email)
        if not role:
            self._json(200, {"email": "", "role": None, "tools": [], "home": "/ops/"})
            return
        spec = access_store.ROLES[role]
        self._json(200, {"email": email, "role": role, "label": spec["label"],
                         "tools": spec["tools"], "home": spec["home"],
                         "admin": role == "admin"})

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
        if not email or role not in access_store.ROLES:
            self._json(400, {"error": "need an email and a valid role"}); return
        try:
            access_store.set_role(email, role)
        except ValueError as e:
            self._json(400, {"error": str(e)}); return
        ok, note = access_store.sync_nginx()
        actor = (self.headers.get("X-User-Email") or "?").strip().lower()
        hub_event("access_change", f"{email} -> {role}", actor, app="key")
        self._json(200, {"ok": True, "email": email, "role": role,
                         "gate": note, "gate_ok": ok})

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
            theme, raw, data = shopify_api.get_settings_data()
            new_current, applied, skipped = shopify_api.apply_theme_patch(data["current"], patch)
            if not applied:
                self._json(200, {"ok": True, "applied": 0, "skipped": len(skipped)}); return
            ts = int(time.time())
            with open(os.path.join(THEME_BACKUPS, f"{theme['id']}-{ts}.json"), "w") as f:
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
        backups = sorted(f for f in os.listdir(THEME_BACKUPS) if f.startswith(theme["id"] + "-"))
        if not backups:
            self._json(404, {"error": "nothing to revert"}); return
        newest = os.path.join(THEME_BACKUPS, backups[-1])
        try:
            with open(newest) as f:
                raw = f.read()
            shopify_api.put_settings_data(theme["id"], raw)
        except shopify_api.ShopifyError as e:
            self._json(502, {"error": str(e)}); return
        os.remove(newest)   # consume the undo step
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
        if (self.headers.get("X-User-Email") or "").strip().lower() not in ADMIN_EMAILS:
            self._json(403, {"error": "admins only"}); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 262144)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"}); return
        import shopify_api
        if not shopify_api.configured():
            self._json(400, {"error": "Shopify isn't connected — open Tools and connect it."}); return
        ids = [i for i in (p.get("ids") or []) if i][:100]
        op = p.get("op")
        if op not in ("append_block", "prepend_block", "replace"):
            self._json(400, {"error": "unknown operation"}); return
        if op == "replace" and not str(p.get("find", "")).strip():
            self._json(400, {"error": "give the text to find"}); return
        if op in ("append_block", "prepend_block") and not str(p.get("block", "")).strip():
            self._json(400, {"error": "the block is empty"}); return
        dry = bool(p.get("dry_run"))
        results, sample = [], None
        for pid in ids:
            try:
                d = shopify_api.get_detail(pid)
                before = d["description"]
                after = apply_content_op(before, op, block=p.get("block", ""),
                                         name=p.get("name", ""), find=p.get("find", ""),
                                         replace=p.get("replace", ""))
                changed = after != before
                if changed and not dry:
                    shopify_api.update_fields(pid, {"descriptionHtml": after})
                results.append({"id": pid, "title": d["title"], "changed": changed,
                                "delta": len(after) - len(before)})
                if changed and sample is None:
                    sample = {"title": d["title"], "before": before[:1400], "after": after[:1400]}
            except shopify_api.ShopifyError as e:
                results.append({"id": pid, "title": pid.split("/")[-1], "error": str(e)})
        n_changed = sum(1 for r in results if r.get("changed"))
        if not dry and n_changed:
            actor = (self.headers.get("X-User-Email") or "?").strip().lower()
            label = {"append_block": "appended a block to", "prepend_block": "prepended a block to",
                     "replace": "find/replaced in"}[op]
            hub_event("bulk_content", f"{label} {n_changed} product"
                      + ("s" if n_changed != 1 else ""), actor, app="shopify")
        self._json(200, {"results": results, "changed": n_changed,
                         "total": len(results), "dry_run": dry, "sample": sample})

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
        phone = str(p.get("customer_phone", "")).strip()[:40]
        email = str(p.get("customer_email", "")).strip()[:200]
        address = str(p.get("address", "")).strip()[:600]
        pincode = str(p.get("pincode", "")).strip()[:20]
        notes = str(p.get("notes", "")).strip()[:1000]
        status = str(p.get("status", "new")).strip()[:40] or "new"
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
            qty = max(1, int(p.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        try:
            price = float(p.get("price_inr")) if str(p.get("price_inr", "")).strip() else None
        except (TypeError, ValueError):
            price = None

        # Photos are whatever /upload just wrote — confine each to UPLOAD_DIR so a
        # crafted path can't push an arbitrary server file to Drive.
        raw_photos = p.get("photo_paths") or ([p["photo_path"]] if p.get("photo_path") else [])
        photos = []
        upload_root = os.path.realpath(UPLOAD_DIR) + os.sep
        for cand in raw_photos[:10]:
            rp = os.path.realpath(str(cand).strip())
            if not rp.startswith(upload_root) or not os.path.isfile(rp):
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
        cur = conn.execute(
            "INSERT INTO orders (customer_name, customer_phone, customer_email, address, "
            "pincode, city, state, source, product, price_inr, quantity, notes, status, "
            "has_image, drive_link, photo_links, case_style, dial_colour, dial_style, "
            "case_colour, movement, watch_size, supplier_visible) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (customer, phone, email, address, pincode, city, state, source, product,
             price, qty, notes, status, 1 if photos else 0, drive_link,
             json.dumps(links) if links else None,
             attrs.get("case_style"), attrs.get("dial_colour"), attrs.get("dial_style"),
             attrs.get("case_colour"), attrs.get("movement"), attrs.get("watch_size")))
        oid = cur.lastrowid

        # Move the photos somewhere permanent BEFORE reporting success. They
        # used to exist only as Drive URLs, so with Google disconnected an
        # attached photo was written to a temp dir, referenced by nothing, and
        # effectively lost. Drive is now a mirror of these, not the record.
        stored = []
        if photos:
            dest_dir = os.path.join(ORDER_PHOTOS, str(oid))
            try:
                os.makedirs(dest_dir, exist_ok=True)
                for i, fp in enumerate(photos):
                    fn = f"{i + 1}{os.path.splitext(fp)[1].lower()}"
                    try:
                        os.replace(fp, os.path.join(dest_dir, fn))
                    except OSError:      # different filesystem — copy instead
                        with open(fp, "rb") as src, open(os.path.join(dest_dir, fn), "wb") as dst:
                            dst.write(src.read())
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    stored.append(fn)
            except Exception as e:
                warnings.append(f"A photo couldn't be filed: {e}")
            if stored:
                conn.execute("UPDATE orders SET local_photos=? WHERE id=?",
                            (json.dumps(stored), oid))
        if not access:
            warnings.append(
                "Google isn't connected, so this order didn't reach the sheet"
                + (" (the photos are saved here and visible to your supplier)" if stored else "")
                + ". Open Drop and reconnect Google.")

        conn.execute(
            "INSERT INTO order_items (order_id, product, quantity, price_inr, line_total) "
            "VALUES (?,?,?,?,?)",
            (oid, product, qty, price, (price or 0) * qty))
        order_event(conn, oid, "created",
                   f"logged via {source or 'order form'} with status {status}", actor)
        row = conn.execute("SELECT received_at FROM orders WHERE id=?", (oid,)).fetchone()
        conn.commit()
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
                    "Order #": oid, "Logged": logged, "Status": status, "Source": source,
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

        hub_event("order_created", f"{product} ×{qty} for {customer}", actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "drive_link": drive_link,
                         "attributes": {k: v for k, v in attrs.items() if v},
                         "customer": {"orders": cust["orders_count"],
                                      "tags": customers_mod.all_tags(cust)} if cust else None,
                         "warnings": warnings})

    def _handle_orders_list(self):
        if not self._has_tool("orders"):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        try:
            rows = conn.execute(
                "SELECT id, received_at, customer_name, customer_phone, customer_email, "
                "address, pincode, city, state, source, product, quantity, price_inr, "
                "notes, status, drive_link, photo_links, local_photos, case_style, "
                "dial_colour, dial_style, case_colour, movement, watch_size, shopify_name, "
                "ref_code FROM orders ORDER BY id DESC LIMIT 100").fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()
        import google_api
        self._json(200, {"orders": [dict(r) for r in rows],
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
        "WHERE o.status != 'cancelled' "
        "AND (o.financial_status IS NULL OR o.financial_status NOT IN ('refunded','voided'))")

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
                    f"WHERE {field} IS NOT NULL AND {field} != '' AND status != 'cancelled' "
                    f"GROUP BY {field} ORDER BY units DESC LIMIT 8")]
            totals = conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(price_inr,0)*COALESCE(quantity,1)),0) "
                "revenue, SUM(CASE WHEN source='website' THEN 1 ELSE 0 END) website FROM orders "
                "WHERE status != 'cancelled' AND (financial_status IS NULL "
                "OR financial_status NOT IN ('refunded','voided'))").fetchone()
            ncust = conn.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
            top_products = [dict(r) for r in conn.execute(
                "SELECT COALESCE(NULLIF(oi.canonical_product,''), oi.product) AS name, "
                "SUM(oi.quantity) AS units, COUNT(DISTINCT oi.order_id) AS orders, "
                "ROUND(SUM(oi.line_total), 2) AS revenue, MAX(o.received_at) AS last_sold "
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
                         "totals": {"orders": totals["n"], "revenue": totals["revenue"] or 0,
                                    "customers": ncust}})

    def _handle_orders_update(self):
        """Patch an existing order. Built mainly so a status can move past
        'new' at all — there was no write path for that before this — but
        takes any of the same descriptive fields /orders/create does, so a
        typo doesn't require re-entering the whole record. Deliberately
        excludes product/quantity/price: those are the receipt of what was
        actually ordered and charged, and shouldn't shift under a general
        PATCH — renaming a product for analytics goes through
        /orders/items/relabel instead, which is retroactive and explicit."""
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
        # Always editable: moving an order along, recording what happened, and
        # correcting a courier reference are things you do *because* it's in
        # flight, so locking them would be backwards.
        always = {"status": 40, "notes": 1000, "tracking_code": 80}
        # Everything describing what gets built and who it's for. Frozen once
        # the supplier has been paid: from that point they've committed money
        # to parts, and a spec that changes underneath them silently is how
        # the wrong watch gets built.
        lockable = {"customer_name": 120, "customer_phone": 40, "customer_email": 200,
                    "address": 600, "city": 80, "state": 80, "pincode": 20,
                    "product": 200, "case_style": 60, "dial_colour": 60,
                    "dial_style": 60, "case_colour": 60, "movement": 60,
                    "watch_size": 60}
        conn = db()
        before = conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()
        if not before:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        from order_form import STATUSES
        cur_status = before["status"] or "new"
        try:
            locked = STATUSES.index(cur_status) >= STATUSES.index("paid")
        except ValueError:
            locked = False          # unknown status: don't block on a guess
        if locked:
            blocked = [k for k in lockable if k in p]
            if blocked:
                conn.close()
                self._json(409, {
                    "error": f"This order is already \"{cur_status}\" — the supplier has "
                             f"been paid and may have bought parts, so the build details "
                             f"are locked. You can still change its status, notes and "
                             f"tracking.",
                    "locked_fields": blocked})
                return
        updatable = dict(always)
        if not locked:
            updatable.update(lockable)
        sets, vals = [], []
        for k, maxlen in updatable.items():
            if k in p:
                sets.append(f"{k}=?")
                vals.append(str(p[k]).strip()[:maxlen])
        if not sets:
            conn.close()
            self._json(400, {"error": "nothing to update"})
            return
        vals.append(oid)
        conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", vals)
        # A status move goes on the order's own timeline, so the supplier sees
        # "paid" appear with a date rather than a value silently changing.
        if "status" in p and str(p["status"]).strip() != (before["status"] or ""):
            order_event(conn, oid, "status",
                       f'{before["status"] or "new"} -> {str(p["status"]).strip()}', actor)
        conn.commit()
        conn.close()
        hub_event("order_updated", f"#{oid}: " + ", ".join(k for k in updatable if k in p),
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid})

    def _handle_orders_bulk(self):
        """One build per screenshot. Send a batch of watch photos and each
        becomes its own order on the supplier queue — no customer yet, that
        gets attached when it sells.

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
        upload_root = os.path.realpath(UPLOAD_DIR) + os.sep

        conn = db()
        created, failed = [], []
        for i, it in enumerate(items[:60]):
            try:
                product = str((it or {}).get("product", "")).strip()[:200]
                photo = str((it or {}).get("photo_path", "")).strip()
                ref = str((it or {}).get("ref_code", "")).strip()[:60]
                rp = os.path.realpath(photo) if photo else ""
                if photo and (not rp.startswith(upload_root) or not os.path.isfile(rp)):
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
                    qty = max(1, int((it or {}).get("quantity") or 1))
                except (TypeError, ValueError):
                    qty = 1
                try:
                    price = (float(it["price_inr"])
                             if str((it or {}).get("price_inr", "")).strip() else None)
                except (TypeError, ValueError):
                    price = None
                attrs = order_taxonomy.extract(product + " " + notes)
                cur = conn.execute(
                    "INSERT INTO orders (customer_name, source, product, price_inr, "
                    "quantity, notes, status, has_image, ref_code, case_style, dial_colour, "
                    "dial_style, case_colour, movement, watch_size, supplier_visible) "
                    "VALUES ('',?,?,?,?,?,'new',?,?,?,?,?,?,?,?,1)",
                    (source, product, price, qty, notes, 1 if rp else 0, ref or None,
                     attrs.get("case_style"), attrs.get("dial_colour"),
                     attrs.get("dial_style"), attrs.get("case_colour"),
                     attrs.get("movement"), attrs.get("watch_size")))
                oid = cur.lastrowid
                if rp:
                    dest = os.path.join(ORDER_PHOTOS, str(oid))
                    os.makedirs(dest, exist_ok=True)
                    fn = "1" + os.path.splitext(rp)[1].lower()
                    try:
                        os.replace(rp, os.path.join(dest, fn))
                    except OSError:
                        with open(rp, "rb") as s, open(os.path.join(dest, fn), "wb") as d:
                            d.write(s.read())
                        try:
                            os.remove(rp)
                        except OSError:
                            pass
                    conn.execute("UPDATE orders SET local_photos=? WHERE id=?",
                                (json.dumps([fn]), oid))
                conn.execute(
                    "INSERT INTO order_items (order_id, product, quantity, price_inr, "
                    "line_total) VALUES (?,?,?,?,?)",
                    (oid, product, qty, price, (price or 0) * qty))
                order_event(conn, oid, "created",
                           f"backfilled in a bulk batch{f' (order {ref})' if ref else ''}", actor)
                conn.commit()
                created.append({"id": oid, "product": product, "ref_code": ref})
            except Exception as e:
                failed.append({"i": i, "error": str(e)[:120]})
        conn.close()
        if created:
            hub_event("orders_bulk", f"{len(created)} build(s) queued from a photo batch",
                     actor, app="orders")
        self._json(200, {"ok": True, "created": created, "failed": failed})

    def _handle_orders_delete(self):
        """Remove an order completely — the row, its line items, its timeline
        and its photos. A real delete, not a hidden flag, because the request
        was to get rid of test and mistaken orders rather than archive them.

        The derived customer is repaired afterwards rather than left behind:
        their totals are recomputed from what's left, and a customer with no
        remaining orders is removed too, so deleting an order can't leave a
        phantom buyer with inflated lifetime spend in the list."""
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
            "SELECT customer_name, customer_phone, customer_email, product "
            "FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
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

        photo_dir = os.path.join(ORDER_PHOTOS, str(oid))
        if os.path.isdir(photo_dir):
            try:
                for fn in os.listdir(photo_dir):
                    os.remove(os.path.join(photo_dir, fn))
                os.rmdir(photo_dir)
            except OSError as e:
                print(f"[orders-delete] photo cleanup: {e}", flush=True)
        hub_event("order_deleted", f'#{oid} {row["product"] or ""} ({row["customer_name"] or "?"})',
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "customer_orders_left": remaining})

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
        # Private: it's behind a role check, so no shared cache may keep it.
        self.send_header("Cache-Control", "private, max-age=3600")
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
        The SELECT itself never names a PII or price column, so there is no
        code path here that could leak one even by accident — this isn't a
        matter of the response happening to omit fields the UI doesn't show.

        No separate "paid" flag: `status` already has a paid stage, and it
        means "we've paid the supplier" — Shopify's financial_status means
        "the customer paid us", a fact about a different relationship
        entirely that has no bearing on building the watch, so it's excluded
        rather than surfaced under a confusingly-similar name."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        from order_form import STATUSES
        conn = db()
        try:
            rows = conn.execute(
                # supplier_cost is the supplier's OWN price to us — safe to
                # return here, unlike price_inr (what the customer pays),
                # which stays absent from this SELECT entirely.
                "SELECT id, received_at, product, quantity, status, notes, "
                "case_style, dial_colour, dial_style, case_colour, movement, "
                "watch_size, local_photos, tracking_code, ref_code, "
                "supplier_cost, supplier_cost_ccy, bill_id FROM orders "
                "WHERE supplier_visible=1 AND status != 'cancelled' "
                "ORDER BY id ASC").fetchall()
            events = {}
            for e in conn.execute(
                    "SELECT order_id, created_at, actor, kind, detail FROM order_events "
                    "WHERE order_id IN (SELECT id FROM orders WHERE supplier_visible=1) "
                    "ORDER BY id ASC"):
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
                n = len(json.loads(d.pop("local_photos") or "[]"))
            except (json.JSONDecodeError, TypeError):
                n = 0
            d["photos"] = n
            d["timeline"] = events.get(d["id"], [])
            # Once the batch carrying this build has been acknowledged, its
            # cost is settled history and the UI shows it read-only.
            d["locked"] = d.get("bill_id") in locked_bills
            # Shown greyed on the card so the rate that will be applied is
            # visible before it's applied, never a surprise on the bill.
            dc, dl = self._default_cost(d.get("product"), d.get("movement"))
            d["default_cost"], d["default_label"] = dc, dl
            out.append(d)
        self._json(200, {"orders": out, "statuses": STATUSES})

    def _handle_supplier_status(self):
        """Status-only, and only on an order actually shared with this role —
        the ownership check is server-side, not left to the UI only offering
        shared orders."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        from order_form import STATUSES
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
        row = conn.execute(
            "SELECT status FROM orders WHERE id=? AND supplier_visible=1", (oid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        was = row["status"] or "new"
        if was == status:
            conn.close()
            self._json(200, {"ok": True, "id": oid, "status": status, "unchanged": True})
            return
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        order_event(conn, oid, "status", f"{was} -> {status}", actor)
        conn.commit()
        conn.close()
        hub_event("order_updated", f"#{oid}: status -> {status}", actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "status": status})

    # Same effective INR/USD rate the Ledger and Home dashboard already use —
    # bank charges and transfer fees included, not the market rate — so a
    # rupee figure here means the same thing as the same figure shown
    # anywhere else in the OS.
    _USD_INR = 100.0

    def _to_inr(self, amount, currency):
        if not amount:
            return 0.0
        return float(amount) * (self._USD_INR if (currency or "USD").upper() == "USD" else 1.0)

    # ------------------------------------------------- per-build supplier cost
    # What a build costs if nobody says otherwise. The supplier can always
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
            "SELECT o.bill_id, b.status FROM orders o "
            "LEFT JOIN supplier_bills b ON b.id = o.bill_id "
            "WHERE o.id=?", (order_id,)).fetchone()
        if not row:
            return False, "no such build"
        if row["status"] == "acknowledged":
            return False, "this build is on a bill you've already acknowledged"
        return True, ""

    def _set_costs(self, ids, cost, currency, actor):
        """Shared by the single and bulk paths — one build or forty, the
        validation and the audit trail are identical, so they're not two
        different code paths that can drift apart."""
        conn = db()
        done, skipped = [], []
        for oid in ids:
            ok, why = self._cost_editable(conn, oid)
            if not ok:
                skipped.append({"id": oid, "reason": why})
                continue
            conn.execute("UPDATE orders SET supplier_cost=?, supplier_cost_ccy=? "
                         "WHERE id=? AND supplier_visible=1", (cost, currency, oid))
            order_event(conn, oid, "cost",
                        f"supplier cost set to {currency} {cost:,.2f}", actor)
            done.append(oid)
        conn.commit()
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
            cost = float(p.get("cost"))
        except (TypeError, ValueError):
            self._json(400, {"error": "a build and a cost are needed"})
            return
        if oid <= 0 or cost < 0:
            self._json(400, {"error": "a build and a cost are needed"})
            return
        currency = (str(p.get("currency") or "INR").strip().upper())[:8] or "INR"
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
        ids = [int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()][:200]
        try:
            cost = float(p.get("cost"))
        except (TypeError, ValueError):
            self._json(400, {"error": "a cost is needed"})
            return
        if not ids or cost < 0:
            self._json(400, {"error": "pick at least one build and a cost"})
            return
        currency = (str(p.get("currency") or "INR").strip().upper())[:8] or "INR"
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
            "SELECT * FROM supplier_bills ORDER BY id DESC LIMIT 100")]
        items = {}
        for it in conn.execute("SELECT * FROM supplier_bill_items ORDER BY id ASC"):
            items.setdefault(it["bill_id"], []).append(dict(it))
        conn.close()
        for b in bills:
            b["items"] = items.get(b["id"], [])
            b["total_inr"] = round(self._to_inr(b["total"], b["currency"]), 2)
        self._json(200, {"bills": bills, "can_acknowledge": self._has_tool("orders")})

    def _handle_supplier_bill_create(self):
        """Turn a selection of costed builds into a bill. Freight is one line
        on the bill rather than smeared across the builds, so per-build cost
        stays the true build cost."""
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
        try:
            shipping = float(p.get("shipping_cost") or 0)
        except (TypeError, ValueError):
            shipping = 0.0
        currency = (str(p.get("currency") or "INR").strip().upper())[:8] or "INR"
        notes = str(p.get("notes") or "").strip()[:400]

        conn = db()
        rows = conn.execute(
            "SELECT id, product, movement, ref_code, supplier_cost, supplier_cost_ccy, "
            "bill_id FROM orders WHERE id IN ({}) AND supplier_visible=1".format(
                ",".join("?" * len(ids))), ids).fetchall()
        if not rows:
            conn.close()
            self._json(400, {"error": "none of those builds are in the queue"})
            return
        already = [r["id"] for r in rows if r["bill_id"]]
        if already:
            conn.close()
            self._json(400, {"error": "already in a batch: "
                                      + ", ".join(f"#{i}" for i in already)})
            return

        bill_no = self._next_bill_no(conn)
        # A build with no price set falls to its movement's default rather
        # than blocking the batch. The rate is written onto the line as a
        # real number, so a batch reads the same whether it was priced by
        # hand or by rule.
        priced, defaulted = [], 0
        for r in rows:
            cost = r["supplier_cost"]
            if cost is None:
                cost, label = self._default_cost(r["product"], r["movement"])
                defaulted += 1
                conn.execute("UPDATE orders SET supplier_cost=?, supplier_cost_ccy=? "
                             "WHERE id=?", (cost, currency, r["id"]))
                order_event(conn, r["id"], "cost",
                            f"default {label} rate applied: {cost:,.0f}", actor)
            priced.append((r, float(cost)))

        subtotal = sum(c for _, c in priced)
        total = subtotal + shipping
        cur = conn.execute(
            "INSERT INTO supplier_bills (bill_no, created_by, status, currency, "
            "subtotal, shipping_cost, total, shipment_id, notes) "
            "VALUES (?,?,'draft',?,?,?,?,?,?)",
            (bill_no, actor, currency, subtotal, shipping, total,
             p.get("shipment_id") or None, notes))
        bill_id = cur.lastrowid
        for r, cost in priced:
            conn.execute(
                "INSERT INTO supplier_bill_items (bill_id, order_id, description, "
                "ref_code, cost) VALUES (?,?,?,?,?)",
                (bill_id, r["id"], r["product"], r["ref_code"], cost))
            conn.execute("UPDATE orders SET bill_id=? WHERE id=?", (bill_id, r["id"]))
            order_event(conn, r["id"], "billed", f"added to batch {bill_no}", actor)
        conn.commit()
        conn.close()
        hub_event("supplier_bill", f"{bill_no} drafted — {currency} {total:,.2f} "
                                   f"across {len(rows)} build(s)", actor, "supplier")
        self._json(200, {"ok": True, "bill_id": bill_id, "bill_no": bill_no,
                         "subtotal": subtotal, "shipping_cost": shipping,
                         "total": total, "defaulted": defaulted})

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
        n = conn.execute("SELECT COUNT(*) FROM supplier_bill_items WHERE bill_id=?",
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
        self._json(200, {"ok": True, "queued": True, "target": target})

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
        row = conn.execute("SELECT bill_no FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        if not row:
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

    def _ledger_supplier_id(self, lconn):
        """Hannan builds the watches; the 31 invoices already in the Ledger
        are parts from a different company in China. Keeping him as his own
        supplier row is what stops build spend and parts spend from being
        averaged into one meaningless per-supplier number."""
        row = lconn.execute("SELECT id FROM suppliers WHERE name=?",
                            ("TimeLabsCo x Sunesra",)).fetchone()
        if row:
            return row[0]
        cur = lconn.execute(
            "INSERT INTO suppliers (name, country, platform, default_currency, notes) "
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
        bill = conn.execute("SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        if not bill:
            conn.close()
            self._json(404, {"error": "no such bill"})
            return
        if bill["status"] == "acknowledged":
            conn.close()
            self._json(400, {"error": "already acknowledged"})
            return
        items = conn.execute("SELECT * FROM supplier_bill_items WHERE bill_id=?",
                             (bid,)).fetchall()

        rate = self._USD_INR if (bill["currency"] or "USD").upper() == "USD" else 1.0
        invoice_id = None
        try:
            lconn = sqlite3.connect(SUPPLIERS_DB, timeout=5)
            lconn.row_factory = sqlite3.Row
            sup_id = self._ledger_supplier_id(lconn)
            cur = lconn.execute(
                "INSERT INTO invoices (supplier_id, invoice_no, order_date, currency, "
                "subtotal, shipping_cost, discount, total, exchange_rate, total_inr, "
                "payment_status, parsed_by, notes) "
                "VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?)",
                (sup_id, bill["bill_no"], time.strftime("%Y-%m-%d"), bill["currency"],
                 bill["subtotal"], bill["shipping_cost"], bill["total"], rate,
                 bill["total"] * rate, "unpaid", "supplier-queue",
                 f"Acknowledged by {actor}"))
            invoice_id = cur.lastrowid
            for it in items:
                lconn.execute(
                    "INSERT INTO invoice_items (invoice_id, description_raw, "
                    "description_en, part_category, quantity, unit_price, line_total, "
                    "unit_price_inr) VALUES (?,?,?,?,1,?,?,?)",
                    (invoice_id, it["description"], it["description"], "build",
                     it["cost"], it["cost"], (it["cost"] or 0) * rate))
            lconn.commit()
            lconn.close()
        except Exception as e:
            conn.close()
            self._json(500, {"error": f"couldn't write to the Ledger: {e}"})
            return

        conn.execute("UPDATE supplier_bills SET status='acknowledged', "
                     "acknowledged_at=datetime('now'), acknowledged_by=?, "
                     "ledger_invoice_id=? WHERE id=?", (actor, invoice_id, bid))
        for it in items:
            if it["order_id"]:
                order_event(conn, it["order_id"], "bill_acknowledged",
                            f"bill {bill['bill_no']} acknowledged — cost locked", actor)
        conn.commit()
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
        bill = conn.execute("SELECT * FROM supplier_bills WHERE id=?", (bid,)).fetchone()
        if not bill:
            conn.close()
            self._json(404, {"error": "no such bill"})
            return
        if bill["ledger_invoice_id"]:
            try:
                lconn = sqlite3.connect(SUPPLIERS_DB, timeout=5)
                lconn.execute("DELETE FROM invoice_items WHERE invoice_id=?",
                              (bill["ledger_invoice_id"],))
                lconn.execute("DELETE FROM invoices WHERE id=?",
                              (bill["ledger_invoice_id"],))
                lconn.commit()
                lconn.close()
            except Exception as e:
                conn.close()
                self._json(500, {"error": f"couldn't reverse the Ledger entry: {e}"})
                return
        items = conn.execute("SELECT order_id FROM supplier_bill_items WHERE bill_id=?",
                             (bid,)).fetchall()
        for it in items:
            if it["order_id"]:
                conn.execute("UPDATE orders SET bill_id=NULL WHERE id=?", (it["order_id"],))
                order_event(conn, it["order_id"], "bill_deleted",
                            f"bill {bill['bill_no']} deleted — cost editable again", actor)
        # Payments pointed at this bill go back to being general credit rather
        # than vanishing with it — the money was still sent.
        conn.execute("UPDATE supplier_payments SET bill_id=NULL WHERE bill_id=?", (bid,))
        conn.execute("DELETE FROM supplier_bill_items WHERE bill_id=?", (bid,))
        conn.execute("DELETE FROM supplier_bills WHERE id=?", (bid,))
        conn.commit()
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
            draft = conn.execute(
                "SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM supplier_bills "
                "WHERE status='draft'").fetchone()
            # What's been costed but not yet put on any bill at all — the
            # gap between work priced and money actually claimed. Summed
            # through _to_inr rather than in SQL, because a plain SUM() would
            # silently add dollars to rupees.
            unbilled_rows = conn.execute(
                "SELECT supplier_cost, supplier_cost_ccy FROM orders "
                "WHERE supplier_visible=1 AND bill_id IS NULL "
                "AND supplier_cost IS NOT NULL").fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            self._json(500, {"error": str(e)})
            return
        conn.close()

        unbilled_inr = sum(self._to_inr(r["supplier_cost"], r["supplier_cost_ccy"])
                           for r in unbilled_rows)
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
            "draft_total": round(draft["t"] or 0, 2),
            "draft_count": draft["n"] or 0,
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
        try:
            amount = float(p.get("amount"))
        except (TypeError, ValueError):
            self._json(400, {"error": "a payment amount is needed"})
            return
        if amount <= 0:
            self._json(400, {"error": "amount should be positive"})
            return
        currency = (str(p.get("currency", "INR")).strip()[:8] or "INR").upper()
        note = str(p.get("note", "")).strip()[:300]
        # Payments settle a bill now that bills are what create the debt.
        # Leaving it unset is still valid — money sent on account, before
        # anyone has agreed which bill it belongs to.
        raw_bid = p.get("bill_id")
        conn = db()
        bill_id, code = None, None
        if raw_bid not in (None, "", 0):
            try:
                bill_id = int(raw_bid)
            except (TypeError, ValueError):
                conn.close()
                self._json(400, {"error": "bad bill"})
                return
            row = conn.execute("SELECT bill_no FROM supplier_bills WHERE id=?",
                              (bill_id,)).fetchone()
            if not row:
                conn.close()
                self._json(404, {"error": "no such bill"})
                return
            code = row["bill_no"]
        cur = conn.execute(
            "INSERT INTO supplier_payments (bill_id, amount, currency, note, actor) "
            "VALUES (?,?,?,?,?)", (bill_id, amount, currency, note, actor))
        pid = cur.lastrowid
        if bill_id:
            for oid in [r["order_id"] for r in conn.execute(
                    "SELECT order_id FROM supplier_bill_items WHERE bill_id=?", (bill_id,))
                    if r["order_id"]]:
                order_event(conn, oid, "payment",
                           f"{currency} {amount:.2f} recorded against {code}", actor)
        conn.commit()
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
        row = conn.execute("SELECT tracking_code FROM orders WHERE id=? AND supplier_visible=1",
                          (oid,)).fetchone()
        if not row:
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
        hub_event("order_tracking", f"#{oid}: {code or 'cleared'}", actor, app="orders")
        self._json(200, {"ok": True, "id": oid, "tracking_code": code})

    def _status_card(self, conn, oid):
        """The shareable summary of one build. Same shape whether it ends up
        as copied text, a WhatsApp message or a PDF, so the three can't drift
        into saying different things about the same order."""
        o = conn.execute(
            "SELECT id, received_at, product, quantity, status, tracking_code, ref_code, "
            "case_style, dial_colour, dial_style, case_colour, movement, watch_size "
            "FROM orders WHERE id=? AND supplier_visible=1", (oid,)).fetchone()
        if not o:
            return None, None
        spec = " · ".join(str(o[k]) for k in
                          ("case_style", "dial_colour", "dial_style", "case_colour",
                           "movement", "watch_size") if o[k])
        events = conn.execute(
            "SELECT created_at, kind, detail FROM order_events "
            "WHERE order_id=? ORDER BY id ASC", (oid,)).fetchall()
        # Lead with the original order number when there is one — that's the
        # number the supplier already knows the build by.
        head_num = f"Order {o['ref_code']}" if o["ref_code"] else f"Order #{o['id']}"
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
            "SELECT * FROM supplier_bill_items WHERE bill_id=? ORDER BY id ASC",
            (bid,)).fetchall()
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE bill_id=?",
            (bid,)).fetchone()[0] or 0.0
        conn.close()

        raw_ccy = (bill["currency"] or "INR").upper()
        # A rupee sign reads as money; "INR 12,500.00" reads as a database row.
        ccy = "\u20b9" if raw_ccy == "INR" else html_mod.escape(raw_ccy) + " "
        rows = ""
        for it in items:
            label = it["ref_code"] or (f"#{it['order_id']}" if it["order_id"] else "")
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
                     + '</td>'
                     + f'<td class="n">{ccy}{it["cost"]:,.2f}</td></tr>')
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
        sql = "SELECT * FROM reddit_threads"
        args = []
        if tag and tag != "all":
            sql += " WHERE tag=?"
            args.append(tag)
        sql += " ORDER BY opportunity_score DESC, created_utc DESC LIMIT 100"
        conn = db()
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
        import reddit_api
        self._json(200, {"mode": reddit_api.mode(), "threads": rows})

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
                r["photos"] = json.loads(r.get("photos") or "[]")
            except (json.JSONDecodeError, TypeError):
                r["photos"] = []
            try:
                r["passes"] = json.loads(r.get("passes") or "{}")
            except (json.JSONDecodeError, TypeError):
                r["passes"] = {}
        self._json(200, {"posts": rows, "subreddit": "IndiaWatchMods"})

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
        photos = [str(x)[:200] for x in (p.get("photos") or [])][:12]
        if not brief and not photos:
            self._json(400, {"error": "add a photo or say something about the build"})
            return
        conn = db()
        cur = conn.execute(
            "INSERT INTO reddit_posts (created_by, kind, brief, photos, status) "
            "VALUES (?,?,?,?, 'queued')",
            (actor, kind, brief, json.dumps(photos)))
        pid = cur.lastrowid
        conn.commit()
        conn.close()

        def _work():
            try:
                subprocess.run(["python3", "/root/ops-dashboard/reddit_draft.py",
                                str(pid)], capture_output=True, text=True, timeout=2400)
            except Exception as e:
                print(f"[reddit_draft] runner: {e}", flush=True)
            c = db()
            row = c.execute("SELECT status, title FROM reddit_posts WHERE id=?",
                            (pid,)).fetchone()
            c.close()
            ok = row and row["status"] == "ready"
            hub_event("reddit_post",
                      f"draft #{pid} " + ("ready" if ok else "failed"), actor, "agent")
            self._alert(f"*Reddit draft ready*\n{row['title']}\n\nReview it at "
                        f"ops.timelabsco.in/ops/reddit.html" if ok else
                        f"Reddit draft #{pid} failed to build.")

        threading.Thread(target=_work, daemon=True).start()
        self._json(200, {"ok": True, "id": pid, "status": "queued"})

    def _alert(self, body):
        """Tell the owner something finished. Uses the health alert
        destination, never the supplier group — Hannan has no reason to see
        Reddit drafts."""
        target = ""
        try:
            with open("/root/ops-dashboard/.env") as f:
                for line in f:
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
            subprocess.run(["hermes", "send", "--to", target, "--quiet", body],
                           capture_output=True, text=True, timeout=120)
        except Exception as e:
            print(f"[alert] {e}", flush=True)

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
        conn = db()
        row = conn.execute("SELECT id FROM reddit_posts WHERE id=?", (pid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such draft"})
            return
        conn.execute("UPDATE reddit_posts SET answer=?, question=NULL, "
                     "status='queued', rounds=COALESCE(rounds,0)+1 WHERE id=?",
                     (answer, pid))
        conn.commit()
        conn.close()

        def _work():
            try:
                subprocess.run(["python3", "/root/ops-dashboard/reddit_draft.py",
                                str(pid)], capture_output=True, text=True, timeout=2400)
            except Exception as e:
                print(f"[reddit_draft] resume: {e}", flush=True)
            c = db()
            r = c.execute("SELECT status, title FROM reddit_posts WHERE id=?",
                          (pid,)).fetchone()
            c.close()
            if r and r["status"] == "ready":
                self._alert(f"*Reddit draft ready*\n{r['title']}\n\n"
                            f"ops.timelabsco.in/ops/reddit.html")

        threading.Thread(target=_work, daemon=True).start()
        self._json(200, {"ok": True})

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
        row = conn.execute("SELECT id FROM reddit_posts WHERE id=?", (pid,)).fetchone()
        if not row:
            conn.close()
            self._json(404, {"error": "no such draft"})
            return
        if p.get("slot_date") is not None or p.get("assigned_to") is not None:
            conn.execute("UPDATE reddit_posts SET slot_date=?, assigned_to=? WHERE id=?",
                         (str(p.get("slot_date") or "")[:10] or None,
                          str(p.get("assigned_to") or "")[:80] or None, pid))
        elif p.get("posted"):
            conn.execute("UPDATE reddit_posts SET status='posted', "
                         "posted_at=datetime('now'), posted_url=? WHERE id=?",
                         (str(p.get("url") or "")[:400], pid))
        elif p.get("discard"):
            conn.execute("DELETE FROM reddit_posts WHERE id=?", (pid,))
        else:
            conn.execute("UPDATE reddit_posts SET title=?, body=? WHERE id=?",
                         (str(p.get("title") or "")[:300],
                          str(p.get("body") or "")[:20000], pid))
        conn.commit()
        conn.close()
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

        def _work():
            try:
                r = subprocess.run(
                    ["python3", "/root/ops-dashboard/reddit_setup.py", kind],
                    capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    print(f"[reddit_setup] {kind}: {r.stderr[:200]}", flush=True)
            except Exception as e:
                print(f"[reddit_setup] {kind}: {e}", flush=True)
            hub_event("reddit_setup", f"{kind} drafted", actor, "agent")

        threading.Thread(target=_work, daemon=True).start()
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
        conn = db()
        t = conn.execute("SELECT id FROM reddit_threads WHERE id=?", (tid,)).fetchone()
        if not t:
            conn.close()
            self._json(404, {"error": "no such thread"})
            return
        cur = conn.execute(
            "INSERT INTO reddit_drafts (thread_id, draft_text, status, created_by, answer) "
            "VALUES (?,'','queued',?,?)",
            (tid, actor, str(p.get("note") or "").strip()[:1000]))
        did = cur.lastrowid
        conn.commit()
        conn.close()

        def _work():
            try:
                subprocess.run(["python3", "/root/ops-dashboard/reddit_reply.py",
                                str(did)], capture_output=True, text=True, timeout=1800)
            except Exception as e:
                print(f"[reddit_reply] runner: {e}", flush=True)
            c = db()
            r = c.execute("SELECT status FROM reddit_drafts WHERE id=?", (did,)).fetchone()
            c.close()
            if r and r["status"] == "ready":
                self._alert("*Reddit reply ready*\nops.timelabsco.in/ops/reddit.html")

        threading.Thread(target=_work, daemon=True).start()
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
        conn = db()
        if not conn.execute("SELECT id FROM reddit_drafts WHERE id=?", (did,)).fetchone():
            conn.close()
            self._json(404, {"error": "no such reply"})
            return
        if p.get("discard"):
            conn.execute("DELETE FROM reddit_drafts WHERE id=?", (did,))
        elif p.get("posted"):
            conn.execute("UPDATE reddit_drafts SET status='posted', "
                         "posted_at=datetime('now') WHERE id=?", (did,))
        elif p.get("answer"):
            conn.execute("UPDATE reddit_drafts SET answer=?, question=NULL, "
                         "status='queued' WHERE id=?",
                         (str(p.get("answer"))[:1000], did))
            conn.commit()
            conn.close()

            def _resume():
                subprocess.run(["python3", "/root/ops-dashboard/reddit_reply.py",
                                str(did)], capture_output=True, text=True, timeout=1800)
            threading.Thread(target=_resume, daemon=True).start()
            self._json(200, {"ok": True})
            return
        else:
            conn.execute("UPDATE reddit_drafts SET draft_text=? WHERE id=?",
                         (str(p.get("text") or "")[:20000], did))
        conn.commit()
        conn.close()
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
        try:
            threads = reddit_api.fetch_new_threads()
        except reddit_api.RedditError as e:
            self._json(400, {"error": str(e)})
            return
        conn = db()
        added = 0
        for t in threads:
            cur = conn.execute("SELECT id FROM reddit_threads WHERE thread_id=?",
                               (t["thread_id"],)).fetchone()
            if cur:
                # COALESCE so an RSS re-sync (score/num_comments unknown,
                # always None) can't blank out real numbers a previous
                # OAuth sync already recorded for this thread.
                conn.execute(
                    "UPDATE reddit_threads SET score=COALESCE(?,score), "
                    "num_comments=COALESCE(?,num_comments), opportunity_score=? "
                    "WHERE thread_id=?",
                    (t["score"], t["num_comments"], t["opportunity_score"], t["thread_id"]))
            else:
                conn.execute(
                    "INSERT INTO reddit_threads (thread_id, subreddit, title, permalink, "
                    "author, created_utc, score, num_comments, tag, opportunity_score) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t["thread_id"], t["subreddit"], t["title"], t["permalink"], t["author"],
                     t["created_utc"], t["score"], t["num_comments"], t["tag"],
                     t["opportunity_score"]))
                added += 1
        conn.commit()
        conn.close()
        hub_event("reddit_sync", f"{added} new thread(s) from {len(reddit_api.TARGET_SUBS)} subs",
                  actor, "agent")
        self._json(200, {"ok": True, "fetched": len(threads), "added": added})

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
            f"SELECT id, product, quantity, case_style, dial_colour, dial_style, "
            f"case_colour, movement, watch_size, notes FROM orders "
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
                f'<td><b>#{r["id"]}</b><br>{html_mod.escape(r["product"] or "")}'
                + (f'<br><span class="spec">{html_mod.escape(spec)}</span>' if spec else "")
                + (f'<br><span class="spec">{html_mod.escape(r["notes"] or "")}</span>'
                   if r["notes"] else "")
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
                pdf = make_pdf(text.replace("\n", "\n\n"), f"Order #{oid} — status")
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
                detail = (f"status sent to WhatsApp ({target})" if ok else
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
                      f"#{oid} status -> WhatsApp" if ok else f"#{oid} WhatsApp failed",
                      actor, app="orders")

        threading.Thread(target=_deliver, daemon=True).start()
        self._json(200, {"ok": True, "id": oid, "target": target, "queued": True})

    def _handle_supplier_shipments(self):
        """Shipments with the builds they carry and what each one costs.

        Per-watch cost is the consignment total divided evenly by how many
        orders are in it, so it moves as builds are added or removed — it's a
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
            s["order_count"] = n
            s["per_watch"] = round((s["total_cost"] or 0) / n, 2) if n else None
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
        currency = (str(p.get("currency", "INR")).strip()[:8] or "INR").upper()
        try:
            total = float(p["total_cost"]) if str(p.get("total_cost", "")).strip() else None
        except (TypeError, ValueError):
            self._json(400, {"error": "the total cost should be a number"})
            return
        conn = db()
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
        ids = [int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()][:200]
        if not ids:
            self._json(400, {"error": "no orders selected"})
            return
        sid = p.get("shipment_id")
        conn = db()
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
        conn.execute(f"UPDATE orders SET shipment_id=? WHERE id IN ({marks}) "
                     f"AND supplier_visible=1", [sid] + ids)
        if code:
            conn.execute(f"UPDATE orders SET tracking_code=? WHERE id IN ({marks}) "
                        f"AND supplier_visible=1 AND (tracking_code IS NULL OR tracking_code='')",
                        [code] + ids)
        for oid in ids:
            order_event(conn, oid, "shipment",
                       f"added to shipment {code}" if code else "removed from its shipment",
                       actor)
        conn.commit()
        conn.close()
        hub_event("shipment_assign",
                 f"{len(ids)} order(s) -> {code or 'no shipment'}", actor, app="orders")
        self._json(200, {"ok": True, "count": len(ids), "shipment_id": sid})

    def _handle_supplier_bulk(self):
        """One action across many builds — the point of the checkboxes. Status
        and tracking only: the things a supplier legitimately changes for a
        whole batch at once ("these six all shipped today")."""
        if not self._supplier_ok():
            self._json(403, {"error": "not available for this account"})
            return
        from order_form import STATUSES
        actor = self._order_user()
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 8192)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        ids = [int(i) for i in (p.get("ids") or []) if str(i).strip().isdigit()][:200]
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
        marks = ",".join("?" for _ in ids)
        owned = [r["id"] for r in conn.execute(
            f"SELECT id FROM orders WHERE id IN ({marks}) AND supplier_visible=1", ids)]
        if not owned:
            conn.close()
            self._json(404, {"error": "none of those orders are in your queue"})
            return
        m2 = ",".join("?" for _ in owned)
        if status:
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
        if not conn.execute("SELECT 1 FROM orders WHERE id=? AND supplier_visible=1",
                            (oid,)).fetchone():
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        order_event(conn, oid, "note", note, actor)
        conn.commit()
        conn.close()
        hub_event("supplier_note", f"#{oid}: {note[:120]}", actor, app="orders")
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
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 4096)).decode()) if length else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        commit = bool(payload.get("commit"))
        # run via the venv python (has openpyxl; this server runs on system python)
        cmd = ["/root/ops-dashboard/venv/bin/python", "/root/ops-dashboard/invoice_import.py", "--json"]
        if commit:
            cmd.append("--commit")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            data = json.loads(out.stdout.strip() or "{}")
            results = data.get("results", [])
        except Exception as e:
            self._json(500, {"error": f"invoice parse failed: {e} · {out.stderr[-160:] if 'out' in dir() else ''}"})
            return
        if commit:
            imported = [r for r in results if not r.get("error")]
            subprocess.Popen(["/root/ops-dashboard/venv/bin/python3", "/root/ops-dashboard/ledger.py"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if imported:
                actor = (self.headers.get("X-User-Email") or "?").strip().lower()
                hub_event("invoice_import",
                          f"{len(imported)} invoice(s): " + ", ".join(r["file"] for r in imported),
                          actor, app="ledger")
        self._json(200, {"committed": commit, "results": results})

    def _handle_new_session(self):
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        conn = db()
        cur = conn.execute("INSERT INTO webchat_sessions (title) VALUES ('New chat')")
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        self._json(200, {"id": sid})

    def _handle_send(self):
        # Shell access is granted per role, and now so is chat access at
        # all — the PREAMBLE hands every caller a fair amount of business
        # context regardless of tool access, which a deliberately-restricted
        # role (supplier, intake) has no reason to receive.
        if not self._has_tool("chat"):
            self._json(403, {"error": "not available for this account"})
            return
        tools_for_caller = toolset_for(self.headers.get("X-User-Email"))
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(min(length, 32768)).decode())
            message = str(payload.get("message", "")).strip()
            session_id = int(payload.get("session_id") or 1)
            # images: [{path, name}] — legacy single image_path/image_name still accepted
            raw_images = payload.get("images") or []
            if payload.get("image_path"):
                raw_images.append({"path": payload["image_path"], "name": payload.get("image_name")})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._json(400, {"error": "bad request"})
            return
        if not ensure_session(session_id):
            self._json(400, {"error": "unknown session"})
            return
        # only accept paths our own /upload handed out (cap 8 per message)
        images = []
        for im in raw_images[:8]:
            p = str((im or {}).get("path") or "")
            if p.startswith(UPLOAD_DIR + "/") and os.path.isfile(p):
                images.append((p, (im.get("name") or os.path.basename(p)), None))
        if not message and not images:
            self._json(400, {"error": "empty message"})
            return
        # /model is handled locally — instant, no agent run, no lock needed
        if message.startswith("/model"):
            store(session_id, "user", message)
            reply = handle_model_command(session_id, message)
            store(session_id, "agent", reply)
            self._json(200, {"reply": reply})
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
            store(session_id, "user", shown)
            expanded = expand_template(message) if message else (
                "Please look at the attached photo." if len(images) == 1
                else f"Please look at the {len(images)} attached photos."
            )
            images = [(p, n, ocr_image(p)) for p, n, _ in images]
            prompt = build_prompt(session_id, expanded, images)
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
                        reply = run_hermes(prompt, model, provider, tools_for_caller)
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
                    store(session_id, "agent", outcome.get("reply") or "(no reply)")
                finally:
                    done.set()
                    LOCK.release()

        threading.Thread(target=worker, daemon=True).start()
        if done.wait(HERMES_SOFT_WAIT):
            self._json(200, {"reply": outcome.get("reply")})
        else:
            self._json(200, {"pending": True, "reply": None})

    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        length = int(self.headers.get("Content-Length", 0))
        if "multipart/form-data" not in ctype or not m or length > MAX_UPLOAD_BYTES:
            self._json(400, {"error": f"expected a multipart image (jpg, png, or webp) under {MAX_UPLOAD_MB} MB"})
            return
        body = self.rfile.read(length)
        boundary = ("--" + m.group(1)).encode()
        for part in body.split(boundary):
            if b"Content-Disposition" not in part or b'name="image"' not in part:
                continue
            header_blob, _, data = part.partition(b"\r\n\r\n")
            data = data.rstrip(b"\r\n-")
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
            path = os.path.join(UPLOAD_DIR, secrets.token_hex(8) + ext)
            with open(path, "wb") as f:
                f.write(data)
            self._json(200, {"path": path})
            return
        self._json(400, {"error": "no image field found"})

    def _handle_export_pdf(self):
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
        elif path == "/orders/items/relabel":
            self._handle_order_items_relabel()
        elif path == "/supplier/status":
            self._handle_supplier_status()
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
        elif path == "/reddit/post/create":
            self._handle_reddit_post_create()
        elif path == "/reddit/post/answer":
            self._handle_reddit_post_answer()
        elif path == "/reddit/post/update":
            self._handle_reddit_post_update()
        elif path == "/reddit/sync":
            self._handle_reddit_sync()
        elif path == "/supplier/payment/record":
            self._handle_supplier_payment_record()
        elif path == "/orders/delete":
            self._handle_orders_delete()
        elif path == "/orders/bulk":
            self._handle_orders_bulk()
        elif path == "/access/set":
            self._handle_access_set()
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
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
