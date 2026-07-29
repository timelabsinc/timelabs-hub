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
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

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

    # What the bank took. Invoices are billed in USD but paid from an Indian
    # account, so the effective rate carries a forex markup + transfer fee on top
    # of the market rate — that gap is real money and was previously invisible.
    fx = q(conn, "SELECT SUM(COALESCE(total,0)), AVG(COALESCE(exchange_rate,0)) "
                 "FROM invoices WHERE UPPER(COALESCE(currency,'USD'))='USD'")[0]
    usd_total, eff_rate = fx[0] or 0, fx[1] or 0
    MARKET_REF = 86.0                       # rough average across the invoice window
    bank_cost = usd_total * (eff_rate - MARKET_REF) if eff_rate else 0
    bank_pct = (bank_cost / (usd_total * MARKET_REF) * 100) if usd_total and eff_rate else 0

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
            ("Total paid", inr(spend), f"{n_inv} invoices · ${usd_total:,.0f} invoiced"),
            ("Cost of banking", inr(bank_cost),
             f"forex + transfer, ~{bank_pct:.0f}% on top" if bank_cost else "rate not set"),
            ("Freight", inr(freight), "international shipping"),
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
        if cost and price:
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
<meta name="color-scheme" content="light dark"><title>Ledger — Labs OS</title>
<style>{HUB_STYLE}</style></head>
<body>
<div class="wrap">
  {hub_header("ledger")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Ledger</h1>
      <p class="page-sub">What sourcing actually costs — suppliers, landed parts, and planned margins.</p>
      <div class="head-controls">
        <button class="btn" id="impBtn">Scan Drop for new invoices</button>
        <span class="refreshed num">refreshed {generated}</span>
      </div>
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
    {hub_footer()}
  </main>
</div>
<script>
  document.getElementById('impBtn').addEventListener('click', async function(){{
    var b = this; b.disabled = true; b.textContent = 'Scanning…';
    try {{
      var res = await fetch('/ops/agent/api/ledger/import', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}});
      var d = await res.json();
      if(!res.ok) throw new Error(d.error || 'Scan failed');
      var ok = (d.results||[]).filter(function(r){{return !r.error;}});
      if(!ok.length){{ alert('No new invoices in Drop — Ledger is up to date.'); b.disabled=false; b.textContent='Scan Drop for new invoices'; return; }}
      var lines = ok.map(function(r){{return '• ' + r.file + ' — ' + r.date + ' · ' + r.items + ' items · $' + r.total_usd;}}).join('\\n');
      if(confirm('Found ' + ok.length + ' new invoice(s):\\n\\n' + lines + '\\n\\nImport into Ledger? (Totals are best-effort — check against the invoice.)')){{
        b.textContent = 'Importing…';
        var committed = await fetch('/ops/agent/api/ledger/import', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{\"commit\":true}}'}});
        var saved = await committed.json().catch(function(){{return {{}};}});
        if(!committed.ok) throw new Error(saved.error || 'Import failed');
        var failed = (saved.results||[]).filter(function(r){{return r.error;}});
        if(failed.length) throw new Error(failed.map(function(r){{return r.file+': '+r.error;}}).join('\\n'));
        setTimeout(function(){{ location.reload(); }}, 1200);
      }} else {{ b.disabled=false; b.textContent='Scan Drop for new invoices'; }}
    }} catch(e){{ alert(e.message || 'Scan failed — try again.'); b.disabled=false; b.textContent='Scan Drop for new invoices'; }}
  }});
</script>
<script>{WHOAMI_JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT} ({n_inv} invoices, {len(suppliers)} suppliers)")


if __name__ == "__main__":
    build()
