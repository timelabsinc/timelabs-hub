#!/usr/bin/env python3
"""Rebuild the Orders + Customers sheet tabs from hermes.db.

The DB is the source of truth, so the mirror must be reproducible from it. Use
this after backfilling columns, after deleting rows, or any time the sheet and
the database have drifted apart.

  python3 orders_resync.py --backfill   fill city/state/attributes on old rows
  python3 orders_resync.py --sheets     rewrite both tabs from the DB
  python3 orders_resync.py --all        both, in that order

Only ever writes the sheet from the DB — never the other way round.
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/root/ops-dashboard")
import customers as customers_mod
import google_api
import india_places
import order_stages
import order_taxonomy

DB = "/root/ops-dashboard/data/hermes.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def backfill():
    """Rows saved before the attribute/geo columns existed get them filled in."""
    conn = db()
    rows = conn.execute("SELECT * FROM orders").fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        upd, vals = [], []
        if d.get("address") and not (d.get("city") and d.get("state")):
            city, state = india_places.parse(d["address"], d.get("pincode"))
            if city and not d.get("city"):
                upd.append("city=?"); vals.append(city)
            if state and not d.get("state"):
                upd.append("state=?"); vals.append(state)
        if not any(d.get(f) for f in order_taxonomy.FIELDS):
            attrs = order_taxonomy.extract((d.get("product") or "") + " " + (d.get("notes") or ""))
            for k, v in attrs.items():
                upd.append(f"{k}=?"); vals.append(v)
        if upd:
            vals.append(d["id"])
            conn.execute(f"UPDATE orders SET {', '.join(upd)} WHERE id=?", vals)
            n += 1
    conn.commit()

    # rebuild every customer from the orders that remain
    customers_mod.ensure_schema(conn)
    conn.execute("DELETE FROM customers")
    conn.commit()
    for r in conn.execute("SELECT * FROM orders ORDER BY id").fetchall():
        d = dict(r)
        customers_mod.upsert_from_order(conn, {
            "customer_name": d.get("customer_name"), "customer_phone": d.get("customer_phone"),
            "customer_email": d.get("customer_email"), "address": d.get("address"),
            "pincode": d.get("pincode"), "city": d.get("city"), "state": d.get("state"),
            "source": d.get("source")})
    ncust = conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    conn.close()
    print(f"backfilled {n} order(s); rebuilt {ncust} customer(s)")


def _order_row(d):
    links = []
    try:
        links = json.loads(d["photo_links"]) if d.get("photo_links") else (
            [d["drive_link"]] if d.get("drive_link") else [])
    except (ValueError, TypeError):
        links = [d["drive_link"]] if d.get("drive_link") else []
    qty = d.get("quantity") or 1
    price = d.get("price_inr")
    sale_total = d.get("sale_total_paise")
    line_total = (sale_total / 100.0 if sale_total is not None else
                  (None if price is None else price * qty))
    return google_api.row_for(google_api.SHEET_HEADERS, {
        "Order #": d.get("order_no") or d["id"],
        "Logged": (d.get("received_at") or "")[:16],
        "Status": order_stages.label(d.get("status")),
        "Source": d.get("source") or "",
        "Customer": d.get("customer_name") or "",
        "Phone": d.get("customer_phone") or "",
        "Email": d.get("customer_email") or "",
        "Address": d.get("address") or "",
        "City": d.get("city") or "",
        "State": d.get("state") or "",
        "Pincode": d.get("pincode") or "",
        "Product": d.get("product") or "",
        "Case style": d.get("case_style") or "",
        "Dial colour": d.get("dial_colour") or "",
        "Dial style": d.get("dial_style") or "",
        "Case colour": d.get("case_colour") or "",
        "Movement": d.get("movement") or "",
        "Size": d.get("watch_size") or "",
        "Qty": qty,
        "Price (INR)": "" if price is None else price,
        "Line total (INR)": "" if line_total is None else line_total,
        "Notes": d.get("notes") or "",
        "Photos": "\n".join(links),
    })


def _replace_tab(access, sid, tab, headers, rows):
    google_api._call(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
        + google_api.urllib.parse.quote(f"{tab}!A1:ZZ100000") + ":clear",
        access, "POST", {})
    google_api._call(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
        + google_api.urllib.parse.quote(f"{tab}!A1") + "?valueInputOption=RAW",
        access, "PUT", {
            "values": [headers] + [google_api._clean(r) for r in rows]})


def push_sheets():
    access = google_api.access_token()
    if not access:
        print("Google isn't connected — nothing pushed")
        return False
    conn = db()
    # A full rebuild must not resurrect a locally-hidden website order in the
    # mirror — its tombstone write already told the sheet it's gone.
    orders = [_order_row(dict(r)) for r in
              conn.execute("SELECT * FROM orders WHERE COALESCE(local_hidden,0)=0 "
                           "ORDER BY id").fetchall()]
    customers_mod.ensure_schema(conn)
    custs = [customers_mod.sheet_row(dict(r)) for r in
             conn.execute("SELECT * FROM customers ORDER BY id").fetchall()]
    conn.close()

    with google_api.sheet_mutation_lock():
        try:
            sid = google_api.ensure_order_sheet(access)
            google_api.ensure_tab(
                access, sid, google_api.ORDERS_TAB, google_api.SHEET_HEADERS,
                rebuilding=True)
            google_api.ensure_tab(
                access, sid, google_api.CUSTOMERS_TAB, customers_mod.HEADERS,
                rebuilding=True)
            for tab, headers, rows in (
                    (google_api.ORDERS_TAB, google_api.SHEET_HEADERS, orders),
                    (google_api.CUSTOMERS_TAB, customers_mod.HEADERS, custs)):
                _replace_tab(access, sid, tab, headers, rows)
                print(f"{tab}: {len(rows)} row(s)")
            # Headers now match the new layout, so these calls are reshape
            # no-ops plus current filter/freeze/number formatting.
            google_api.migrate_orders_tab(access)
            google_api.migrate_customers_tab(access)
            google_api.record_mirror_status(True)
        except Exception as exc:
            google_api.record_mirror_status(
                False, str(exc), len(orders))
            raise
    print(google_api.sheet_url())
    return True


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or "--all" in args:
        backfill(); push_sheets()
    else:
        if "--backfill" in args:
            backfill()
        if "--sheets" in args:
            push_sheets()
