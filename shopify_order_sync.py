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
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
import shopify_api
import customers as customers_mod

DB = "/root/ops-dashboard/data/hermes.db"
WATERMARK = "/root/ops-dashboard/.shopify-order-sync.json"
PAGE_SIZE = 50

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
        nodes { title quantity discountedUnitPriceSet { shopMoney { amount } } }
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
                cur = conn.execute(
                    "INSERT INTO orders (received_at, customer_name, customer_phone, "
                    "customer_email, address, pincode, city, state, source, product, "
                    "price_inr, quantity, status, shopify_order_id, shopify_name, "
                    "financial_status, supplier_visible) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    ((o.get("createdAt") or "")[:19].replace("T", " "),
                     name, phone, email, _address(o), addr.get("zip") or "",
                     addr.get("city") or "", addr.get("province") or "",
                     "website", product, unit_price, qty, "new",
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
    print(json.dumps(sync(), indent=2))
