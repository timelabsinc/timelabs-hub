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
import urllib.request

HOST, PORT = "127.0.0.1", 8901
DB = "/root/ops-dashboard/data/hermes.db"
UPLOAD_DIR = "/root/ops-dashboard/data/uploads"
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
            "financial_status": "TEXT"}
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


_ensure_orders_schema()
_ensure_order_items_schema()


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
        """Any signed-in (allowlisted) user may log orders — access is managed
        in Key, not here. An empty header means SSO didn't populate it."""
        return (self.headers.get("X-User-Email") or "").strip().lower()

    def _handle_order_create(self):
        actor = self._order_user()
        if not actor:
            self._json(403, {"error": "sign in first"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            p = json.loads(self.rfile.read(min(length, 16384)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "bad request"})
            return
        customer = str(p.get("customer_name", "")).strip()[:120]
        product = str(p.get("product", "")).strip()[:200]
        if not customer or not product:
            self._json(400, {"error": "customer name and product are both needed"})
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
        if not access:
            warnings.append("Google isn't connected — photos and the sheet mirror were "
                            "skipped. Open Drop and reconnect Google.")
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
            "case_colour, movement, watch_size) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (customer, phone, email, address, pincode, city, state, source, product,
             price, qty, notes, status, 1 if links else 0, drive_link,
             json.dumps(links) if links else None,
             attrs.get("case_style"), attrs.get("dial_colour"), attrs.get("dial_style"),
             attrs.get("case_colour"), attrs.get("movement"), attrs.get("watch_size")))
        oid = cur.lastrowid
        conn.execute(
            "INSERT INTO order_items (order_id, product, quantity, price_inr, line_total) "
            "VALUES (?,?,?,?,?)",
            (oid, product, qty, price, (price or 0) * qty))
        row = conn.execute("SELECT received_at FROM orders WHERE id=?", (oid,)).fetchone()
        conn.commit()
        logged = row["received_at"] if row else ""

        # Customer roll-up recomputes from orders, so it stays correct even if
        # an order is later edited or removed.
        import customers as customers_mod
        cust = None
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
        if not self._order_user():
            self._json(403, {"error": "sign in first"})
            return
        conn = db()
        try:
            rows = conn.execute(
                "SELECT id, received_at, customer_name, customer_phone, customer_email, "
                "address, pincode, city, state, source, product, quantity, price_inr, "
                "notes, status, drive_link, photo_links, case_style, dial_colour, "
                "dial_style, case_colour, movement, watch_size, shopify_name FROM orders "
                "ORDER BY id DESC LIMIT 100").fetchall()
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
        Keeps the vocabulary server-side so there's one implementation of it."""
        if not self._order_user():
            self._json(403, {"error": "sign in first"})
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
        if not self._order_user():
            self._json(403, {"error": "sign in first"})
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
        if not actor:
            self._json(403, {"error": "sign in first"})
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
        updatable = {"status": 40, "notes": 1000, "customer_name": 120,
                     "customer_phone": 40, "customer_email": 200, "address": 600,
                     "city": 80, "state": 80, "pincode": 20, "case_style": 60,
                     "dial_colour": 60, "dial_style": 60, "case_colour": 60,
                     "movement": 60, "watch_size": 60}
        sets, vals = [], []
        for k, maxlen in updatable.items():
            if k in p:
                sets.append(f"{k}=?")
                vals.append(str(p[k]).strip()[:maxlen])
        if not sets:
            self._json(400, {"error": "nothing to update"})
            return
        conn = db()
        if not conn.execute("SELECT 1 FROM orders WHERE id=?", (oid,)).fetchone():
            conn.close()
            self._json(404, {"error": "no such order"})
            return
        vals.append(oid)
        conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        conn.close()
        hub_event("order_updated", f"#{oid}: " + ", ".join(k for k in updatable if k in p),
                 actor, app="orders")
        self._json(200, {"ok": True, "id": oid})

    def _handle_order_items_relabel(self):
        """Rename a product for analytics — retroactive across every order
        that used the old text, so a messy free-typed name only needs fixing
        once. Never touches the order itself or what it says was charged,
        only the label What's Selling groups by."""
        actor = self._order_user()
        if not actor:
            self._json(403, {"error": "sign in first"})
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
        combobox usable even if Shopify is briefly unreachable."""
        if not self._order_user():
            self._json(403, {"error": "sign in first"})
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

    def _handle_customers_list(self):
        if not self._order_user():
            self._json(403, {"error": "sign in first"})
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
        conn = db()
        cur = conn.execute("INSERT INTO webchat_sessions (title) VALUES ('New chat')")
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        self._json(200, {"id": sid})

    def _handle_send(self):
        # Shell access is granted per role, not to everyone who can chat.
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
