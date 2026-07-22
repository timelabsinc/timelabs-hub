#!/usr/bin/env python3
"""Customer roll-up derived from orders — the Shopify-style customer list.

Every saved order upserts a customer, keyed on phone (falling back to email,
then name) since that's what's reliably present in a pasted block. Counts,
spend and dates are recomputed from the orders themselves rather than
incremented, so a deleted or edited order can't drift the totals.

Tags are half automatic, half yours: "repeat" and "VIP" are recomputed on every
write, anything you type by hand is preserved alongside them.
"""
import sqlite3

DB = "/root/ops-dashboard/data/hermes.db"

VIP_SPEND = 50000.0   # ₹ lifetime — above this they're a VIP
VIP_ORDERS = 5        # …or this many orders, whichever lands first

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ckey TEXT NOT NULL UNIQUE,
    name TEXT, email TEXT, phone TEXT,
    address TEXT, pincode TEXT, city TEXT, state TEXT,
    orders_count INTEGER DEFAULT 0,
    total_spent REAL DEFAULT 0,
    avg_order_value REAL DEFAULT 0,
    first_order_at TEXT, last_order_at TEXT,
    first_source TEXT,
    auto_tags TEXT, manual_tags TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


def ensure_schema(conn):
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone)")


def key_for(phone, email, name):
    """Phone first — it's the one field a pasted order almost always carries."""
    digits = "".join(c for c in (phone or "") if c.isdigit())[-10:]
    if len(digits) == 10:
        return "p:" + digits
    if (email or "").strip():
        return "e:" + email.strip().lower()
    return "n:" + (name or "").strip().lower()


def _tags(orders_count, total_spent):
    t = []
    if orders_count > 1:
        t.append("repeat")
    if total_spent >= VIP_SPEND or orders_count >= VIP_ORDERS:
        t.append("VIP")
    return ",".join(t)


def upsert_from_order(conn, order):
    """order: dict with customer_name/phone/email/address/pincode/city/state/
    price_inr/quantity/status/source. Returns the customer row as a dict."""
    ensure_schema(conn)
    ckey = key_for(order.get("customer_phone"), order.get("customer_email"),
                   order.get("customer_name"))

    row = conn.execute("SELECT * FROM customers WHERE ckey=?", (ckey,)).fetchone()

    # Recompute from the orders table so totals can't drift out of step.
    digits = "".join(c for c in (order.get("customer_phone") or "") if c.isdigit())[-10:]
    if len(digits) == 10:
        where, params = "REPLACE(REPLACE(customer_phone,' ',''),'-','') LIKE ?", ("%" + digits,)
    elif (order.get("customer_email") or "").strip():
        where, params = "LOWER(customer_email)=?", (order["customer_email"].strip().lower(),)
    else:
        where, params = "LOWER(customer_name)=?", ((order.get("customer_name") or "").strip().lower(),)

    agg = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(COALESCE(price_inr,0)*COALESCE(quantity,1)),0) spent, "
        f"MIN(received_at) first_at, MAX(received_at) last_at FROM orders "
        f"WHERE status != 'cancelled' AND {where}", params).fetchone()

    n = agg["n"] or 0
    spent = float(agg["spent"] or 0)
    aov = round(spent / n, 2) if n else 0.0
    auto = _tags(n, spent)

    if row:
        conn.execute(
            "UPDATE customers SET name=COALESCE(NULLIF(?,''),name), "
            "email=COALESCE(NULLIF(?,''),email), phone=COALESCE(NULLIF(?,''),phone), "
            "address=COALESCE(NULLIF(?,''),address), pincode=COALESCE(NULLIF(?,''),pincode), "
            "city=COALESCE(NULLIF(?,''),city), state=COALESCE(NULLIF(?,''),state), "
            "orders_count=?, total_spent=?, avg_order_value=?, "
            "first_order_at=?, last_order_at=?, auto_tags=?, updated_at=datetime('now') "
            "WHERE ckey=?",
            (order.get("customer_name") or "", order.get("customer_email") or "",
             order.get("customer_phone") or "", order.get("address") or "",
             order.get("pincode") or "", order.get("city") or "", order.get("state") or "",
             n, spent, aov, agg["first_at"], agg["last_at"], auto, ckey))
    else:
        conn.execute(
            "INSERT INTO customers (ckey, name, email, phone, address, pincode, city, state, "
            "orders_count, total_spent, avg_order_value, first_order_at, last_order_at, "
            "first_source, auto_tags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ckey, order.get("customer_name"), order.get("customer_email"),
             order.get("customer_phone"), order.get("address"), order.get("pincode"),
             order.get("city"), order.get("state"), n, spent, aov,
             agg["first_at"], agg["last_at"], order.get("source"), auto))
    conn.commit()
    return dict(conn.execute("SELECT * FROM customers WHERE ckey=?", (ckey,)).fetchone())


def all_tags(row):
    parts = [t.strip() for t in ((row.get("auto_tags") or "") + "," +
                                 (row.get("manual_tags") or "")).split(",")]
    return ", ".join([t for t in parts if t])


def sheet_row(c):
    """Row shape for the Customers tab — column A is the id, the join key."""
    return [c["id"], c.get("name") or "", c.get("phone") or "", c.get("email") or "",
            c.get("address") or "", c.get("city") or "", c.get("state") or "",
            c.get("pincode") or "", c.get("orders_count") or 0,
            c.get("total_spent") or 0, c.get("avg_order_value") or 0,
            (c.get("first_order_at") or "")[:16], (c.get("last_order_at") or "")[:16],
            c.get("first_source") or "", all_tags(c)]


HEADERS = ["Customer ID", "Name", "Phone", "Email", "Address", "City", "State",
           "Pincode", "Orders", "Total spent", "Avg order value", "First order",
           "Last order", "First source", "Tags"]
