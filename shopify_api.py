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
import copy
import json
import os
import urllib.request
import urllib.error
import urllib.parse

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


# --------------------------------------------------------------- blog articles
def list_blogs():
    nodes = admin_graphql("{ blogs(first: 20) { nodes { id title handle } } }")["blogs"]["nodes"]
    return [{"id": b["id"], "title": b["title"], "handle": b["handle"]} for b in nodes]


def list_articles(blog_id=None, first=50):
    """Articles, newest first. Shopify has no blogId filter on `articles`, so we
    read the blog's own connection when one is named."""
    if blog_id:
        q = """query($id: ID!, $n: Int!) { blog(id: $id) { articles(first: $n, reverse: true) {
                 nodes { id title handle isPublished publishedAt
                         author { name } } } } }"""
        blog = admin_graphql(q, {"id": blog_id, "n": first}).get("blog") or {}
        nodes = (blog.get("articles") or {}).get("nodes") or []
    else:
        q = """query($n: Int!) { articles(first: $n, reverse: true) {
                 nodes { id title handle isPublished publishedAt author { name } } } }"""
        nodes = admin_graphql(q, {"n": first})["articles"]["nodes"]
    return [{"id": a["id"], "title": a["title"], "handle": a.get("handle"),
             "published": bool(a.get("isPublished")),
             "published_at": a.get("publishedAt"),
             "author": (a.get("author") or {}).get("name") or ""} for a in nodes]


def get_article(article_id):
    q = """query($id: ID!) { article(id: $id) {
             id title handle body summary isPublished publishedAt tags
             author { name } blog { id title } image { url } } }"""
    a = admin_graphql(q, {"id": article_id}).get("article")
    if not a:
        raise ShopifyError("article not found")
    return {"id": a["id"], "title": a["title"], "handle": a.get("handle"),
            "body": a.get("body") or "", "summary": a.get("summary") or "",
            "published": bool(a.get("isPublished")), "tags": a.get("tags") or [],
            "author": (a.get("author") or {}).get("name") or "",
            "blog_id": (a.get("blog") or {}).get("id"),
            "blog_title": (a.get("blog") or {}).get("title"),
            "image": (a.get("image") or {}).get("url")}


def _article_payload(title=None, body=None, summary=None, tags=None,
                     author=None, published=None, image_url=None):
    p = {}
    if title is not None:
        p["title"] = str(title).strip()
    if body is not None:
        p["body"] = str(body)
    if summary is not None:
        p["summary"] = str(summary)
    if tags is not None:
        p["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if author:
        p["author"] = {"name": str(author).strip()}
    if published is not None:
        p["isPublished"] = bool(published)
    if image_url:
        p["image"] = {"url": str(image_url).strip()}
    return p


def create_article(blog_id, title, body="", summary="", tags=None,
                   author="", published=False, image_url=""):
    if not blog_id:
        raise ShopifyError("pick a blog to publish into")
    if not (title or "").strip():
        raise ShopifyError("the post needs a title")
    article = _article_payload(title, body, summary, tags, author, published, image_url)
    article["blogId"] = blog_id
    q = """mutation($article: ArticleCreateInput!) {
      articleCreate(article: $article) {
        article { id handle isPublished } userErrors { field message } } }"""
    data = admin_graphql(q, {"article": article})["articleCreate"]
    if data["userErrors"]:
        raise ShopifyError(data["userErrors"][0]["message"])
    return data["article"]


def update_article(article_id, **fields):
    article = _article_payload(**fields)
    if not article:
        return None
    q = """mutation($id: ID!, $article: ArticleUpdateInput!) {
      articleUpdate(id: $id, article: $article) {
        article { id handle isPublished } userErrors { field message } } }"""
    data = admin_graphql(q, {"id": article_id, "article": article})["articleUpdate"]
    if data["userErrors"]:
        raise ShopifyError(data["userErrors"][0]["message"])
    return data["article"]


# --------------------------------------------------------------- theme access
def _admin_rest(method, path, body=None):
    """Minimal Admin REST call (used for the Asset API, which has no GraphQL
    equivalent for reading/writing settings_data.json)."""
    shop, token = credentials()
    if not configured():
        raise ShopifyError("Shopify isn't connected yet.")
    url = f"https://{shop}/admin/api/{API_VERSION}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if e.code == 401:
            raise ShopifyError("Shopify rejected the token (401).")
        if e.code == 403:
            raise ShopifyError("Access denied — token may lack theme access (read_themes/write_themes).")
        raise ShopifyError(f"Shopify API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ShopifyError(f"Couldn't reach Shopify: {e.reason}")


def theme_main():
    """The live (published) theme. Reliably filters by role=MAIN."""
    nodes = admin_graphql("{ themes(first:1, roles:[MAIN]){ nodes { id name role } } }")["themes"]["nodes"]
    if not nodes:
        raise ShopifyError("no live theme found")
    n = nodes[0]
    return {"gid": n["id"], "id": n["id"].split("/")[-1], "name": n["name"], "role": n["role"]}


def theme_status():
    """Probe theme access. {available, theme, count} on success, or
    {available:False, reason} when the token lacks read_themes."""
    try:
        nodes = admin_graphql("{ themes(first:20){ nodes { role } } }")["themes"]["nodes"]
        main = theme_main()
    except ShopifyError as e:
        return {"available": False, "reason": str(e)}
    return {"available": True, "theme": {"name": main["name"], "role": main["role"].lower()},
            "count": len(nodes)}


SETTINGS_KEY = "config/settings_data.json"


def get_settings_data(theme_id=None):
    """Returns (theme, raw_json_string, data_dict) for the theme's settings."""
    theme = theme_main() if theme_id is None else {"id": theme_id, "name": "", "gid": ""}
    raw = _admin_rest("GET", f"themes/{theme['id']}/assets.json?asset%5Bkey%5D="
                      + urllib.parse.quote(SETTINGS_KEY)).get("asset", {}).get("value", "")
    if not raw:
        raise ShopifyError("couldn't read the theme's settings")
    return theme, raw, json.loads(raw)


def put_settings_data(theme_id, value_string):
    return _admin_rest("PUT", f"themes/{theme_id}/assets.json",
                       {"asset": {"key": SETTINGS_KEY, "value": value_string}})


_SKIP_KEYS = ("sections", "blocks", "content_for_index")


def flatten_theme_settings(current):
    """Editable leaf settings as a flat dict: scalar settings by name, and each
    colour-scheme colour as color_schemes.<scheme>.settings.<key>."""
    out = {}
    for k, v in current.items():
        if k in _SKIP_KEYS:
            continue
        if k == "color_schemes" and isinstance(v, dict):
            for scheme, sd in v.items():
                for ck, cv in (sd.get("settings", {}) or {}).items():
                    if not isinstance(cv, (dict, list)):
                        out[f"color_schemes.{scheme}.settings.{ck}"] = cv
        elif not isinstance(v, (dict, list)):
            out[k] = v
    return out


_COERCE_FAILED = object()


def _coerce(old, val):
    """Coerce a new value to match the existing value's type, or
    _COERCE_FAILED if it can't be — e.g. the AI proposed a non-numeric string
    for an int setting. Previously returned `old` unchanged on failure, which
    made a failed coercion indistinguishable from "no change" to the caller:
    the key silently vanished from both applied and skipped."""
    if isinstance(old, bool):
        return val if isinstance(val, bool) else str(val).strip().lower() in ("true", "1", "yes", "on")
    if isinstance(old, int) and not isinstance(old, bool):
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return _COERCE_FAILED
    if isinstance(old, float):
        try:
            return float(val)
        except (TypeError, ValueError):
            return _COERCE_FAILED
    return str(val)


def apply_theme_patch(current, patch):
    """Return (new_current, applied, skipped). Only pre-existing leaf keys are
    honoured, type-coerced to match. applied = {key:{old,new}}."""
    cur = copy.deepcopy(current)
    applied, skipped = {}, []
    for key, val in (patch or {}).items():
        parts = key.split(".")
        if len(parts) == 4 and parts[0] == "color_schemes" and parts[2] == "settings":
            node = cur.get("color_schemes", {}).get(parts[1], {}).get("settings", {})
            leaf = parts[3]
            if leaf in node and not isinstance(node[leaf], (dict, list)):
                new = _coerce(node[leaf], val)
                if new is _COERCE_FAILED:
                    skipped.append(key)
                elif new != node[leaf]:
                    applied[key] = {"old": node[leaf], "new": new}
                    node[leaf] = new
            else:
                skipped.append(key)
        elif key in cur and not isinstance(cur[key], (dict, list)):
            new = _coerce(cur[key], val)
            if new is _COERCE_FAILED:
                skipped.append(key)
            elif new != cur[key]:
                applied[key] = {"old": cur[key], "new": new}
                cur[key] = new
        else:
            skipped.append(key)
    return cur, applied, skipped


# --------------------------------------------------------------- store pages
def list_pages(first=100):
    """Online-store pages (About, Contact, policies…). Needs read_online_store_pages."""
    q = """query($first: Int!) {
      pages(first: $first, sortKey: UPDATED_AT, reverse: true) {
        nodes { id title handle updatedAt isPublished }
      }
    }"""
    nodes = admin_graphql(q, {"first": first})["pages"]["nodes"]
    return [{"id": n["id"], "title": n["title"], "handle": n["handle"],
             "updated_at": n.get("updatedAt"), "published": n.get("isPublished")}
            for n in nodes]


def get_page(page_id):
    q = """query($id: ID!) {
      page(id: $id) { id title handle body isPublished }
    }"""
    p = admin_graphql(q, {"id": page_id})["page"]
    if not p:
        raise ShopifyError("page not found")
    return {"id": p["id"], "title": p["title"], "handle": p["handle"],
            "body": p.get("body") or "", "published": p.get("isPublished")}


def update_page(page_id, title=None, body=None):
    """Update a page's title and/or body (HTML). Needs write_online_store_pages."""
    page = {}
    if title is not None:
        page["title"] = str(title)
    if body is not None:
        page["body"] = str(body)
    if not page:
        return None
    q = """mutation($id: ID!, $page: PageUpdateInput!) {
      pageUpdate(id: $id, page: $page) {
        page { id } userErrors { field message }
      }
    }"""
    data = admin_graphql(q, {"id": page_id, "page": page})
    errs = data["pageUpdate"]["userErrors"]
    if errs:
        raise ShopifyError(errs[0]["message"])
    return data["pageUpdate"]["page"]


def create_product(title, description="", product_type="", tags=None,
                   status="DRAFT", vendor="", price=None, image_urls=None):
    """Create a new product. Optionally attach images from public URLs (Drop
    share /raw links work) and set the default variant's price. Returns
    {id, handle, status, variant_id, admin_url}."""
    title = (title or "").strip()
    if not title:
        raise ShopifyError("a product needs a title")
    status = status if status in ("ACTIVE", "DRAFT", "ARCHIVED") else "DRAFT"
    inp = {"title": title, "status": status}
    if description:
        inp["descriptionHtml"] = str(description)
    if product_type:
        inp["productType"] = str(product_type).strip()
    if vendor:
        inp["vendor"] = str(vendor).strip()
    if tags:
        inp["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    media = [{"originalSource": str(u).strip(), "mediaContentType": "IMAGE"}
             for u in (image_urls or [])
             if str(u).strip().startswith(("http://", "https://"))]
    q = """mutation($input: ProductInput!, $media: [CreateMediaInput!]) {
      productCreate(input: $input, media: $media) {
        product { id handle status variants(first: 1) { nodes { id } } }
        userErrors { field message }
      }
    }"""
    data = admin_graphql(q, {"input": inp, "media": media})
    res = data["productCreate"]
    if res["userErrors"]:
        raise ShopifyError(res["userErrors"][0]["message"])
    p = res["product"]
    variant_id = (p.get("variants", {}).get("nodes") or [{}])[0].get("id")
    price_warning = None
    if price not in (None, "") and variant_id:
        try:
            set_price(p["id"], variant_id, f"{float(price):.2f}")
        except (ValueError, ShopifyError) as e:
            # The product itself is already created (and may be ACTIVE) —
            # that can't be undone here without a second failure mode, so
            # this surfaces as a warning on an otherwise-successful create
            # rather than vanishing. Silent before: a product could go live
            # at an unset/zero price with a plain success response.
            price_warning = f"Product created, but the price didn't save: {e}"
    shop, _ = credentials()
    numeric = p["id"].split("/")[-1]
    out = {"id": p["id"], "handle": p.get("handle"),
           "status": p.get("status"), "variant_id": variant_id,
           "admin_url": f"https://{shop}/admin/products/{numeric}"}
    if price_warning:
        out["price_warning"] = price_warning
    return out


if __name__ == "__main__":
    print("configured:", configured())
    if configured():
        r = list_products(first=3)
        for p in r["products"]:
            print(f"  {p['status']:8} ₹{p['price']:>9}  {p['title']}")
    else:
        shop, _ = credentials()
        print(f"shop={shop or '(unset)'} — add SHOPIFY_ADMIN_TOKEN to {ENV_PATH}")
