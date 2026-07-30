#!/usr/bin/env python3
"""Durable allocation of staff-facing order numbers.

Order rows may be hard-deleted only for explicit test/mistake/draft records.
Using ``MAX(order_no)+1`` then reuses the deleted highest number and collides
with its historical Sheet row.  This one-row sequence never moves backwards.
Callers must already hold (or immediately start) a SQLite write transaction.
"""


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS order_number_seq (
        id INTEGER PRIMARY KEY CHECK (id=1),
        last_value INTEGER NOT NULL
    )""")
    current = conn.execute(
        "SELECT COALESCE(MAX(order_no),0) FROM orders").fetchone()[0]
    conn.execute(
        "INSERT INTO order_number_seq (id,last_value) VALUES (1,?) "
        "ON CONFLICT(id) DO UPDATE SET last_value="
        "CASE WHEN excluded.last_value > order_number_seq.last_value "
        "THEN excluded.last_value ELSE order_number_seq.last_value END",
        (int(current or 0),),
    )


def next_number(conn):
    ensure(conn)
    conn.execute(
        "UPDATE order_number_seq SET last_value=last_value+1 WHERE id=1")
    row = conn.execute(
        "SELECT last_value FROM order_number_seq WHERE id=1").fetchone()
    return int(row[0])
