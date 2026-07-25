#!/usr/bin/env python3
"""Regenerates /var/www/ops/index.html from live Shopify / GA4 / Meta data.

Each source is independent and fails soft: a missing or invalid credential
shows as "not connected" on the page instead of crashing the whole run.
Run manually for an on-demand refresh, or via the daily systemd timer.
"""
import datetime
import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = "/root/ops-dashboard"
# The effective rupees-per-dollar the business actually pays once bank charges
# and transfer fees are in — same figure fx_apply.py uses on invoices, not the
# market rate, so shipment costs here line up with the Ledger.
USD_INR_EFFECTIVE = 100.0
sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import (  # the Hub/Face "Meridian" shell + chart engine
    page, source_chip, _refund_pct, kpi_card, stat_pill, svg_revenue_chart,
    verdict_banner,
)

ENV_PATH = "/root/ops-dashboard/.env"
OUT_PATH = "/var/www/ops/index.html"
LOOKBACK_DAYS = int(os.environ.get("OPS_RANGE_DAYS", "90"))
if LOOKBACK_DAYS not in (7, 30, 90):
    LOOKBACK_DAYS = 90


def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


ENV = load_env(ENV_PATH)


def http_json(url, headers=None, data=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


# --------------------------------------------------------------------------- Shopify
def fetch_shopify():
    shop = ENV.get("SHOPIFY_SHOP")
    token = ENV.get("SHOPIFY_ADMIN_TOKEN")
    if not shop or not token:
        return {"connected": False}

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    url = (
        f"https://{shop}/admin/api/2024-10/orders.json"
        f"?status=any&created_at_min={since}&limit=250&fields=id,total_price,created_at,refunds,financial_status"
    )
    headers = {"X-Shopify-Access-Token": token}

    orders = []
    next_url = url
    while next_url:
        req = urllib.request.Request(next_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
            orders.extend(body.get("orders", []))
            link = resp.headers.get("Link", "")
        next_url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")

    total_sales = sum(float(o.get("total_price") or 0) for o in orders)
    refund_total = 0.0
    for o in orders:
        for r in o.get("refunds") or []:
            for t in r.get("transactions") or []:
                refund_total += float(t.get("amount") or 0)

    return {
        "connected": True,
        "order_count": len(orders),
        "total_sales": total_sales,
        "refund_total": refund_total,
        "aov": (total_sales / len(orders)) if orders else 0,
    }


# --------------------------------------------------------------------------- GA4
def fetch_ga4():
    property_id = ENV.get("GA4_PROPERTY_ID")
    sa_path = ENV.get("GA4_SERVICE_ACCOUNT_JSON")
    if not property_id or not sa_path or not os.path.exists(sa_path):
        return {"connected": False}

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GRequest
    except ImportError:
        return {"connected": False, "error": "google-auth not installed"}

    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    creds.refresh(GRequest())

    end = datetime.date.today()
    start = end - datetime.timedelta(days=LOOKBACK_DAYS)
    body = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}],
    }
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    headers = {"Authorization": f"Bearer {creds.token}"}
    result = http_json(url, headers=headers, data=body, method="POST")

    by_channel = {}
    total_sessions = 0
    for row in result.get("rows", []):
        channel = row["dimensionValues"][0]["value"]
        sessions = int(row["metricValues"][0]["value"])
        by_channel[channel] = sessions
        total_sessions += sessions

    return {"connected": True, "total_sessions": total_sessions, "by_channel": by_channel}


# --------------------------------------------------------------------------- Meta Ads
def fetch_meta():
    token = ENV.get("META_ACCESS_TOKEN")
    account_ids = [a.strip() for a in ENV.get("META_AD_ACCOUNT_IDS", "").split(",") if a.strip()]
    if not token or not account_ids:
        return {"connected": False}

    since = (datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = datetime.date.today().isoformat()
    campaigns = []
    total_spend = 0.0
    for acct in account_ids:
        acct_id = acct if acct.startswith("act_") else f"act_{acct}"
        url = (
            f"https://graph.facebook.com/v21.0/{acct_id}/insights"
            f"?level=campaign&fields=campaign_name,spend,clicks,ctr,cpc"
            f"&time_range={{'since':'{since}','until':'{until}'}}"
            f"&access_token={token}"
        ).replace("'", '"')
        try:
            result = http_json(url)
        except urllib.error.HTTPError:
            continue
        for row in result.get("data", []):
            spend = float(row.get("spend") or 0)
            total_spend += spend
            campaigns.append({
                "name": row.get("campaign_name", ""),
                "spend": spend,
                "clicks": int(row.get("clicks") or 0),
                "ctr": float(row.get("ctr") or 0),
                "cpc": float(row.get("cpc") or 0),
            })

    campaigns.sort(key=lambda c: -c["spend"])
    return {"connected": True, "total_spend": total_spend, "campaigns": campaigns}


# --------------------------------------------------------------------------- Verified findings (hermes.db)
DB_PATH = "/root/ops-dashboard/data/hermes.db"


def fetch_findings(limit=6):
    """Latest high-confidence business facts logged by Hermes/Claude — the
    command center shows these so verified findings live where decisions happen."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT date(created_at), category, fact FROM memory_facts "
            "WHERE confidence='high' AND category IN ('business','operational') "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[findings] {e}", file=sys.stderr)
        return []


def fetch_snapshot():
    """Latest metrics snapshot pushed by a Claude session via its connectors.
    Used whenever a source has no direct API credential configured."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT created_at, payload FROM metrics_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return None
        snap = json.loads(row[1])
        snap["_stored_at"] = row[0]
        return snap
    except Exception as e:
        print(f"[snapshot] {e}", file=sys.stderr)
        return None


def fetch_action_plan():
    """Open action items, grouped by project — the shared plan Hermes and the
    dashboard both work from."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id, project, title, why, owner, status, blocked_on FROM action_items "
            "WHERE status != 'done' ORDER BY project, id"
        ).fetchall()
        done = conn.execute(
            "SELECT COUNT(*) FROM action_items WHERE status='done'"
        ).fetchone()[0]
        conn.close()
        return rows, done
    except Exception as e:
        print(f"[plan] {e}", file=sys.stderr)
        return [], 0


def fetch_logged_orders(limit=60):
    """Orders logged into hermes.db — the order form and WhatsApp capture.
    `source` is stored on the row; when it's blank we infer it (a WhatsApp
    capture always carries a chat_id/sender_number, a form paste never does)."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT received_at, customer_name, product, price_inr, quantity, status, "
            "source, sender_number, chat_id, id FROM orders "
            f"WHERE received_at >= date('now', '-{LOOKBACK_DAYS} days') "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"[orders] {e}", file=sys.stderr)
        return []
    out = []
    for r in rows:
        src = (r[6] or "").strip().lower()
        if src in ("", "none"):
            src = "whatsapp" if (r[7] or r[8]) else "form"
        out.append({"when": (r[0] or "")[:16], "customer": r[1] or "—",
                    "product": r[2] or "—", "amount": float(r[3] or 0),
                    "qty": int(r[4] or 1), "status": (r[5] or "new").lower(),
                    # id is what lets shipment costs be looked up for exactly
                    # the orders on screen
                    "source": src, "ref": "", "id": r[9]})
    return out


def fetch_shopify_orders(limit=30):
    """Live storefront orders. Needs read_orders (granted 2026-07-21); fails
    soft to [] so the dashboard still builds if Shopify is unreachable."""
    try:
        sys.path.insert(0, "/root/ops-dashboard")
        import shopify_api
        if not shopify_api.configured():
            return []
        q = """query($n: Int!) { orders(first: $n, sortKey: CREATED_AT, reverse: true) {
                 nodes { name createdAt displayFinancialStatus
                         totalPriceSet { shopMoney { amount } }
                         customer { displayName }
                         lineItems(first: 5) { nodes { title quantity } } } } }"""
        nodes = shopify_api.admin_graphql(q, {"n": limit})["orders"]["nodes"]
    except Exception as e:
        print(f"[shopify-orders] {e}", file=sys.stderr)
        return []
    out = []
    for n in nodes:
        li = (n.get("lineItems") or {}).get("nodes") or []
        title = li[0]["title"] if li else "—"
        if len(li) > 1:
            title += f" +{len(li) - 1} more"
        qty = sum(int(x.get("quantity") or 1) for x in li) or 1
        try:
            amt = float(((n.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        out.append({"when": (n.get("createdAt") or "")[:16].replace("T", " "),
                    "customer": (n.get("customer") or {}).get("displayName") or "—",
                    "product": title, "amount": amt, "qty": qty,
                    "status": (n.get("displayFinancialStatus") or "").lower(),
                    "source": "website", "ref": n.get("name") or ""})
    return out


def fetch_orders(limit=40):
    """Every order, whatever door it came through, newest first."""
    merged = fetch_logged_orders() + fetch_shopify_orders()
    merged.sort(key=lambda o: o.get("when") or "", reverse=True)
    return merged[:limit]


SOURCE_LABEL = {"website": "Website", "form": "Order form",
                "whatsapp": "WhatsApp", "instagram": "Instagram"}


def source_label(src):
    """Hand-typed short codes stay uppercase.

    The live data carries `TLC` and `CC` alongside the known channels, and
    `.title()` rendered those as "Tlc" and "Cc" — which reads as a typo
    rather than as a channel name.
    """
    if src in SOURCE_LABEL:
        return SOURCE_LABEL[src]
    if not src:
        return "Other"
    return src.upper() if len(src) <= 4 else src.title()


def order_source_pill(src):
    # One modifier, not one per channel. The distinction worth seeing at a
    # glance is "this arrived on its own" against "somebody typed it in";
    # a class per source would mean four dead class names for three of them.
    auto = " auto" if src == "website" else ""
    return (f'<span class="osrc{auto}">'
            f'{html.escape(source_label(src))}</span>')


def product_analytics(top=8):
    """What's selling and what isn't — units + revenue per product, across
    every channel. Reads order_items directly (kept current by the Shopify
    sync and every order-form save) instead of re-deriving totals from a
    fetched order list, so Home, the order form's own What's Selling tab and
    `labs sales` can never disagree about the same number."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(oi.canonical_product,''), oi.product) AS name, "
            "SUM(oi.quantity) AS units, SUM(oi.line_total) AS revenue, "
            "COUNT(DISTINCT oi.order_id) AS orders "
            "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            "WHERE o.status != 'cancelled' AND (o.financial_status IS NULL "
            "OR o.financial_status NOT IN ('refunded','voided')) "
            "GROUP BY 1 ORDER BY units DESC").fetchall()
        conn.close()
    except Exception as e:
        print(f"[product-analytics] {e}", file=sys.stderr)
        return [], []
    ranked = [(r[0], {"units": r[1] or 0, "revenue": r[2] or 0.0, "orders": r[3] or 0})
              for r in rows]
    return ranked[:top], ranked[-top:][::-1] if len(ranked) > top else []


def shipment_cost_for(orders):
    """What the shipments carrying these orders cost us, in rupees.

    Only counts each shipment once even when several of its watches are in
    the list — otherwise a consignment's freight would be added again for
    every build inside it. Costs are entered per consignment in the supplier
    queue, usually in USD, so they're converted at the same effective rate
    the Ledger uses rather than the market rate.
    """
    import sqlite3
    ids = [o.get("id") for o in orders if o.get("id")]
    if not ids:
        return 0.0
    try:
        conn = sqlite3.connect(DB_PATH)
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT DISTINCT s.id, s.total_cost, s.currency FROM shipments s "
            f"JOIN orders o ON o.shipment_id = s.id WHERE o.id IN ({marks})", ids).fetchall()
        conn.close()
    except Exception as e:
        print(f"[shipment-cost] {e}", file=sys.stderr)
        return 0.0
    total = 0.0
    for _sid, cost, cur in rows:
        if not cost:
            continue
        total += float(cost) * (USD_INR_EFFECTIVE if (cur or "USD").upper() == "USD" else 1.0)
    return total


def fetch_research():
    """Latest competitor research report + total run count."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT created_at, source, report_md FROM competitor_research ORDER BY id DESC LIMIT 1"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM competitor_research").fetchone()[0]
        conn.close()
        return row, count
    except Exception as e:
        print(f"[research] {e}", file=sys.stderr)
        return None, 0


def fetch_content(limit=6):
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT date(created_at), kind, title FROM content_library ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[content] {e}", file=sys.stderr)
        return []


def fetch_health():
    """Live status of every service in the stack — a command center must show
    whether the machine under it is actually running."""
    import subprocess as sp
    checks = [
        ("Web server", ["systemctl", "is-active", "nginx"]),
        ("Login service", ["systemctl", "is-active", "ops-auth"]),
        ("Agent chat", ["systemctl", "is-active", "ops-agent-chat"]),
        ("Daily refresh", ["systemctl", "is-active", "ops-dashboard.timer"]),
        ("Hermes gateway", ["systemctl", "--user", "is-active", "hermes-gateway"]),
        ("Hermes console", ["systemctl", "--user", "is-active", "hermes-dashboard"]),
    ]
    env = dict(os.environ, XDG_RUNTIME_DIR="/run/user/0")  # --user works from system services too
    out = []
    for label, cmd in checks:
        try:
            state = sp.run(cmd, capture_output=True, text=True, timeout=10, env=env).stdout.strip()
        except Exception:
            state = "unknown"
        out.append((label, state == "active"))
    return out


# --------------------------------------------------------------------------- Analysis (LLM)
ANALYSIS_MODEL = ENV.get("ANALYSIS_MODEL", "claude-sonnet-4-6")
DROP_ROOT = "/srv/timelabs-drop"


def fetch_drop_recent(limit=5):
    """Most recent files in Drop — surfaced on Face so uploads are instantly visible."""
    out = []
    try:
        for base, dirs, files in os.walk(DROP_ROOT):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                p = os.path.join(base, f)
                rel = os.path.relpath(p, DROP_ROOT)
                st = os.stat(p)
                out.append((st.st_mtime, rel, f, st.st_size))
        out.sort(reverse=True)
    except OSError as e:
        print(f"[drop] {e}", file=sys.stderr)
    return out[:limit]


ANALYSIS_TIMEOUT = 150  # accuracy over speed — give the primary model room to think


def _run_hermes_oneshot(prompt, model, provider, timeout):
    cmd = ["hermes", "-z", prompt, "-m", model, "--provider", provider]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"hermes -z exit {result.returncode}: {result.stderr[-500:]}")
    text = result.stdout.strip()
    if not text:
        raise RuntimeError("empty response")
    return text


def fetch_analysis(shopify, ga4, meta):
    """Try Claude first (accuracy priority, recurring-subscription capacity),
    fall back to Minimax if Anthropic is unavailable, exhausted, or unauthenticated."""
    facts = []
    if shopify.get("connected"):
        facts.append(
            f"Shopify (last {LOOKBACK_DAYS}d): {shopify['order_count']} orders, "
            f"{fmt_inr(shopify['total_sales'])} sales, AOV {fmt_inr(shopify['aov'])}, "
            f"{fmt_inr(shopify['refund_total'])} refunded."
        )
    if ga4.get("connected"):
        chans = ", ".join(f"{k}: {v}" for k, v in sorted(ga4["by_channel"].items(), key=lambda x: -x[1]))
        facts.append(f"GA4 (last {LOOKBACK_DAYS}d): {ga4['total_sessions']} sessions. By channel — {chans}.")
    if meta.get("connected"):
        top = ", ".join(f"{c['name']} ({fmt_inr(c['spend'])}, {c['ctr']:.2f}% CTR)" for c in meta["campaigns"][:5])
        facts.append(f"Meta Ads (last {LOOKBACK_DAYS}d): {fmt_inr(meta['total_spend'])} spend. Top campaigns — {top}.")

    if not facts:
        return None

    prompt = (
        "You are a blunt, numbers-first operations analyst for a small e-commerce business "
        "(Timelabs Co, custom watch mods, India). Given this trailing-90-day data, write ONE tight "
        "paragraph (3-4 sentences max, no bullet points, no markdown) naming the single most "
        "important constraint or opportunity right now, and what to do about it. Be specific and "
        "use the actual numbers given. Do not invent numbers not present below.\n\n"
        + "\n".join(facts)
    )

    try:
        return {
            "text": _run_hermes_oneshot(prompt, ANALYSIS_MODEL, "anthropic", ANALYSIS_TIMEOUT),
            "model": f"anthropic/{ANALYSIS_MODEL}",
        }
    except Exception as e:
        print(f"[analysis] anthropic failed, falling back to minimax: {e}", file=sys.stderr)

    try:
        return {
            "text": _run_hermes_oneshot(prompt, "MiniMax-M3", "minimax-oauth", 60),
            "model": "minimax-oauth/MiniMax-M3",
        }
    except Exception as e:
        print(f"[analysis] minimax fallback also failed: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- Render
def fmt_inr(n):
    return f"₹{n:,.0f}"


def status_pill(source):
    if isinstance(source, dict):
        if source.get("via_bridge"):
            import datetime
            try:
                age = (datetime.date.today() - datetime.date.fromisoformat(source["via_bridge"])).days
            except ValueError:
                age = 0
            label = f'Bridged · {html.escape(source["via_bridge"])}'
            if age > 3:
                return f'<span class="pill crit">{label} — stale, ask Claude to refresh</span>'
            return f'<span class="pill warn">{label}</span>'
        if source.get("connected"):
            return '<span class="pill good">Live API</span>'
        return '<span class="pill crit">Not connected</span>'
    return (
        '<span class="pill good">Connected</span>' if source
        else '<span class="pill crit">Not connected</span>'
    )


def render(shopify, ga4, meta, generated_at, analysis, findings, plan, plan_done,
           orders, content, health, research, research_count):
    research_html = ""
    if research:
        created, source, report_md = research
        sys.path.insert(0, "/root/ops-dashboard")
        from agent_chat_server import md_to_html
        research_html = (
            '<section><h2>Competition research</h2><div class="panel">'
            f'<div class="research-bar"><span class="f-meta">run {research_count} · '
            f'{html.escape(created[:16])} · by {html.escape(source)}</span>'
            '<button id="researchBtn" onclick="runResearch(this)">Run fresh analysis</button></div>'
            f'<details open class="research-body"><summary>Latest report</summary>'
            f'<div class="md">{md_to_html(report_md)}</div></details>'
            '<p class="f-note">Fresh runs take 2–4 minutes — the agent researches the live web, '
            'then this section updates. Older runs stay in the database for comparison '
            '(ask the agent: "compare the last two competition reports").</p>'
            '</div></section>'
        )
    health_html = "".join(
        f'<span class="chiplet {"ok" if ok else "bad"}">{html.escape(label)}</span>'
        for label, ok in health
    )
    orders_html = ""
    if orders:
        by_src = {}
        for o in orders:
            by_src[o["source"]] = by_src.get(o["source"], 0) + 1
        mix = " · ".join(f'{source_label(s)}: <b>{n}</b>'
                         for s, n in sorted(by_src.items(), key=lambda kv: -kv[1]))
        total_rev = sum(o["amount"] for o in orders)
        rows = "".join(
            f'<tr><td class="num">{html.escape(o["when"])}</td>'
            f'<td>{order_source_pill(o["source"])}</td>'
            f'<td>{html.escape(o["customer"])}'
            + (f' <span class="f-meta num">{html.escape(o["ref"])}</span>' if o["ref"] else "")
            + f'</td><td>{html.escape(o["product"])}</td>'
            f'<td class="n num">{fmt_inr(o["amount"]) if o["amount"] else "—"}</td>'
            f'<td class="n num">{o["qty"]}</td>'
            f'<td>{html.escape(o["status"] or "new")}</td></tr>'
            for o in orders
        )
        top, slow = product_analytics()
        max_units = max((v["units"] for _, v in top), default=1) or 1
        sell_rows = "".join(
            f'<div class="sell-row"><span class="sell-nm">{html.escape(n)}</span>'
            f'<span class="sell-bar"><i style="width:{max(6, round(v["units"] / max_units * 100))}%"></i></span>'
            f'<span class="sell-n num">{v["units"]}</span>'
            f'<span class="sell-rev num">{fmt_inr(v["revenue"])}</span></div>'
            for n, v in top
        )
        slow_html = ""
        if slow:
            slow_html = (
                '<p class="f-note" style="margin-top:12px">Slowest movers: '
                + ", ".join(f'{html.escape(n)} ({v["units"]})' for n, v in slow[:5]) + '</p>'
            )
        # Headline numbers for exactly the orders shown below — the owner asked
        # for totals that follow the current range rather than a fixed all-time
        # figure, so these move with LOOKBACK_DAYS and say so.
        total_cost = shipment_cost_for(orders)
        cost_bit = (f'<div class="okpi"><b>{fmt_inr(total_cost)}</b>'
                    f'<span>Shipping cost</span></div>' if total_cost else '')
        orders_html = (
            '<section><h2>Orders — every source</h2><div class="panel">'
            '<div class="okpis">'
            f'<div class="okpi"><b>{len(orders)}</b><span>Orders</span></div>'
            f'<div class="okpi"><b>{fmt_inr(total_rev)}</b><span>Order value</span></div>'
            f'{cost_bit}'
            f'<div class="okpi"><b>{fmt_inr(total_rev / len(orders)) if orders else "—"}</b>'
            f'<span>Average</span></div>'
            '</div>'
            f'<p class="f-note" style="margin-top:10px">{mix} &nbsp;·&nbsp; '
            f'last {LOOKBACK_DAYS} days</p></div>'
            '<div class="panel tscroll" style="margin-top:10px">'
            '<table><thead><tr><th>When</th><th>Source</th><th>Customer</th><th>Product</th>'
            '<th class="n">Value</th><th class="n">Qty</th><th>Status</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></section>'
            '<section><h2>What&#8217;s selling</h2><div class="panel">'
            f'{sell_rows}{slow_html}'
            '<p class="f-note">Units across every channel — storefront, order form and WhatsApp combined.</p>'
            '</div></section>'
        )
    else:
        orders_html = (
            '<section><h2>Orders — every source</h2><div class="panel">'
            '<p class="empty">No orders in this window yet. They appear here automatically from '
            'the storefront, the <a href="/ops/order-form.html">order form</a>, and WhatsApp capture.</p>'
            '</div></section>'
        )
    overview_orders_html = ""
    if orders:
        rows = "".join(
            f'<div class="oo-row"><span class="f-meta num">{html.escape(o["when"])}</span>'
            f'{order_source_pill(o["source"])}'
            f'<b>{html.escape(o["product"])}</b>'
            f'<span class="num">{fmt_inr(o["amount"]) if o["amount"] else "—"}</span></div>'
            for o in orders[:4]
        )
        overview_orders_html = (
            '<section><h2>Latest orders</h2><div class="panel">' + rows +
            '<p class="f-note"><a href="#orders" onclick="showTab(\'orders\')">All orders &#8594;</a>'
            ' &nbsp;·&nbsp; <a href="/ops/order-form.html">Log an order &#8594;</a></p>'
            '</div></section>'
        )
    drop_recent = fetch_drop_recent()
    drop_html = ""
    if drop_recent:
        rows = "".join(
            f'<div class="oo-row"><b>{html.escape(name)}</b>'
            f'<span class="num">{(size/1e6):.1f} MB</span>'
            f'<span class="f-meta">{html.escape(rel.rsplit("/", 1)[0] if "/" in rel else "Drop")}</span></div>'
            for _mt, rel, name, size in drop_recent
        )
        drop_html = (
            '<section><h2>Recent in Drop</h2><div class="panel">' + rows +
            '<p class="f-note"><a href="/drop/">Open Drop →</a></p></div></section>'
        )

    content_html = ""
    if content:
        items = "".join(
            f'<div class="finding"><span class="f-meta">{html.escape(d)} · {html.escape(kind)}</span>'
            f'<p>{html.escape(title)}</p></div>'
            for d, kind, title in content
        )
        content_html = (
            '<section><h2>Content library</h2><div class="panel">' + items +
            '<p class="f-note">Approved marketing copy, saved by the agent — ask it to reuse or adapt any piece.</p>'
            '</div></section>'
        )
    funnel_html = ""
    funnel = shopify.get("funnel") if isinstance(shopify, dict) else None
    if funnel:
        steps = [
            ("Sessions", funnel.get("sessions", 0)),
            ("Added to cart", funnel.get("added_to_cart", 0)),
            ("Reached checkout", funnel.get("reached_checkout", 0)),
            ("Completed online", funnel.get("completed", 0)),
        ]
        top = steps[0][1] or 1
        rows = ""
        for label, n in steps:
            # sqrt scale keeps small tail steps visible
            width = max(3, round(100 * (n / top) ** 0.5))
            rows += (
                f'<div class="fstep"><span class="fname">{html.escape(label)}</span>'
                f'<div class="ftrack"><div class="ffill" style="width:{width}%"><span class="num">{n:,}</span></div></div>'
                f'<span class="fpct num">{(100*n/top):.2f}%</span></div>'
            )
        funnel_html = (
            '<section><h2>Where the funnel leaks</h2><div class="panel">'
            + rows +
            '<p class="f-note">Bar widths use a &#8730; scale for legibility. Most real orders close via WhatsApp — '
            'the online completion step understates true sales, but the cart&rarr;checkout drop is the verified leak.</p>'
            '</div></section>'
        )
    plan_html = ""
    if plan:
        by_project = {}
        for item_id, project, title, why, owner, status, blocked_on in plan:
            by_project.setdefault(project, []).append((item_id, title, why, owner, status, blocked_on))
        blocks = ""
        for project, items in by_project.items():
            rows = ""
            for item_id, title, why, owner, status, blocked_on in items:
                pill_cls = {"todo": "neutral", "doing": "warn", "blocked": "crit"}.get(status, "neutral")
                blocked_note = (
                    f'<p class="a-blocked">Waiting on — {html.escape(blocked_on)}</p>'
                    if status == "blocked" and blocked_on else ""
                )
                rows += (
                    f'<div class="a-item"><div class="a-head">'
                    f'<span class="pill {pill_cls}">{html.escape(status)}</span>'
                    f'<span class="pill neutral">{html.escape(owner)}</span>'
                    f'<b>{html.escape(title)}</b>'
                    f'<button class="donebtn" onclick="markDone({item_id}, this)">&#10003; done</button>'
                    f'</div>'
                    f'<p class="a-why">{html.escape(why or "")}</p>{blocked_note}</div>'
                )
            blocks += f'<h3 class="a-project">{html.escape(project)}</h3>{rows}'
        plan_html = (
            '<section><h2>Marketing manager — action plan</h2><div class="panel">'
            + blocks +
            f'<p class="f-note">{plan_done} item(s) completed · plan lives in hermes.db, updated by your AI team and this page</p></div></section>'
        )
    findings_html = ""
    if findings:
        items = "".join(
            f'<div class="finding"><span class="f-meta">{html.escape(d)} · {html.escape(cat)}</span>'
            f'<p>{html.escape(fact)}</p></div>'
            for d, cat, fact in findings
        )
        findings_html = (
            '<section><h2>Verified findings</h2><div class="panel">'
            + items +
            '<p class="f-note">Logged by your AI team as facts are verified — newest first.</p></div></section>'
        )
    win = shopify.get("window_days", LOOKBACK_DAYS)
    sales_daily = shopify.get("sales_daily") or []       # sparse [(date, sales, orders)]
    sessions_daily = shopify.get("sessions_daily") or [] # [(date, sessions)]
    sales_vals = [float(v) for _, v, *rest in sales_daily]
    session_vals = [float(v) for _, v in sessions_daily]

    kpi_html = ""
    if shopify.get("connected"):
        gross = shopify.get("gross_sales", shopify.get("total_sales", 0))
        kpi_html += kpi_card("Orders", str(shopify["order_count"]), f"{win}-day trailing",
                             raw=shopify["order_count"])
        kpi_html += kpi_card("Gross sales", fmt_inr(gross), f"AOV {fmt_inr(shopify['aov'])}",
                             spark_vals=sales_vals, raw=round(gross), prefix="₹")
        if shopify.get("net_sales") is not None:
            kpi_html += kpi_card("Net sales", fmt_inr(shopify["net_sales"]),
                                 "after discounts & returns",
                                 raw=round(shopify["net_sales"]), prefix="₹", spark_tone="lume")
        kpi_html += kpi_card("Refunds", fmt_inr(shopify["refund_total"]), _refund_pct(shopify),
                             raw=round(shopify["refund_total"]), prefix="₹", spark_tone="crit")
    if ga4.get("connected"):
        kpi_html += kpi_card("Sessions", f"{ga4['total_sessions']:,}",
                             f"GA4 · {ga4.get('as_of', '—')}",
                             spark_vals=session_vals, raw=ga4["total_sessions"])
    if meta.get("connected"):
        kpi_html += kpi_card("Ad spend", fmt_inr(meta["total_spend"]),
                             f"Meta · {meta.get('as_of', '—')}",
                             raw=round(meta["total_spend"]), prefix="₹")
    kpi_html = kpi_html or '<p class="empty">No sources connected yet.</p>'

    # --- conversion stat strip + revenue chart (fail-soft when data absent) ---
    stats_html = ""
    if shopify.get("connected"):
        gross = shopify.get("gross_sales") or shopify.get("total_sales") or 0
        if gross > 0 and shopify.get("refund_total"):
            pct = 100 * shopify["refund_total"] / gross
            stats_html += stat_pill("refund rate", f"{pct:.0f}%",
                                    tone="crit" if pct > 12 else "")
        funnel = shopify.get("funnel") or {}
        cart, checkout = funnel.get("added_to_cart") or 0, funnel.get("reached_checkout") or 0
        sessions = funnel.get("sessions") or 0
        if sessions and cart:
            stats_html += stat_pill("session → cart", f"{100 * cart / sessions:.1f}%")
        if cart:
            stats_html += stat_pill("cart → checkout", f"{100 * checkout / cart:.0f}%")

    revenue_chart_html = ""
    if sales_daily and shopify.get("sales_daily_span"):
        chart = svg_revenue_chart(sales_daily, shopify["sales_daily_span"])
        if chart:
            revenue_chart_html = (
                '<section><h2>Daily sales — last 90 days</h2><div class="panel">'
                + chart +
                '<div class="chart-legend"><span class="lg-sale"><i></i>sale day</span>'
                '<span class="lg-refund"><i></i>refund day</span></div>'
                '</div></section>'
            )

    channel_rows = ""
    if ga4.get("connected") and ga4.get("by_channel"):
        max_sessions = max(ga4["by_channel"].values()) or 1
        for channel, sessions in sorted(ga4["by_channel"].items(), key=lambda x: -x[1]):
            pct = round(100 * sessions / max_sessions)
            channel_rows += (
                f'<div class="srcrow"><span class="s-name">{html.escape(channel)}</span>'
                f'<div class="s-track"><div class="s-fill" style="width:{pct}%"></div></div>'
                f'<span class="s-val num">{sessions:,}</span></div>'
            )

    campaign_rows = ""
    if meta.get("connected") and meta.get("campaigns"):
        for c in meta["campaigns"][:12]:
            campaign_rows += (
                f'<tr><td>{html.escape(c["name"])}</td>'
                f'<td class="n num">{fmt_inr(c["spend"])}</td>'
                f'<td class="n num">{c["clicks"]:,}</td>'
                f'<td class="n num">{c["ctr"]:.2f}%</td></tr>'
            )

    verdict_html = verdict_banner(analysis)

    source_status_html = (
        source_chip(shopify, "Shopify")
        + source_chip(ga4, "GA4")
        + source_chip(meta, "Meta Ads")
    )

    channel_html = (
        "<section><h2>Sessions by channel</h2><div class='panel'>" + channel_rows + "</div></section>"
    ) if channel_rows else ""

    campaign_html = (
        "<section><h2>Meta campaigns</h2><div class='panel tscroll'><table><thead><tr>"
        "<th>Campaign</th><th class='n'>Spend</th><th class='n'>Clicks</th><th class='n'>CTR</th>"
        "</tr></thead><tbody>" + campaign_rows + "</tbody></table></div></section>"
    ) if campaign_rows else ""

    return page(
        generated_at=generated_at,
        lookback_days=LOOKBACK_DAYS,
        source_status_html=source_status_html,
        kpi_html=kpi_html,
        verdict_html=verdict_html,
        funnel_html=funnel_html,
        channel_html=channel_html,
        campaign_html=campaign_html,
        overview_orders_html=overview_orders_html,
        orders_html=orders_html,
        plan_html=plan_html,
        research_html=research_html,
        content_html=content_html,
        findings_html=findings_html,
        health_html=health_html,
        stats_html=stats_html,
        revenue_chart_html=revenue_chart_html + drop_html,
        drop_ready=True,
    )


def main():
    shopify = {"connected": False}
    ga4 = {"connected": False}
    meta = {"connected": False}

    try:
        shopify = fetch_shopify()
    except Exception as e:
        print(f"[shopify] error: {e}", file=sys.stderr)
    try:
        ga4 = fetch_ga4()
    except Exception as e:
        print(f"[ga4] error: {e}", file=sys.stderr)
    try:
        meta = fetch_meta()
    except Exception as e:
        print(f"[meta] error: {e}", file=sys.stderr)

    # Bridge fallback: when a source has no direct credential, use the latest
    # snapshot a Claude session pushed through its own connectors.
    snap = fetch_snapshot()
    if snap:
        stamp = snap.get("as_of", "")
        for name, live in (("shopify", shopify), ("ga4", ga4), ("meta", meta)):
            if not live.get("connected") and snap.get(name, {}).get("connected"):
                snapped = dict(snap[name])
                snapped["via_bridge"] = stamp
                if name == "shopify":
                    snapped.setdefault("total_sales", snapped.get("gross_sales", 0))
                    shopify = snapped
                elif name == "ga4":
                    ga4 = snapped
                else:
                    meta = snapped

    analysis = None
    try:
        analysis = fetch_analysis(shopify, ga4, meta)
    except Exception as e:
        print(f"[analysis] unexpected error: {e}", file=sys.stderr)

    generated_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    plan, plan_done = fetch_action_plan()
    research, research_count = fetch_research()
    html_out = render(shopify, ga4, meta, generated_at, analysis, fetch_findings(),
                      plan, plan_done, fetch_orders(), fetch_content(), fetch_health(),
                      research, research_count)

    tmp_path = OUT_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(html_out)
    os.replace(tmp_path, OUT_PATH)
    os.system(f"chown www-data:www-data {OUT_PATH}")
    print(f"wrote {OUT_PATH} (shopify={shopify.get('connected')}, ga4={ga4.get('connected')}, meta={meta.get('connected')})")
    try:
        import ledger
        ledger.build()
    except Exception as e:
        print(f"[ledger] {e}", file=sys.stderr)
    # Drop is a hand-written SPA, so its page is a file in the repo rather
    # than something a generator renders. Publishing it was a manual copy
    # that only ever worked because someone remembered — the same shape as
    # the generator list below, which has already drifted once. Copy first,
    # then let drop_chrome rewrite the header region in place.
    try:
        drop_src = os.path.join(BASE, "www", "drop-index.html")
        drop_dst = "/var/www/drop/index.html"
        src_html = open(drop_src, encoding="utf-8").read()
        # The chrome region is rewritten in the published copy, so compare
        # everything outside it — otherwise this would rewrite on every run.
        strip = lambda s: re.sub(r"<!--labs:chrome-->.*?<!--/labs:chrome-->",
                                 "", s, flags=re.S)
        try:
            cur = open(drop_dst, encoding="utf-8").read()
        except OSError:
            cur = ""
        if strip(src_html) != strip(cur):
            tmp = drop_dst + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(src_html)
            os.replace(tmp, drop_dst)
            os.system(f"chown www-data:www-data {drop_dst}")
            print(f"published {drop_dst} from www/drop-index.html")
    except Exception as e:
        print(f"[drop] publish failed: {e}", file=sys.stderr)

    # drop_chrome doesn't render a page — it re-syncs Drop's header/nav from
    # hub_shell so the hand-written SPA can't drift out of step with the rest.
    generators = ("tools", "blog", "product_updater", "product_builder",
                  "content_updater", "theme_editor", "files", "order_form",
                  "supplier", "access", "architecture", "blog_uploader",
                  "drop_chrome", "reddit")
    for mod in generators:
        try:
            __import__(mod).build()
        except Exception as e:
            print(f"[{mod}] {e}", file=sys.stderr)

    # This list is hand-maintained, and it has already drifted once: access,
    # architecture and blog_uploader silently fell out of it and went ~17
    # hours without a rebuild, so they missed shared-shell fixes everything
    # else got. Shout about any generator that isn't covered rather than
    # letting the next one rot quietly.
    # ledger is built explicitly further up (it needs the invoice DB), so it
    # is covered despite not being in the loop.
    handled = set(generators) | {"generate", "ledger"}
    missed = []
    for path in glob.glob(os.path.join(BASE, "*.py")):
        name = os.path.basename(path)[:-3]
        if name in handled:
            continue
        try:
            with open(path) as f:
                src = f.read()
        except OSError:
            continue
        if 'OUT = "/var/www/' in src and "\ndef build(" in src:
            missed.append(name)
    if missed:
        print(f"[refresh] WARNING: generator(s) not in the refresh loop, so their "
              f"pages will go stale: {', '.join(sorted(missed))}", file=sys.stderr)


if __name__ == "__main__":
    main()
