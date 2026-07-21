#!/usr/bin/env python3
"""Drop backend — Timelabs Hub's file tunnel (replaces copyparty).

Runs on 127.0.0.1:8903 behind nginx (gated by Key / oauth2-proxy):
  GET  /list?path=          folder listing (JSON)
  POST /mkdir               {path, name}
  POST /rename              {path, old, new}
  POST /delete              {path, names: []}   -> soft-delete into .trash/
  POST /upload/init         {path, name, size, mtime} -> {id, offset}
  PUT  /upload/chunk?id=&offset=   raw bytes, appended at offset
  POST /upload/finish       {id} -> {name}  (collision-safe final placement)
  GET  /thumb?path=         320px jpeg thumbnail (Pillow images / ffmpeg video)

Uploads are resumable by design: files land in .uploads/<id>.part where <id>
is derived from (path, name, size, mtime) — the same file re-picked after a
dropped connection or page reload resumes at the byte the server already has.
Downloads/streaming are NOT served here: nginx serves /drop/files/ straight
from disk (with HTTP Range for video scrubbing).

Storage layout under /srv/timelabs-drop:
  <user folders/files>     visible content
  .uploads/                in-flight chunked uploads (meta json + .part)
  .thumbs/                 thumbnail cache
  .trash/                  soft-deleted files, timestamp-prefixed
"""
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

HOST, PORT = "127.0.0.1", 8903
ROOT = "/srv/timelabs-drop"
UPLOADS = os.path.join(ROOT, ".uploads")
THUMBS = os.path.join(ROOT, ".thumbs")
TRASH = os.path.join(ROOT, ".trash")
SHARES = os.path.join(ROOT, ".shares")   # public share tokens -> target json
PUBLIC_BASE = "https://ops.timelabsco.in"
GDRIVE_TOKEN = "/root/ops-dashboard/.gdrive-token.json"
OAUTH_CLIENT = "/root/oauth-client.json"
GDRIVE_REDIRECT = PUBLIC_BASE + "/drop/api/gdrive/callback"
HERMES_DB = "/root/ops-dashboard/data/hermes.db"


def hub_event(kind, detail, actor="?"):
    """Log to hub_events in hermes.db — the shared feed Hermes watches, so the
    whole Hub stays visible to the agent (uploads, shares, organize, access)."""
    try:
        import sqlite3
        conn = sqlite3.connect(HERMES_DB, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS hub_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            app TEXT NOT NULL DEFAULT 'drop',
            kind TEXT NOT NULL, actor TEXT, detail TEXT)""")
        conn.execute("INSERT INTO hub_events (kind, actor, detail) VALUES (?,?,?)",
                     (kind, actor, detail[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[events] {e}", flush=True)
MAX_CHUNK = 16 * 1024 * 1024          # per-request cap; client sends 6 MB
MAX_FILE = 4 * 1024 * 1024 * 1024     # 4 GB per file is plenty for product video
NAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,200}$")

for d in (ROOT, UPLOADS, THUMBS, TRASH, SHARES):
    os.makedirs(d, exist_ok=True)

_locks = {}
_locks_guard = threading.Lock()


def _lock_for(uid):
    with _locks_guard:
        return _locks.setdefault(uid, threading.Lock())


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".hevc", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".pptx", ".key"}
ZIP_EXT = {".zip", ".rar", ".7z", ".tar", ".gz"}


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOC_EXT:
        return "doc"
    if ext in ZIP_EXT:
        return "archive"
    return "file"


def safe_rel(rel):
    """Resolve a client-supplied relative path to an absolute path inside ROOT,
    or None if it escapes / touches hidden internals."""
    rel = (rel or "").strip().strip("/")
    if rel in ("", "."):
        return ROOT
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None
    path = os.path.realpath(os.path.join(ROOT, *parts))
    if path != ROOT and not path.startswith(ROOT + os.sep):
        return None
    return path


def safe_name(name):
    name = (name or "").strip()
    if not NAME_RE.match(name) or name.startswith("."):
        return None
    return name


def unique_path(dirpath, name):
    """name.ext -> name (2).ext on collision."""
    base, ext = os.path.splitext(name)
    cand, i = name, 1
    while os.path.exists(os.path.join(dirpath, cand)):
        i += 1
        cand = f"{base} ({i}){ext}"
    return os.path.join(dirpath, cand)


try:  # iPhone HEIC/HEIF support — registers a Pillow plugin when available
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def make_thumb(src, dst, px=320):
    """px-wide jpeg. Pillow for images, ffmpeg first-second frame for videos."""
    kind = kind_of(src)
    if kind == "image":
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((px, px))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=82)
        return True
    if kind == "video":
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", src,
             "-frames:v", "1", "-vf", f"scale={px}:-2", dst],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            # very short clips: retry at 0s
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                 "-frames:v", "1", "-vf", f"scale={px}:-2", dst],
                capture_output=True, timeout=30,
            )
        return r.returncode == 0 and os.path.exists(dst)
    return False


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self, cap=65536):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(min(length, cap)).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _user(self):
        return (self.headers.get("X-User-Email") or "?").strip().lower()

    # ------------------------------------------------------------- GET
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if path.startswith("/s/"):
            self._share_get(path, params)
            return
        if path == "/list":
            self._list(params.get("path", ""))
        elif path == "/share/info":
            self._share_info(params.get("path", ""), params.get("name", ""))
        elif path == "/thumb":
            self._thumb(params.get("path", ""), big=params.get("big") == "1")
        elif path == "/gdrive/status":
            self._gdrive_status()
        elif path == "/gdrive/connect":
            self._gdrive_connect()
        elif path == "/gdrive/callback":
            self._gdrive_callback(params)
        elif path == "/gdrive/list":
            self._gdrive_list(params.get("folder", "root"))
        elif path == "/download-zip":
            self._download_zip(params.get("path", ""))
        else:
            self._json(404, {"error": "not found"})

    def _list(self, rel):
        d = safe_rel(rel)
        if d is None or not os.path.isdir(d):
            self._json(404, {"error": "no such folder"})
            return
        dirs, files = [], []
        with os.scandir(d) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                st = e.stat()
                if e.is_dir():
                    try:
                        n = sum(1 for x in os.scandir(e.path) if not x.name.startswith("."))
                    except OSError:
                        n = 0
                    dirs.append({"name": e.name, "mtime": int(st.st_mtime), "count": n})
                else:
                    k = kind_of(e.name)
                    files.append({"name": e.name, "size": st.st_size,
                                  "mtime": int(st.st_mtime), "kind": k,
                                  "thumb": k in ("image", "video")})
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: -x["mtime"])
        # free disk space for the header strip
        stat = shutil.disk_usage(ROOT)
        self._json(200, {"path": (rel or "").strip("/"), "dirs": dirs, "files": files,
                         "free_gb": round(stat.free / 1e9, 1)})

    def _thumb(self, rel, big=False):
        src = safe_rel(rel)
        if src is None or not os.path.isfile(src):
            self._json(404, {"error": "no such file"})
            return
        px = 1280 if big else 320
        st = os.stat(src)
        key = hashlib.sha1(f"{rel}|{st.st_size}|{int(st.st_mtime)}|{px}".encode()).hexdigest()
        dst = os.path.join(THUMBS, key + ".jpg")
        if not os.path.exists(dst):
            try:
                ok = make_thumb(src, dst, px)
            except Exception:
                ok = False
            if not ok:
                self._json(415, {"error": "no thumbnail for this type"})
                return
        with open(dst, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------- POST/PUT
    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/organize/scan":
            self._organize_scan()
        elif path == "/organize/apply":
            self._organize_apply()
        elif path == "/gdrive/import":
            self._gdrive_import()
        elif path == "/share":
            self._share_create()
        elif path == "/share/revoke":
            self._share_revoke()
        elif path == "/mkdir":
            self._mkdir()
        elif path == "/rename":
            self._rename()
        elif path == "/delete":
            self._delete()
        elif path == "/upload/init":
            self._upload_init()
        elif path == "/upload/finish":
            self._upload_finish()
        else:
            self._json(404, {"error": "not found"})

    def do_PUT(self):
        path, _, query = self.path.partition("?")
        if path == "/upload/chunk":
            self._upload_chunk(dict(urllib.parse.parse_qsl(query)))
        else:
            self._json(404, {"error": "not found"})

    def _mkdir(self):
        p = self._body_json()
        d = safe_rel((p or {}).get("path", ""))
        name = safe_name((p or {}).get("name", ""))
        if d is None or not name:
            self._json(400, {"error": "bad folder name"})
            return
        try:
            os.makedirs(os.path.join(d, name), exist_ok=False)
        except FileExistsError:
            self._json(409, {"error": "already exists"})
            return
        print(f"[drop] {self._user()} mkdir {p.get('path','')}/{name}", flush=True)
        self._json(200, {"ok": True})

    def _rename(self):
        p = self._body_json()
        d = safe_rel((p or {}).get("path", ""))
        old = safe_name((p or {}).get("old", ""))
        new = safe_name((p or {}).get("new", ""))
        if d is None or not old or not new:
            self._json(400, {"error": "bad name"})
            return
        src = os.path.join(d, old)
        if not os.path.exists(src):
            self._json(404, {"error": "not found"})
            return
        dst = os.path.join(d, new)
        if os.path.exists(dst):
            self._json(409, {"error": "a file with that name exists"})
            return
        os.rename(src, dst)
        print(f"[drop] {self._user()} rename {old} -> {new}", flush=True)
        self._json(200, {"ok": True})

    def _delete(self):
        p = self._body_json()
        d = safe_rel((p or {}).get("path", ""))
        names = (p or {}).get("names") or []
        if d is None or not isinstance(names, list) or not names:
            self._json(400, {"error": "bad request"})
            return
        done = []
        for raw in names[:100]:
            name = safe_name(str(raw))
            if not name:
                continue
            src = os.path.join(d, name)
            if not os.path.exists(src):
                continue
            shutil.move(src, os.path.join(TRASH, f"{int(time.time())}-{name}"))
            done.append(name)
        print(f"[drop] {self._user()} trashed {done}", flush=True)
        hub_event("delete", ", ".join(done), self._user())
        self._json(200, {"ok": True, "deleted": done})

    # ------------------------------------------------------------- organize
    @staticmethod
    def _dhash(path):
        """64-bit difference hash — near-identical shots land within ~10 bits."""
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("L").resize((9, 8))
            px = list(im.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        return bits

    _vocab_cache = None

    @classmethod
    def _vocab(cls):
        """Watch-domain vocabulary from suppliers.db: movements, style families,
        part categories. OCR tokens matched here become folder names."""
        if cls._vocab_cache is not None:
            return cls._vocab_cache
        vocab = {}
        try:
            import sqlite3
            conn = sqlite3.connect("/root/ops-dashboard/data/suppliers.db", timeout=5)
            for (m,) in conn.execute("SELECT DISTINCT model_compat FROM invoice_items WHERE model_compat IS NOT NULL"):
                for part in re.split(r"[/,\s]+", m):
                    if 3 <= len(part) <= 8:
                        vocab[part.upper()] = part.upper()
            for (s,) in conn.execute("SELECT DISTINCT style_ref FROM invoice_items WHERE style_ref IS NOT NULL"):
                pretty = s.replace("-", " ").title()
                vocab[s.replace("-", "").upper()] = pretty
                for w in s.split("-"):
                    if len(w) >= 4:
                        vocab[w.upper()] = pretty
            for (c,) in conn.execute("SELECT DISTINCT part_category FROM invoice_items WHERE part_category IS NOT NULL"):
                if c and len(c) >= 4:
                    vocab[c.upper()] = c.title() + "s"
            conn.close()
        except Exception as e:
            print(f"[organize] vocab: {e}", flush=True)
        for extra in ("SKX", "SEIKO", "NH35A", "NH36A"):
            vocab.setdefault(extra, extra.replace("A", "") if extra.endswith("A") else extra.title())
        cls._vocab_cache = vocab
        return vocab

    def _vision_names(self, d, groups):
        """Best-effort: ask Hermes (vision) to name still-generic groups from a
        sample thumbnail each. Silent fallback — never blocks organize."""
        generic = [g for g in groups if g["name"].startswith("Set ")]
        if not generic:
            return
        samples = []
        for g in generic[:6]:
            fp = os.path.join(d, g["files"][0])
            st = os.stat(fp)
            key = hashlib.sha1(f"vn|{fp}|{st.st_size}".encode()).hexdigest()
            tp = os.path.join(THUMBS, key + ".jpg")
            if not os.path.exists(tp):
                try:
                    if not make_thumb(fp, tp, 640):
                        continue
                except Exception:
                    continue
            samples.append((g, tp))
        if not samples:
            return
        listing = "\n".join(f"{i+1}. {tp}" for i, (_, tp) in enumerate(samples))
        prompt = ("Look at each numbered image file with your vision tool. Each shows watch "
                  "parts or watches (Seiko-mod business). Reply ONLY with JSON: a list of "
                  f"short 2-4 word folder names, one per image, same order. Images:\n{listing}")
        try:
            r = subprocess.run(["hermes", "-t", "vision", "-z", prompt],
                               capture_output=True, text=True, timeout=90)
            m = re.search(r"\[.*\]", r.stdout, re.S)
            names = json.loads(m.group(0)) if m else []
            for (g, _), nm in zip(samples, names):
                nm = safe_name(re.sub(r"[^\w \-]", "", str(nm)).strip()[:40] or "")
                if nm:
                    g["name"] = nm.title()
        except Exception as e:
            print(f"[organize] vision naming skipped: {e}", flush=True)

    @staticmethod
    def _ocr_tokens(path):
        """Meaningful uppercase-ish tokens (model codes like NH35, SKX) via tesseract."""
        try:
            r = subprocess.run(["tesseract", path, "stdout", "--psm", "6", "-l", "eng"],
                               capture_output=True, text=True, timeout=8)
            words = re.findall(r"[A-Za-z0-9]{3,12}", r.stdout or "")
            out = []
            for w in words:
                if any(c.isdigit() for c in w) and any(c.isalpha() for c in w):
                    out.append(w.upper())          # model-code shaped: NH35, SKX007
                elif w.isupper() and len(w) >= 4:
                    out.append(w)
            return out[:10]
        except Exception:
            return []

    def _organize_scan(self):
        p = self._body_json()
        rel = (p or {}).get("path", "")
        d = safe_rel(rel)
        if d is None or not os.path.isdir(d):
            self._json(404, {"error": "no such folder"})
            return
        imgs = []
        with os.scandir(d) as it:
            for e in it:
                if e.is_file() and not e.name.startswith(".") and kind_of(e.name) == "image":
                    imgs.append(e.name)
        imgs = sorted(imgs)[:200]
        if len(imgs) < 3:
            self._json(200, {"groups": [], "note": "not enough images to organize"})
            return
        hashes, tokens = {}, {}
        for name in imgs:
            fp = os.path.join(d, name)
            try:
                hashes[name] = self._dhash(fp)
            except Exception:
                continue
            if len(imgs) <= 60:                    # OCR only when the set is small
                ext = os.path.splitext(name)[1].lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                    tokens[name] = self._ocr_tokens(fp)
                else:
                    tokens[name] = []
        names = [n for n in imgs if n in hashes]
        # union-find over pairwise hamming distance
        parent = {n: n for n in names}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if bin(hashes[a] ^ hashes[b]).count("1") <= 12:
                    union(a, b)
        clusters = {}
        for n in names:
            clusters.setdefault(find(n), []).append(n)
        groups = []
        seq = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            toks = {}
            for m in members:
                for t in tokens.get(m, []):
                    toks[t] = toks.get(t, 0) + 1
            vocab = self._vocab()
            hits = {}
            for t, c in toks.items():
                key = t.replace("-", "").upper()
                if key in vocab:
                    hits[vocab[key]] = hits.get(vocab[key], 0) + c
            seq += 1
            if hits:
                ranked = sorted(hits.items(), key=lambda x: -x[1])
                gname = " · ".join(n for n, _ in ranked[:2])
            else:
                common = [t for t, c in sorted(toks.items(), key=lambda x: -x[1]) if c >= 2]
                gname = (common[0].title() if common else f"Set {seq}")
            groups.append({"name": gname, "files": sorted(members)})
        groups.sort(key=lambda g: -len(g["files"]))
        self._vision_names(d, groups)
        self._json(200, {"groups": groups,
                         "scanned": len(names),
                         "ungrouped": len(names) - sum(len(g["files"]) for g in groups)})

    def _organize_apply(self):
        p = self._body_json(cap=256 * 1024)
        rel = (p or {}).get("path", "")
        d = safe_rel(rel)
        groups = (p or {}).get("groups") or []
        if d is None or not isinstance(groups, list) or not groups:
            self._json(400, {"error": "bad request"})
            return
        moved, made = 0, []
        for g in groups[:40]:
            gname = safe_name(str((g or {}).get("name", "")))
            files = (g or {}).get("files") or []
            if not gname or not isinstance(files, list):
                continue
            target = os.path.join(d, gname)
            if os.path.exists(target) and not os.path.isdir(target):
                continue
            os.makedirs(target, exist_ok=True)
            made.append(gname)
            for raw in files[:500]:
                fn = safe_name(str(raw))
                if not fn:
                    continue
                src_p = os.path.join(d, fn)
                if not os.path.isfile(src_p):
                    continue
                shutil.move(src_p, unique_path(target, fn))
                moved += 1
        print(f"[drop] {self._user()} organized {moved} files into {made}", flush=True)
        hub_event("organize", f"{moved} photos into folders: {', '.join(made)}", self._user())
        self._json(200, {"ok": True, "moved": moved, "folders": made})

    # ------------------------------------------------------------- google drive
    @staticmethod
    def _gclient():
        try:
            d = json.load(open(OAUTH_CLIENT))
            c = d.get("web") or d.get("installed") or {}
            return c.get("client_id"), c.get("client_secret")
        except (OSError, json.JSONDecodeError):
            return None, None

    def _gdrive_access(self):
        """A valid access token, refreshing via the stored refresh token."""
        try:
            tok = json.load(open(GDRIVE_TOKEN))
        except (OSError, json.JSONDecodeError):
            return None
        if tok.get("exp", 0) > time.time() + 60:
            return tok.get("access_token")
        cid, csec = self._gclient()
        if not cid or not tok.get("refresh_token"):
            return None
        body = urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec,
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                fresh = json.loads(r.read())
        except Exception as e:
            print(f"[gdrive] refresh failed: {e}", flush=True)
            return None
        tok["access_token"] = fresh["access_token"]
        tok["exp"] = time.time() + fresh.get("expires_in", 3500)
        with open(GDRIVE_TOKEN, "w") as f:
            json.dump(tok, f)
        os.chmod(GDRIVE_TOKEN, 0o600)
        return tok["access_token"]

    def _gdrive_status(self):
        cid, _ = self._gclient()
        self._json(200, {"configured": bool(cid),
                         "connected": self._gdrive_access() is not None})

    def _gdrive_connect(self):
        cid, _ = self._gclient()
        if not cid:
            self._json(500, {"error": "oauth client not configured on server"})
            return
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid,
            "redirect_uri": GDRIVE_REDIRECT,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/drive.readonly",
            "access_type": "offline",
            "prompt": "consent",
        })
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _gdrive_callback(self, params):
        code = params.get("code")
        cid, csec = self._gclient()
        if not code or not cid:
            self._html(400, "<h2>Drive connect failed — no code returned</h2>")
            return
        body = urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec, "code": code,
            "grant_type": "authorization_code", "redirect_uri": GDRIVE_REDIRECT,
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            self._html(400, f"<h2>Drive connect failed</h2><p>{e.read().decode()[:300]}</p>")
            return
        tok["exp"] = time.time() + tok.get("expires_in", 3500)
        with open(GDRIVE_TOKEN, "w") as f:
            json.dump(tok, f)
        os.chmod(GDRIVE_TOKEN, 0o600)
        print(f"[gdrive] connected by {self._user()}", flush=True)
        self.send_response(302)
        self.send_header("Location", "/drop/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _gdrive_api(self, url):
        access = self._gdrive_access()
        if not access:
            return None, "not connected"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            if e.code == 403 and ("has not been used" in msg or "disabled" in msg):
                return None, ("Google Drive API isn't switched on for your project yet. "
                              "Open Google Cloud console, enable 'Google Drive API', "
                              "wait ~2 minutes, then tap the cloud again.")
            if e.code == 401:
                return None, "not connected"
            return None, (msg[:180] or f"Drive API error {e.code}")

    # google-native types → what they import AS (label shown on the button)
    GDOC_EXPORT = {
        "application/vnd.google-apps.document": ("application/pdf", ".pdf", "PDF"),
        "application/vnd.google-apps.spreadsheet":
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", "Excel"),
        "application/vnd.google-apps.presentation": ("application/pdf", ".pdf", "PDF"),
        "application/vnd.google-apps.drawing": ("image/png", ".png", "PNG"),
    }

    def _gdrive_list(self, folder):
        folder = re.sub(r"[^A-Za-z0-9_-]", "", folder) or "root"
        q = urllib.parse.quote(f"'{folder}' in parents and trashed=false")
        fields = urllib.parse.quote("files(id,name,size,mimeType,modifiedTime),nextPageToken")
        data, err = self._gdrive_api(
            f"https://www.googleapis.com/drive/v3/files?q={q}&fields={fields}"
            "&pageSize=200&orderBy=folder,name")
        if err:
            self._json(502 if err != "not connected" else 401, {"error": err})
            return
        items = []
        for f in data.get("files", []):
            mime = f["mimeType"]
            is_dir = mime == "application/vnd.google-apps.folder"
            is_gdoc = mime.startswith("application/vnd.google-apps") and not is_dir
            exp = self.GDOC_EXPORT.get(mime)
            items.append({"id": f["id"], "name": f["name"], "dir": is_dir,
                          "gdoc": is_gdoc, "mime": mime,
                          "as": exp[2] if exp else "",           # "PDF"/"Excel" or "" (binary)
                          "importable": (not is_dir) and (not is_gdoc or exp is not None),
                          "size": int(f.get("size", 0)), "mtime": f.get("modifiedTime", "")})
        self._json(200, {"items": items})

    def _gdrive_import(self):
        p = self._body_json()
        fid = re.sub(r"[^A-Za-z0-9_-]", "", str((p or {}).get("id", "")))
        name = safe_name((p or {}).get("name", ""))
        dest = safe_rel((p or {}).get("path", ""))
        mime = str((p or {}).get("mime", ""))
        if not fid or not name or dest is None:
            self._json(400, {"error": "bad import request"})
            return
        access = self._gdrive_access()
        if not access:
            self._json(401, {"error": "not connected"})
            return
        user = self._user()

        # Google-native docs must be *exported* (to PDF/Excel); binaries download raw.
        exp = self.GDOC_EXPORT.get(mime)
        if exp:
            exp_mime, ext, _ = exp
            url = (f"https://www.googleapis.com/drive/v3/files/{fid}/export"
                   f"?mimeType={urllib.parse.quote(exp_mime)}")
            if not name.lower().endswith(ext):
                name = name + ext
        else:
            url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media"
        name = safe_name(name) or "import"

        def worker():
            tmp = os.path.join(UPLOADS, "gdrive-" + fid + ".part")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                final = unique_path(dest, name)
                shutil.move(tmp, final)
                os.chmod(final, 0o644)
                print(f"[gdrive] {user} imported {name}", flush=True)
                hub_event("gdrive_import", name, user)
            except Exception as e:
                print(f"[gdrive] import {name} failed: {e}", flush=True)
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self._json(200, {"ok": True, "importing": name})

    # ------------------------------------------------------------- shares
    @staticmethod
    def _share_all():
        out = {}
        for fn in os.listdir(SHARES):
            if fn.endswith(".json"):
                try:
                    out[fn[:-5]] = json.load(open(os.path.join(SHARES, fn)))
                except (OSError, json.JSONDecodeError):
                    pass
        return out

    @staticmethod
    def _share_target(meta):
        """Absolute path of a share's target, re-validated on every use."""
        rel = meta.get("rel", "")
        p = safe_rel(rel)
        return p if p and os.path.exists(p) else None

    def _share_info(self, rel_dir, name):
        want = (rel_dir.strip("/") + "/" + name).strip("/") if name else rel_dir.strip("/")
        for tok, meta in self._share_all().items():
            if meta.get("rel") == want:
                self._json(200, {"token": tok, "url": f"{PUBLIC_BASE}/s/{tok}"})
                return
        self._json(200, {"token": None})

    def _share_create(self):
        p = self._body_json()
        d = safe_rel((p or {}).get("path", ""))
        name = safe_name((p or {}).get("name", ""))
        if d is None or not name or not os.path.exists(os.path.join(d, name)):
            self._json(404, {"error": "no such file or folder"})
            return
        rel = ((p.get("path") or "").strip("/") + "/" + name).strip("/")
        for tok, meta in self._share_all().items():   # reuse existing link
            if meta.get("rel") == rel:
                self._json(200, {"token": tok, "url": f"{PUBLIC_BASE}/s/{tok}"})
                return
        tok = secrets.token_urlsafe(16)
        with open(os.path.join(SHARES, tok + ".json"), "w") as f:
            json.dump({"rel": rel, "by": self._user(), "created": int(time.time()),
                       "is_dir": os.path.isdir(os.path.join(d, name))}, f)
        print(f"[drop] {self._user()} shared {rel} -> /s/{tok}", flush=True)
        hub_event("share_created", f"public link for {rel}", self._user())
        self._json(200, {"token": tok, "url": f"{PUBLIC_BASE}/s/{tok}"})

    def _share_revoke(self):
        p = self._body_json()
        tok = re.sub(r"[^A-Za-z0-9_-]", "", str((p or {}).get("token", "")))[:40]
        fp = os.path.join(SHARES, tok + ".json")
        if not tok or not os.path.exists(fp):
            self._json(404, {"error": "no such share"})
            return
        os.remove(fp)
        print(f"[drop] {self._user()} revoked share {tok}", flush=True)
        hub_event("share_revoked", tok, self._user())
        self._json(200, {"ok": True})

    # ---- public, token-gated (reached via nginx /s/ without login) ----
    def _share_get(self, path, params):
        parts = path.split("/")           # ["", "s", token, ...maybe "raw"]
        tok = re.sub(r"[^A-Za-z0-9_-]", "", parts[2] if len(parts) > 2 else "")[:40]
        fp = os.path.join(SHARES, tok + ".json")
        if not tok or not os.path.exists(fp):
            self._html(404, "<h2>Link expired or removed</h2>")
            return
        meta = json.load(open(fp))
        target = self._share_target(meta)
        if target is None:
            self._html(404, "<h2>File no longer exists</h2>")
            return
        want_raw = len(parts) > 3 and parts[3] == "raw"
        if os.path.isdir(target):
            sub = safe_name(params.get("f", "") or "")
            if want_raw and sub and os.path.isfile(os.path.join(target, sub)):
                self._send_file(os.path.join(target, sub), sub)
                return
            if params.get("thumb") and sub and os.path.isfile(os.path.join(target, sub)):
                self._share_thumb(meta["rel"] + "/" + sub)
                return
            self._share_folder_page(tok, meta, target)
            return
        if want_raw:
            self._send_file(target, os.path.basename(target))
            return
        if params.get("thumb"):
            self._share_thumb(meta["rel"])
            return
        self._share_file_page(tok, meta, target)

    def _share_thumb(self, rel):
        # reuse the cached-thumb machinery for public preview images
        self._thumb(rel, big=True)

    def _html(self, status, body_html, title="Labs Drop"):
        page = ('<!doctype html><html><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<meta name="robots" content="noindex">'
                f'<title>{title}</title><style>'
                ':root{color-scheme:light dark}'
                'body{margin:0;background:#f8f8f7;color:#191918;'
                'font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;'
                'display:flex;flex-direction:column;min-height:100vh}'
                '@media(prefers-color-scheme:dark){body{background:#111113;color:#f2f2f0}}'
                'main{flex:1;max-width:900px;width:100%;margin:0 auto;padding:24px 16px}'
                'h2{font-size:20px} a{color:#996c1f}'
                '.f{display:flex;align-items:center;gap:12px;padding:10px 0;'
                'border-bottom:1px solid rgba(128,128,128,.25);font-size:14.5px}'
                '.f img{width:52px;height:52px;object-fit:cover;border-radius:8px}'
                '.f .nm{flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis}'
                '.f small{opacity:.6}'
                '.dl{display:inline-block;background:#191918;color:#f8f8f7;border-radius:8px;'
                'padding:10px 18px;font-weight:600;text-decoration:none;margin-top:14px}'
                '@media(prefers-color-scheme:dark){.dl{background:#f2f2f0;color:#111113}}'
                'img.hero,video.hero{max-width:100%;max-height:70vh;border-radius:12px;display:block;margin:14px 0}'
                'footer{padding:14px;text-align:center;font-size:12px;opacity:.55}'
                '</style></head><body><main>' + body_html + '</main>'
                '<footer>Shared via Labs Drop</footer></body></html>').encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.end_headers()
        self.wfile.write(page)

    def _share_file_page(self, tok, meta, target):
        name = os.path.basename(target)
        kind = kind_of(name)
        size = os.path.getsize(target)
        raw = f"/s/{tok}/raw"
        if kind == "image":
            ext = os.path.splitext(name)[1].lower()
            src = f"/s/{tok}?thumb=1" if ext in (".heic", ".heif", ".avif") else raw
            hero = f'<img class="hero" src="{src}" alt="">'
        elif kind == "video":
            hero = f'<video class="hero" src="{raw}" controls playsinline></video>'
        elif kind == "audio":
            hero = f'<video class="hero" src="{raw}" controls style="max-height:70px"></video>'
        else:
            hero = ""
        mb = f"{size/1e6:.1f} MB" if size >= 1e6 else f"{size/1e3:.0f} KB"
        self._html(200, f"<h2>{name}</h2><p>{mb}</p>{hero}"
                        f'<a class="dl" href="{raw}" download>Download</a>', name)

    def _share_folder_page(self, tok, meta, target):
        rows = []
        with os.scandir(target) as it:
            entries = sorted((e for e in it if e.is_file() and not e.name.startswith(".")),
                             key=lambda e: e.name.lower())
        for e in entries:
            k = kind_of(e.name)
            size = e.stat().st_size
            mb = f"{size/1e6:.1f} MB" if size >= 1e6 else f"{size/1e3:.0f} KB"
            img = (f'<img loading="lazy" src="/s/{tok}?thumb=1&f={urllib.parse.quote(e.name)}" '
                   'onerror="this.style.visibility=\'hidden\'">'
                   if k in ("image", "video") else '<span style="width:52px"></span>')
            rows.append(f'<div class="f">{img}<span class="nm">{e.name}</span>'
                        f'<small>{mb}</small>'
                        f'<a href="/s/{tok}/raw?f={urllib.parse.quote(e.name)}" download>Download</a></div>')
        name = os.path.basename(target)
        self._html(200, f"<h2>{name}</h2>" + ("".join(rows) or "<p>Empty folder.</p>"), name)

    def _download_zip(self, rel):
        """Package a folder as a .zip and stream it (export a whole folder)."""
        import tempfile
        import zipfile
        d = safe_rel(rel)
        if d is None or not os.path.isdir(d):
            self._json(404, {"error": "no such folder"})
            return
        total, filelist = 0, []
        for base, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                fp = os.path.join(base, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    continue
                filelist.append(fp)
        if total > 4 * 1024 * 1024 * 1024:
            self._json(413, {"error": "folder too large to zip (over 4 GB) — download files individually"})
            return
        tmp = tempfile.NamedTemporaryFile(dir=UPLOADS, suffix=".zip", delete=False)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for fp in filelist:
                    z.write(fp, os.path.relpath(fp, d))
            tmp.close()
            size = os.path.getsize(tmp.name)
            zipname = (os.path.basename(d) or "Drop") + ".zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{zipname}"')
            self.end_headers()
            with open(tmp.name, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
            hub_event("download_zip", f"{os.path.basename(d)} ({len(filelist)} files)", self._user())
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass

    def _send_file(self, path, name):
        """Stream a file with single-range support (public share downloads/video)."""
        size = os.path.getsize(path)
        ctype = "application/octet-stream"
        ext = os.path.splitext(name)[1].lower()
        types = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf",
                 ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
        ctype = types.get(ext, ctype)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng and rng.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start <= end < size:
                    status = 206
                else:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining:
                chunk = f.read(min(remaining, 256 * 1024))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # ------------------------------------------------------------- uploads
    def _upload_init(self):
        p = self._body_json()
        d = safe_rel((p or {}).get("path", ""))
        name = safe_name((p or {}).get("name", ""))
        try:
            size = int((p or {}).get("size"))
            mtime = int((p or {}).get("mtime") or 0)
        except (TypeError, ValueError):
            size = -1
            mtime = 0
        if d is None or not name or not (0 <= size <= MAX_FILE):
            self._json(400, {"error": "bad upload request"})
            return
        rel = (p.get("path") or "").strip("/")
        uid = hashlib.sha1(f"{rel}|{name}|{size}|{mtime}".encode()).hexdigest()[:24]
        meta_p = os.path.join(UPLOADS, uid + ".json")
        part_p = os.path.join(UPLOADS, uid + ".part")
        with _lock_for(uid):
            if not os.path.exists(meta_p):
                with open(meta_p, "w") as f:
                    json.dump({"rel": rel, "name": name, "size": size,
                               "by": self._user(), "started": int(time.time())}, f)
                open(part_p, "ab").close()
            offset = os.path.getsize(part_p)
        self._json(200, {"id": uid, "offset": offset})

    def _upload_chunk(self, params):
        uid = re.sub(r"[^a-f0-9]", "", params.get("id", ""))[:24]
        try:
            offset = int(params.get("offset", -1))
        except ValueError:
            offset = -1
        length = int(self.headers.get("Content-Length", 0))
        meta_p = os.path.join(UPLOADS, uid + ".json")
        part_p = os.path.join(UPLOADS, uid + ".part")
        if not uid or offset < 0 or not os.path.exists(meta_p):
            self._json(404, {"error": "unknown upload — init first"})
            return
        if length <= 0 or length > MAX_CHUNK:
            self._json(400, {"error": "bad chunk size"})
            return
        meta = json.load(open(meta_p))
        with _lock_for(uid):
            have = os.path.getsize(part_p)
            if offset != have:
                # client out of sync (retry after drop) — tell it where we are
                self.rfile.read(length)  # drain
                self._json(409, {"error": "offset mismatch", "offset": have})
                return
            if have + length > meta["size"]:
                self.rfile.read(length)
                self._json(400, {"error": "would exceed declared size"})
                return
            remaining = length
            with open(part_p, "ab") as f:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1024 * 256))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            have = os.path.getsize(part_p)
        if remaining:
            self._json(400, {"error": "short read", "offset": have})
            return
        self._json(200, {"offset": have})

    def _upload_finish(self):
        p = self._body_json()
        uid = re.sub(r"[^a-f0-9]", "", str((p or {}).get("id", "")))[:24]
        meta_p = os.path.join(UPLOADS, uid + ".json")
        part_p = os.path.join(UPLOADS, uid + ".part")
        if not uid or not os.path.exists(meta_p):
            self._json(404, {"error": "unknown upload"})
            return
        meta = json.load(open(meta_p))
        with _lock_for(uid):
            have = os.path.getsize(part_p)
            if have != meta["size"]:
                self._json(409, {"error": "incomplete", "offset": have,
                                 "expected": meta["size"]})
                return
            d = safe_rel(meta["rel"])
            if d is None:
                self._json(400, {"error": "bad destination"})
                return
            os.makedirs(d, exist_ok=True)
            dst = unique_path(d, meta["name"])
            shutil.move(part_p, dst)
            os.chmod(dst, 0o644)
            os.remove(meta_p)
        print(f"[drop] {self._user()} uploaded {os.path.basename(dst)} "
              f"({meta['size']} B) -> {meta['rel'] or '/'}", flush=True)
        hub_event("upload", f"{os.path.basename(dst)} ({meta['size']//1000000} MB) into /{meta['rel']}", self._user())
        self._json(200, {"ok": True, "name": os.path.basename(dst)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
