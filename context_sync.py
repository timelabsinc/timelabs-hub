#!/usr/bin/env python3
"""Keep what Hermes knows about this system current.

`memory_facts` is append-only and only ever gets written when somebody is
having a conversation. So it rots quietly. On the day this was written the
newest fact was eight days old, and the system brief still described three
authorized SSH keys an hour after one had been revoked. Nothing was broken.
Nobody had talked to Hermes about it, which turns out to be the same thing.

The fix is not more facts, it is facts that expire. This writes a small set
of *living* rows, one per topic, replaced on every run, under category
'state'. Durable knowledge (decisions, architecture, preferences) stays
append-only in its own categories and is never touched here.

Everything is counted in Python. Nothing is handed to a model to add up, for
the same reason weekly_brief.py does it that way: a confident wrong total is
worse than no total.

Runs on a timer, costs nothing, needs no network.
"""
import json
import os
import sqlite3
import subprocess
import sys

BASE = "/root/ops-dashboard"
DB = f"{BASE}/data/hermes.db"
SDB = f"{BASE}/data/suppliers.db"
CONTEXT_DOC = "/root/labs-os-context.md"

# Services that are meant to be running. Anything here that is dead is worth
# Hermes knowing about before the owner asks why a page is blank.
SERVICES = [
    "ops-agent-chat", "nginx", "oauth2-proxy", "drop",
    "ops-auth", "ops-dashboard-refresh",
]


def money(n):
    return f"Rs {n:,.0f}"


def ensure_schema(conn):
    """Give facts a replaceable identity. Additive, so old readers are fine."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_facts)")]
    if "fact_key" not in cols:
        conn.execute("ALTER TABLE memory_facts ADD COLUMN fact_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_key "
                 "ON memory_facts(fact_key)")


def put(conn, key, text):
    """One row per key. Replacing beats appending: a stale row that still
    reads as true is what made this necessary in the first place."""
    conn.execute("DELETE FROM memory_facts WHERE fact_key=?", (key,))
    conn.execute(
        "INSERT INTO memory_facts (category, fact, fact_key, confidence, "
        "source_chat_id) VALUES ('state',?,?,'high','context_sync')",
        (text, key))


def gather():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    f = {}
    q = conn.execute

    f["orders_7d"] = q("SELECT COUNT(*) FROM orders WHERE received_at >= "
                       "date('now','-7 day')").fetchone()[0]
    f["orders_30d"] = q("SELECT COUNT(*) FROM orders WHERE received_at >= "
                        "date('now','-30 day')").fetchone()[0]
    f["value_7d"] = q("SELECT COALESCE(SUM(price_inr*COALESCE(quantity,1)),0) "
                      "FROM orders WHERE received_at >= date('now','-7 day')"
                      ).fetchone()[0]
    f["value_30d"] = q("SELECT COALESCE(SUM(price_inr*COALESCE(quantity,1)),0) "
                       "FROM orders WHERE received_at >= date('now','-30 day')"
                       ).fetchone()[0]
    f["orders_total"] = q("SELECT COUNT(*) FROM orders").fetchone()[0]

    f["pipeline"] = [dict(r) for r in q(
        "SELECT status, COUNT(*) n FROM orders "
        "WHERE COALESCE(local_hidden,0)=0 AND status NOT IN "
        "('delivered','cancelled') GROUP BY status ORDER BY n DESC")]

    f["selling"] = [dict(r) for r in q(
        "SELECT product, COUNT(*) n FROM orders WHERE received_at >= "
        "date('now','-30 day') AND product IS NOT NULL "
        "GROUP BY product ORDER BY n DESC LIMIT 5")]

    # Same definition of "stuck" the Monday brief uses. Two places disagreeing
    # about what stuck means would be worse than either definition being wrong.
    f["stuck"] = [dict(r) for r in q(
        "SELECT o.id, o.product, o.status, "
        "  (SELECT MAX(created_at) FROM order_events e WHERE e.order_id=o.id) last_move "
        "FROM orders o WHERE o.supplier_visible=1 "
        "  AND o.status NOT IN ('delivered','cancelled','shipped') "
        "  AND COALESCE((SELECT MAX(created_at) FROM order_events e "
        "      WHERE e.order_id=o.id), o.received_at) < datetime('now','-7 day') "
        "ORDER BY last_move ASC LIMIT 8")]

    bills = q("SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM supplier_bills "
              "WHERE status='acknowledged'").fetchone()
    paid = q("SELECT COALESCE(SUM(amount),0) FROM supplier_payments").fetchone()[0]
    f["owed"] = round((bills["t"] or 0) - (paid or 0), 2)
    f["bills_open"] = bills["n"] or 0
    f["drafts_unagreed"] = q("SELECT COUNT(*) FROM supplier_bills "
                             "WHERE status='draft'").fetchone()[0]

    f["reddit_ready"] = q("SELECT COUNT(*) FROM reddit_posts "
                          "WHERE status='ready'").fetchone()[0]
    f["reddit_scheduled"] = q("SELECT COUNT(*) FROM reddit_posts "
                              "WHERE slot_date >= date('now')").fetchone()[0]
    f["threads_7d"] = q("SELECT COUNT(*) FROM reddit_threads WHERE fetched_at "
                        ">= datetime('now','-7 day')").fetchone()[0]

    f["products"] = [dict(r) for r in q(
        "SELECT status, COUNT(*) n FROM products GROUP BY status")]
    f["customers"] = q("SELECT COUNT(*) FROM customers").fetchone()[0]
    # Most rows in `orders` are hidden QA builds. Reporting the raw total
    # would have Hermes quoting 42 when the business has done 8.
    f["orders_hidden"] = q("SELECT COUNT(*) FROM orders "
                           "WHERE COALESCE(local_hidden,0)=1").fetchone()[0]

    f["activity_7d"] = [dict(r) for r in q(
        "SELECT app||'/'||kind k, COUNT(*) n FROM hub_events "
        "WHERE created_at >= datetime('now','-7 day') "
        "GROUP BY k ORDER BY n DESC LIMIT 10")]

    f["facts_durable"] = q(
        "SELECT COUNT(*) FROM memory_facts WHERE category != 'state'"
    ).fetchone()[0]
    f["facts_newest"] = q(
        "SELECT MAX(created_at) FROM memory_facts WHERE category != 'state'"
    ).fetchone()[0]
    conn.close()

    try:
        s = sqlite3.connect(SDB, timeout=30)
        f["parts_spend_30d"] = s.execute(
            "SELECT COALESCE(SUM(total_inr),0) FROM invoices "
            "WHERE order_date >= date('now','-30 day')").fetchone()[0]
        s.close()
    except Exception:
        f["parts_spend_30d"] = None

    f["services_down"] = []
    for svc in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=15)
            if r.stdout.strip() != "active":
                f["services_down"].append(svc)
        except Exception:
            f["services_down"].append(f"{svc} (unknown)")

    # Access inventory. Counts and labels only, never key material. This is
    # the fact that was wrong today, so it is the one most worth refreshing.
    try:
        with open("/root/.ssh/authorized_keys") as fh:
            labels = [ln.split()[-1] for ln in fh
                      if ln.strip() and ln.startswith("ssh-")]
        f["ssh_keys"] = labels
    except OSError:
        f["ssh_keys"] = None
    try:
        with open(f"{BASE}/access.json") as fh:
            f["roles"] = json.load(fh).get("roles", {})
    except (OSError, json.JSONDecodeError):
        f["roles"] = {}

    try:
        age_days = (
            __import__("time").time() - os.path.getmtime(CONTEXT_DOC)) / 86400
        f["context_age_days"] = round(age_days, 1)
    except OSError:
        f["context_age_days"] = None
    return f


def drift(f):
    """Things that look like nobody is watching them. This is the part that
    makes the job worth running rather than just informative."""
    out = []
    if f["services_down"]:
        out.append(f"services not active: {', '.join(f['services_down'])}")
    if f["stuck"]:
        out.append(f"{len(f['stuck'])} build(s) have not moved in over a week")
    if f["drafts_unagreed"]:
        out.append(f"{f['drafts_unagreed']} supplier batch(es) waiting on the "
                   "owner to agree them")
    if f["reddit_ready"] == 0 and f["reddit_scheduled"] == 0:
        out.append("Reddit rota is empty, nothing queued to post")
    if f["context_age_days"] and f["context_age_days"] > 14:
        out.append(f"labs-os-context.md is {f['context_age_days']:.0f} days "
                   "old and is the brief new sessions get onboarded with")
    return out


def as_facts(conn, f):
    put(conn, "state/orders",
        f"Orders: {f['orders_7d']} in the last 7 days worth "
        f"{money(f['value_7d'])}, {f['orders_30d']} in 30 days worth "
        f"{money(f['value_30d'])}. {f['orders_total']} order rows all time, "
        f"of which {f['orders_hidden']} are hidden QA or test builds, so "
        f"{f['orders_total'] - f['orders_hidden']} are real. "
        f"{f['customers']} customers on file.")

    pipe = ", ".join(f"{r['status']}: {r['n']}" for r in f["pipeline"]) or "empty"
    put(conn, "state/pipeline", f"Open build pipeline right now, {pipe}.")

    owed = (f"Owed to the supplier: {money(f['owed'])} across "
            f"{f['bills_open']} agreed batch(es). "
            f"{f['drafts_unagreed']} batch(es) still waiting to be agreed.")
    if f["parts_spend_30d"]:
        owed += f" Parts spend in the last 30 days: {money(f['parts_spend_30d'])}."
    put(conn, "state/money", owed)

    if f["selling"]:
        top = ", ".join(f"{r['product'][:44]} ({r['n']})" for r in f["selling"])
        put(conn, "state/selling", f"Selling most in the last 30 days: {top}.")

    if f["stuck"]:
        rows = "; ".join(f"#{r['id']} {(r['product'] or '')[:36]} [{r['status']}] "
                         f"since {(r['last_move'] or '')[:10]}"
                         for r in f["stuck"][:5])
        put(conn, "state/stuck",
            f"{len(f['stuck'])} build(s) not moved in over a week: {rows}.")
    else:
        put(conn, "state/stuck",
            "No builds are stuck. Everything moved in the last week.")

    put(conn, "state/reddit",
        f"Reddit: {f['reddit_ready']} draft(s) ready, {f['reddit_scheduled']} "
        f"on the rota, {f['threads_7d']} new threads seen in 7 days.")

    prods = ", ".join(f"{r['status'] or 'unset'}: {r['n']}" for r in f["products"])
    put(conn, "state/catalog",
        f"Product builder staging table by status, {prods}." if prods else
        "The Labs OS `products` staging table is empty. That is expected: it "
        "only holds drafts mid-build. The live catalog is Shopify, so answer "
        "catalog questions from the Shopify tools, not this table.")

    if f["activity_7d"]:
        act = ", ".join(f"{r['k']} {r['n']}" for r in f["activity_7d"])
        put(conn, "state/activity",
            f"System activity in the last 7 days ({act}).")

    access = []
    if f["ssh_keys"] is not None:
        access.append(f"{len(f['ssh_keys'])} authorized SSH key(s): "
                      f"{', '.join(f['ssh_keys'])}")
    if f["roles"]:
        access.append("non-admin Labs OS roles: " + ", ".join(
            f"{k} = {v}" for k, v in f["roles"].items()))
    else:
        access.append("no non-admin Labs OS roles are assigned")
    put(conn, "state/access", "Access right now. " + ". ".join(access) + ".")

    put(conn, "state/services",
        "All expected services are active."
        if not f["services_down"] else
        f"Services NOT active: {', '.join(f['services_down'])}.")

    d = drift(f)
    put(conn, "state/needs-attention",
        "Nothing is drifting. No stuck builds, no dead services, nothing "
        "waiting on the owner."
        if not d else "Needs attention: " + "; ".join(d) + ".")

    put(conn, "state/freshness",
        f"These 'state' facts are refreshed automatically by context_sync.py. "
        f"Durable knowledge is {f['facts_durable']} fact(s), newest written "
        f"{(f['facts_newest'] or 'never')[:10]}. If a 'state' fact and an older "
        f"fact disagree, the 'state' one is current.")


def main():
    f = gather()
    conn = sqlite3.connect(DB, timeout=30)
    try:
        ensure_schema(conn)
        as_facts(conn, f)
        conn.commit()
    finally:
        conn.close()

    d = drift(f)
    print(f"[context-sync] refreshed state facts; "
          f"{len(d)} thing(s) drifting", flush=True)
    for line in d:
        print(f"  - {line}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
