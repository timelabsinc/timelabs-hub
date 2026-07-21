#!/usr/bin/env python3
"""Ledger auto-fill — parse Shenzhen Chengda supplier invoices from Drop into
suppliers.db. Idempotent (imports only files not already present by name),
best-effort for this one supplier's template, and never touches existing rows.

Usage:
  python3 invoice_import.py            # dry run — print what it would import
  python3 invoice_import.py --commit   # actually insert new invoices
"""
import glob
import os
import re
import sqlite3
import sys

DB = "/root/ops-dashboard/data/suppliers.db"
INBOX = "/srv/timelabs-drop/Supplier Invoices"
SUPPLIER_NAME = "Shenzhen Chengdaxin Technology Co.,Ltd"


def _num(v):
    """Best-effort number from a cell: 45, '68*2+7', '2126+26', '105.5'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("＋", "+").replace("×", "*").replace(",", "")
    if re.fullmatch(r"[0-9.+\-*() ]+", s) and any(ch.isdigit() for ch in s):
        try:
            return float(eval(s, {"__builtins__": {}}, {}))  # arithmetic only
        except Exception:
            return None
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def parse_invoice(path):
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True).active
    grid = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}

    # date — the cell next to a "DATE" label
    order_date = None
    for coord, val in grid.items():
        if isinstance(val, str) and val.strip().upper() == "DATE":
            for col in "BCDEFGH":
                cand = grid.get(col + coord[1:])
                if cand is not None:
                    order_date = str(cand)[:10]
                    break
    if order_date and not re.match(r"\d{4}-\d\d-\d\d", order_date):
        order_date = None
    if not order_date:  # fall back to filename YYYYMMDD
        m = re.search(r"(\d{4})(\d\d)(\d\d)", os.path.basename(path))
        order_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

    # header row: find the row containing "Qty" and "Unit price"
    hdr_row = None
    for coord, val in grid.items():
        if isinstance(val, str) and val.strip().lower() == "qty":
            hdr_row = int(re.search(r"\d+", coord).group())
            break
    items, explicit_total, shipping = [], None, 0.0
    if hdr_row:
        max_row = ws.max_row
        for r in range(hdr_row + 1, max_row + 1):
            label = " ".join(str(grid.get(c + str(r), "")) for c in "AB").lower()
            hval = _num(grid.get("H" + str(r)))
            if "in total" in label or label.strip() == "total":
                explicit_total = hval
                continue
            if "shipping" in label:
                shipping += hval or 0
                continue
            if "product cost" in label:
                continue
            qty = _num(grid.get("F" + str(r)))
            price = _num(grid.get("G" + str(r)))
            name = (str(grid.get("A" + str(r)) or grid.get("D" + str(r)) or "")).split("\n")[0].strip()
            if qty and (price is not None or hval is not None) and name:
                line_total = hval if hval is not None else round(qty * price, 2)
                items.append({"name": name[:120], "qty": qty, "unit_price": price,
                              "line_total": line_total})

    line_sum = round(sum(i["line_total"] or 0 for i in items), 2)
    total = explicit_total if explicit_total is not None else round(line_sum + shipping, 2)
    return {"order_date": order_date, "currency": "USD", "items": items,
            "line_sum": line_sum, "shipping": shipping, "total": total,
            "source_file": os.path.basename(path)}


def import_new(commit=False):
    conn = sqlite3.connect(DB)
    have = {r[0] for r in conn.execute("SELECT source_file FROM invoices")}
    sup = conn.execute("SELECT id FROM suppliers WHERE name=?", (SUPPLIER_NAME,)).fetchone()
    supplier_id = sup[0] if sup else conn.execute(
        "INSERT INTO suppliers (name, country, default_currency) VALUES (?,?,?)",
        (SUPPLIER_NAME, "China", "USD")).lastrowid

    results = []
    for path in sorted(glob.glob(os.path.join(INBOX, "*.xlsx"))):
        fn = os.path.basename(path)
        if fn in have:
            continue
        try:
            inv = parse_invoice(path)
        except Exception as e:
            results.append({"file": fn, "error": str(e)})
            continue
        results.append({"file": fn, "date": inv["order_date"], "items": len(inv["items"]),
                        "total_usd": inv["total"]})
        if commit and inv["order_date"] and inv["items"]:
            cur = conn.execute(
                "INSERT INTO invoices (supplier_id, order_date, currency, subtotal, "
                "total, source_file, parsed_by) VALUES (?,?,?,?,?,?,?)",
                (supplier_id, inv["order_date"], "USD", inv["line_sum"], inv["total"],
                 fn, "invoice_import.py"))
            inv_id = cur.lastrowid
            for it in inv["items"]:
                conn.execute(
                    "INSERT INTO invoice_items (invoice_id, description_en, quantity, "
                    "unit_price, line_total) VALUES (?,?,?,?,?)",
                    (inv_id, it["name"], it["qty"], it["unit_price"], it["line_total"]))
    if commit:
        conn.commit()
    conn.close()
    return results


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    res = import_new(commit=commit)
    if "--json" in sys.argv:
        import json
        print(json.dumps({"committed": commit, "results": res}))
    else:
        print(("COMMITTED" if commit else "DRY RUN") + f" — {len(res)} new file(s):")
        for r in res:
            print("  ", r)
