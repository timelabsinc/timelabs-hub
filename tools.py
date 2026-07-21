#!/usr/bin/env python3
"""Tools — Hub's "view all" launcher. Renders /var/www/ops/tools.html.

Quick-nav holds the daily few; this page holds everything, including tools
still being built (shown as In progress). Add a tool = add a row to TOOLS.
Served by the existing auth-gated /ops location — no nginx change.
"""
import html
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav

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
}

# (key, name, description, href-or-None, status)  status: "live" | "soon" | "admin"
TOOLS = [
    ("Daily", [
        ("face", "Face", "The live dashboard — sales, funnel, plan, findings.", "/ops/#overview", "live"),
        ("drop", "Drop", "Files &amp; product video — upload, share, organize.", "/drop/", "live"),
        ("ledger", "Ledger", "Costs &amp; margins from your supplier invoices.", "/ops/ledger.html", "live"),
        ("chat", "Chat", "Ask the team (Hermes / Claude) with photos.", "/ops/agent/", "live"),
    ]),
    ("Content &amp; growth", [
        ("blog", "Blog builder", "Your 27 queued topics → SEO drafts for the store.", "/ops/blog.html", "live"),
        ("content", "Content updater", "Bulk-refresh product copy &amp; store pages in your voice.", None, "soon"),
    ]),
    ("Shopify", [
        ("theme", "Theme editor", "Tune the storefront look without touching Shopify's editor.", None, "soon"),
        ("price", "Quick product updater", "Change prices, stock &amp; status fast, in bulk.", None, "soon"),
        ("product", "Product builder", "Spin up a new product from parts, photos &amp; a spec.", None, "soon"),
    ]),
    ("Admin", [
        ("key", "Key", "Who can sign in to Hub — invite &amp; remove people.", "/ops/#key", "admin"),
        ("map", "System map", "How the whole OS fits together — services, health, roadmap.", "/ops/architecture.html", "admin"),
    ]),
]


def build():
    groups = ""
    for section, tools in TOOLS:
        cards = ""
        for key, name, desc, href, status in tools:
            badge = {
                "live": '<span class="t-badge live">Ready</span>',
                "soon": '<span class="t-badge soon">In progress</span>',
                "admin": '<span class="t-badge admin">Admin</span>',
            }[status]
            icon = f'<svg viewBox="0 0 24 24" class="t-ic">{IC[key]}</svg>'
            inner = (f'<div class="t-top">{icon}{badge}</div>'
                     f'<div class="t-nm">{name}</div><div class="t-d">{desc}</div>')
            if href:
                cls = "tcard admin-only" if status == "admin" else "tcard"
                cards += f'<a class="{cls}" href="{href}">{inner}</a>'
            else:
                cards += f'<div class="tcard soon">{inner}</div>'
        groups += f'<section><h2>{section}</h2><div class="tgrid">{cards}</div></section>'

    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Tools — Timelabs Hub</title>
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
</style></head>
<body>
<div class="wrap">
  <header class="topbar">
    <a class="brand" href="/ops/#overview"><span class="brand-dot">T</span>
    <span class="brand-name">Timelabs <span>Tools</span></span></a>
  </header>
  {_appnav(active="tools", drop_ready=True)}
  <main>
    <div class="page-head">
      <h1 class="page-title">All tools</h1>
      <p class="page-sub">Everything in Hub. The ones you use daily are pinned in the bar above; the rest live here — including what we're still building.</p>
    </div>
    {groups}
    <footer><span>Timelabs Hub · Tools</span><span>refreshed {generated}</span></footer>
  </main>
</div>
<script>
  // reveal admin-only tiles (Key) for admins
  fetch('/ops/agent/api/whoami').then(function(r){{return r.ok?r.json():null;}}).then(function(i){{
    if(i&&i.admin) document.body.classList.add('is-admin');
  }}).catch(function(){{}});
</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
