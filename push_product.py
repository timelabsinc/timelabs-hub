#!/usr/bin/env python3
"""Push a product draft from hermes.db to Shopify as a DRAFT product.

Usage: push_product.py <product_row_id>

Reads SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN from /root/ops-dashboard/.env.
The product is created with status "draft" — it never goes live without a
human flipping it in Shopify admin. On success, writes shopify_product_id
and pushed_at back to the row and sets status='pushed'.
"""
import base64
import json
import sqlite3
import sys
import urllib.request

ENV_PATH = "/root/ops-dashboard/.env"
DB_PATH = "/root/ops-dashboard/data/hermes.db"


def load_env(path):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: push_product.py <product_row_id>")
    row_id = int(sys.argv[1])

    env = load_env(ENV_PATH)
    shop = env.get("SHOPIFY_SHOP")
    token = env.get("SHOPIFY_ADMIN_TOKEN")
    if not shop or not token:
        sys.exit("SHOPIFY_SHOP / SHOPIFY_ADMIN_TOKEN not set in .env — draft stays queued locally.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM products WHERE id = ?", (row_id,)).fetchone()
    if not row:
        sys.exit(f"no products row with id {row_id}")
    if row["status"] == "pushed":
        sys.exit(f"row {row_id} already pushed (shopify id {row['shopify_product_id']})")
    if not row["title"] or row["price_inr"] is None:
        sys.exit(f"row {row_id} missing mandatory fields (title/price) — status: {row['status']}")

    product = {
        "title": row["title"],
        "body_html": row["description"] or "",
        "status": "draft",
        "tags": row["tags"] or "",
        "variants": [{
            "price": f"{row['price_inr']:.2f}",
            "inventory_quantity": row["quantity"] or 1,
            "inventory_management": "shopify",
        }],
    }
    if row["compare_at_price_inr"]:
        product["variants"][0]["compare_at_price"] = f"{row['compare_at_price_inr']:.2f}"

    image_path = row["image_processed_path"] or row["image_original_path"]
    if image_path:
        try:
            with open(image_path, "rb") as f:
                product["images"] = [{"attachment": base64.b64encode(f.read()).decode()}]
        except OSError as e:
            print(f"warning: could not attach image ({e}), pushing without", file=sys.stderr)

    req = urllib.request.Request(
        f"https://{shop}/admin/api/2024-10/products.json",
        data=json.dumps({"product": product}).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode())

    pid = str(body["product"]["id"])
    conn.execute(
        "UPDATE products SET status='pushed', shopify_product_id=?, pushed_at=datetime('now') WHERE id=?",
        (pid, row_id),
    )
    conn.commit()
    print(f"pushed row {row_id} → Shopify draft product {pid} ({body['product']['title']})")


if __name__ == "__main__":
    main()
