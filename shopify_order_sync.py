#!/usr/bin/env python3
"""Shopify order sync — brings storefront orders into hermes.db.

Runs every 5 minutes off ops-order-sync.timer (and once by hand for the
initial backfill). Each Shopify order becomes one row in `orders`, so it
gets the same auto-incrementing id as a WhatsApp or order-form order — one
number, one sequence, whichever door the order came through — plus one
`order_items` row per line item, so per-product analytics stay accurate even
when an order has more than one watch in it.

Idempotent by design: orders.shopify_order_id is UNIQUE, so re-running (or
two overlapping runs) can only ever skip a duplicate, never insert one. The
watermark file just keeps each run's Shopify query small; it is not what
protects against duplicates, so it's safe to delete and re-backfill from
scratch at any time.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/root/ops-dashboard")
import shopify_api
import customers as customers_mod

DB = "/root/ops-dashboard/data/hermes.db"
WATERMARK = "/root/ops-dashboard/.shopify-order-sync.json"
ORDER_PHOTOS = "/root/ops-dashboard/data/order-photos"
PAGE_SIZE = 50
MAX_IMAGE_BYTES = 12 * 1024 * 1024

ORDERS_QUERY = """
query($n: Int!, $after: String, $q: String) {
  orders(first: $n, after: $after, sortKey: CREATED_AT, query: $q) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt displayFinancialStatus email phone test
      totalPriceSet { shopMoney { amount } }
      customer { displayName phone email }
      shippingAddress { address1 address2 city province zip phone }
      lineItems(first: 20) {
        nodes {
          title quantity image { url altText }
          discountedUnitPriceSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""


def _watermark():
    try:
        return json.load(open(WATERMARK)).get("since", "2020-01-01T00:00:00Z")
    except (OSError, json.JSONDecodeError):
        return "2020-01-01T00:00:00Z"


def _save_watermark(since):
    tmp = WATERMARK + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"since": since, "synced_at": time.time()}, f)
    os.replace(tmp, WATERMARK)


def _address(o):
    a = o.get("shippingAddress") or {}
    parts = [a.get("address1"), a.get("address2")]
    return ", ".join(p for p in parts if p)


def _image_urls(order):
    """One stable reference image per distinct Shopify line-item image."""
    out = []
    for item in (order.get("lineItems") or {}).get("nodes") or []:
        url = str((item.get("image") or {}).get("url") or "").strip()
        if url and url not in out:
            out.append(url)
    return out[:10]


def _image_extension(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _shopify_image_url(url):
    """Keep this downloader from becoming an SSRF path through Shopify data."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "cdn.shopify.com"
        or host.endswith(".shopifycdn.com")
        or host == "shopifycdn.net"
        or host.endswith(".shopifycdn.net")
    )


def _store_images(conn, order_id, urls):
    """Download Shopify CDN images into the same protected store as form photos.

    Files are written atomically and the database is updated only after at
    least one complete, recognizable image is present.
    """
    if not urls:
        return []
    row = conn.execute(
        "SELECT local_photos FROM orders WHERE id=?", (order_id,)).fetchone()
    if row and row[0] not in (None, "", "[]"):
        try:
            current = json.loads(row[0])
            if current:
                return current
        except (TypeError, json.JSONDecodeError):
            pass

    dest = os.path.join(ORDER_PHOTOS, str(order_id))
    os.makedirs(dest, exist_ok=True)
    stored = []
    for url in urls:
        if not _shopify_image_url(url):
            print(f"[shopify-sync] refused non-Shopify image URL for order {order_id}",
                  flush=True)
            continue
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Labs-OS-Shopify-Order-Sync/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                declared = int(resp.headers.get("Content-Length") or 0)
                if declared > MAX_IMAGE_BYTES:
                    raise ValueError("image exceeds size limit")
                data = resp.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("image exceeds size limit")
            ext = _image_extension(data)
            if not ext:
                raise ValueError("response is not a supported image")
            name = f"{len(stored) + 1}{ext}"
            final = os.path.join(dest, name)
            tmp = final + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, final)
            stored.append(name)
        except Exception as e:
            print(f"[shopify-sync] image for order {order_id} skipped: {e}", flush=True)
    if stored:
        conn.execute(
            "UPDATE orders SET has_image=1, local_photos=? WHERE id=?",
            (json.dumps(stored), order_id))
        conn.commit()
    else:
        try:
            os.rmdir(dest)
        except OSError:
            pass
    return stored


def _fetch_new(since):
    """Every order created after `since`, oldest first, one page at a time —
    ascending order matters: it's what makes the local id sequence a true
    arrival-order number across every channel, not just within Shopify."""
    after, out = None, []
    q = f"created_at:>'{since}'"
    while True:
        data = shopify_api.admin_graphql(ORDERS_QUERY, {"n": PAGE_SIZE, "after": after, "q": q})
        conn = data["orders"]
        out.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return out


def backfill_images(actor="shopify-image-backfill"):
    """Attach images to already-logged Shopify orders without changing orders."""
    try:
        remote = _fetch_new("2020-01-01T00:00:00Z")
    except Exception as e:
        return {"updated": 0, "missing": 0, "failed": 0, "error": str(e)}
    by_id = {o.get("id"): o for o in remote if o.get("id")}
    conn = sqlite3.connect(DB, timeout=5)
    rows = conn.execute(
        "SELECT id, shopify_order_id FROM orders "
        "WHERE shopify_order_id IS NOT NULL "
        "AND COALESCE(NULLIF(local_photos,''),'[]')='[]' "
        "ORDER BY id").fetchall()
    updated = missing = failed = 0
    for oid, shopify_id in rows:
        order = by_id.get(shopify_id)
        if not order:
            missing += 1
            continue
        try:
            if _store_images(conn, oid, _image_urls(order)):
                updated += 1
            else:
                missing += 1
        except Exception as e:
            failed += 1
            print(f"[shopify-sync] image backfill for order {oid} failed: {e}", flush=True)
    conn.close()
    if updated:
        try:
            hconn = sqlite3.connect(DB, timeout=5)
            hconn.execute(
                "INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
                ("orders", "shopify_image_backfill", actor,
                 f"Added product photos to {updated} storefront order(s)"))
            hconn.commit()
            hconn.close()
        except Exception:
            pass
    return {"updated": updated, "missing": missing, "failed": failed}


def sync(actor="shopify-sync"):
    if not shopify_api.configured():
        return {"synced": 0, "skipped": 0, "error": "Shopify isn't connected"}
    since = _watermark()
    try:
        orders = _fetch_new(since)
    except Exception as e:
        return {"synced": 0, "skipped": 0, "error": str(e)}
    if not orders:
        return {"synced": 0, "skipped": 0}

    import google_api
    access = google_api.access_token()

    conn = sqlite3.connect(DB, timeout=5)
    conn.row_factory = sqlite3.Row
    synced, skipped, failed, newest = 0, 0, 0, since

    for o in orders:
        newest = max(newest, o.get("createdAt") or newest)
        if o.get("test"):
            # Shopify's own sandbox/bogus-gateway flag — a real order that
            # merely has "test" in its title (someone checking checkout
            # works) is NOT this, and stays in; only orders Shopify itself
            # marks as test transactions are dropped here.
            skipped += 1
            continue
        try:
            li = (o.get("lineItems") or {}).get("nodes") or []
            cust = o.get("customer") or {}
            addr = o.get("shippingAddress") or {}
            name = cust.get("displayName") or "Website customer"
            phone = o.get("phone") or addr.get("phone") or cust.get("phone") or ""
            email = o.get("email") or cust.get("email") or ""
            product = li[0]["title"] if li else "—"
            if len(li) > 1:
                product += f" +{len(li) - 1} more"
            qty = sum(int(x.get("quantity") or 1) for x in li) or 1
            try:
                amount = float((o.get("totalPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            fin_status = (o.get("displayFinancialStatus") or "").lower()

            # orders.price_inr means PER-UNIT everywhere else in this codebase
            # (the order form has one Price field next to Qty, and every
            # revenue/spend calculation multiplies the two) — so the order's
            # total must be divided back down before it lands in that column.
            # The full total still isn't lost: it's `amount` in the sheet's
            # Line total column and the true sum behind every order_items row.
            unit_price = amount / qty if qty else amount
            try:
                order_no = conn.execute(
                    "SELECT COALESCE(MAX(order_no), 0) + 1 AS n FROM orders").fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO orders (received_at, customer_name, customer_phone, "
                    "customer_email, address, pincode, city, state, source, product, "
                    "price_inr, quantity, status, order_no, shopify_order_id, shopify_name, "
                    "financial_status, supplier_visible) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    ((o.get("createdAt") or "")[:19].replace("T", " "),
                     name, phone, email, _address(o), addr.get("zip") or "",
                     addr.get("city") or "", addr.get("province") or "",
                     "website", product, unit_price, qty, "pending", order_no,
                     o["id"], o.get("name") or "", fin_status))
            except sqlite3.IntegrityError:
                skipped += 1
                continue
            oid = cur.lastrowid

            for item in li:
                try:
                    unit = float((item.get("discountedUnitPriceSet") or {})
                                 .get("shopMoney", {}).get("amount") or 0)
                except (TypeError, ValueError):
                    unit = 0.0
                iqty = int(item.get("quantity") or 1)
                conn.execute(
                    "INSERT INTO order_items (order_id, product, quantity, price_inr, "
                    "line_total) VALUES (?,?,?,?,?)",
                    (oid, item.get("title") or "—", iqty, unit, unit * iqty))
            conn.commit()
            try:
                _store_images(conn, oid, _image_urls(o))
            except Exception as e:
                # The order is the record of sale and must survive even if its
                # optional reference image is temporarily unavailable.
                print(f"[shopify-sync] order {o.get('name', '?')} image skipped: {e}",
                      flush=True)
            synced += 1

            cust_row = None
            try:
                cust_row = customers_mod.upsert_from_order(conn, {
                    "customer_name": name, "customer_phone": phone,
                    "customer_email": email, "address": _address(o),
                    "pincode": addr.get("zip") or "", "city": addr.get("city") or "",
                    "state": addr.get("province") or "", "source": "website"})
            except Exception as e:
                print(f"[shopify-sync] customer roll-up skipped for {o.get('name')}: {e}",
                      flush=True)

            if access:
                try:
                    import google_api as gapi
                    gapi.append_order_row(access, gapi.row_for(gapi.SHEET_HEADERS, {
                        "Order #": oid,
                        "Logged": (o.get("createdAt") or "")[:16].replace("T", " "),
                        "Status": "new", "Source": "website", "Customer": name,
                        "Phone": phone, "Email": email, "Address": _address(o),
                        "City": addr.get("city") or "", "State": addr.get("province") or "",
                        "Pincode": addr.get("zip") or "", "Product": product, "Qty": qty,
                        "Price (INR)": unit_price, "Line total (INR)": amount,
                        "Notes": f"Shopify {o.get('name', '')}"}))
                    if cust_row:
                        gapi.upsert_customer_row(access, customers_mod.HEADERS,
                                                 customers_mod.sheet_row(cust_row))
                except Exception as e:
                    print(f"[shopify-sync] sheet mirror skipped for {o.get('name')}: {e}",
                          flush=True)
        except Exception as e:
            # One malformed order must never wedge every order after it.
            failed += 1
            print(f"[shopify-sync] order {o.get('name', '?')} failed: {e}", flush=True)

    conn.close()
    if newest != since:
        _save_watermark(newest)

    if synced:
        try:
            hconn = sqlite3.connect(DB, timeout=5)
            detail = f"{synced} storefront order(s) synced in"
            if skipped:
                detail += f", {skipped} already had a local row"
            if failed:
                detail += f", {failed} failed"
            hconn.execute("INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
                         ("orders", "shopify_order_sync", actor, detail))
            hconn.commit()
            hconn.close()
        except Exception:
            pass
    return {"synced": synced, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backfill-images", action="store_true",
        help="attach Shopify product images to existing local storefront orders")
    args = parser.parse_args()
    print(json.dumps(backfill_images() if args.backfill_images else sync(), indent=2))
