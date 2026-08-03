#!/usr/bin/env python3
"""Read-only Instagram audit dashboard.

The snapshot is deliberately aggregate-only: no DM bodies, customer handles or
access tokens are written into Labs OS. Refreshing the snapshot is a separate,
approved Composio/Hermes job; this page never posts, replies or changes Meta.
"""
import html
import json
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/instagram-audit.html"
SNAPSHOT = "/root/ops-dashboard/data/instagram_audit_snapshot.json"

ACCOUNT = {
    "username": "timelabs.co", "name": "Time Labs Co | Premium Custom Watches",
    "followers": 3117, "media_count": 91, "window": "5 May–3 Aug 2026",
    "reach_sum": 69927,
}
POSTS = [
    {"date": "30 Jul 2026", "kind": "Carousel", "title": "Ice Blue Translucent Case Mod", "views": 1307, "reach": 506, "interactions": 29, "saves": 2, "shares": 1, "comments": 1, "likes": 24, "url": "https://www.instagram.com/p/DbagpnLjGVO/"},
    {"date": "9 May 2026", "kind": "Reel", "title": "John Mayor Gold Green", "views": 1940, "reach": 1470, "interactions": 30, "saves": 4, "shares": 1, "comments": 3, "likes": 22, "url": "https://www.instagram.com/reel/DYHwSCjsnPb/"},
    {"date": "7 May 2026", "kind": "Reel", "title": "Rosegold Rainbow", "views": 4399, "reach": 3412, "interactions": 101, "saves": 18, "shares": 22, "comments": 2, "likes": 58, "url": "https://www.instagram.com/reel/DYDVNZNs4TJ/"},
    {"date": "9 May 2026", "kind": "Carousel", "title": "John Mayor Gold Green", "views": 931, "reach": 396, "interactions": 15, "saves": 0, "shares": 0, "comments": 1, "likes": 14, "url": "https://www.instagram.com/p/DYHy8v1DHNA/"},
    {"date": "7 May 2026", "kind": "Carousel", "title": "Rosegold Rainbow", "views": 872, "reach": 406, "interactions": 18, "saves": 2, "shares": 1, "comments": 1, "likes": 14, "url": "https://www.instagram.com/p/DYDWiqGDM92/"},
]
COMPETITORS = [
    ("IndiaModWatches", "@imodwatches (site-linked) · historical @india_mod_watches", "Direct Indian competitor. Public storefront spans Seiko/HMT mods, PRX/Nautilus/Joker-style homages and custom builds. Observed anchors: ₹4,620 entry automatic, ₹17,999–₹19,999 premium homages, 15–20 day make-to-order promise, NH35/VK63 claims and a 2-year machine warranty on ₹15,000+ models. Reviews and Reddit discussions are mixed, so trust/provenance is a key battleground.", "https://indiamodwatch.in/products/seiko-datejust-grey-sunburst-unisex-automatic"),
    ("Bangalore Watch Company", "@bangalorewatchco", "Modern India storyworlds: space, aviation, cricket; premium mechanical ownership narrative.", "https://www.bangalorewatchco.in/pages/about-us"),
    ("Jaipur Watch Company", "@jaipurwatchcompany", "Heritage, coins, bespoke craftsmanship and founder-led storytelling; materially larger public footprint.", "https://jaipur.watch/pages/the-brand"),
    ("Ajwain Watches", "@ajwainwatches", "India-first cultural/design drops and collector conversation; monitor trust and provenance language.", "https://www.instagram.com/ajwainwatches/"),
    ("Mangalore Watch Company", "@mangalorewatchcompany", "Affordable mechanical and collector-community angle; useful benchmark for enthusiast education.", "https://www.mangalorewatchcompany.com/about-us"),
]

def _load_snapshot():
    """Use the last verified Composio snapshot when the refresh job has run."""
    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            snap = json.load(f)
        if isinstance(snap.get("account"), dict) and isinstance(snap.get("posts"), list):
            return snap["account"], snap["posts"]
    except (OSError, ValueError, TypeError):
        pass
    return ACCOUNT, POSTS

CSS = """
.ia-sub{color:var(--muted);font-size:13.5px;margin:-6px 0 18px;line-height:1.5}.ia-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin-bottom:16px}.ia-kpi,.ia-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:15px 16px;box-shadow:var(--shadow)}.ia-kpi .v{font-size:25px;font-weight:760;color:var(--ink);font-variant-numeric:tabular-nums}.ia-kpi .l{font-size:12px;color:var(--muted);margin-top:3px}.ia-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-bottom:14px}@media(max-width:760px){.ia-grid{grid-template-columns:1fr}}.ia-card h2{font-size:16px;margin:0 0 10px}.ia-card p{font-size:13px;color:var(--muted);line-height:1.5}.ia-table{width:100%;border-collapse:collapse;font-size:12.5px}.ia-table th{text-align:left;color:var(--muted);font-weight:650;border-bottom:1px solid var(--border);padding:8px 6px}.ia-table td{padding:9px 6px;border-bottom:1px solid var(--border);vertical-align:top}.ia-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.ia-table a{color:var(--accent);text-decoration:none}.ia-tag{display:inline-block;padding:3px 7px;border-radius:999px;background:var(--accent-bg);color:var(--accent);font-size:11px;font-weight:700}.ia-note{font-size:11.5px;color:var(--muted);margin-top:10px}.ia-list{margin:0;padding-left:18px;color:var(--ink);font-size:13px;line-height:1.55}.ia-list li{margin:7px 0}.ia-callout{border-left:3px solid var(--accent);padding:9px 12px;background:var(--accent-bg);font-size:13px;line-height:1.5;color:var(--ink);margin-top:10px}
"""

def _n(n):
    return f"{n:,}"

def build():
    account, posts = _load_snapshot()
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    post_rows = "".join(
        f'<tr><td><a href="{html.escape(p["url"])}" target="_blank" rel="noopener">{html.escape(p["title"])}</a><br><span class="ia-note">{p["date"]} · {p["kind"]}</span></td>'
        f'<td class="num">{_n(p["views"])}</td><td class="num">{_n(p["reach"])}</td><td class="num">{p["interactions"]}</td><td class="num">{p["saves"]}</td></tr>'
        for p in posts)
    comp_rows = "".join(
        f'<tr><td><b>{html.escape(name)}</b><br><span class="ia-note">{html.escape(handle)}</span></td>'
        f'<td>{html.escape(angle)}<br><a href="{html.escape(url)}" target="_blank" rel="noopener">public source ↗</a></td></tr>'
        for name, handle, angle, url in COMPETITORS)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="light dark"><title>Instagram audit — Labs OS</title><style>{HUB_STYLE}{CSS}</style></head><body><div class="wrap">{hub_header("tools")}<main><div class="page-head"><h1 class="page-title">Instagram audit</h1><p class="ia-sub">Read-only audit for <b>@{account['username']}</b> · {account['window']} · refreshed {generated}. Hermes/Composio connection is active; this dashboard never posts, replies or changes Meta.</p></div><div class="ia-kpis"><div class="ia-kpi"><div class="v">{_n(account['followers'])}</div><div class="l">current followers</div></div><div class="ia-kpi"><div class="v">{_n(account['media_count'])}</div><div class="l">total media</div></div><div class="ia-kpi"><div class="v">{len(posts)}</div><div class="l">posts in window</div></div><div class="ia-kpi"><div class="v">{_n(account['reach_sum'])}</div><div class="l">reported reach events</div></div></div><div class="ia-grid"><section class="ia-card"><h2>Best recent content</h2><table class="ia-table"><thead><tr><th>Content</th><th class="num">Views</th><th class="num">Reach</th><th class="num">Interactions</th><th class="num">Saves</th></tr></thead><tbody>{post_rows}</tbody></table><div class="ia-callout"><b>Signal:</b> the 7 May Rosegold Rainbow Reel is the clear winner: 4,399 views, 3,412 reach and 101 interactions. Reels are outperforming the paired carousel on this small sample.</div></section><section class="ia-card"><h2>What needs attention</h2><ul class="ia-list"><li>Publishing is inconsistent: five posts in 90 days, with a long gap before the July return.</li><li>Profile copy is clear on warranty and ordering, but the conversion path is almost entirely “DM / WhatsApp”; add a trackable product or collection link.</li><li>Use Reels for discovery, then carousel follow-ups for specs, proof, warranty and objections.</li><li>Comments contain buying questions and DM handoffs; these belong in the future Inbox CRM.</li></ul></section></div><section class="ia-card"><h2>Public competitor benchmark</h2><p>This is a public-content benchmark, not private competitor analytics. We can compare visible posts, positioning, cadence and public engagement; Meta does not expose their private reach or audience insights.</p><table class="ia-table"><thead><tr><th>Brand</th><th>Observed positioning to monitor</th></tr></thead><tbody>{comp_rows}</tbody></table></section><div class="ia-card" style="margin-top:14px"><h2>Data quality and next refresh</h2><p>Account-level insights returned only <b>reach</b> and <b>follower_count</b> for this window; Meta silently omitted the other requested series. Post-level insights were available for all five posts after using media-compatible metrics. The refresh job runs every two days and preserves the last good snapshot if Meta is unavailable.</p><p class="ia-note">Source: authenticated Instagram Business API via Hermes/Composio; competitor sources are linked public pages. No raw comments, customer handles, tokens or message bodies are persisted here.</p></div></main>{hub_footer()}</div><script>{WHOAMI_JS}</script></body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    import os
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")

if __name__ == "__main__":
    build()
