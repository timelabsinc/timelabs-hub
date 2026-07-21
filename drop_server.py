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
import shutil
import subprocess
import threading
import time
import urllib.parse

HOST, PORT = "127.0.0.1", 8903
ROOT = "/srv/timelabs-drop"
UPLOADS = os.path.join(ROOT, ".uploads")
THUMBS = os.path.join(ROOT, ".thumbs")
TRASH = os.path.join(ROOT, ".trash")
MAX_CHUNK = 16 * 1024 * 1024          # per-request cap; client sends 6 MB
MAX_FILE = 4 * 1024 * 1024 * 1024     # 4 GB per file is plenty for product video
NAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,200}$")

for d in (ROOT, UPLOADS, THUMBS, TRASH):
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


def make_thumb(src, dst):
    """320px jpeg. Pillow for images, ffmpeg first-second frame for videos."""
    kind = kind_of(src)
    if kind == "image":
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((320, 320))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=82)
        return True
    if kind == "video":
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", src,
             "-frames:v", "1", "-vf", "scale=320:-2", dst],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            # very short clips: retry at 0s
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                 "-frames:v", "1", "-vf", "scale=320:-2", dst],
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
        if path == "/list":
            self._list(params.get("path", ""))
        elif path == "/thumb":
            self._thumb(params.get("path", ""))
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

    def _thumb(self, rel):
        src = safe_rel(rel)
        if src is None or not os.path.isfile(src):
            self._json(404, {"error": "no such file"})
            return
        st = os.stat(src)
        key = hashlib.sha1(f"{rel}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()
        dst = os.path.join(THUMBS, key + ".jpg")
        if not os.path.exists(dst):
            try:
                ok = make_thumb(src, dst)
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
        if path == "/mkdir":
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
        self._json(200, {"ok": True, "deleted": done})

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
        self._json(200, {"ok": True, "name": os.path.basename(dst)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
