#!/usr/bin/env python3
"""Tools — Hub's "view all" launcher. Renders /var/www/ops/tools.html.

Quick-nav holds the daily few; this page holds everything, including tools
still being built (shown as In progress). Add a tool = add a row to TOOLS.
Served by the existing auth-gated /ops location — no nginx change.
"""
import html
import os
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/tools.html"

IC = {
    "face": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 14l3-3 2 2 3-4"/>',
    "drop": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>',
    "ledger": '<path d="M5 3h14v18H5z"/><path d="M9 7h6M9 11h6M9 15h4"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5z"/>',
    "key": '<circle cx="8" cy="14" r="4"/><path d="M11 11l8-8M17 4l3 3M14 7l2 2"/>',
    "blog": '<path d="M4 4h16v16H4z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "theme": '<path d="M3 9l9-6 9 6v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 9h18"/>',
    "price": '<path d="M20 12l-8 8-8-8V4h8z"/><circle cx="8.5" cy="7.5" r="1.5"/>',
    "product": '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 12l9-5M12 12v10M12 12L3 7"/>',
    "content": '<path d="M17 3l4 4L8 20l-5 1 1-5z"/><path d="M14 6l4 4"/>',
    "map": '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/>',
    "files": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "access": '<path d="M16 11a4 4 0 1 0-8 0"/><circle cx="12" cy="7" r="3"/><path d="M4 21v-1a6 6 0 0 1 6-6h1"/><rect x="14" y="14" width="7" height="6" rx="1.5"/><path d="M16 14v-2a2 2 0 0 1 4 0v2"/>',
    "post": '<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h4"/><path d="M15 3l3 3"/>',
    "orders": '<path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/>',
    "supplier": '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    "reddit": '<circle cx="12" cy="13" r="7"/><circle cx="8.5" cy="13" r="1"/><circle cx="15.5" cy="13" r="1"/><path d="M8.5 16.5c1 .8 2.2 1.2 3.5 1.2s2.5-.4 3.5-1.2"/><path d="M12 6V3M12 3l2.2 1"/>',
}

# (key, name, description, href-or-None, status)  status: "live" | "soon" | "admin"
TOOLS = [
    ("Daily", [
        ("face", "Home", "The live dashboard — sales, funnel, plan, findings.", "/ops/#overview", "live"),
        ("drop", "Drop", "Files &amp; product video — upload, share, organize.", "/drop/", "live"),
        ("ledger", "Ledger", "Costs &amp; margins from your supplier invoices.", "/ops/ledger.html", "live"),
        ("chat", "Command", "Run the business with Hermes — conversations, context and controlled actions.", "/ops/command.html", "live"),
        ("orders", "Order form", "Log an order in seconds — with a photo; syncs to Orders &amp; Customers.", "/ops/order-form.html", "live"),
    ]),
    ("Content &amp; growth", [
        ("blog", "Blog builder", "Your 27 queued topics → the content plan.", "/ops/blog.html", "live"),
        ("post", "Blog uploader", "Write a post and publish it to the store.", "/ops/blog-uploader.html", "live"),
        ("content", "Content updater", "Bulk-refresh product copy &amp; edit store pages in your voice.", "/ops/content-updater.html", "live"),
        ("reddit", "Reddit listener", "Surfaces watch-hobbyist threads worth a genuine reply — read-only, nothing auto-posts.", "/ops/reddit.html", "live"),
    ]),
    ("Shopify", [
        ("theme", "Theme editor", "Describe a look — Claude restyles your storefront, you approve it.", "/ops/theme-editor.html", "live"),
        ("price", "Quick product updater", "Change prices &amp; status fast, in bulk.", "/ops/product-updater.html", "live"),
        ("product", "Product builder", "Spin up a new product from parts, photos &amp; a spec.", "/ops/product-builder.html", "live"),
    ]),
    ("Admin", [
        ("access", "People &amp; access", "Invite people, set roles, remove — who can sign in and what they can do.", "/ops/access.html", "admin"),
        ("files", "System files", "Browse the Hermes server files — read-only, hidden-file toggle.", "/ops/files.html", "admin"),
        ("map", "System map", "How the whole OS fits together — services, health, roadmap.", "/ops/architecture.html", "admin"),
        ("supplier", "Builds", "Production queue for specs, references, stages and supplier batches. No customer details or selling price.", "/ops/supplier.html", "admin"),
    ]),
]


def _service_active(unit):
    try:
        return subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True,
            timeout=5).stdout.strip() == "active"
    except Exception:
        return False


def _db_ok(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        conn.close()
        return ok
    except sqlite3.Error:
        return False


def _availability(key, declared):
    if declared == "admin":
        return "admin"
    if key in {"theme", "price", "product", "content", "post"}:
        try:
            import shopify_api
            return "live" if shopify_api.configured() else "setup"
        except Exception:
            return "degraded"
    if key == "chat":
        return ("live" if shutil.which("hermes") and _service_active("ops-agent-chat")
                else "degraded")
    if key == "drop":
        return "live" if _service_active("drop") else "degraded"
    if key == "ledger":
        return ("live" if _db_ok("/root/ops-dashboard/data/suppliers.db")
                else "degraded")
    if key == "orders":
        return ("live" if _db_ok("/root/ops-dashboard/data/hermes.db")
                else "degraded")
    if key == "reddit":
        return "live" if _service_active("ops-agent-chat") else "degraded"
    return declared


def build():
    groups = ""
    for section, tools in TOOLS:
        cards = ""
        for key, name, desc, href, status in tools:
            status = _availability(key, status)
            permission = "blog" if key == "post" else key
            badge = {
                "live": '<span class="t-badge live">Ready</span>',
                "soon": '<span class="t-badge soon">In progress</span>',
                "admin": '<span class="t-badge admin">Admin</span>',
                "locked": '<span class="t-badge locked">Needs access</span>',
                "setup": '<span class="t-badge setup">Needs setup</span>',
                "degraded": '<span class="t-badge degraded">Degraded</span>',
            }[status]
            icon = f'<svg viewBox="0 0 24 24" class="t-ic">{IC[key]}</svg>'
            inner = (f'<div class="t-top">{icon}{badge}</div>'
                     f'<div class="t-nm">{name}</div><div class="t-d">{desc}</div>')
            if href:
                cls = "tcard admin-only" if status == "admin" else "tcard"
                cards += (
                    f'<a class="{cls}" data-tool="{permission}" href="{href}">{inner}</a>')
            else:
                cards += f'<div class="tcard soon" data-tool="{permission}">{inner}</div>'
        groups += f'<section><h2>{section}</h2><div class="tgrid">{cards}</div></section>'

    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Tools — Labs OS</title>
<style>{HUB_STYLE}
  .tgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;}}
  .tcard{{display:flex;flex-direction:column;gap:7px;background:var(--card);border:1px solid var(--border);
    border-radius:var(--r);padding:16px;box-shadow:var(--shadow);text-decoration:none;
    transition:transform .15s var(--ease),border-color .15s,box-shadow .15s;}}
  a.tcard:hover{{transform:translateY(-2px);border-color:var(--border-2);box-shadow:var(--shadow-lg);text-decoration:none;}}
  a.tcard:active{{transform:scale(.98);}}
  .tcard.soon{{opacity:.72;}}
  .t-top{{display:flex;align-items:center;justify-content:space-between;}}
  .t-ic{{width:26px;height:26px;stroke:var(--accent);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;}}
  .t-nm{{font-size:15.5px;font-weight:700;color:var(--ink);letter-spacing:-.01em;}}
  .t-d{{font-size:13px;color:var(--muted);line-height:1.45;}}
  .t-badge{{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:6px;}}
  .t-badge.live{{color:var(--good);background:var(--good-bg);}}
  .t-badge.soon{{color:var(--accent);background:var(--accent-bg);}}
  .t-badge.admin{{color:var(--muted);background:var(--card-2);}}
  .t-badge.locked{{color:var(--accent);background:var(--accent-bg);}}
  .t-badge.setup{{color:var(--accent);background:var(--accent-bg);}}
  .t-badge.degraded{{color:var(--crit);background:var(--crit-bg);}}
  .tsearch{{width:100%;max-width:420px;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--card);color:var(--ink);padding:10px 13px;margin-bottom:6px;}}
  .tsearch:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .tempty{{color:var(--muted);font-size:14px;padding:14px 2px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">All tools</h1>
      <p class="page-sub">Everything in Labs OS. Your daily tools are pinned in the nav bar; the rest live here.</p>
    </div>
    <input id="tsearch" class="tsearch" type="search" placeholder="Search tools…" aria-label="Search tools" autocomplete="off">
    {groups}
    <p class="tempty" id="tempty" hidden>No tools match.</p>
    {hub_footer()}
  </main>
</div>
<script>
(function(){{
  var q=document.getElementById('tsearch'); if(!q) return;
  var cards=[].slice.call(document.querySelectorAll('.tcard'));
  var secs=[].slice.call(document.querySelectorAll('main section'));
  var empty=document.getElementById('tempty');
  q.addEventListener('input', function(){{
    var t=q.value.trim().toLowerCase(), any=false;
    cards.forEach(function(c){{
      if(c.hidden)return;
      var hit=!t||c.textContent.toLowerCase().indexOf(t)>=0;
      c.style.display=hit?'':'none'; if(hit) any=true;
    }});
    secs.forEach(function(s){{
      var vis=s.querySelectorAll('.tcard:not([hidden]):not([style*="none"])').length;
      s.style.display=vis?'':'none';
    }});
    if(empty) empty.hidden=any;
  }});
  q.addEventListener('keydown', function(e){{
    if(e.key==='Enter'){{ var first=cards.filter(function(c){{return !c.hidden&&c.style.display!=='none'&&c.tagName==='A';}})[0]; if(first) location.href=first.getAttribute('href'); }}
  }});
}})();
{WHOAMI_JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
