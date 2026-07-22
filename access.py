#!/usr/bin/env python3
"""Tool access — admin tool. Renders /var/www/ops/access.html.

Key answers "who can sign in"; this answers "and what may they touch once they
do". Pick a role per person and the gate is rewritten immediately: nginx bounces
a restricted role off /ops/ entirely, so they can't even fetch a page that has
revenue baked into its HTML — hiding a nav item would protect nobody.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/access.html"


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Tool access — Labs OS</title>
<style>{HUB_STYLE}
  .ac-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    box-shadow:var(--shadow);overflow:hidden;}}
  .ac-row{{display:flex;align-items:center;gap:14px;padding:14px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap;}}
  .ac-row:last-child{{border-bottom:none;}}
  .ac-who{{flex:1;min-width:180px;}}
  .ac-who b{{display:block;font-size:14px;color:var(--ink);word-break:break-all;}}
  .ac-who small{{font-size:12px;color:var(--muted);}}
  .ac-roles{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;flex-wrap:wrap;}}
  .ac-roles button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:12.5px;
    font-weight:600;padding:8px 13px;cursor:pointer;white-space:nowrap;}}
  .ac-roles button+button{{border-left:1px solid var(--border);}}
  .ac-roles button.on{{background:var(--ink);color:var(--bg);}}
  .ac-roles button[disabled]{{opacity:.45;cursor:not-allowed;}}
  .ac-legend{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:16px;}}
  .ac-leg{{border:1px solid var(--border);border-radius:var(--r-s);padding:13px;background:var(--card);}}
  .ac-leg b{{font-size:13px;color:var(--ink);}}
  .ac-leg small{{display:block;font-size:12.5px;color:var(--muted);line-height:1.5;margin-top:4px;}}
  .ac-leg code{{background:var(--card-2);border-radius:4px;padding:1px 5px;font-size:11.5px;}}
  .note{{font-size:13px;color:var(--muted);background:var(--accent-bg);border-radius:var(--r-s);
    padding:13px 15px;line-height:1.6;margin-top:16px;}}
  .note a{{color:var(--accent);font-weight:600;}}
  .empty2{{padding:22px;text-align:center;color:var(--muted);font-size:13.5px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Tool access</h1>
      <p class="page-sub">Who may use which tools. Changing a role takes effect immediately — a restricted person is bounced off the pages they don't own, not just hidden from the menu.</p>
    </div>
    <div class="ac-card" id="list"><div class="empty2">Loading…</div></div>
    <div class="ac-legend" id="legend"></div>
    <div class="note">Adding or removing <b>people</b> is <a href="/ops/#key">Key</a> — this page only decides what an existing person may do.
      Someone on <b>Intake only</b> lands on <code>/intake/</code>, sees just the order form, and is redirected away from everything else.</div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API='/ops/agent/api';
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},3000);}}
async function api(p,o){{
  var r=await fetch(API+p,o);
  if(r.status===403){{location.href='/oauth2/start?rd=/ops/access.html';throw new Error('auth');}}
  var d=await r.json().catch(function(){{return {{}};}});
  if(!r.ok)throw new Error(d.error||'failed');
  return d;
}}
var ROLES=[];
async function load(){{
  try{{
    var d=await api('/access/list');
    ROLES=d.roles||[];
    if(!d.people.length){{ $('list').innerHTML='<div class="empty2">Nobody on the allowlist yet.</div>'; return; }}
    $('list').innerHTML=d.people.map(function(p){{
      var btns=ROLES.map(function(r){{
        var on=r.key===p.role?' on':'';
        var dis=(p.is_admin&&r.key!=='admin')?' disabled title="Admins are set in code"':'';
        return '<button class="'+on.trim()+'" data-e="'+esc(p.email)+'" data-r="'+esc(r.key)+'"'+dis+'>'+esc(r.label)+'</button>';
      }}).join('');
      return '<div class="ac-row"><div class="ac-who"><b>'+esc(p.email)+'</b>'+
        '<small>lands on '+esc(p.home)+(p.is_admin?' · admin':'')+'</small></div>'+
        '<div class="ac-roles">'+btns+'</div></div>';
    }}).join('');
    $('list').querySelectorAll('.ac-roles button').forEach(function(b){{
      if(b.disabled)return;
      b.onclick=function(){{ setRole(b.dataset.e, b.dataset.r, b); }};
    }});
    $('legend').innerHTML=ROLES.map(function(r){{
      return '<div class="ac-leg"><b>'+esc(r.label)+'</b><small>'+esc(r.blurb)+
        '<br>Lands on <code>'+esc(r.home)+'</code></small></div>';
    }}).join('');
  }}catch(e){{ if(e.message!=='auth') $('list').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}
async function setRole(email, role, btn){{
  var row=btn.closest('.ac-roles');
  row.querySelectorAll('button').forEach(function(x){{x.classList.remove('on');}});
  btn.classList.add('on');
  try{{
    var d=await api('/access/set',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:email,role:role}})}});
    toast(email+' → '+role+(d.gate_ok?'':' (gate: '+d.gate+')'));
    load();
  }}catch(e){{ toast(e.message); load(); }}
}}
load();
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
