#!/usr/bin/env python3
"""Shopify order sync — reconciles storefront orders into hermes.db.

Runs every 5 minutes off ops-order-sync.timer (and once by hand for the
initial backfill). Each Shopify order becomes one row in `orders`, so it
gets the same auto-incrementing id as a WhatsApp or order-form order — one
number, one sequence, whichever door the order came through — plus one
`order_items` row per line item, so per-product analytics stay accurate even
when an order has more than one watch in it.

Idempotent by design: orders.shopify_order_id is UNIQUE and every fetched
Shopify order replaces its analytical line-item snapshot transactionally.
The watermark follows ``updatedAt`` (with a deliberate overlap), so refunds,
returns and order edits are reconciled instead of being frozen at checkout.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/root/ops-dashboard")
import shopify_api
import customers as customers_mod
import order_numbers

DB = "/root/ops-dashboard/data/hermes.db"
WATERMARK = "/root/ops-dashboard/.shopify-order-sync.json"
SYNC_LOCK = "/root/ops-dashboard/data/.shopify-order-sync.lock"
ORDER_PHOTOS = "/root/ops-dashboard/data/order-photos"
PAGE_SIZE = 50
MAX_IMAGE_BYTES = 12 * 1024 * 1024
DEFAULT_SINCE = "2020-01-01T00:00:00Z"
WATERMARK_OVERLAP_SECONDS = 5 * 60


def hub_event(kind, detail, actor="shopify-sync", app="orders"):
    """Best-effort activity entry without importing the agent server.

    Importing agent_chat_server performs schema migrations and job recovery,
    neither of which belongs in a timer-driven order sync.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute(
            "INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
            (app, kind, actor, str(detail)[:500]),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()


ORDERS_QUERY = """
query($n: Int!, $after: String, $q: String) {
  orders(first: $n, after: $after, sortKey: UPDATED_AT, query: $q) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt updatedAt cancelledAt displayFinancialStatus email phone test
      currentTotalPriceSet { shopMoney { amount } }
      netPaymentSet { shopMoney { amount } }
      customer { displayName phone email }
      shippingAddress { name address1 address2 city province zip phone }
      lineItems(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes {
          title quantity currentQuantity image { url altText }
          priceAfterAllDiscountsBeforeTaxesSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

LINE_ITEMS_QUERY = """
query($id: ID!, $n: Int!, $after: String) {
  order(id: $id) {
    lineItems(first: $n, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        title quantity currentQuantity image { url altText }
        priceAfterAllDiscountsBeforeTaxesSet { shopMoney { amount } }
      }
    }
  }
}
"""


def _watermark():
    try:
        # Deliberately do not reuse the old creation-time `since` key. The
        # first run of this reconciler must revisit accessible existing orders
        # once so historical refunds and edits stop remaining frozen locally.
        return json.load(open(WATERMARK)).get("updated_since", DEFAULT_SINCE)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SINCE


def _save_watermark(since):
    tmp = WATERMARK + f".{os.getpid()}-{secrets.token_hex(4)}.tmp"
    with open(tmp, "x") as f:
        json.dump({"updated_since": since, "synced_at": time.time()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, WATERMARK)


def _query_watermark(since):
    """Overlap the cursor so equal timestamps and a crash at the edge are safe."""
    try:
        stamp = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
        stamp -= datetime.timedelta(seconds=WATERMARK_OVERLAP_SECONDS)
        return stamp.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return DEFAULT_SINCE


def _money(record, field):
    try:
        return max(0.0, float(
            (record.get(field) or {}).get("shopMoney", {}).get("amount") or 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _line_snapshot(order):
    """Current sellable lines, excluding quantities Shopify removed/refunded."""
    out = []
    for item in (order.get("lineItems") or {}).get("nodes") or []:
        try:
            quantity = max(0, int(item.get("currentQuantity") or 0))
        except (TypeError, ValueError):
            quantity = 0
        total = _money(item, "priceAfterAllDiscountsBeforeTaxesSet")
        unit = total / quantity if quantity else 0.0
        out.append({
            "product": str(item.get("title") or "—").strip() or "—",
            "quantity": quantity,
            "price_inr": unit,
            "line_total": total,
        })
    return out


def _complete_line_items(order):
    """Page nested line items so unusually large orders cannot be truncated."""
    connection = order.get("lineItems") or {}
    nodes = list(connection.get("nodes") or [])
    page = connection.get("pageInfo") or {}
    after = page.get("endCursor")
    while page.get("hasNextPage"):
        data = shopify_api.admin_graphql(
            LINE_ITEMS_QUERY, {"id": order["id"], "n": 100, "after": after})
        connection = (data.get("order") or {}).get("lineItems") or {}
        nodes.extend(connection.get("nodes") or [])
        page = connection.get("pageInfo") or {}
        after = page.get("endCursor")
    order["lineItems"] = {"nodes": nodes, "pageInfo": page}
    return order


def _order_amount(order):
    """Net order value: current total normally, actual net payment after refunds."""
    status = str(order.get("displayFinancialStatus") or "").strip().lower()
    if status in ("partially_refunded", "refunded"):
        return _money(order, "netPaymentSet")
    return _money(order, "currentTotalPriceSet")


def _summary(lines):
    live = [line for line in lines if line["quantity"] > 0] or lines
    product = live[0]["product"] if live else "—"
    if len(live) > 1:
        product += f" +{len(live) - 1} more"
    quantity = sum(line["quantity"] for line in lines)
    return product, quantity


def _address(o):
    a = o.get("shippingAddress") or {}
    parts = [a.get("address1"), a.get("address2")]
    return ", ".join(p for p in parts if p)


def _contact_values(order):
    """Normalized remote contact snapshot plus a non-reversible revision key."""
    customer = order.get("customer") or {}
    address = order.get("shippingAddress") or {}

    def text(value):
        return str(value or "").strip()

    raw_name = text(customer.get("displayName") or address.get("name"))
    values = {
        "customer_name": raw_name or "Website customer",
        "customer_phone": text(
            order.get("phone") or address.get("phone") or customer.get("phone")),
        "customer_email": text(order.get("email") or customer.get("email")),
        "address": _address(order),
        "pincode": text(address.get("zip")),
        "city": text(address.get("city")),
        "state": text(address.get("province")),
    }
    fingerprint = hashlib.sha256(
        json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return values, fingerprint


def _contact_matches(row, values):
    return all(str(row[key] or "").strip() == values[key] for key in values)


def _stable_customer(row):
    """Anonymous storefront rows are orders, not one shared fake customer."""
    return bool(
        customers_mod.normalize_phone(row.get("customer_phone"))
        or str(row.get("customer_email") or "").strip()
        or str(row.get("customer_name") or "").strip().lower()
        not in ("", "website customer")
    )


def _ensure_sync_schema(conn):
    """Keep the timer safe even if it runs before the agent service restarts."""
    conn.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
    for name, declaration in (
            ("shopify_contact_fingerprint", "TEXT"),
            ("shopify_contact_override", "INTEGER DEFAULT 0"),
            ("shopify_conflict_fingerprint", "TEXT"),
            ("shopify_build_override", "INTEGER DEFAULT 0"),
            ("photo_override", "INTEGER DEFAULT 0"),
            ("sale_total_paise", "INTEGER"),
            ("local_hidden", "INTEGER DEFAULT 0")):
        if name not in columns:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {declaration}")
    conn.commit()


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

    A staff/supplier edit sets ``photo_override`` and is always authoritative,
    including an intentionally empty list.  Downloads use unique staged names;
    after the network work finishes, BEGIN IMMEDIATE + a fresh row read prevents
    a concurrent manual upload from being overwritten by the sync.
    """
    if not urls:
        return []
    row = conn.execute(
        "SELECT local_photos, COALESCE(photo_override,0) FROM orders WHERE id=?",
        (order_id,)).fetchone()

    def current_photos(value):
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return [name for name in parsed if isinstance(name, str)] \
            if isinstance(parsed, list) else []

    current = current_photos(row[0]) if row else []
    if not row or row[1] or current:
        return current

    dest = os.path.join(ORDER_PHOTOS, str(order_id))
    os.makedirs(dest, exist_ok=True)
    staged = []
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
            token = secrets.token_hex(16)
            name = f"shopify-{token}{ext}"
            stage = os.path.join(dest, f".shopify-stage-{token}{ext}")
            with open(stage, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            staged.append((stage, name))
        except Exception as e:
            print(f"[shopify-sync] image for order {order_id} skipped: {e}", flush=True)
    if not staged:
        try:
            os.rmdir(dest)
        except OSError:
            pass
        return []

    published = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT local_photos, COALESCE(photo_override,0) FROM orders WHERE id=?",
            (order_id,)).fetchone()
        current = current_photos(row[0]) if row else []
        if not row or row[1] or current:
            conn.rollback()
            return current
        for stage, name in staged:
            final = os.path.join(dest, name)
            os.replace(stage, final)
            published.append(final)
        dir_fd = os.open(dest, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        parent_fd = os.open(
            ORDER_PHOTOS, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        stored = [name for _stage, name in staged]
        conn.execute(
            "UPDATE orders SET has_image=1, local_photos=? "
            "WHERE id=? AND COALESCE(photo_override,0)=0",
            (json.dumps(stored), order_id))
        conn.commit()
        return stored
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        for final in published:
            try:
                os.remove(final)
            except OSError:
                pass
        raise
    finally:
        for stage, _name in staged:
            try:
                os.remove(stage)
            except FileNotFoundError:
                pass


def _fetch_updates(since):
    """Every order updated near `since`, oldest first, one page at a time."""
    after, out = None, []
    q = f"updated_at:>'{_query_watermark(since)}'"
    while True:
        data = shopify_api.admin_graphql(ORDERS_QUERY, {"n": PAGE_SIZE, "after": after, "q": q})
        conn = data["orders"]
        out.extend(_complete_line_items(order) for order in conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return out


def backfill_images(actor="shopify-image-backfill"):
    """Attach images to already-logged Shopify orders without changing orders."""
    try:
        remote = _fetch_updates(DEFAULT_SINCE)
    except Exception as e:
        return {"updated": 0, "missing": 0, "failed": 0, "error": str(e)}
    by_id = {o.get("id"): o for o in remote if o.get("id")}
    conn = sqlite3.connect(DB, timeout=5)
    _ensure_sync_schema(conn)
    rows = conn.execute(
        "SELECT id, shopify_order_id FROM orders "
        "WHERE shopify_order_id IS NOT NULL "
        "AND COALESCE(photo_override,0)=0 "
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


def _same_number(a, b):
    try:
        return round(float(a or 0), 4) == round(float(b or 0), 4)
    except (TypeError, ValueError):
        return False


def _replace_items(conn, order_id, lines):
    """Replace one Shopify order's analytical lines, retaining manual aliases."""
    aliases = {}
    for row in conn.execute(
            "SELECT product, canonical_product FROM order_items WHERE order_id=?",
            (order_id,)):
        if row["canonical_product"]:
            aliases.setdefault(row["product"], row["canonical_product"])
    conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    for line in lines:
        conn.execute(
            "INSERT INTO order_items "
            "(order_id, product, canonical_product, quantity, price_inr, line_total) "
            "VALUES (?,?,?,?,?,?)",
            (order_id, line["product"], aliases.get(line["product"]),
             line["quantity"], line["price_inr"], line["line_total"]))


def _items_changed(conn, order_id, lines):
    current = conn.execute(
        "SELECT product, quantity, price_inr, line_total FROM order_items "
        "WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    if len(current) != len(lines):
        return True
    for row, line in zip(current, lines):
        if (row["product"] != line["product"]
                or int(row["quantity"] or 0) != line["quantity"]
                or not _same_number(row["price_inr"], line["price_inr"])
                or not _same_number(row["line_total"], line["line_total"])):
            return True
    return False


def _build_lines_changed(conn, order_id, lines):
    """Only product/quantity drift matters to a supplier already building it."""
    current = [
        (row["product"], int(row["quantity"] or 0))
        for row in conn.execute(
            "SELECT product, quantity FROM order_items "
            "WHERE order_id=? ORDER BY id", (order_id,))
    ]
    remote = [(line["product"], line["quantity"]) for line in lines]
    return current != remote


def _sheet_row(order_no, row, amount):
    import google_api
    # Staff may record a final selling amount alongside receipts. Shopify's
    # unit/financial data keeps syncing, but the order Sheet must preserve the
    # deliberate local total instead of restoring the remote amount.
    if "sale_total_paise" in row.keys() and row["sale_total_paise"] is not None:
        amount = int(row["sale_total_paise"]) / 100.0
    notes = str(row["notes"] or "").strip()
    shopify_ref = str(row["shopify_name"] or "").strip()
    if shopify_ref and shopify_ref not in notes:
        notes = (notes + " · " if notes else "") + f"Shopify {shopify_ref}"
    return google_api.row_for(google_api.SHEET_HEADERS, {
        "Order #": order_no,
        "Logged": str(row["received_at"] or "")[:16],
        "Status": str(row["status"] or "pending").replace("_", " ").title(),
        "Source": row["source"] or "website",
        "Customer": row["customer_name"] or "",
        "Phone": row["customer_phone"] or "",
        "Email": row["customer_email"] or "",
        "Address": row["address"] or "",
        "City": row["city"] or "",
        "State": row["state"] or "",
        "Pincode": row["pincode"] or "",
        "Product": row["product"] or "",
        "Case style": row["case_style"] or "",
        "Dial colour": row["dial_colour"] or "",
        "Dial style": row["dial_style"] or "",
        "Case colour": row["case_colour"] or "",
        "Movement": row["movement"] or "",
        "Size": row["watch_size"] or "",
        "Qty": row["quantity"] or 1,
        "Price (INR)": row["price_inr"] or 0,
        "Line total (INR)": amount,
        "Notes": notes,
    })


def _sync_locked(actor):
    if not shopify_api.configured():
        return {"synced": 0, "skipped": 0, "error": "Shopify isn't connected"}
    since = _watermark()
    try:
        orders = _fetch_updates(since)
    except Exception as e:
        return {"synced": 0, "skipped": 0, "error": str(e)}
    if not orders:
        return {"synced": 0, "created": 0, "updated": 0, "skipped": 0}

    try:
        import google_api
        access = google_api.access_token()
    except Exception as e:
        access = None
        print(f"[shopify-sync] Google mirror unavailable: {e}", flush=True)

    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    _ensure_sync_schema(conn)
    created = updated = skipped = failed = 0
    conflicts = []
    contact_reviews = []
    mirror_orders = []
    mirror_customers = {}
    newest = since

    for o in orders:
        newest = max(newest, o.get("updatedAt") or newest)
        if o.get("test"):
            # Shopify's own sandbox/bogus-gateway flag — a real order that
            # merely has "test" in its title (someone checking checkout
            # works) is NOT this, and stays in; only orders Shopify itself
            # marks as test transactions are dropped here.
            skipped += 1
            continue
        try:
            lines = _line_snapshot(o)
            contact, contact_fingerprint = _contact_values(o)
            product, current_qty = _summary(lines)
            amount = _order_amount(o)
            fin_status = (o.get("displayFinancialStatus") or "").lower()
            outcome = "unchanged"
            conflict_order_no = None
            contact_review_order_no = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM orders WHERE shopify_order_id=?",
                    (o.get("id"),)).fetchone()
                if not existing:
                    order_no = order_numbers.next_number(conn)
                    parent_qty = max(1, current_qty)
                    status = "cancelled" if o.get("cancelledAt") else "pending"
                    cur = conn.execute(
                        "INSERT INTO orders (received_at, customer_name, customer_phone, "
                        "customer_email, address, pincode, city, state, source, product, "
                        "price_inr, quantity, status, order_no, shopify_order_id, "
                        "shopify_name, financial_status, supplier_visible, "
                        "shopify_contact_fingerprint, shopify_contact_override) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,0)",
                        ((o.get("createdAt") or "")[:19].replace("T", " "),
                         contact["customer_name"], contact["customer_phone"],
                         contact["customer_email"], contact["address"],
                         contact["pincode"], contact["city"], contact["state"],
                         "website", product, amount / parent_qty, parent_qty, status,
                         order_no, o["id"], o.get("name") or "", fin_status,
                         contact_fingerprint))
                    oid = cur.lastrowid
                    _replace_items(conn, oid, lines)
                    outcome = "created"
                else:
                    import order_stages
                    oid = existing["id"]
                    order_no = existing["order_no"] or oid
                    existing_dict = dict(existing)
                    old_customer_key = (
                        customers_mod.key_for(
                            existing["customer_phone"], existing["customer_email"],
                            existing["customer_name"])
                        if _stable_customer(existing_dict) else None
                    )
                    committed = order_stages.is_committed(
                        existing["status"], existing["supplier_visible"],
                        existing["shipment_id"], existing["bill_id"])
                    build_override = bool(existing["shopify_build_override"] or 0)
                    parent_qty = max(1, int(existing["quantity"] or 1))
                    parent_product = existing["product"] or "—"
                    if not committed and not build_override:
                        parent_qty = max(1, current_qty)
                        parent_product = product
                    next_status = existing["status"]
                    if (o.get("cancelledAt") and not committed
                            and (existing["status"] or "pending") == "pending"):
                        next_status = "cancelled"
                    unit_price = amount / parent_qty
                    item_changed = _items_changed(conn, oid, lines)
                    build_lines_changed = _build_lines_changed(conn, oid, lines)
                    financial_changed = (
                        str(existing["financial_status"] or "") != fin_status)
                    amount_changed = not _same_number(
                        float(existing["price_inr"] or 0) * parent_qty, amount)
                    material_supplier_change = committed and (
                        build_lines_changed
                        or bool(o.get("cancelledAt"))
                        or financial_changed
                        or amount_changed
                    )
                    conflict_payload = {
                        "cancelled": bool(o.get("cancelledAt")),
                        "financial_status": fin_status,
                        "amount": round(amount, 4),
                        "lines": [
                            (line["product"], line["quantity"]) for line in lines
                        ],
                    }
                    conflict_fingerprint = hashlib.sha256(
                        json.dumps(
                            conflict_payload, sort_keys=True,
                            ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    stored_conflict_fingerprint = str(
                        existing["shopify_conflict_fingerprint"] or "")
                    supplier_conflict = (
                        material_supplier_change
                        and stored_conflict_fingerprint != conflict_fingerprint)
                    next_conflict_fingerprint = (
                        conflict_fingerprint
                        if material_supplier_change
                        else stored_conflict_fingerprint or None)
                    contact_matches = _contact_matches(existing, contact)
                    stored_fingerprint = (
                        existing["shopify_contact_fingerprint"] or "")
                    contact_override = bool(
                        existing["shopify_contact_override"] or 0)
                    local_contact_meaningful = bool(
                        customers_mod.normalize_phone(existing["customer_phone"])
                        or str(existing["customer_email"] or "").strip()
                        or str(existing["customer_name"] or "").strip().lower()
                        not in ("", "website customer")
                        or any(str(existing[key] or "").strip() for key in (
                            "address", "pincode", "city", "state")))
                    contact_review = False
                    if not stored_fingerprint and not contact_matches:
                        # First run after this migration cannot know whether a
                        # difference is a stale import or a staff correction.
                        # Preserve meaningful local data once and mark it as an
                        # override rather than silently replacing shipping PII.
                        contact_override = local_contact_meaningful
                        contact_review = contact_override
                    elif contact_override and contact_matches:
                        # Updating Shopify to match the Labs correction is the
                        # explicit resolution path; normal syncing resumes.
                        contact_override = False
                    elif (contact_override
                          and stored_fingerprint != contact_fingerprint
                          and not contact_matches):
                        contact_review = True
                    apply_remote_contact = (
                        not contact_override
                        and stored_fingerprint != contact_fingerprint)
                    effective_contact = (
                        contact if apply_remote_contact else {
                            key: str(existing[key] or "").strip()
                            for key in contact
                        })
                    changed = (
                        str(existing["financial_status"] or "") != fin_status
                        or not _same_number(existing["price_inr"], unit_price)
                        or existing["shopify_name"] != (o.get("name") or "")
                        or existing["status"] != next_status
                        or existing["product"] != parent_product
                        or int(existing["quantity"] or 1) != parent_qty
                        or any(
                            str(existing[key] or "").strip()
                            != effective_contact[key]
                            for key in contact)
                        or stored_fingerprint != contact_fingerprint
                        or bool(existing["shopify_contact_override"] or 0)
                        != contact_override
                        or str(existing["shopify_conflict_fingerprint"] or "")
                        != str(next_conflict_fingerprint or "")
                        or item_changed
                    )
                    if changed:
                        conn.execute(
                            "UPDATE orders SET financial_status=?, price_inr=?, "
                            "shopify_name=?, status=?, product=?, quantity=?, "
                            "customer_name=?, customer_phone=?, customer_email=?, "
                            "address=?, pincode=?, city=?, state=?, "
                            "shopify_contact_fingerprint=?, "
                            "shopify_contact_override=?, "
                            "shopify_conflict_fingerprint=? WHERE id=?",
                            (fin_status, unit_price, o.get("name") or "", next_status,
                             parent_product, parent_qty,
                             effective_contact["customer_name"],
                             effective_contact["customer_phone"],
                             effective_contact["customer_email"],
                             effective_contact["address"],
                             effective_contact["pincode"],
                             effective_contact["city"],
                             effective_contact["state"],
                             contact_fingerprint, 1 if contact_override else 0,
                             next_conflict_fingerprint, oid))
                        _replace_items(conn, oid, lines)
                        conn.execute(
                            "INSERT INTO order_events (order_id, kind, detail, actor) "
                            "VALUES (?,?,?,?)",
                            (oid, "shopify_sync",
                             "Shopify financials or line items reconciled", actor))
                        if supplier_conflict:
                            conn.execute(
                                "INSERT INTO order_events "
                                "(order_id, kind, detail, actor) VALUES (?,?,?,?)",
                                (oid, "shopify_conflict",
                                 "Shopify changed after supplier commitment; staff review needed",
                                 actor))
                            conflict_order_no = order_no
                        if contact_review:
                            conn.execute(
                                "INSERT INTO order_events "
                                "(order_id, kind, detail, actor) VALUES (?,?,?,?)",
                                (oid, "shopify_contact_review",
                                 "Shopify contact changed while a Labs correction "
                                 "is active; local shipping details were preserved",
                                 actor))
                            contact_review_order_no = order_no
                        outcome = "updated"
                conn.commit()
                if outcome == "created":
                    created += 1
                elif outcome == "updated":
                    updated += 1
                    if conflict_order_no is not None:
                        conflicts.append(conflict_order_no)
                    if contact_review_order_no is not None:
                        contact_reviews.append(contact_review_order_no)
                else:
                    skipped += 1
            except sqlite3.IntegrityError as e:
                conn.rollback()
                if "UNIQUE constraint failed: orders.shopify_order_id" in str(e):
                    skipped += 1
                    continue
                raise
            except Exception:
                conn.rollback()
                raise

            try:
                _store_images(conn, oid, _image_urls(o))
            except Exception as e:
                conn.rollback()
                print(f"[shopify-sync] order {o.get('name', '?')} image skipped: {e}",
                      flush=True)

            local = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            cust_row = None
            try:
                if outcome == "updated" and old_customer_key:
                    customers_mod.recount(conn, old_customer_key)
                local_dict = dict(local)
                if _stable_customer(local_dict):
                    cust_row = customers_mod.upsert_from_order(conn, local_dict)
            except Exception as e:
                conn.rollback()
                print(f"[shopify-sync] customer roll-up skipped for {o.get('name')}: {e}",
                      flush=True)

            mirror_orders.append(
                (order_no, _sheet_row(order_no, local, amount)))
            if cust_row:
                customer_sheet_row = customers_mod.sheet_row(cust_row)
                mirror_customers[str(customer_sheet_row[0])] = customer_sheet_row
        except Exception as e:
            # One malformed order must never wedge every order after it.
            failed += 1
            print(f"[shopify-sync] order {o.get('name', '?')} failed: {e}", flush=True)

    conn.close()
    sheet_failed = False
    if mirror_orders:
        import google_api as gapi
        with gapi.sheet_mutation_lock():
            try:
                if not access:
                    raise gapi.GoogleError("Google Sheets is not connected")
                mirrored = gapi.batch_upsert_mirror(
                    access, mirror_orders, customers_mod.HEADERS,
                    list(mirror_customers.values()))
                recovered = gapi.record_mirror_status(True)
                if recovered:
                    hub_event(
                        "sheet_mirror_recovered",
                        "Google Sheets order mirror caught up", actor, "orders")
                print(
                    f"[shopify-sync] sheet mirror: {mirrored['orders']} order(s), "
                    f"{mirrored['customers']} customer(s)", flush=True)
            except Exception as e:
                sheet_failed = True
                changed = gapi.record_mirror_status(
                    False, str(e), len(mirror_orders))
                if changed:
                    hub_event(
                        "sheet_mirror_lag",
                        f"Google Sheets order mirror is behind "
                        f"({len(mirror_orders)} row(s) pending)", actor, "orders")
                print(f"[shopify-sync] sheet mirror deferred: {e}", flush=True)

    # A failed order or mirror batch keeps the old watermark. The next run
    # safely refetches successful rows, overwrites their explicit Sheet rows,
    # and retries the gap without creating duplicates.
    if newest != since and not failed and not sheet_failed:
        _save_watermark(newest)

    synced = created + updated
    for order_no in conflicts:
        hub_event(
            "shopify_conflict",
            f"Order #{order_no} changed in Shopify after supplier commitment; review needed",
            actor, "orders")
    for order_no in contact_reviews:
        hub_event(
            "shopify_contact_review",
            f"Order #{order_no} has a Shopify contact change while a Labs "
            "correction is active; review shipping details",
            actor, "orders")
    if synced:
        try:
            hconn = sqlite3.connect(DB, timeout=5)
            detail = f"{created} storefront order(s) created, {updated} reconciled"
            if skipped:
                detail += f", {skipped} unchanged/test"
            if failed:
                detail += f", {failed} failed"
            if sheet_failed:
                detail += ", Sheet mirror pending"
            hconn.execute("INSERT INTO hub_events (app, kind, actor, detail) VALUES (?,?,?,?)",
                         ("orders", "shopify_order_sync", actor, detail))
            hconn.commit()
            hconn.close()
        except Exception:
            pass
    return {"synced": synced, "created": created, "updated": updated,
            "skipped": skipped, "failed": failed, "conflicts": len(conflicts),
            "sheet_mirror": "pending" if sheet_failed else "ok"}


def sync(actor="shopify-sync"):
    """Run one reconciler at a time across timer and manual invocations."""
    os.makedirs(os.path.dirname(SYNC_LOCK), exist_ok=True)
    with open(SYNC_LOCK, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"synced": 0, "created": 0, "updated": 0, "skipped": 0,
                    "error": "another Shopify sync is already running"}
        return _sync_locked(actor)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backfill-images", action="store_true",
        help="attach Shopify product images to existing local storefront orders")
    args = parser.parse_args()
    print(json.dumps(backfill_images() if args.backfill_images else sync(), indent=2))
