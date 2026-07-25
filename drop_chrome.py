#!/usr/bin/env python3
"""Keeps Drop's header and app nav in sync with every other page.

Drop is a hand-written single-page app, so its top bar and nav were a frozen
copy of what hub_shell.py produced on the day it was written. Copies drift:
by the time this was added, Drop's nav still offered "Ledger" while every
generated page had replaced that item with "Orders", and Drop had no who-chip
at all. Someone signed in as one account had no way to tell from Drop, and a
nav item pointed somewhere the rest of the OS no longer considered primary.

Rather than rewrite the SPA as a generator (a large change to a working
60KB app for a small problem), this rewrites just the chrome — the region
between the markers below — from the same hub_shell functions everything
else uses. Drop keeps its own toolbar buttons via hub_header's `actions`
argument, so nothing app-specific is lost.

Run by generate.py's refresh loop, so the chrome re-syncs on every rebuild
and cannot silently drift again. Idempotent: it replaces the marked region
with a freshly generated one, so running it twice does nothing the once
didn't.
"""
import os
import re
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import hub_header

TARGET = "/var/www/drop/index.html"
BEGIN = "<!--labs:chrome-->"
END = "<!--/labs:chrome-->"

# Drop carries its own stylesheet rather than HUB_STYLE, so the shared
# touch-input floor has to be injected here too. Same reason as in
# hub_shell.py: Drop's search box is 14px, and iOS zooms the page on focus
# for anything under 16 and never zooms back — which is exactly what tapping
# Drop's search button did.
MOBILE_FIX = """<style id="labs-mobile-fix">
@media (pointer:coarse){
  input:not([type=button]):not([type=submit]):not([type=reset]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]),
  textarea, select{font-size:16px !important;}
}
/* The upload FAB is the ONLY upload trigger on a phone (the toolbar Upload
   button is desktop-only). Its correct rule places it above the 60px bottom
   nav bar, but a later unscoped `.fab{bottom:26px}` overrides that and drops
   it behind the nav bar, where it's half-hidden and awkward to tap — the
   "photo selector has UI issues on phone" report. Restore the nav-aware
   position on touch layouts; injected last so it wins. */
@media (max-width:759px){
  .fab{bottom:calc(var(--nav-h,60px) + env(safe-area-inset-bottom,0px) + 18px) !important;}
}
/* The header markup above is generated from hub_shell, but Drop carries its
   own stylesheet instead of HUB_STYLE, so any class the shared header relies
   on has to be defined here too. .top-actions and .who were never in Drop's
   CSS at all: the container holding search, view, New folder and Upload was
   an unstyled block, so the buttons fell out of the top bar and stacked
   loose above the nav. Syncing the markup without the CSS it depends on is
   the failure mode this whole file exists to prevent, so they live with the
   sync rather than in Drop's stylesheet where the next edit could miss them. */
.topbar .top-actions{display:flex;align-items:center;gap:8px;margin-left:auto;
  flex-wrap:nowrap;}
.topbar .who{font-size:12px;color:var(--muted);max-width:150px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
@media (max-width:759px){ .topbar .who{display:none;} }
</style>"""

# Drop's own toolbar controls. The SPA's JS binds to these ids, so they must
# survive the header being regenerated — that's what hub_header(actions=...)
# is for.
DROP_ACTIONS = (
    '<button class="iconbtn" id="searchBtn" title="Search" aria-label="Search">'
    '<svg class="ic" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/>'
    '<path d="M20 20l-3.5-3.5"/></svg></button>'
    '<button class="iconbtn" id="viewBtn" title="Toggle view" aria-label="Toggle grid or list view">'
    '<svg class="ic" id="viewIcGrid" viewBox="0 0 24 24">'
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'
    '</svg><svg class="ic" id="viewIcList" viewBox="0 0 24 24" style="display:none">'
    '<path d="M4 6h16M4 12h16M4 18h16"/></svg></button>'
    '<button class="btn desktop-only" id="newBtn">'
    '<svg class="ic" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    '<path d="M12 11v6M9 14h6"/></svg>New folder</button>'
    '<button class="btn primary desktop-only" id="upBtn2">'
    '<svg class="ic" viewBox="0 0 24 24"><path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/></svg>'
    'Upload</button>'
)


def chrome():
    return f"{BEGIN}\n  " + hub_header("drop", actions=DROP_ACTIONS) + f"\n{END}"


def build():
    try:
        html = open(TARGET).read()
    except OSError as e:
        print(f"[drop-chrome] {e}", file=sys.stderr)
        return

    new = chrome()
    if BEGIN in html and END in html:
        out = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: new, html, flags=re.S)
    else:
        # First run: adopt the existing hand-written header+nav by replacing it
        # wholesale, then leave markers behind so later runs are a simple swap.
        m = re.search(r'<header class="topbar">.*?</nav>', html, re.S)
        if not m:
            print("[drop-chrome] no header/nav found — left untouched", file=sys.stderr)
            return
        out = html[:m.start()] + new + html[m.end():]

    # Replace an existing block so edits to MOBILE_FIX actually take effect on
    # a redeploy, not just on first injection.
    if 'id="labs-mobile-fix"' in out:
        out = re.sub(r'<style id="labs-mobile-fix">.*?</style>', lambda _: MOBILE_FIX,
                     out, count=1, flags=re.S)
    else:
        out = out.replace("</head>", MOBILE_FIX + "\n</head>", 1)

    if out == html:
        print("chrome already current")
        return
    tmp = TARGET + ".tmp"
    with open(tmp, "w") as f:
        f.write(out)
    os.replace(tmp, TARGET)
    os.system(f"chown www-data:www-data {TARGET}")
    print(f"synced chrome into {TARGET}")


if __name__ == "__main__":
    build()
