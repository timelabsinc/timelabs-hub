#!/usr/bin/env python3
"""Session login + email password reset for the Timelabs ops dashboard.

Runs on 127.0.0.1:8899 behind nginx:
  GET  /auth           -> 200 if valid session cookie, else 401 (nginx auth_request)
  GET  /login          -> branded login page      POST /login -> sign in
  GET  /reset          -> request reset code      POST /reset -> email a code
  GET  /reset/confirm  -> enter code + new pw     POST /reset/confirm -> apply

Reset emails go to the registered owner address only (RESET_EMAIL in
/root/ops-dashboard/.env, default timelabs.inc@gmail.com) via Gmail SMTP
(SMTP_USER + SMTP_APP_PASSWORD in .env). On a successful reset the signing
secret rotates, logging out every existing session.
"""
import hashlib
import hmac
import http.server
import os
import secrets
import smtplib
import time
import urllib.parse
from email.message import EmailMessage

HOST, PORT = "127.0.0.1", 8899
BASE = "/root/ops-dashboard"
SECRET_PATH = f"{BASE}/.auth_secret"
HASH_PATH = f"{BASE}/.ops_password_hash"
ENV_PATH = f"{BASE}/.env"
SESSION_DAYS = 30
MAX_FAILS = 5
LOCKOUT_SECONDS = 900
RESET_CODE_TTL = 900
RESET_MAX_ATTEMPTS = 5


def load_env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def get_secret():
    if not os.path.exists(SECRET_PATH):
        rotate_secret()
    return open(SECRET_PATH).read().strip().encode()


def rotate_secret():
    with open(SECRET_PATH, "w") as f:
        f.write(secrets.token_hex(32))
    os.chmod(SECRET_PATH, 0o600)


def set_password(new_password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(new_password.encode(), salt=salt, n=16384, r=8, p=1)
    with open(HASH_PATH, "w") as f:
        f.write(f"{salt.hex()}${derived.hex()}")
    os.chmod(HASH_PATH, 0o600)


def check_password(candidate):
    try:
        salt_hex, hash_hex = open(HASH_PATH).read().strip().split("$")
    except (OSError, ValueError):
        return False
    derived = hashlib.scrypt(candidate.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1)
    return hmac.compare_digest(derived.hex(), hash_hex)


def make_token():
    expiry = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(get_secret(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def valid_token(token):
    try:
        expiry, sig = token.split(".")
    except ValueError:
        return False
    expected = hmac.new(get_secret(), expiry.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return int(expiry) > time.time()


_fails = {}          # ip -> (count, first_ts) for login attempts
_reset_state = {}    # single pending reset: {code_hash, expires, attempts}
_reset_requests = {} # ip -> (count, first_ts) rate limit for requesting codes


def locked_out(store, ip, max_n, window):
    entry = store.get(ip)
    if not entry:
        return False
    count, first_ts = entry
    if time.time() - first_ts > window:
        del store[ip]
        return False
    return count >= max_n


def record(store, ip, window):
    now = time.time()
    count, first_ts = store.get(ip, (0, now))
    if now - first_ts > window:
        count, first_ts = 0, now
    store[ip] = (count + 1, first_ts)


def send_reset_email(code):
    env = load_env()
    smtp_user = env.get("SMTP_USER")
    smtp_pass = env.get("SMTP_APP_PASSWORD")
    to_addr = env.get("RESET_EMAIL", "timelabs.inc@gmail.com")
    if not smtp_user or not smtp_pass:
        return False, "email-not-configured"
    msg = EmailMessage()
    msg["Subject"] = "Timelabs Ops — password reset code"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.set_content(
        f"Your Timelabs Ops password reset code is: {code}\n\n"
        f"It expires in 15 minutes. If you didn't request this, ignore this email."
    )
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Timelabs — {title}</title>
<style>
  :root{{--ground:#0f1512;--surface:#161f1a;--ink:#eef2ee;--muted:#9fb0a6;--line:#293831;
    --accent:#c9a35e;--crit:#e06a45;--good:#5cbf88;
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}}
  @media (prefers-color-scheme: light){{
    :root{{--ground:#ecefe8;--surface:#fff;--ink:#16211b;--muted:#586b60;--line:#dde3dc;
      --accent:#8a6a2c;--crit:#c1502e;--good:#2f8f5b;}}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:var(--ground);color:var(--ink);font-family:var(--sans);}}
  .card{{width:min(360px,90vw);background:var(--surface);border:1px solid var(--line);
    border-radius:14px;padding:2.2rem 2rem;box-shadow:0 12px 34px rgba(0,0,0,.25);}}
  .mark{{font-family:var(--serif);font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;
    color:var(--accent);margin-bottom:.4rem;}}
  h1{{font-family:var(--serif);font-weight:600;font-size:1.45rem;margin:0 0 1.4rem;}}
  label{{display:block;font-size:.78rem;color:var(--muted);margin-bottom:.4rem;
    text-transform:uppercase;letter-spacing:.06em;}}
  input{{width:100%;padding:.7rem .8rem;border:1px solid var(--line);border-radius:8px;
    background:var(--ground);color:var(--ink);font-size:1rem;margin-bottom:1.1rem;}}
  input:focus{{outline:2px solid var(--accent);border-color:var(--accent);}}
  button{{width:100%;padding:.75rem;border:none;border-radius:8px;background:var(--accent);
    color:#141310;font-size:.95rem;font-weight:600;cursor:pointer;}}
  button:hover{{filter:brightness(1.08);}}
  .err{{color:var(--crit);font-size:.85rem;margin:0 0 1rem;}}
  .ok{{color:var(--good);font-size:.85rem;margin:0 0 1rem;}}
  .hint{{color:var(--muted);font-size:.75rem;margin-top:1.2rem;line-height:1.5;}}
  a{{color:var(--accent);}}
</style>
</head>
<body>
{body}
</body>
</html>"""

LOGIN_FORM = """<form class="card" method="POST" action="/ops/login">
  <div class="mark">Timelabs Co</div>
  <h1>Sign in to Ops</h1>
  {notice}
  <label for="pw">Password</label>
  <input id="pw" type="password" name="password" autofocus autocomplete="current-password">
  <button type="submit">Sign in</button>
  <p class="hint">You'll stay signed in on this device for 30 days.<br>
  <a href="/ops/reset">Forgot password?</a></p>
</form>"""

RESET_FORM = """<form class="card" method="POST" action="/ops/reset">
  <div class="mark">Timelabs Co</div>
  <h1>Reset password</h1>
  {notice}
  <label for="em">Your email</label>
  <input id="em" type="email" name="email" autofocus autocomplete="email" placeholder="you@example.com">
  <button type="submit">Email me a code</button>
  <p class="hint">A 6-digit code goes to the registered owner email.<br>
  <a href="/ops/login">Back to sign in</a></p>
</form>"""

CONFIRM_FORM = """<form class="card" method="POST" action="/ops/reset/confirm">
  <div class="mark">Timelabs Co</div>
  <h1>Enter code</h1>
  {notice}
  <label for="code">6-digit code from your email</label>
  <input id="code" name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" autofocus autocomplete="one-time-code">
  <label for="npw">New password</label>
  <input id="npw" type="password" name="password" autocomplete="new-password">
  <button type="submit">Set new password</button>
  <p class="hint">Code expires 15 minutes after it was sent.<br>
  <a href="/ops/login">Back to sign in</a></p>
</form>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _client_ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def _cookie_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith("ops_session="):
                return part.split("=", 1)[1]
        return None

    def _send_html(self, status, title, body):
        data = PAGE.format(title=title, body=body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(min(length, 4096)).decode()
        return urllib.parse.parse_qs(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/auth":
            token = self._cookie_token()
            self.send_response(200 if token and valid_token(token) else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/login":
            self._send_html(200, "Sign in", LOGIN_FORM.format(notice=""))
        elif path == "/reset":
            self._send_html(200, "Reset password", RESET_FORM.format(notice=""))
        elif path == "/reset/confirm":
            self._send_html(200, "Enter code", CONFIRM_FORM.format(notice=""))
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        ip = self._client_ip()

        if path == "/login":
            if locked_out(_fails, ip, MAX_FAILS, LOCKOUT_SECONDS):
                self._send_html(429, "Sign in", LOGIN_FORM.format(
                    notice='<p class="err">Too many attempts. Try again in 15 minutes.</p>'))
                return
            password = (self._form().get("password") or [""])[0]
            if check_password(password):
                _fails.pop(ip, None)
                token = make_token()
                self.send_response(302)
                self.send_header("Location", "/ops/")
                self.send_header(
                    "Set-Cookie",
                    f"ops_session={token}; Path=/; Max-Age={SESSION_DAYS*86400}; HttpOnly; SameSite=Lax",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                record(_fails, ip, LOCKOUT_SECONDS)
                self._send_html(401, "Sign in", LOGIN_FORM.format(
                    notice='<p class="err">Wrong password. Try again.</p>'))

        elif path == "/reset":
            if locked_out(_reset_requests, ip, 3, 3600):
                self._send_html(429, "Reset password", RESET_FORM.format(
                    notice='<p class="err">Too many reset requests. Try again in an hour.</p>'))
                return
            record(_reset_requests, ip, 3600)
            email = (self._form().get("email") or [""])[0].strip().lower()
            registered = load_env().get("RESET_EMAIL", "timelabs.inc@gmail.com").lower()
            generic = ('<p class="ok">If that email is registered, a code is on its way. '
                       '<a href="/ops/reset/confirm">Enter the code</a>.</p>')
            if email == registered:
                code = f"{secrets.randbelow(1000000):06d}"
                _reset_state.clear()
                _reset_state.update({
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "expires": time.time() + RESET_CODE_TTL,
                    "attempts": 0,
                })
                ok, err = send_reset_email(code)
                if not ok:
                    _reset_state.clear()
                    if err == "email-not-configured":
                        self._send_html(503, "Reset password", RESET_FORM.format(
                            notice='<p class="err">Email sending isn\'t configured yet — '
                                   'ask your admin to finish SMTP setup.</p>'))
                    else:
                        self._send_html(502, "Reset password", RESET_FORM.format(
                            notice='<p class="err">Couldn\'t send the email. Try again shortly.</p>'))
                    return
            self._send_html(200, "Reset password", RESET_FORM.format(notice=generic))

        elif path == "/reset/confirm":
            form = self._form()
            code = (form.get("code") or [""])[0].strip()
            new_pw = (form.get("password") or [""])[0]
            state = _reset_state
            if not state or time.time() > state.get("expires", 0):
                self._send_html(400, "Enter code", CONFIRM_FORM.format(
                    notice='<p class="err">No active reset (or the code expired). '
                           '<a href="/ops/reset">Request a new one</a>.</p>'))
                return
            if state["attempts"] >= RESET_MAX_ATTEMPTS:
                _reset_state.clear()
                self._send_html(429, "Enter code", CONFIRM_FORM.format(
                    notice='<p class="err">Too many wrong codes. '
                           '<a href="/ops/reset">Request a new one</a>.</p>'))
                return
            state["attempts"] += 1
            if not hmac.compare_digest(
                hashlib.sha256(code.encode()).hexdigest(), state["code_hash"]
            ):
                self._send_html(401, "Enter code", CONFIRM_FORM.format(
                    notice='<p class="err">Wrong code. Check the email and try again.</p>'))
                return
            if len(new_pw) < 8:
                self._send_html(400, "Enter code", CONFIRM_FORM.format(
                    notice='<p class="err">Password must be at least 8 characters.</p>'))
                return
            set_password(new_pw)
            rotate_secret()  # log out every existing session
            _reset_state.clear()
            self._send_html(200, "Sign in", LOGIN_FORM.format(
                notice='<p class="ok">Password updated. Sign in with your new password.</p>'))

        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
