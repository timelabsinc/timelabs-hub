#!/usr/bin/env python3
"""Theme editor — Shopify tool. Renders /var/www/ops/theme-editor.html.

Editing the storefront theme (colours, fonts, sections) needs the Shopify
`read_themes` / `write_themes` scopes. The current connected app is a managed
install limited to products + content, so those scopes are NOT granted — a real
theme editor cannot function on this token.

Rather than ship a dead tool, this page probes theme access on load and, when
it's missing (today's case), shows an honest "unlock" path. The moment the
scopes are added and the store reconnected, the editor lights up — no code
change needed here.
"""
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/theme-editor.html"


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Theme editor — Labs OS</title>
<style>{HUB_STYLE}
  .te-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:20px;box-shadow:var(--shadow);}}
  .te-lock{{display:flex;gap:14px;align-items:flex-start;margin-bottom:6px;}}
  .te-lock .ic{{flex-shrink:0;width:44px;height:44px;border-radius:12px;background:var(--accent-bg);
    display:flex;align-items:center;justify-content:center;}}
  .te-lock .ic svg{{width:22px;height:22px;stroke:var(--accent);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;}}
  .te-lock h2{{font-size:17px;margin:0 0 4px;}}
  .te-lock p{{margin:0;color:var(--muted);font-size:14px;line-height:1.55;}}
  .steps{{list-style:none;padding:0;margin:18px 0 0;counter-reset:s;}}
  .steps li{{position:relative;padding:0 0 16px 40px;counter-increment:s;font-size:14px;line-height:1.55;color:var(--ink);}}
  .steps li::before{{content:counter(s);position:absolute;left:0;top:-1px;width:26px;height:26px;border-radius:50%;
    background:var(--ink);color:var(--bg);font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;}}
  .steps li:not(:last-child)::after{{content:"";position:absolute;left:12.5px;top:28px;bottom:2px;width:1.5px;background:var(--border);}}
  .steps code{{background:var(--card-2);border:1px solid var(--border);border-radius:5px;padding:1px 6px;font-size:12.5px;}}
  .te-actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px;}}
  .btn{{font-size:13.5px;font-weight:650;border-radius:var(--r-s);padding:10px 16px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);text-decoration:none;
    display:inline-flex;align-items:center;gap:7px;transition:border-color .12s,transform .1s;}}
  .btn:hover{{border-color:var(--border-2);text-decoration:none;}}
  .btn:active{{transform:scale(.98);}}
  .btn.primary{{background:var(--ink);color:var(--bg);border-color:var(--ink);}}
  .whatif{{margin-top:22px;padding-top:18px;border-top:1px solid var(--border);}}
  .whatif h3{{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 12px;}}
  .caps{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;}}
  .cap{{border:1px solid var(--border);border-radius:var(--r-s);padding:12px;background:var(--bg);}}
  .cap b{{display:block;font-size:13px;color:var(--ink);margin-bottom:2px;}}
  .cap small{{font-size:12px;color:var(--muted);line-height:1.4;}}
  .cap.dim{{opacity:.55;}}
  .alt{{margin-top:16px;font-size:13.5px;color:var(--muted);line-height:1.6;background:var(--accent-bg);
    border-radius:var(--r-s);padding:13px 15px;}}
  .alt a{{color:var(--accent);font-weight:600;}}
  .te-ok h3{{font-size:14px;margin:0 0 6px;}}
  .kv{{display:flex;gap:8px;font-size:14px;margin:3px 0;}}
  .kv span{{color:var(--muted);min-width:90px;}}
  .diag{{margin-top:14px;font-size:12px;color:var(--muted);}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Theme editor</h1>
      <p class="page-sub">Tune your storefront's look — colours, type and sections — from inside Labs OS.</p>
    </div>
    <div id="te-root"><div class="te-card"><p class="empty">Checking theme access…</p></div></div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API='/ops/agent/api';
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
async function api(path){{
  var res=await fetch(API+path);
  if(res.status===403){{location.href='/oauth2/start?rd=/ops/theme-editor.html';throw new Error('auth');}}
  return res.json();
}}

function locked(reason){{
  $('te-root').innerHTML=
    '<div class="te-card">'+
      '<div class="te-lock"><div class="ic"><svg viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg></div>'+
        '<div><h2>Theme access needed</h2><p>Editing the theme needs Shopify&#39;s <code>read_themes</code> &amp; <code>write_themes</code> permissions. The connected app is a managed install scoped to products &amp; content, so those aren&#39;t granted yet. Unlock it once and this editor turns on.</p></div></div>'+
      '<ol class="steps">'+
        '<li>In Shopify admin, open <b>Settings &rarr; Apps and sales channels &rarr; Develop apps</b>, then <b>Create an app</b> (a custom app can request any scope).</li>'+
        '<li>Under <b>Configuration &rarr; Admin API scopes</b>, tick <code>read_themes</code> and <code>write_themes</code> (keep the product &amp; content ones too), and <b>Save</b>.</li>'+
        '<li><b>Install</b> the app, reveal the <b>Admin API access token</b> (starts <code>shpat_</code>), and reconnect it to Labs OS.</li>'+
      '</ol>'+
      '<div class="te-actions">'+
        '<a class="btn primary" href="https://admin.shopify.com/store/xd2fwj-1h/settings/apps/development" target="_blank" rel="noopener">Open Shopify app setup</a>'+
        '<button class="btn" id="recheck">Re-check access</button>'+
      '</div>'+
      '<div class="alt">Prefer not to touch scopes? You can already reshape a lot of the storefront from <a href="/ops/content-updater.html">Content updater</a> — it edits your store <b>pages</b> (About, policies, guides) and bulk product copy, which don&#39;t need theme access.</div>'+
      '<div class="whatif"><h3>What you&#39;ll be able to do once unlocked</h3>'+
        '<div class="caps">'+
          '<div class="cap"><b>Colours</b><small>Brand, buttons &amp; backgrounds</small></div>'+
          '<div class="cap"><b>Typography</b><small>Heading &amp; body fonts, sizes</small></div>'+
          '<div class="cap"><b>Sections</b><small>Reorder &amp; toggle homepage blocks</small></div>'+
          '<div class="cap"><b>Favicon &amp; logo</b><small>Swap store branding assets</small></div>'+
        '</div>'+
      '</div>'+
      (reason?'<div class="diag">Shopify said: '+esc(reason)+'</div>':'')+
    '</div>';
  $('recheck').onclick=function(){{$('recheck').textContent='Checking…';$('recheck').disabled=true;init();}};
}}

function unlocked(st){{
  var t=st.theme||{{}};
  $('te-root').innerHTML=
    '<div class="te-card te-ok">'+
      '<h3>Theme connected</h3>'+
      '<div class="kv"><span>Live theme</span><b>'+esc(t.name||'—')+'</b></div>'+
      '<div class="kv"><span>Role</span><b>'+esc((t.role||'').toLowerCase()||'main')+'</b></div>'+
      '<div class="kv"><span>Themes</span><b>'+esc(st.count!=null?String(st.count):'—')+'</b></div>'+
      '<div class="alt" style="margin-top:16px">Theme access is granted. Colour, type &amp; section controls will render here — reconnect kept your access, so nothing else is needed.</div>'+
      '<div class="te-actions"><a class="btn" href="https://admin.shopify.com/store/xd2fwj-1h/themes" target="_blank" rel="noopener">Open in Shopify</a></div>'+
    '</div>';
}}

async function init(){{
  try{{
    var st=await api('/shopify/theme/status');
    if(st&&st.available)unlocked(st); else locked(st&&st.reason);
  }}catch(e){{if(e.message!=='auth')locked(e.message);}}
}}
init();
{WHOAMI_JS}
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
