#!/usr/bin/env python3
"""Who may use which tools.

Signing in is one question (oauth2-proxy's allowlist); what you may *do* once
you're in is another, and until now there was no answer to the second — anyone
allowlisted saw everything. This adds roles on top of the allowlist.

Enforcement is deliberately layered, because hiding a nav item protects nobody:
  1. nginx  — a generated map bounces a restricted user off /ops/ entirely, so
              they can never fetch a page whose HTML already contains revenue.
  2. server — the API refuses actions outside the role.
  3. UI     — nav and tiles only show what the role allows.

Stdlib only: the agent server runs on system python and imports this directly.
"""
import json
import os
import posixpath
import re
import subprocess
import threading
import time
import urllib.parse

STORE = "/root/ops-dashboard/access.json"
# .conf, not .map — nginx.conf only includes conf.d/*.conf
NGINX_MAP = "/etc/nginx/conf.d/labs-access.conf"
ALLOWLIST = "/etc/oauth2-proxy/allowlist.txt"

# Mirrors ADMIN_EMAILS elsewhere; admins always resolve to the admin role.
ADMINS = ("timelabs.inc@gmail.com", "schezan.m@gmail.com")

# A role is a landing page + the set of tools it may use. "*" means everything.
ROLES = {
    "admin": {
        "label": "Admin",
        "blurb": "Everything, including access control, system files and the map.",
        "home": "/ops/",
        "tools": "*",
    },
    "full": {
        "label": "Full",
        "blurb": "Daily operations — dashboard, orders, Drop, Ledger, Command and Reddit. "
                 "Live Shopify publishing stays admin-only.",
        "home": "/ops/",
        "tools": ["face", "drop", "ledger", "chat", "orders", "reddit"],
    },
    "creator": {
        "label": "Creator",
        "blurb": "Content drafting in private Command conversations, plus Reddit and Drop. "
                 "No orders, customers, margins, owner chats, sending or publishing.",
        "home": "/ops/command.html",
        "tools": ["chat", "reddit", "drop"],
    },
    "orders": {
        "label": "Orders",
        "blurb": "The order form and the order/customer lists. No revenue dashboard, no Shopify tools.",
        "home": "/ops/order-form.html",
        "tools": ["orders"],
    },
    "intake": {
        "label": "Intake only",
        "blurb": "Can log new orders and nothing else — no lists, no totals, no other tool.",
        "home": "/intake/",
        "tools": ["intake"],
    },
    "supplier": {
        "label": "Supplier",
        "blurb": "The build queue only — order number, spec, photos, status. No customer "
                 "name, phone, address, or price ever leaves the server for this role.",
        "home": "/ops/supplier.html",
        "tools": ["supplier"],
    },
}
# Unknown or partially-created accounts get no tools. Existing legacy members
# were explicitly backfilled before this default was changed; new invitations
# always write a role before activating the OAuth allowlist.
DEFAULT_ROLE = None
ACCESS_LOCK = threading.RLock()
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"roles": {}}


def _save(data):
    data["updated"] = int(time.time())
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STORE)
    os.chmod(STORE, 0o600)


def get_role(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    if email in ADMINS:
        return "admin"
    role = _load().get("roles", {}).get(email)
    # A stale or hand-edited record must never manufacture an admin. Admin
    # identities are deliberately immutable and live only in ADMINS.
    return role if role in ROLES and role != "admin" else None


def set_role(email, role):
    email = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email address")
    if role not in ROLES:
        raise ValueError("unknown role")
    if role == "admin" and email not in ADMINS:
        raise ValueError("admin access is fixed in code and cannot be granted here")
    if email in ADMINS and role != "admin":
        raise ValueError("admins keep the admin role — remove them from ADMINS in code first")
    with ACCESS_LOCK:
        before = _load()
        data = json.loads(json.dumps(before))
        data.setdefault("roles", {})[email] = role
        _save(data)
        ok, note = sync_nginx()
        if not ok:
            _save(before)
            sync_nginx()
            raise RuntimeError(note)
        return role


def can_use(email, tool_key):
    """Is this person allowed this tool key (the keys used in tools.py)?"""
    role = get_role(email)
    if not role:
        return False
    allowed = ROLES[role]["tools"]
    return allowed == "*" or tool_key in allowed


def home_for(email):
    role = get_role(email)
    return ROLES[role]["home"] if role in ROLES else "/oauth2/sign_out"


_OPS_PATH_TO_TOOL = {
    "": "face",
    "index.html": "face",
    "command.html": "chat",
    "order-form.html": "orders",
    "supplier.html": "supplier",
    "ledger.html": "ledger",
    "reddit.html": "reddit",
    "blog.html": "blog",
    "blog-uploader.html": "blog",
    "content-updater.html": "content",
    "theme-editor.html": "theme",
    "product-updater.html": "price",
    "product-builder.html": "product",
}


def canonical_request_path(uri):
    """Return the one canonical path nginx selected, or ``None`` if unsafe.

    nginx chooses a location after decoding and normalizing the URI, while
    ``$request_uri`` deliberately retains the raw bytes. The access subrequest
    must therefore decode once and reject traversal itself; authorizing a raw
    ``/drop/%2e%2e/ops/...`` prefix would otherwise let a Drop role fetch the
    normalized privileged ``/ops`` file.
    """
    raw = str(uri or "").split("?", 1)[0]
    if (not raw.startswith("/") or raw.startswith("//")
            or "\x00" in raw or "\\" in raw
            or re.search(r"%(?![0-9A-Fa-f]{2})", raw)):
        return None
    try:
        decoded = urllib.parse.unquote(raw, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if (not decoded.startswith("/") or decoded.startswith("//")
            or "\x00" in decoded or "\\" in decoded
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
        return None
    segments = decoded.split("/")
    if any(segment in (".", "..") for segment in segments):
        return None
    canonical = posixpath.normpath(decoded)
    return canonical if canonical.startswith("/") else None


def can_open_path(email, uri):
    """Authorize generated HTML before nginx serves it.

    Unknown /ops pages fail closed to admins instead of relying on the page to
    hide privileged controls after it has already reached the browser.
    """
    path = canonical_request_path(uri)
    if not path:
        return False
    role = get_role(email)
    if role == "admin":
        return True
    if not role:
        return False
    if path == "/drop" or path.startswith("/drop/"):
        return can_use(email, "drop")
    if path == "/intake" or path.startswith("/intake/"):
        return role == "intake"
    if path != "/ops" and not path.startswith("/ops/"):
        return False
    rel = path[4:].lstrip("/")
    if rel == "tools.html":
        return role in ("full", "creator")
    if rel.startswith("agent/"):
        return can_use(email, "chat")
    tool = _OPS_PATH_TO_TOOL.get(rel)
    return bool(tool and can_use(email, tool))


def allowlist_emails():
    """Everyone who can sign in at all — the oauth2-proxy allowlist."""
    try:
        with open(ALLOWLIST) as f:
            return sorted({ln.strip().lower() for ln in f
                           if ln.strip() and not ln.startswith("#")})
    except OSError:
        return []


def everyone():
    """[{email, role, label, home, is_admin}] for the admin UI."""
    roles = _load().get("roles", {})
    out = []
    for e in allowlist_emails():
        stored = roles.get(e)
        r = ("admin" if e in ADMINS
             else (stored if stored in ROLES and stored != "admin" else None))
        spec = ROLES.get(r)
        out.append({"email": e, "role": r,
                    "label": spec["label"] if spec else "No access",
                    "home": spec["home"] if spec else "/oauth2/sign_out",
                    "is_admin": e in ADMINS})
    return out


def forget(email):
    """Drop a person's role record (call when they leave the allowlist) and
    refresh the gate so no stale restriction lingers."""
    email = (email or "").strip().lower()
    with ACCESS_LOCK:
        data = _load()
        if data.get("roles", {}).pop(email, None) is not None:
            _save(data)
        sync_nginx()


def _write_allowlist(emails):
    tmp = ALLOWLIST + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(emails) + "\n")
    os.replace(tmp, ALLOWLIST)


def invite(email, role):
    """Add one person with their final role already in place.

    Both files and the nginx gate are rolled back if validation or reload
    fails, so there is never an allowlisted interval at DEFAULT_ROLE.
    """
    email = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email address")
    if role not in ROLES:
        raise ValueError("unknown role")
    if role == "admin" and email not in ADMINS:
        raise ValueError("admin access is fixed in code and cannot be granted here")
    if email in ADMINS and role != "admin":
        raise ValueError("admins keep the admin role")
    with ACCESS_LOCK:
        before_roles = _load()
        before_members = allowlist_emails()
        data = json.loads(json.dumps(before_roles))
        data.setdefault("roles", {})[email] = role
        members = list(before_members)
        if email not in members:
            members.append(email)
        try:
            # Role first: even if oauth2-proxy notices the later allowlist
            # replace immediately, the restrictive role already exists.
            _save(data)
            _write_allowlist(members)
            ok, note = sync_nginx()
            if not ok:
                raise RuntimeError(note)
        except Exception:
            _save(before_roles)
            _write_allowlist(before_members)
            sync_nginx()
            raise
        return role, note, email in before_members


# ------------------------------------------------------------------ nginx gate
def build_map():
    """nginx map: authenticated email -> where they're confined.

    Empty value = unrestricted. A non-empty value is the URL that /ops/ bounces
    them to, which is why a restricted user can never be served a page that
    already has the numbers baked into its HTML."""
    lines = ["# generated by access_store.py — do not edit by hand",
             "map $labs_email $labs_restrict {",
             '    default "";']
    for p in everyone():
        home = ROLES[p["role"]]["home"] if p["role"] in ROLES else "/oauth2/sign_out"
        if home != "/ops/":                      # confined to something narrower
            lines.append(f'    "{p["email"]}" "{home}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def sync_nginx(reload=True):
    """Write the map and reload nginx — but only if the config still tests OK,
    so a bad entry can never take the site down."""
    new = build_map()
    try:
        with open(NGINX_MAP) as f:
            if f.read() == new:
                return True, "unchanged"
    except OSError:
        pass
    existed = os.path.exists(NGINX_MAP)
    old = None
    if existed:
        try:
            with open(NGINX_MAP) as f:
                old = f.read()
        except OSError:
            old = None

    def restore():
        if existed and old is not None:
            with open(NGINX_MAP, "w") as f:
                f.write(old)
        elif not existed:
            try:
                os.remove(NGINX_MAP)
            except FileNotFoundError:
                pass

    with open(NGINX_MAP, "w") as f:
        f.write(new)
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test.returncode != 0:
        restore()
        return False, "nginx config test failed — reverted: " + test.stderr[-200:]
    if reload:
        reloaded = subprocess.run(
            ["systemctl", "reload", "nginx"], capture_output=True, text=True)
        if reloaded.returncode != 0:
            restore()
            subprocess.run(["nginx", "-t"], capture_output=True, text=True)
            subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True)
            return False, "nginx reload failed — reverted: " + reloaded.stderr[-200:]
    return True, "reloaded"


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "set":
        print(set_role(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else DEFAULT_ROLE))
    elif len(sys.argv) >= 2 and sys.argv[1] == "sync":
        print(sync_nginx())
    else:
        for p in everyone():
            print(f'{p["email"]:34} {p["role"]:8} -> {p["home"]}')
