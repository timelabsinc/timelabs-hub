#!/usr/bin/env python3
"""Put INR against the supplier invoices — and separate what the bank took.

Every invoice is billed in USD but paid from an Indian account, so the number
that matters is the *effective* rate: market rate + the bank's forex markup +
the wire/transfer fee. The owner recalls that landing near Rs 100/USD, well
above the market rate over the same period — that gap is the real cost of
paying suppliers, and it was invisible because total_inr was never filled in.

Uploaded invoices are treated as already paid (that's the owner's rule).

  python3 fx_apply.py                 apply the default effective rate
  python3 fx_apply.py --rate 102      use a different effective rate
  python3 fx_apply.py --market 86     reference market rate (for the bank-cost split)
  python3 fx_apply.py --dry-run       show what would change, write nothing

The rate is stored per invoice, so any individual one can be corrected later
with the exact figure actually debited without redoing the rest.
"""
import argparse
import sqlite3

DB = "/root/ops-dashboard/data/suppliers.db"

# Effective INR per USD actually paid — includes the bank's forex markup and the
# outward-transfer fee, not just the market rate.
DEFAULT_EFFECTIVE = 100.0
# Rough market average across the invoice window (2025-06 .. 2026-06), used only
# to express how much of the spend was banking cost rather than goods.
DEFAULT_MARKET = 86.0


def apply_fx(rate=DEFAULT_EFFECTIVE, market=DEFAULT_MARKET, dry=False):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    invs = conn.execute(
        "SELECT id, invoice_no, order_date, currency, total FROM invoices ORDER BY order_date"
    ).fetchall()
    usd_total = sum(float(i["total"] or 0) for i in invs if (i["currency"] or "USD").upper() == "USD")

    if not dry:
        for i in invs:
            if (i["currency"] or "USD").upper() != "USD":
                continue          # leave non-USD alone rather than guess
            conn.execute(
                "UPDATE invoices SET exchange_rate=?, total_inr=ROUND(total*?,2), "
                "payment_status='paid' WHERE id=?", (rate, rate, i["id"]))
        # line items inherit the same rate as their invoice
        conn.execute(
            "UPDATE invoice_items SET unit_price_inr = ROUND(unit_price * "
            "(SELECT COALESCE(exchange_rate,?) FROM invoices WHERE invoices.id = invoice_items.invoice_id), 2) "
            "WHERE unit_price IS NOT NULL", (rate,))
        conn.commit()

    landed = usd_total * rate
    goods = usd_total * market
    bank = landed - goods
    out = {
        "invoices": len(invs), "usd": round(usd_total, 2),
        "effective_rate": rate, "market_rate": market,
        "landed_inr": round(landed, 2), "goods_at_market_inr": round(goods, 2),
        "bank_cost_inr": round(bank, 2),
        "bank_pct": round(bank / goods * 100, 1) if goods else 0.0,
    }
    conn.close()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=DEFAULT_EFFECTIVE)
    ap.add_argument("--market", type=float, default=DEFAULT_MARKET)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = apply_fx(a.rate, a.market, a.dry_run)
    print(f"{'DRY RUN — nothing written' if a.dry_run else 'applied'}")
    print(f"  invoices          : {r['invoices']} (all treated as paid)")
    print(f"  invoiced          : ${r['usd']:,.2f}")
    print(f"  effective rate    : Rs {r['effective_rate']}/USD  (market ref Rs {r['market_rate']})")
    print(f"  landed cost       : Rs {r['landed_inr']:,.0f}")
    print(f"  goods at market   : Rs {r['goods_at_market_inr']:,.0f}")
    print(f"  cost of banking   : Rs {r['bank_cost_inr']:,.0f}  ({r['bank_pct']}% on top of goods)")
