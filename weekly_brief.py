#!/usr/bin/env python3
"""The Monday morning read: what happened, what is owed, what is stuck.

Everything here already exists somewhere in Labs OS. The point is not new
data, it is not having to open four tools on a Monday to find out whether
anything needs attention.

The numbers are computed in Python and handed to Hermes only to be written
up. A model asked to both calculate and summarise will occasionally produce
a confident wrong total, and a brief you have to double-check is worse than
no brief.

Runs on Hermes so it costs no Claude budget.
"""
import datetime
import json
import sqlite3
import subprocess
import sys

BASE = "/root/ops-dashboard"
DB = f"{BASE}/data/hermes.db"
SDB = f"{BASE}/data/suppliers.db"
USD_INR = 100.0


def money(n):
    return f"Rs {n:,.0f}"


def gather():
    """Facts only. No interpretation, no rounding that hides anything."""
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    f = {}

    f["orders_7d"] = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE received_at >= date('now','-7 day')"
    ).fetchone()[0]
    f["orders_prev7"] = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE received_at >= date('now','-14 day') "
        "AND received_at < date('now','-7 day')").fetchone()[0]
    f["value_7d"] = conn.execute(
        "SELECT COALESCE(SUM(price_inr),0) FROM orders "
        "WHERE received_at >= date('now','-7 day')").fetchone()[0]

    f["selling"] = [dict(r) for r in conn.execute(
        "SELECT product, COUNT(*) n FROM orders "
        "WHERE received_at >= date('now','-30 day') AND product IS NOT NULL "
        "GROUP BY product ORDER BY n DESC LIMIT 5")]

    # Builds sitting in one stage for a while. "Stuck" is the useful word:
    # a queue is only a problem when something stops moving through it.
    f["stuck"] = [dict(r) for r in conn.execute(
        "SELECT o.id, o.product, o.status, "
        "  (SELECT MAX(created_at) FROM order_events e WHERE e.order_id=o.id) last_move "
        "FROM orders o WHERE o.supplier_visible=1 "
        "  AND o.status NOT IN ('delivered','cancelled','shipped') "
        "  AND COALESCE((SELECT MAX(created_at) FROM order_events e WHERE e.order_id=o.id), "
        "      o.received_at) < datetime('now','-7 day') "
        "ORDER BY last_move ASC LIMIT 8")]

    bills = conn.execute(
        "SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM supplier_bills "
        "WHERE status='acknowledged'").fetchone()
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM supplier_payments").fetchone()[0]
    f["owed"] = round((bills["t"] or 0) - (paid or 0), 2)
    f["bills_open"] = bills["n"] or 0
    f["drafts_unagreed"] = conn.execute(
        "SELECT COUNT(*) FROM supplier_bills WHERE status='draft'").fetchone()[0]

    f["reddit_ready"] = conn.execute(
        "SELECT COUNT(*) FROM reddit_posts WHERE status='ready'").fetchone()[0]
    f["reddit_scheduled"] = conn.execute(
        "SELECT COUNT(*) FROM reddit_posts WHERE slot_date >= date('now')").fetchone()[0]
    f["threads_new"] = conn.execute(
        "SELECT COUNT(*) FROM reddit_threads "
        "WHERE fetched_at >= datetime('now','-7 day')").fetchone()[0]
    conn.close()

    try:
        s = sqlite3.connect(SDB, timeout=30)
        f["parts_spend_30d"] = s.execute(
            "SELECT COALESCE(SUM(total_inr),0) FROM invoices "
            "WHERE order_date >= date('now','-30 day')").fetchone()[0]
        s.close()
    except Exception:
        f["parts_spend_30d"] = None
    return f


def as_text(f):
    lines = [
        f"Orders in the last 7 days: {f['orders_7d']} "
        f"(previous 7 days: {f['orders_prev7']})",
        f"Value of those orders: {money(f['value_7d'])}",
        f"Owed to the supplier right now: {money(f['owed'])} "
        f"across {f['bills_open']} agreed batch(es)",
    ]
    if f["drafts_unagreed"]:
        lines.append(f"Batches waiting for you to agree them: {f['drafts_unagreed']}")
    if f["parts_spend_30d"]:
        lines.append(f"Parts spend in the last 30 days: {money(f['parts_spend_30d'])}")
    if f["selling"]:
        top = ", ".join(f"{r['product'][:40]} ({r['n']})" for r in f["selling"][:3])
        lines.append(f"Selling most in 30 days: {top}")
    if f["stuck"]:
        lines.append(f"Builds not moved in over a week: {len(f['stuck'])}")
        for r in f["stuck"][:5]:
            lines.append(f"  #{r['id']} {(r['product'] or '')[:44]} "
                         f"[{r['status']}] since {(r['last_move'] or '')[:10]}")
    else:
        lines.append("No builds are stuck: everything has moved in the last week.")
    lines.append(f"Reddit: {f['reddit_ready']} draft(s) ready, "
                 f"{f['reddit_scheduled']} on the rota, "
                 f"{f['threads_new']} new threads seen this week")
    return "\n".join(lines)


def write_up(facts_text):
    prompt = (
        "You are writing the Monday morning note for the owner of Timelabs Co, "
        "a small Indian brand that builds Seiko watch mods. He reads it on a "
        "phone in under a minute.\n\n"
        "These are this week's actual figures. They are correct. Do not "
        "recalculate them, do not invent any number that is not here, and do "
        "not add advice you cannot support from them.\n\n"
        + facts_text +
        "\n\nWrite it as short plain sentences. Lead with anything that needs "
        "him to act, and if nothing does, say so in the first line rather than "
        "burying it. No greeting, no sign-off, no headings, no bullet symbols. "
        "Under 130 words. Never use an em dash or an en dash.")
    r = subprocess.run(["hermes", "-z", prompt], capture_output=True,
                       text=True, timeout=600)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    sys.path.insert(0, BASE)
    import reddit_clean
    text, _ = reddit_clean.clean(r.stdout.strip())
    return text


def notify(body):
    target = ""
    try:
        with open(f"{BASE}/.env") as fh:
            for line in fh:
                if line.startswith("HEALTH_ALERT_TARGET="):
                    target = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except OSError:
        pass
    if not target:
        return False
    try:
        subprocess.run(["hermes", "send", "--to", target, "--quiet",
                        "*Monday brief*\n" + body],
                       capture_output=True, text=True, timeout=180)
        return True
    except Exception as e:
        print(f"[brief] send failed: {e}", flush=True)
        return False


def main():
    facts = gather()
    facts_text = as_text(facts)
    body = write_up(facts_text)
    # If Hermes is unreachable the figures still matter, so fall back to them
    # rather than producing nothing.
    final = body or facts_text

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS weekly_briefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        body TEXT, facts TEXT)""")
    conn.execute("INSERT INTO weekly_briefs (body, facts) VALUES (?,?)",
                 (final, json.dumps(facts)))
    conn.commit()
    conn.close()

    sent = notify(final)
    print(f"[brief] {'sent' if sent else 'stored only (no alert destination)'}",
          flush=True)
    print(final, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
