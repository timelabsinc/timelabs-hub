#!/usr/bin/env python3
"""Blog builder — Hub's content engine view. Renders /var/www/ops/blog.html.

Surfaces the content_queue (drafted + proposed topics) so the 27-topic plan
is visible and actionable instead of buried in a table. Drafts link into
Shopify. Generating/publishing is wired to the Blog Studio (/opt/shopify-blog-system)
and improves over time; this page is the front door to it.
"""
import html
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

DB = "/root/ops-dashboard/data/hermes.db"
OUT = "/var/www/ops/blog.html"
STORE = "xd2fwj-1h"
ARTICLES_URL = f"https://admin.shopify.com/store/{STORE}/content/articles"


def token_ready():
    try:
        c = json.load(open("/opt/shopify-blog-system/config.json"))
        return str(c.get("SHOPIFY_ADMIN_TOKEN", "")).startswith("shpat")
    except Exception:
        return False


def build():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT n, pillar, title, primary_keyword, funnel, status, shopify_article_id "
        "FROM content_queue ORDER BY status DESC, priority, n"
    ).fetchall()
    conn.close()

    drafted = [r for r in rows if r[5] == "drafted"]
    proposed = [r for r in rows if r[5] != "drafted"]

    def card(r):
        n, pillar, title, kw, funnel, status, art = r
        chip = ('<span class="b-chip draft">drafted</span>' if status == "drafted"
                else '<span class="b-chip prop">queued</span>')
        link = (f'<a class="b-open" href="{ARTICLES_URL}" target="_blank" rel="noopener">Open in Shopify ↗</a>'
                if art else "")
        return (f'<div class="bcard"><div class="b-top">'
                f'<span class="b-pillar">{html.escape(pillar or "—")}</span>{chip}</div>'
                f'<div class="b-title">{html.escape(title)}</div>'
                f'<div class="b-kw">{html.escape(kw or "")}'
                f'{" · " + html.escape(funnel) if funnel else ""}</div>{link}</div>')

    ready = token_ready()
    setup = (
        '<div class="setup ok">Blog Studio connected — drafts can publish to the store.</div>'
        if ready else
        '<div class="setup">One step to go: the Blog Studio needs a real Shopify Admin token to publish '
        '(it has a placeholder now). Drafts are still written and reviewed here until then.</div>'
    )
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Blog builder — Labs OS</title>
<style>{HUB_STYLE}
  .setup{{background:var(--accent-bg);border:1px solid transparent;border-radius:var(--r);padding:12px 15px;
    font-size:13.5px;color:var(--ink);margin-bottom:20px;}}
  .setup.ok{{background:var(--good-bg);}}
  .bgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;}}
  .bcard{{display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--border);
    border-radius:var(--r);padding:15px;box-shadow:var(--shadow);}}
  .b-top{{display:flex;align-items:center;justify-content:space-between;gap:8px;}}
  .b-pillar{{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--accent);}}
  .b-title{{font-size:14.5px;font-weight:650;color:var(--ink);line-height:1.35;}}
  .b-kw{{font-size:12px;color:var(--muted);}}
  .b-open{{font-size:12.5px;font-weight:600;color:var(--accent);margin-top:4px;}}
  .b-chip{{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;padding:2px 8px;border-radius:6px;}}
  .b-chip.draft{{color:var(--good);background:var(--good-bg);}}
  .b-chip.prop{{color:var(--muted);background:var(--card-2);}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Blog builder</h1>
      <p class="page-sub">Your content plan, made to win AI-search citations for Seiko-modding in India — {len(drafted)} drafted, {len(proposed)} queued.</p>
    </div>
    {setup}
    <section><h2>Drafted — ready to review</h2>
      <div class="bgrid">{''.join(card(r) for r in drafted) or '<p class="empty">No drafts yet.</p>'}</div></section>
    <section><h2>Queued — {len(proposed)} topics</h2>
      <div class="bgrid">{''.join(card(r) for r in proposed)}</div></section>
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
    print(f"wrote {OUT} ({len(drafted)} drafted, {len(proposed)} queued)")


if __name__ == "__main__":
    build()
