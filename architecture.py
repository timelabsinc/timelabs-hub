#!/usr/bin/env python3
"""System map — admin tool. Renders /var/www/ops/architecture.html.

Replaces the old hand-authored diagram that went stale the moment a tool was
added. This reads the truth at build time: the tool list comes from tools.py,
service health from systemctl, table counts from the databases, and recent
activity from hub_events. Re-run (or the daily refresh) and it's current.
"""
import os
import subprocess
import sqlite3
import shutil
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, hub_header, hub_footer, WHOAMI_JS
from tools import TOOLS

OUT = "/var/www/ops/architecture.html"
HDB = "/root/ops-dashboard/data/hermes.db"
SDB = "/root/ops-dashboard/data/suppliers.db"

# (systemd unit, port, one-line role)
SERVICES = [
    ("nginx", "443", "TLS reverse proxy — the only public surface"),
    ("oauth2-proxy", "4180", "Google SSO gate (allowlist = who can sign in)"),
    ("ops-agent-chat", "8901", "The agent + every Shopify/theme/content/FS endpoint"),
    ("drop", "8903", "Labs Drop — uploads, thumbnails, share links, search"),
    ("ops-auth", "8899", "Legacy login (now redirects to SSO)"),
    ("ops-dashboard-refresh", "8877", "On-demand dashboard rebuild trigger"),
    ("fail2ban", "—", "Bans brute-force SSH scanners"),
]

FLOW = [
    ("Browser", "the only client — no native app"),
    ("nginx :443", "Let's Encrypt TLS; routes /ops, /drop, /s, /oauth2"),
    ("oauth2-proxy", "checks the Google session against the allowlist"),
    ("Tool access gate", "restricted roles bounced to their own landing page"),
    ("Service (:8901 / :8903)", "runs the request; sets X-User-Email from the session"),
    ("Data + Shopify/Google", "hermes.db, suppliers.db, Drop storage, the live store"),
]


def _svc_active(unit):
    try:
        return subprocess.run(["systemctl", "is-active", unit],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


def _count(conn, sql):
    try:
        return conn.execute(sql).fetchone()[0]
    except Exception:
        return "—"


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1:.1f} {unit}" if False else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def build():
    # services
    svc_rows = ""
    up = 0
    for unit, port, role in SERVICES:
        st = _svc_active(unit)
        ok = st == "active"
        up += 1 if ok else 0
        dot = "good" if ok else "bad"
        svc_rows += (f'<tr><td><span class="dot {dot}"></span>{unit}</td>'
                     f'<td class="num">{port}</td><td>{role}</td>'
                     f'<td><span class="pill {"live" if ok else "off"}">{st}</span></td></tr>')

    # tools inventory from tools.py
    n_tools = 0
    tool_html = ""
    for section, items in TOOLS:
        cells = ""
        for key, name, desc, href, status in items:
            n_tools += 1
            badge = {"live": "Ready", "admin": "Admin", "soon": "Soon",
                     "locked": "Needs access"}.get(status, status)
            cells += (f'<div class="trow"><b>{name}</b>'
                      f'<span class="pill {"live" if status=="live" else "muted"}">{badge}</span>'
                      f'<small>{desc}</small></div>')
        tool_html += f'<div class="mcard"><h3>{section}</h3>{cells}</div>'

    # data stores
    hconn = sqlite3.connect(HDB)
    orders = _count(hconn, "SELECT COUNT(*) FROM orders")
    products = _count(hconn, "SELECT COUNT(*) FROM products")
    facts = _count(hconn, "SELECT COUNT(*) FROM memory_facts")
    events_n = _count(hconn, "SELECT COUNT(*) FROM hub_events")
    recent = []
    try:
        recent = hconn.execute("SELECT created_at, app, kind, actor, detail FROM hub_events "
                               "ORDER BY id DESC LIMIT 15").fetchall()
    except sqlite3.OperationalError:
        pass
    hconn.close()
    sconn = sqlite3.connect(SDB)
    invoices = _count(sconn, "SELECT COUNT(*) FROM invoices")
    sconn.close()
    try:
        du = shutil.disk_usage("/srv/timelabs-drop")
        drop_used = sum(os.path.getsize(os.path.join(b, f))
                        for b, _, fs in os.walk("/srv/timelabs-drop") for f in fs) / 1e6
        drop_line = f"{drop_used:.0f} MB used · {du.free/1e9:.0f} GB free"
    except Exception:
        drop_line = "—"

    data_rows = "".join(
        f'<tr><td>{label}</td><td class="num">{val}</td><td>{note}</td></tr>'
        for label, val, note in [
            ("hermes.db · orders", orders, "form + WhatsApp logged (storefront lives in Shopify)"),
            ("hermes.db · products", products, "catalogue mirror"),
            ("hermes.db · memory_facts", facts, "what the agent knows"),
            ("hermes.db · hub_events", events_n, "the activity feed"),
            ("suppliers.db · invoices", invoices, "landed cost source"),
            ("Drop storage", drop_line, "/srv/timelabs-drop"),
        ])

    flow_html = "".join(
        f'<div class="fnode"><b>{i+1}. {name}</b><small>{note}</small></div>'
        + ('<div class="farrow">&#8595;</div>' if i < len(FLOW) - 1 else '')
        for i, (name, note) in enumerate(FLOW))

    act_html = "".join(
        f'<div class="act"><span class="f-meta num">{(r[0] or "")[:16]}</span>'
        f'<span class="pill muted">{r[1]}</span><b>{r[2]}</b>'
        f'<span class="adet">{(r[4] or "")[:60]}</span></div>'
        for r in recent) or '<p class="empty">No activity yet.</p>'

    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>System map — Labs OS</title>
<style>{HUB_STYLE}
  .kpirow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:18px;}}
  .kpi2{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow);}}
  .kpi2 .v{{font-size:24px;font-weight:750;color:var(--ink);}}
  .kpi2 .l{{font-size:12px;color:var(--muted);}}
  section h2{{font-size:15px;margin:22px 0 10px;}}
  .panel2{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden;}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px;}}
  th,td{{text-align:left;padding:9px 13px;border-bottom:1px solid var(--border);}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);}}
  tr:last-child td{{border-bottom:none;}} td.num{{font-variant-numeric:tabular-nums;color:var(--muted);}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;}}
  .dot.good{{background:var(--good);}} .dot.bad{{background:var(--bad);}}
  .pill{{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;padding:2px 7px;border-radius:5px;}}
  .pill.live{{color:var(--good);background:var(--good-bg);}} .pill.off{{color:var(--bad);background:var(--bad-bg);}}
  .pill.muted{{color:var(--muted);background:var(--card-2);}}
  .flow{{display:flex;flex-direction:column;align-items:center;gap:2px;}}
  .fnode{{background:var(--card);border:1px solid var(--border);border-radius:var(--r-s);padding:10px 15px;
    box-shadow:var(--shadow);text-align:center;max-width:460px;width:100%;}}
  .fnode b{{font-size:13.5px;color:var(--ink);}} .fnode small{{display:block;font-size:12px;color:var(--muted);margin-top:2px;}}
  .farrow{{color:var(--muted);font-size:15px;line-height:1.4;}}
  .mgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;}}
  .mcard{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow);}}
  .mcard h3{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 10px;}}
  .trow{{display:grid;grid-template-columns:1fr auto;gap:4px 8px;padding:7px 0;border-bottom:1px solid var(--border);}}
  .trow:last-child{{border-bottom:none;}} .trow b{{font-size:13.5px;color:var(--ink);}}
  .trow small{{grid-column:1/-1;font-size:12px;color:var(--muted);line-height:1.4;}}
  .act{{display:flex;align-items:center;gap:9px;padding:8px 13px;border-bottom:1px solid var(--border);font-size:13px;flex-wrap:wrap;}}
  .act:last-child{{border-bottom:none;}} .act b{{color:var(--ink);}} .adet{{color:var(--muted);flex:1;min-width:120px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">System map</h1>
      <p class="page-sub">How Labs OS fits together — read live at build time, so it stays current. Refreshed {generated}.</p>
    </div>
    <div class="kpirow">
      <div class="kpi2"><div class="v">{up}/{len(SERVICES)}</div><div class="l">services up</div></div>
      <div class="kpi2"><div class="v">{n_tools}</div><div class="l">tools</div></div>
      <div class="kpi2"><div class="v">{orders}</div><div class="l">logged orders</div></div>
      <div class="kpi2"><div class="v">{facts}</div><div class="l">agent memories</div></div>
    </div>
    <section><h2>Request flow</h2><div class="flow">{flow_html}</div></section>
    <section><h2>Services</h2><div class="panel2"><table>
      <thead><tr><th>Service</th><th class="num">Port</th><th>Role</th><th>Status</th></tr></thead>
      <tbody>{svc_rows}</tbody></table></div></section>
    <section><h2>Tools</h2><div class="mgrid">{tool_html}</div></section>
    <section><h2>Data stores</h2><div class="panel2"><table>
      <thead><tr><th>Store</th><th class="num">Count</th><th>What</th></tr></thead>
      <tbody>{data_rows}</tbody></table></div></section>
    <section><h2>Recent activity</h2><div class="panel2">{act_html}</div></section>
    {hub_footer()}
  </main>
</div>
<script>{WHOAMI_JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT} ({up}/{len(SERVICES)} services up, {n_tools} tools)")


if __name__ == "__main__":
    build()
