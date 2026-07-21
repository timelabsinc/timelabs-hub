#!/usr/bin/env python3
"""Tiny localhost-only HTTP service: POST /refresh regenerates the dashboard.

Sits behind nginx's basic auth on /ops/refresh — never exposed directly.
"""
import http.server
import json
import subprocess
import threading

HOST = "127.0.0.1"
PORT = 8877
LOCK = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        if path.rstrip("/") != "/refresh":
            self._json(404, {"error": "not found"})
            return
        days = "90"
        for p in query.split("&"):
            if p.startswith("days="):
                days = p[5:]
        if days not in ("7", "30", "90"):
            days = "90"
        if not LOCK.acquire(blocking=False):
            self._json(409, {"error": "refresh already running"})
            return
        try:
            import os
            env = dict(os.environ, OPS_RANGE_DAYS=days)
            result = subprocess.run(
                ["/root/ops-dashboard/venv/bin/python3", "/root/ops-dashboard/generate.py"],
                capture_output=True, text=True, timeout=260, env=env,
            )
            ok = result.returncode == 0
            self._json(200 if ok else 500, {
                "ok": ok,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            })
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "generate.py timed out after 260s"})
        finally:
            LOCK.release()

    def log_message(self, fmt, *args):
        pass  # keep journald quiet; systemd captures stdout/stderr separately if needed


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
