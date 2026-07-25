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
import subprocess
import time

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
        "blurb": "Every day-to-day tool — dashboard, orders, Drop, Ledger, Shopify tools. No admin.",
        "home": "/ops/",
        "tools": ["face", "drop", "ledger", "chat", "blog", "content",
                  "theme", "price", "product", "orders", "reddit"],
    },
    "content": {
        "label": "Content",
        "blurb": "The Reddit tool and the writing tools. No orders, no customer "
                 "list, no Ledger, no Shopify admin.",
        "home": "/ops/reddit.html",
        "tools": ["reddit", "blog", "content", "drop"],
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
DEFAULT_ROLE = "full"


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
    return role if role in ROLES else DEFAULT_ROLE


def set_role(email, role):
    email = (email or "").strip().lower()
    if role not in ROLES:
        raise ValueError("unknown role")
    if email in ADMINS and role != "admin":
        raise ValueError("admins keep the admin role — remove them from ADMINS in code first")
    data = _load()
    data.setdefault("roles", {})[email] = role
    _save(data)
    sync_nginx()
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
    return ROLES.get(role, ROLES[DEFAULT_ROLE])["home"]


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
        r = "admin" if e in ADMINS else (roles.get(e) if roles.get(e) in ROLES else DEFAULT_ROLE)
        out.append({"email": e, "role": r, "label": ROLES[r]["label"],
                    "home": ROLES[r]["home"], "is_admin": e in ADMINS})
    return out


def forget(email):
    """Drop a person's role record (call when they leave the allowlist) and
    refresh the gate so no stale restriction lingers."""
    email = (email or "").strip().lower()
    data = _load()
    if data.get("roles", {}).pop(email, None) is not None:
        _save(data)
    sync_nginx()


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
        home = ROLES[p["role"]]["home"]
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
    backup = None
    if os.path.exists(NGINX_MAP):
        backup = f"{NGINX_MAP}.bak.{int(time.time())}"
        try:
            with open(NGINX_MAP) as a, open(backup, "w") as b:
                b.write(a.read())
        except OSError:
            backup = None
    with open(NGINX_MAP, "w") as f:
        f.write(new)
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test.returncode != 0:
        if backup and os.path.exists(backup):
            os.replace(backup, NGINX_MAP)
        else:
            os.remove(NGINX_MAP)
        return False, "nginx config test failed — reverted: " + test.stderr[-200:]
    if reload:
        subprocess.run(["systemctl", "reload", "nginx"], capture_output=True)
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
