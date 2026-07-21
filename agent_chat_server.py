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

HOST, PORT = "127.0.0.1", 8901
DB = "/root/ops-dashboard/data/hermes.db"
UPLOAD_DIR = "/root/ops-dashboard/data/uploads"
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

PREAMBLE = (
    "You are Timelabs Co's agent, chatting via the private web command center "
    "with the owner (Schezan) or his partner. Be helpful and direct. Your replies "
    "render as rich Markdown — use headers, **bold**, bullet lists, and tables where "
    "they make the answer clearer, and the reader can export your reply as a PDF, "
    "so structure longer answers like a clean document. You have your usual tools "
    "(terminal, files, web, vision) and the business database at "
    "/root/ops-dashboard/data/hermes.db (orders, products, action_items, "
    "memory_facts, content_library, and hub_events — a live feed of everything "
    "happening in Hub: file uploads, public share links, photo organizing, and "
    "access changes; check it when asked what's new). Team files live in Drop at "
    "/srv/timelabs-drop (browse it directly). Sourcing costs and margins are in "
    "/root/ops-dashboard/data/suppliers.db, surfaced at /ops/ledger.html. "
    "When a conversation surfaces something durable, save it to memory_facts. "
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


def run_hermes(prompt, model=None, provider=None):
    cmd = ["hermes"]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    cmd += ["-t", SAFE_WEB_TOOLSET]
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
        self._json(200, {"ok": True, "members": members})

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
                        reply = run_hermes(prompt, model, provider)
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
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
