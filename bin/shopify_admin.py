#!/usr/bin/env python3
"""
shopify_admin — the tool that lets Hermes ACT on the Shopify store (not just talk).

Hermes calls this via its `terminal` tool. It is deliberately safe-by-default:
  * writes require --execute (a bare call is a dry-run that only PRINTS the plan)
  * sanity bounds reject obviously-misparsed changes unless --force
  * every executed change is logged to hermes.db `shopify_changes`

Reads SHOPIFY_SHOP (e.g. your-store.myshopify.com) and SHOPIFY_ADMIN_TOKEN from
/root/ops-dashboard/.env. If they are missing it prints exactly what to add and
where — so Hermes can tell the owner the one specific thing that's blocking, and
NEVER just say "I can't".

Usage:
  shopify_admin.py search "<query>"                 # list matching products
  shopify_admin.py set-price --query "<q>" --price N # DRY RUN: show old->new
  shopify_admin.py set-price --query "<q>" --price N --execute   # apply
  shopify_admin.py set-status --query "<q>" --status active|draft [--execute]
Options: --force (bypass sanity bounds), --limit N (max products, default 40)
"""
import argparse, json, os, sys, urllib.request, urllib.error, sqlite3
from datetime import datetime, timezone

ENV = "/root/ops-dashboard/.env"
HDB = "/root/ops-dashboard/data/hermes.db"
API = "2024-10"
PRICE_MIN, PRICE_MAX, DEFAULT_MAX_PRODUCTS = 100, 500000, 40


def load_env(path):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def creds():
    env = load_env(ENV)
    shop, token = env.get("SHOPIFY_SHOP"), env.get("SHOPIFY_ADMIN_TOKEN")
    if not shop or not token:
        print("NOT CONFIGURED — Shopify admin access is not set up yet.\n"
              "To enable store actions, add these two lines to " + ENV + ":\n"
              "  SHOPIFY_SHOP=<your-store>.myshopify.com\n"
              "  SHOPIFY_ADMIN_TOKEN=shpat_xxx  (Shopify admin -> Settings -> Apps -> "
              "Develop apps -> create app -> Admin API scopes write_products/read_products "
              "-> Install -> reveal Admin API access token)\n"
              "Until then I can read the store via the owner's Claude Code connector, "
              "but I cannot write from here.", file=sys.stderr)
        sys.exit(3)
    return shop, token


def gql(shop, token, query, variables=None):
    req = urllib.request.Request(
        f"https://{shop}/admin/api/{API}/graphql.json",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Shopify API error {e.code}: {e.read().decode()[:300]}", file=sys.stderr); sys.exit(4)
    if body.get("errors"):
        print(f"GraphQL error: {json.dumps(body['errors'])[:300]}", file=sys.stderr); sys.exit(4)
    return body["data"]


def find_products(shop, token, query, limit):
    q = """query($q:String!,$n:Int!){products(first:$n,query:$q){edges{node{
        id title status
        variants(first:100){edges{node{id price}}}
        priceRangeV2{minVariantPrice{amount}maxVariantPrice{amount}}}}}}"""
    data = gql(shop, token, q, {"q": query, "n": limit})
    return [e["node"] for e in data["products"]["edges"]]


def audit(action, query, detail):
    try:
        c = sqlite3.connect(HDB)
        c.execute("CREATE TABLE IF NOT EXISTS shopify_changes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                  "ts TEXT, action TEXT, query TEXT, detail TEXT, by_actor TEXT DEFAULT 'hermes')")
        c.execute("INSERT INTO shopify_changes (ts,action,query,detail) VALUES (?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), action, query, detail))
        c.commit(); c.close()
    except Exception as e:
        print(f"(warning: could not write audit log: {e})", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query")
    for name in ("set-price", "set-status"):
        p = sub.add_parser(name)
        p.add_argument("--query", required=True)
        p.add_argument("--price", type=float)
        p.add_argument("--status", choices=["active", "draft", "archived"])
        p.add_argument("--execute", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--limit", type=int, default=DEFAULT_MAX_PRODUCTS)
    a = ap.parse_args()
    shop, token = creds()

    if a.cmd == "search":
        for p in find_products(shop, token, a.query, DEFAULT_MAX_PRODUCTS):
            pr = p["priceRangeV2"]["minVariantPrice"]["amount"]
            print(f"{p['status']:8} ₹{pr:>9}  {p['title']}")
        return

    prods = find_products(shop, token, a.query, a.limit)
    if not prods:
        print(f"No products match: {a.query}"); sys.exit(2)
    if len(prods) > a.limit and not a.force:
        print(f"{len(prods)} products matched (>{a.limit}). Refine the query or pass --force.", file=sys.stderr); sys.exit(5)

    if a.cmd == "set-price":
        if a.price is None: print("--price required", file=sys.stderr); sys.exit(1)
        if not a.force and not (PRICE_MIN <= a.price <= PRICE_MAX):
            print(f"Price ₹{a.price:.0f} is outside the sanity range ₹{PRICE_MIN}-{PRICE_MAX}. "
                  f"If you really mean it, pass --force.", file=sys.stderr); sys.exit(5)
        print(f"{'APPLYING' if a.execute else 'DRY RUN — plan only'}: set price → ₹{a.price:.0f} "
              f"on {len(prods)} product(s) matching '{a.query}':")
        for p in prods:
            cur = p["priceRangeV2"]["minVariantPrice"]["amount"]
            print(f"  {p['title']}: ₹{cur} → ₹{a.price:.0f} ({len(p['variants']['edges'])} variants)")
        if not a.execute:
            print("\nNothing changed. Re-run with --execute to apply."); return
        m = """mutation($id:ID!,$v:[ProductVariantsBulkInput!]!){productVariantsBulkUpdate(productId:$id,variants:$v){userErrors{message}}}"""
        done = 0
        for p in prods:
            variants = [{"id": e["node"]["id"], "price": f"{a.price:.2f}"} for e in p["variants"]["edges"]]
            gql(shop, token, m, {"id": p["id"], "v": variants}); done += 1
        audit("set-price", a.query, f"₹{a.price:.0f} on {done} products")
        print(f"\n✓ Done — {done} product(s) set to ₹{a.price:.0f}. Logged to shopify_changes.")

    elif a.cmd == "set-status":
        if not a.status: print("--status required", file=sys.stderr); sys.exit(1)
        print(f"{'APPLYING' if a.execute else 'DRY RUN'}: status → {a.status} on {len(prods)} product(s):")
        for p in prods: print(f"  {p['title']}: {p['status']} → {a.status.upper()}")
        if not a.execute:
            print("\nNothing changed. Re-run with --execute to apply."); return
        m = """mutation($p:ProductUpdateInput!){productUpdate(product:$p){userErrors{message}}}"""
        for p in prods:
            gql(shop, token, m, {"p": {"id": p["id"], "status": a.status.upper()}})
        audit("set-status", a.query, f"{a.status} on {len(prods)} products")
        print(f"\n✓ Done — {len(prods)} product(s) set to {a.status}. Logged to shopify_changes.")


if __name__ == "__main__":
    main()
