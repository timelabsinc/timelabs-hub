#!/usr/bin/env python3
"""Regenerates /var/www/ops/index.html from live Shopify / GA4 / Meta data.

Each source is independent and fails soft: a missing or invalid credential
shows as "not connected" on the page instead of crashing the whole run.
Run manually for an on-demand refresh, or via the daily systemd timer.
"""
import datetime
import html
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

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

    since = (datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat() + "Z"
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


def fetch_orders(limit=12):
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT received_at, customer_name, product, price_inr, quantity, status, "
            "extraction_confidence FROM orders "
            f"WHERE received_at >= date('now', '-{LOOKBACK_DAYS} days') "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[orders] {e}", file=sys.stderr)
        return []


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
        rows = "".join(
            f'<tr><td class="num">{html.escape((r[0] or "")[:16])}</td>'
            f'<td>{html.escape(r[1] or "—")}</td><td>{html.escape(r[2] or "—")}</td>'
            f'<td class="n num">{fmt_inr(r[3]) if r[3] else "—"}</td>'
            f'<td class="n num">{r[4] or 1}</td>'
            f'<td>{html.escape(r[5] or "new")}</td>'
            f'<td>{html.escape(r[6] or "")}</td></tr>'
            for r in orders
        )
        orders_html = (
            '<section><h2>Orders captured from WhatsApp</h2><div class="panel tscroll">'
            '<table><thead><tr><th>When</th><th>Customer</th><th>Product</th>'
            '<th class="n">Price</th><th class="n">Qty</th><th>Status</th><th>Confidence</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></section>'
        )
    else:
        orders_html = (
            '<section><h2>Orders captured from WhatsApp</h2><div class="panel">'
            '<p class="empty">None logged yet. Post "order received" + the details in the '
            'TimeLabsHomeBot group and the row appears here with what the agent extracted.</p>'
            '</div></section>'
        )
    overview_orders_html = ""
    if orders:
        rows = "".join(
            f'<div class="oo-row"><span class="f-meta num">{html.escape((r[0] or "")[:16])}</span>'
            f'<b>{html.escape(r[2] or "—")}</b>'
            f'<span class="num">{fmt_inr(r[3]) if r[3] else "—"}</span>'
            f'<span class="pill neutral">{html.escape(r[5] or "new")}</span></div>'
            for r in orders[:3]
        )
        overview_orders_html = (
            '<section><h2>Latest orders</h2><div class="panel">' + rows +
            '<p class="f-note"><a href="#orders" onclick="showTab(\'orders\')">All orders &#8594;</a></p>'
            '</div></section>'
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
    kpis = []
    if shopify.get("connected"):
        kpis.append(("Orders", str(shopify["order_count"]), f"{LOOKBACK_DAYS}-day trailing"))
        kpis.append(("Total sales", fmt_inr(shopify["total_sales"]), f"AOV {fmt_inr(shopify['aov'])}"))
        kpis.append(("Refunds", fmt_inr(shopify["refund_total"]), ""))
    if ga4.get("connected"):
        kpis.append(("Sessions", f"{ga4['total_sessions']:,}", f"{LOOKBACK_DAYS}-day trailing"))
    if meta.get("connected"):
        kpis.append(("Ad spend", fmt_inr(meta["total_spend"]), f"{len(meta['campaigns'])} campaigns"))

    kpi_html = "".join(
        f'<div class="kpi"><div class="label">{html.escape(k)}</div>'
        f'<div class="val num">{html.escape(v)}</div><div class="ctx">{html.escape(c)}</div></div>'
        for k, v, c in kpis
    ) or '<p class="empty">No sources connected yet.</p>'

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

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Timelabs Co — Operations &amp; Growth Control Panel</title>
<style>
  :root{{
    --ground:#0f1512; --surface:#161f1a; --surface-2:#1d2822; --ink:#eef2ee; --muted:#9fb0a6;
    --line:#293831; --accent:#c9a35e; --accent-soft:rgba(201,163,94,.14);
    --good:#5cbf88; --good-soft:rgba(92,191,136,.14);
    --crit:#e06a45; --crit-soft:rgba(224,106,69,.16);
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:"SF Mono","Cascadia Code","Consolas",ui-monospace,monospace;
  }}
  @media (prefers-color-scheme: light){{
    :root{{ --ground:#ecefe8; --surface:#ffffff; --surface-2:#f4f6f1; --ink:#16211b; --muted:#586b60;
      --line:#dde3dc; --accent:#8a6a2c; --accent-soft:rgba(138,106,44,.12);
      --good:#2f8f5b; --good-soft:rgba(47,143,91,.12);
      --crit:#c1502e; --crit-soft:rgba(193,80,46,.12); }}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.5;padding:2rem 1.5rem 4rem;}}
  .wrap{{max-width:920px;margin:0 auto;}}
  .num{{font-variant-numeric:tabular-nums lining-nums}}
  header{{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;flex-wrap:wrap;
    padding-bottom:1.1rem;border-bottom:1px solid var(--line);margin-bottom:1.6rem;}}
  h1{{font-family:var(--serif);font-weight:600;font-size:clamp(1.5rem,3.4vw,2.05rem);margin:.3rem 0;text-wrap:balance;}}
  .wordmark{{font-family:var(--mono);font-size:.7rem;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);}}
  .window{{font-family:var(--mono);font-size:.76rem;color:var(--muted);}}
  h2{{font-family:var(--serif);font-size:1.02rem;font-weight:600;margin:0 0 .8rem;}}
  section{{margin-bottom:1.9rem;}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem;}}
  .kpi{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;}}
  .kpi .label{{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;}}
  .kpi .val{{font-family:var(--serif);font-size:1.5rem;margin:.35rem 0 .1rem;}}
  .kpi .ctx{{font-size:.76rem;color:var(--muted);}}
  .panel{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.2rem;}}
  .srcrow{{display:grid;grid-template-columns:130px 1fr 60px;align-items:center;gap:.6rem;margin:.5rem 0;}}
  .s-name{{font-size:.82rem;}}
  .s-track{{background:var(--surface-2);border-radius:6px;height:14px;overflow:hidden;border:1px solid var(--line);}}
  .s-fill{{height:100%;background:var(--accent);}}
  .s-val{{font-size:.8rem;color:var(--muted);text-align:right;}}
  table{{width:100%;border-collapse:collapse;font-size:.83rem;}}
  th,td{{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--line);}}
  th{{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);}}
  td.n,th.n{{text-align:right;}}
  .pill{{font-size:.66rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;padding:.2rem .5rem;
    border-radius:999px;border:1px solid transparent;}}
  .pill.good{{color:var(--good);background:var(--good-soft);border-color:var(--good);}}
  .pill.crit{{color:var(--crit);background:var(--crit-soft);border-color:var(--crit);}}
  .sources{{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.4rem;}}
  .empty{{color:var(--muted);font-size:.88rem;}}
  .fstep{{display:grid;grid-template-columns:130px 1fr 64px;align-items:center;gap:.7rem;margin:.55rem 0;}}
  .fname{{font-size:.84rem;}}
  .ftrack{{background:var(--surface-2);border-radius:7px;height:24px;overflow:hidden;border:1px solid var(--line);}}
  .ffill{{height:100%;border-radius:6px;background:linear-gradient(90deg,#3a6b4b,var(--accent));
    display:flex;align-items:center;justify-content:flex-end;min-width:30px;}}
  .ffill span{{font-size:.72rem;color:#0f1512;font-weight:700;padding-right:.5rem;}}
  .fpct{{font-size:.76rem;color:var(--muted);text-align:right;}}
  .a-project{{font-family:var(--mono);font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;
    color:var(--accent);margin:1rem 0 .2rem;}}
  .a-item{{padding:.6rem 0;border-bottom:1px solid var(--line);}}
  .a-item:last-of-type{{border-bottom:none;}}
  .a-head{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;font-size:.9rem;}}
  .a-why{{margin:.3rem 0 0;font-size:.8rem;color:var(--muted);line-height:1.5;}}
  .a-blocked{{margin:.3rem 0 0;font-size:.78rem;color:var(--crit);line-height:1.5;}}
  .tabbar{{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
    gap:1rem;flex-wrap:wrap;background:var(--ground);padding:.7rem 0 .6rem;margin-bottom:1.2rem;
    border-bottom:1px solid var(--line);}}
  .tabs{{display:flex;gap:.25rem;flex-wrap:wrap;}}
  .tabbtn{{font-family:var(--mono);font-size:.76rem;border:1px solid transparent;background:transparent;
    color:var(--muted);border-radius:8px;padding:.45rem .8rem;cursor:pointer;transition:color .12s;}}
  .tabbtn:hover{{color:var(--ink);}}
  .tabbtn.active{{background:var(--surface);border-color:var(--accent);color:var(--ink);font-weight:700;}}
  .tabbadge{{margin-left:.35rem;font-size:.62rem;color:var(--accent);}}
  #q{{font-family:var(--sans);font-size:.84rem;border:1px solid var(--line);border-radius:8px;
    background:var(--surface);color:var(--ink);padding:.45rem .7rem;width:min(240px,100%);}}
  #q:focus{{outline:2px solid var(--accent);border-color:var(--accent);}}
  .tabpanel{{display:none;}}
  .tabpanel.active{{display:block;animation:fadein .18s ease;}}
  @keyframes fadein{{from{{opacity:.4;transform:translateY(3px)}}to{{opacity:1;transform:none}}}}
  @media (prefers-reduced-motion: reduce){{.tabpanel.active{{animation:none;}}}}
  .srch-hidden{{display:none!important;}}
  .oo-row{{display:flex;align-items:center;gap:.8rem;padding:.5rem 0;border-bottom:1px solid var(--line);
    font-size:.88rem;flex-wrap:wrap;}}
  .oo-row:last-of-type{{border-bottom:none;}}
  .oo-row b{{flex:1;min-width:8rem;font-weight:600;}}
  .f-note a{{color:var(--accent);}}
  .rangebar{{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;}}
  .rangebar button{{font-family:var(--mono);font-size:.72rem;border:none;background:var(--surface);
    color:var(--muted);padding:.42rem .7rem;cursor:pointer;}}
  .rangebar button.active{{background:var(--accent-soft);color:var(--ink);font-weight:700;}}
  .rangebar button:hover{{color:var(--ink);}}
  .research-bar{{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;
    margin-bottom:.5rem;}}
  #researchBtn{{font-family:var(--mono);font-size:.72rem;border:1px solid var(--accent);color:var(--accent);
    background:transparent;border-radius:6px;padding:.35rem .8rem;cursor:pointer;}}
  #researchBtn:hover{{background:var(--accent-soft);}}
  #researchBtn:disabled{{opacity:.55;cursor:wait;}}
  .research-body summary{{cursor:pointer;font-family:var(--mono);font-size:.72rem;color:var(--muted);
    margin-bottom:.4rem;}}
  .md h1{{font-family:var(--serif);font-size:1.15rem;margin:.8rem 0 .3rem;}}
  .md h2{{font-family:var(--serif);font-size:1rem;margin:.8rem 0 .3rem;}}
  .md p,.md li{{font-size:.87rem;line-height:1.55;}}
  .md ul,.md ol{{padding-left:1.3rem;}}
  .md table{{border-collapse:collapse;font-size:.8rem;display:block;overflow-x:auto;margin:.5rem 0;}}
  .md th,.md td{{border:1px solid var(--line);padding:.3rem .55rem;text-align:left;}}
  .md th{{background:var(--surface-2);}}
  .md a{{color:var(--accent);}}
  .md code{{font-family:var(--mono);font-size:.82em;background:var(--surface-2);padding:.06em .3em;border-radius:3px;}}
  .chiplet{{font-family:var(--mono);font-size:.66rem;padding:.22rem .55rem;border-radius:999px;
    border:1px solid var(--line);color:var(--muted);}}
  .chiplet.ok{{border-color:var(--good);color:var(--good);}}
  .chiplet.bad{{border-color:var(--crit);color:var(--crit);font-weight:700;}}
  .healthrow{{display:flex;gap:.45rem;flex-wrap:wrap;margin:.6rem 0 0;}}
  .donebtn{{margin-left:auto;font-family:var(--mono);font-size:.62rem;color:var(--muted);
    background:transparent;border:1px solid var(--line);border-radius:5px;padding:.18rem .5rem;cursor:pointer;}}
  .donebtn:hover{{border-color:var(--good);color:var(--good);}}
  .tscroll{{overflow-x:auto;}}
  .finding{{padding:.7rem 0;border-bottom:1px solid var(--line);}}
  .finding:last-of-type{{border-bottom:none;}}
  .finding p{{margin:.25rem 0 0;font-size:.88rem;line-height:1.55;}}
  .f-meta{{font-family:var(--mono);font-size:.68rem;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;}}
  .f-note{{color:var(--muted);font-size:.74rem;margin:.8rem 0 0;}}
  footer{{margin-top:2.2rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);
    font-family:var(--mono);font-size:.72rem;display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem;}}
  #refreshBtn{{font-family:var(--mono);font-size:.74rem;cursor:pointer;border:1px solid var(--line);
    background:var(--surface);color:var(--ink);border-radius:6px;padding:.4rem .8rem;transition:border-color .15s;}}
  #refreshBtn:hover{{border-color:var(--accent);}}
  #refreshBtn:disabled{{opacity:.55;cursor:wait;}}
  #agentBtn{{font-family:var(--mono);font-size:.74rem;text-decoration:none;border:1px solid var(--accent);
    background:var(--accent);color:#141310;font-weight:600;border-radius:6px;padding:.4rem .8rem;}}
  #agentBtn:hover{{filter:brightness(1.08);}}
  .hlink{{font-family:var(--mono);font-size:.74rem;color:var(--muted);text-decoration:none;
    border:1px solid var(--line);border-radius:6px;padding:.4rem .8rem;}}
  .hlink:hover{{border-color:var(--accent);color:var(--ink);}}
  .verdict{{margin:0 0 1.6rem;display:flex;gap:.9rem;align-items:flex-start;background:var(--surface);
    border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;padding:1rem 1.2rem;}}
  .verdict b{{font-family:var(--serif);font-weight:600;display:block;margin-bottom:.3rem;}}
  .verdict p{{margin:0;color:var(--muted);font-size:.88rem;line-height:1.55;}}
  .verdict .stale{{font-family:var(--mono);font-size:.68rem;color:var(--muted);display:block;margin-top:.5rem;}}
</style>
<script>
  async function markDone(id, btn){{
    btn.disabled = true; btn.textContent = '…';
    try {{
      const res = await fetch('/ops/agent/api/plan/toggle', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{id}}),
      }});
      if (res.ok) {{ setTimeout(() => location.reload(), 600); return; }}
    }} catch (e) {{}}
    btn.disabled = false; btn.textContent = '✓ done';
  }}
  // ---- tabs + global search ----
  const TABS = ['overview','orders','plan','competition','content','findings'];
  // what counts as a searchable "item" per tab; overview/competition are
  // badge-counted but not hidden (tiles and one long report don't filter well)
  const SEARCHABLE = {{
    overview: {{sel: '.kpi, .srcrow, .fstep, #tab-overview tbody tr', hide: false}},
    orders: {{sel: '#tab-orders tbody tr', hide: true}},
    plan: {{sel: '#tab-plan .a-item', hide: true}},
    competition: {{sel: '#tab-competition .md h2, #tab-competition .md p, #tab-competition .md li, #tab-competition .md td', hide: false}},
    content: {{sel: '#tab-content .finding', hide: true}},
    findings: {{sel: '#tab-findings .finding', hide: true}},
  }};
  function showTab(name){{
    if(!TABS.includes(name)) name = 'overview';
    document.querySelectorAll('.tabpanel').forEach(p => p.classList.toggle('active', p.id === 'tab-'+name));
    document.querySelectorAll('.tabbtn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    if(history.replaceState) history.replaceState(null, '', '#'+name);
    applySearch();
  }}
  function applySearch(){{
    const q = document.getElementById('q').value.trim().toLowerCase();
    for(const tab of TABS){{
      const cfg = SEARCHABLE[tab];
      const items = document.querySelectorAll(cfg.sel);
      let hits = 0;
      items.forEach(el => {{
        const match = !q || el.textContent.toLowerCase().includes(q);
        if(match) hits++;
        if(cfg.hide) el.classList.toggle('srch-hidden', !match);
      }});
      const btn = document.querySelector('.tabbtn[data-tab="'+tab+'"] .tabbadge');
      btn.textContent = q ? (hits > 0 ? hits : '·') : '';
    }}
  }}
  document.addEventListener('DOMContentLoaded', () => {{
    document.querySelectorAll('.tabbtn').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
    document.getElementById('q').addEventListener('input', applySearch);
    document.addEventListener('keydown', e => {{
      if(e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA'){{
        e.preventDefault();
        document.getElementById('q').focus();
      }}
    }});
    showTab((location.hash || '#overview').slice(1));
  }});

  async function runResearch(btn){{
    btn.disabled = true;
    const start = Date.now();
    btn.textContent = 'Researching…';
    const tick = setInterval(() => {{
      btn.textContent = 'Researching — ' + Math.round((Date.now()-start)/1000) + 's';
    }}, 1000);
    try {{
      const res = await fetch('/ops/agent/api/research/run', {{method:'POST'}});
      clearInterval(tick);
      if (res.ok) {{ location.reload(); return; }}
      const body = await res.json().catch(() => ({{}}));
      btn.textContent = body.error || 'Failed — retry';
    }} catch (e) {{
      clearInterval(tick);
      btn.textContent = 'Failed — retry';
    }}
    setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Run fresh analysis'; }}, 4000);
  }}
  async function doRefresh(days){{
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true; btn.textContent = 'Refreshing…';
    const url = days ? ('/ops/refresh?days=' + days) : '/ops/refresh';
    try {{
      const res = await fetch(url, {{method:'POST'}});
      if (res.ok) {{ location.reload(); return; }}
      const body = await res.json().catch(() => ({{}}));
      btn.textContent = body.error === 'refresh already running' ? 'Already running…' : 'Failed — retry';
    }} catch (e) {{
      btn.textContent = 'Failed — retry';
    }}
    setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Refresh now'; }}, 3000);
  }}
</script>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <div class="wordmark">Timelabs Co</div>
      <h1>Operations &amp; Growth Control Panel</h1>
    </div>
    <div style="display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;">
      <div class="rangebar">
        <button class="{'active' if LOOKBACK_DAYS == 7 else ''}" onclick="doRefresh(7)">7d</button>
        <button class="{'active' if LOOKBACK_DAYS == 30 else ''}" onclick="doRefresh(30)">30d</button>
        <button class="{'active' if LOOKBACK_DAYS == 90 else ''}" onclick="doRefresh(90)">90d</button>
      </div>
      <div class="window num">refreshed {generated_at}</div>
      <button id="refreshBtn" onclick="doRefresh()">Refresh now</button>
      <a id="agentBtn" href="/ops/agent/">Talk to Agent &#8599;</a>
      <a class="hlink" href="/ops/playbooks/community-playbook.pdf">Playbook PDF</a>
    </div>
  </header>

  <nav class="tabbar" aria-label="Sections">
    <div class="tabs">
      <button class="tabbtn" data-tab="overview">Overview<span class="tabbadge"></span></button>
      <button class="tabbtn" data-tab="orders">Orders<span class="tabbadge"></span></button>
      <button class="tabbtn" data-tab="plan">Plan<span class="tabbadge"></span></button>
      <button class="tabbtn" data-tab="competition">Competition<span class="tabbadge"></span></button>
      <button class="tabbtn" data-tab="content">Content<span class="tabbadge"></span></button>
      <button class="tabbtn" data-tab="findings">Findings<span class="tabbadge"></span></button>
    </div>
    <input id="q" type="search" placeholder="Search everything…" aria-label="Search all sections">
  </nav>

  <div class="tabpanel" id="tab-overview">
  {"".join([
      '<div class="verdict"><div><b>Reading</b><p>',
      html.escape(analysis["text"]),
      '</p><span class="stale">via ', html.escape(analysis["model"]), '</span></div></div>'
  ]) if analysis else ""}

  <section>
    <h2>Source status</h2>
    <div class="sources">
      <span>Shopify {status_pill(shopify)}</span>
      <span>GA4 {status_pill(ga4)}</span>
      <span>Meta Ads {status_pill(meta)}</span>
    </div>
    <div class="healthrow">{health_html}</div>
  </section>

  <section>
    <h2>Key numbers</h2>
    <div class="kpis">{kpi_html}</div>
  </section>

  {overview_orders_html}

  {funnel_html}

  {"<section><h2>Sessions by channel (GA4)</h2><div class='panel'>" + channel_rows + "</div></section>" if channel_rows else ""}

  {"<section><h2>Meta campaigns</h2><div class='panel'><table><thead><tr><th>Campaign</th><th class='n'>Spend</th><th class='n'>Clicks</th><th class='n'>CTR</th></tr></thead><tbody>" + campaign_rows + "</tbody></table></div></section>" if campaign_rows else ""}
  </div>

  <div class="tabpanel" id="tab-orders">{orders_html}</div>

  <div class="tabpanel" id="tab-plan">{plan_html}</div>

  <div class="tabpanel" id="tab-competition">{research_html or '<section><div class="panel"><p class="empty">No research yet — run one from the agent.</p></div></section>'}</div>

  <div class="tabpanel" id="tab-content">{content_html or '<section><div class="panel"><p class="empty">Approved marketing copy will collect here.</p></div></section>'}</div>

  <div class="tabpanel" id="tab-findings">{findings_html}</div>

  <footer>
    <span>generated by /root/ops-dashboard/generate.py</span>
    <span>daily refresh + on-demand</span>
  </footer>
</div>
</body>
</html>
"""


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


if __name__ == "__main__":
    main()
