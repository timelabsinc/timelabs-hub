#!/usr/bin/env python3
"""Shared Shopify Admin access layer — the foundation every Shopify tool in Hub
uses (Quick product updater, Product builder, Content updater, Theme editor).

Reads SHOPIFY_SHOP + SHOPIFY_ADMIN_TOKEN from /root/ops-dashboard/.env.
Until a real token is added it reports not-configured cleanly, so the tools
show a setup state instead of breaking.

Create the token: Shopify admin → Settings → Apps and sales channels →
Develop apps → (reuse existing or create) → Admin API scopes:
read/write_products, read/write_inventory, read/write_content → Install →
reveal the Admin API access token (starts shpat_). Put in .env as:
  SHOPIFY_SHOP=xd2fwj-1h.myshopify.com
  SHOPIFY_ADMIN_TOKEN=shpat_xxxxx
"""
import json
import os
import urllib.request
import urllib.error

ENV_PATH = "/root/ops-dashboard/.env"
API_VERSION = "2024-10"


def _env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def credentials():
    env = _env()
    shop = env.get("SHOPIFY_SHOP", "")
    token = env.get("SHOPIFY_ADMIN_TOKEN", "")
    if shop and "." not in shop:
        shop = shop + ".myshopify.com"
    return shop, token


def configured():
    shop, token = credentials()
    return bool(shop and token.startswith("shpat"))


class ShopifyError(Exception):
    pass


def admin_graphql(query, variables=None):
    """Run an Admin GraphQL operation. Raises ShopifyError with a readable
    message on transport errors or GraphQL userErrors."""
    shop, token = credentials()
    if not configured():
        raise ShopifyError("Shopify isn't connected yet — add a token to .env.")
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if e.code == 401:
            raise ShopifyError("Shopify rejected the token (401) — it may be wrong or lack scopes.")
        raise ShopifyError(f"Shopify API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ShopifyError(f"Couldn't reach Shopify: {e.reason}")
    if data.get("errors"):
        raise ShopifyError("; ".join(e.get("message", "?") for e in data["errors"])[:300])
    return data.get("data", {})


# --------------------------------------------------------------- product reads
def list_products(search="", first=25, after=None, sort="UPDATED_AT", reverse=True):
    q = """
    query($q: String, $first: Int!, $after: String, $sort: ProductSortKeys, $rev: Boolean) {
      products(query: $q, first: $first, after: $after, sortKey: $sort, reverse: $rev) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id title status handle productType vendor totalInventory featuredImage { url }
          variants(first: 1) { nodes { id price sku inventoryQuantity inventoryItem { id } } }
        }
      }
    }"""
    data = admin_graphql(q, {"q": search or None, "first": first, "after": after,
                             "sort": sort, "rev": reverse})
    conn = data["products"]
    out = []
    for n in conn["nodes"]:
        v = (n.get("variants", {}).get("nodes") or [{}])[0]
        out.append({
            "id": n["id"], "title": n["title"], "status": n["status"],
            "handle": n["handle"], "type": n.get("productType") or "",
            "image": (n.get("featuredImage") or {}).get("url"),
            "price": v.get("price"), "sku": v.get("sku"),
            "inventory": v.get("inventoryQuantity"),
            "variant_id": v.get("id"), "inventory_item_id": (v.get("inventoryItem") or {}).get("id"),
        })
    return {"products": out, "has_more": conn["pageInfo"]["hasNextPage"],
            "cursor": conn["pageInfo"]["endCursor"]}


# --------------------------------------------------------------- product writes
def set_status(product_id, status):
    """status: ACTIVE | DRAFT | ARCHIVED"""
    q = """mutation($input: ProductInput!) {
      productUpdate(input: $input) { product { id status } userErrors { field message } }
    }"""
    data = admin_graphql(q, {"input": {"id": product_id, "status": status}})
    errs = data["productUpdate"]["userErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["productUpdate"]["product"]


def set_price(product_id, variant_id, price):
    q = """mutation($pid: ID!, $variants: [ProductVariantsBulkInput!]!) {
      productVariantsBulkUpdate(productId: $pid, variants: $variants) {
        productVariants { id price } userErrors { field message }
      }
    }"""
    data = admin_graphql(q, {"pid": product_id,
                             "variants": [{"id": variant_id, "price": str(price)}]})
    errs = data["productVariantsBulkUpdate"]["userErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["productVariantsBulkUpdate"]["productVariants"][0]


def get_detail(product_id):
    """Full editable detail for one product, including images."""
    q = """query($id: ID!) {
      product(id: $id) {
        id title status handle productType vendor tags descriptionHtml
        variants(first: 1) { nodes { id price sku } }
        media(first: 20) { nodes { id ... on MediaImage { image { url } } } }
      }
    }"""
    p = admin_graphql(q, {"id": product_id})["product"]
    if not p:
        raise ShopifyError("product not found")
    v = (p.get("variants", {}).get("nodes") or [{}])[0]
    imgs = []
    for m in p.get("media", {}).get("nodes", []):
        url = (m.get("image") or {}).get("url")
        if url:
            imgs.append({"id": m["id"], "url": url})
    return {"id": p["id"], "title": p["title"], "status": p["status"],
            "type": p.get("productType") or "", "vendor": p.get("vendor") or "",
            "tags": p.get("tags") or [], "description": p.get("descriptionHtml") or "",
            "price": v.get("price"), "sku": v.get("sku"), "variant_id": v.get("id"),
            "images": imgs}


def update_fields(product_id, fields):
    """Update any of: title, descriptionHtml, productType, tags(list), status."""
    allowed = {"title", "descriptionHtml", "productType", "tags", "status"}
    inp = {"id": product_id}
    inp.update({k: v for k, v in fields.items() if k in allowed})
    if len(inp) == 1:
        return None
    q = """mutation($input: ProductInput!) {
      productUpdate(input: $input) { product { id } userErrors { field message } }
    }"""
    data = admin_graphql(q, {"input": inp})
    errs = data["productUpdate"]["userErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["productUpdate"]["product"]


def add_media_url(product_id, url):
    """Attach an image to a product from a public URL (e.g. a Drop share link)."""
    q = """mutation($pid: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $pid, media: $media) {
        media { id } mediaUserErrors { field message }
      }
    }"""
    data = admin_graphql(q, {"pid": product_id, "media": [
        {"originalSource": url, "mediaContentType": "IMAGE"}]})
    errs = data["productCreateMedia"]["mediaUserErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["productCreateMedia"]["media"]


def remove_media(product_id, media_id):
    q = """mutation($pid: ID!, $ids: [ID!]!) {
      productDeleteMedia(productId: $pid, mediaIds: $ids) {
        deletedMediaIds mediaUserErrors { field message }
      }
    }"""
    data = admin_graphql(q, {"pid": product_id, "ids": [media_id]})
    errs = data["productDeleteMedia"]["mediaUserErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["productDeleteMedia"]["deletedMediaIds"]


if __name__ == "__main__":
    print("configured:", configured())
    if configured():
        r = list_products(first=3)
        for p in r["products"]:
            print(f"  {p['status']:8} ₹{p['price']:>9}  {p['title']}")
    else:
        shop, _ = credentials()
        print(f"shop={shop or '(unset)'} — add SHOPIFY_ADMIN_TOKEN to {ENV_PATH}")
