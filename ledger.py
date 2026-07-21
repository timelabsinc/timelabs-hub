#!/usr/bin/env python3
"""Ledger — the CFO view. Renders /var/www/ops/ledger.html from suppliers.db.

Surfaces what was captured invoice-by-invoice but shown nowhere: total spend,
per-supplier history, landed part costs, and planned-SKU margins.
Regenerated alongside Face (generate.py calls build()); served by the existing
auth-gated /ops location — no nginx change needed.
"""
import html
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav

DB = "/root/ops-dashboard/data/suppliers.db"
OUT = "/var/www/ops/ledger.html"


def q(conn, sql):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error as e:
        print(f"[ledger] {e}", file=sys.stderr)
        return []


def inr(n):
    return f"₹{(n or 0):,.0f}"


def build():
    conn = sqlite3.connect(DB)
    totals = q(conn, """SELECT COUNT(*), SUM(COALESCE(total_inr,0)),
                        SUM(COALESCE(freight_inr,0)), SUM(COALESCE(customs_inr,0))
                        FROM invoices""")[0]
    n_inv, spend, freight, customs = totals[0] or 0, totals[1] or 0, totals[2] or 0, totals[3] or 0
    landed = spend + freight + customs

    suppliers = q(conn, "SELECT name, invoices, first_order, last_order, total_paid_inr "
                        "FROM v_supplier_summary WHERE invoices > 0 ORDER BY total_paid_inr DESC")
    parts = q(conn, "SELECT part_category, model_compat, total_qty, avg_unit_inr, total_spend_inr "
                    "FROM v_part_costs WHERE part_category IS NOT NULL "
                    "ORDER BY total_spend_inr DESC LIMIT 20")
    skus = q(conn, "SELECT name, category, price_inr, cost_inr, status FROM planned_skus "
                   "WHERE price_inr IS NOT NULL ORDER BY launch_phase, price_inr DESC")
    conn.close()

    kpis = "".join(
        f'<div class="kpi"><div class="kpi-top"><span class="label">{k}</span></div>'
        f'<div class="val num">{v}</div><div class="ctx">{c}</div></div>'
        for k, v, c in [
            ("Total paid", inr(spend), f"{n_inv} invoices"),
            ("Freight", inr(freight), "international shipping"),
            ("Customs", inr(customs), "BCD + SWS + IGST"),
            ("Landed total", inr(landed), "everything, in INR"),
        ])

    sup_rows = "".join(
        f'<tr><td>{html.escape(s[0])}</td><td class="n num">{s[1]}</td>'
        f'<td class="num">{html.escape(s[2] or "—")}</td><td class="num">{html.escape(s[3] or "—")}</td>'
        f'<td class="n num">{inr(s[4])}</td></tr>' for s in suppliers)

    part_rows = "".join(
        f'<tr><td>{html.escape((p[0] or "—").title())}</td><td>{html.escape(p[1] or "—")}</td>'
        f'<td class="n num">{p[2] or 0:.0f}</td><td class="n num">{inr(p[3])}</td>'
        f'<td class="n num">{inr(p[4])}</td></tr>' for p in parts)

    sku_rows = ""
    for name, cat, price, cost, status in skus:
        if cost:
            margin = 100 * (price - cost) / price
            mcls = "good" if margin >= 55 else ("warn" if margin >= 35 else "crit")
            mtxt = f'<span class="pill {mcls}">{margin:.0f}%</span>'
        else:
            mtxt = '<span class="pill">no cost yet</span>'
        sku_rows += (f'<tr><td>{html.escape(name)}</td><td>{html.escape(cat or "—")}</td>'
                     f'<td class="n num">{inr(price)}</td><td class="n num">{inr(cost) if cost else "—"}</td>'
                     f'<td class="n">{mtxt}</td><td>{html.escape(status or "—")}</td></tr>')

    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Ledger — Timelabs Hub</title>
<style>{HUB_STYLE}</style></head>
<body>
<div class="wrap">
  <header class="topbar">
    <a class="brand" href="/ops/#overview"><span class="brand-dot">T</span>
    <span class="brand-name">Timelabs <span>Ledger</span></span></a>
  </header>
  {_appnav(active="ledger", drop_ready=True)}
  <main>
    <div class="page-head">
      <h1 class="page-title">Ledger</h1>
      <p class="page-sub">What sourcing actually costs — suppliers, landed parts, and planned margins.</p>
      <span class="refreshed num">refreshed {generated}</span>
    </div>
    <section><div class="kpis">{kpis}</div></section>
    <section><h2>Planned SKU margins</h2><div class="panel tscroll"><table>
      <thead><tr><th>SKU</th><th>Category</th><th class="n">Price</th><th class="n">Cost</th>
      <th class="n">Margin</th><th>Status</th></tr></thead>
      <tbody>{sku_rows or '<tr><td colspan="6" class="empty">No planned SKUs priced yet.</td></tr>'}</tbody>
    </table></div></section>
    <section><h2>Cost per part</h2><div class="panel tscroll"><table>
      <thead><tr><th>Part</th><th>Fits</th><th class="n">Qty</th><th class="n">Avg unit</th>
      <th class="n">Total spend</th></tr></thead>
      <tbody>{part_rows or '<tr><td colspan="5" class="empty">No invoice lines yet.</td></tr>'}</tbody>
    </table></div></section>
    <section><h2>Suppliers</h2><div class="panel tscroll"><table>
      <thead><tr><th>Supplier</th><th class="n">Invoices</th><th>First</th><th>Last</th>
      <th class="n">Total paid</th></tr></thead>
      <tbody>{sup_rows or '<tr><td colspan="5" class="empty">No suppliers yet.</td></tr>'}</tbody>
    </table></div></section>
    <footer><span>Timelabs Hub · Ledger</span><span>from suppliers.db — invoices in Drop land here once parsed</span></footer>
  </main>
</div>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT} ({n_inv} invoices, {len(suppliers)} suppliers)")


if __name__ == "__main__":
    build()
