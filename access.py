#!/usr/bin/env python3
"""People & access — admin tool. Renders /var/www/ops/access.html.

One surface for the whole question of access, because signing-in and what-you-
can-do are two halves of the same decision: invite a person, give them a role,
remove them — all here. Inviting writes the oauth2-proxy allowlist (who may sign
in at all); the role decides which tools they get once inside, enforced by nginx
so a restricted person can't even fetch a page they don't own.
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
<meta name="color-scheme" content="light dark"><title>People &amp; access — Labs OS</title>
<style>{HUB_STYLE}
  .invite{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:var(--card);
    border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow);margin-bottom:16px;}}
  .invite input{{flex:1;min-width:200px;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:10px 12px;}}
  .invite input:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .invite select{{font-size:13.5px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:10px 12px;}}
  .invite button{{font-size:13.5px;font-weight:650;border:none;border-radius:var(--r-s);
    background:var(--ink);color:var(--bg);padding:10px 18px;cursor:pointer;}}
  .invite button:active{{transform:scale(.97);}}
  .ac-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden;}}
  .ac-row{{display:flex;align-items:center;gap:14px;padding:14px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap;}}
  .ac-row:last-child{{border-bottom:none;}}
  .ac-who{{flex:1;min-width:170px;}}
  .ac-who b{{display:block;font-size:14px;color:var(--ink);word-break:break-all;}}
  .ac-who small{{font-size:12px;color:var(--muted);}}
  .ac-roles{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;flex-wrap:wrap;}}
  .ac-roles button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:12.5px;
    font-weight:600;padding:8px 13px;cursor:pointer;white-space:nowrap;}}
  .ac-roles button+button{{border-left:1px solid var(--border);}}
  .ac-roles button.on{{background:var(--ink);color:var(--bg);}}
  .ac-roles button[disabled]{{opacity:.45;cursor:not-allowed;}}
  .ac-rm{{border:1px solid var(--border);background:none;color:var(--muted);border-radius:var(--r-s);
    padding:8px 12px;font-size:12.5px;cursor:pointer;}}
  .ac-rm:hover{{border-color:var(--bad);color:var(--bad);}}
  .ac-rm[disabled]{{opacity:.35;cursor:not-allowed;}}
  .ac-legend{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:16px;}}
  .ac-leg{{border:1px solid var(--border);border-radius:var(--r-s);padding:13px;background:var(--card);}}
  .ac-leg b{{font-size:13px;color:var(--ink);}}
  .ac-leg small{{display:block;font-size:12.5px;color:var(--muted);line-height:1.5;margin-top:4px;}}
  .ac-leg code{{background:var(--card-2);border-radius:4px;padding:1px 5px;font-size:11.5px;}}
  .note{{font-size:13px;color:var(--muted);background:var(--accent-bg);border-radius:var(--r-s);
    padding:13px 15px;line-height:1.6;margin-top:16px;}}
  .empty2{{padding:22px;text-align:center;color:var(--muted);font-size:13.5px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">People &amp; access</h1>
      <p class="page-sub">Who can sign in, and what each person may do. Inviting lets someone in; their role decides which tools they get — and a restricted person is bounced off the pages they don't own, not just hidden from the menu.</p>
    </div>
    <div class="invite">
      <input id="inv-email" type="email" inputmode="email" autocomplete="off" placeholder="teammate@gmail.com">
      <select id="inv-role"></select>
      <button id="inv-add">Invite</button>
    </div>
    <div class="ac-card" id="list"><div class="empty2">Loading…</div></div>
    <div class="ac-legend" id="legend"></div>
    <div class="note">A person must sign in with the exact Google account you invite here. Removing someone revokes access immediately. Admins are set in code and can't be demoted or removed from this screen.</div>
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
    ROLES=(d.roles||[]).filter(function(r){{return r.key!=='admin';}});
    $('inv-role').innerHTML=ROLES.map(function(r){{return '<option value="'+esc(r.key)+'">'+esc(r.label)+'</option>';}}).join('');
    if(!d.people.length){{ $('list').innerHTML='<div class="empty2">Nobody invited yet.</div>'; }}
    else $('list').innerHTML=d.people.map(function(p){{
      var btns=(d.roles||[]).map(function(r){{
        var on=r.key===p.role?' on':'';
        var dis=(p.is_admin&&r.key!=='admin')?' disabled title="Admins are set in code"':'';
        return '<button class="'+on.trim()+'" data-e="'+esc(p.email)+'" data-r="'+esc(r.key)+'"'+dis+'>'+esc(r.label)+'</button>';
      }}).join('');
      var rm=p.is_admin?'<button class="ac-rm" disabled title="Admins can\\'t be removed here">Remove</button>'
                      :'<button class="ac-rm" data-rm="'+esc(p.email)+'">Remove</button>';
      return '<div class="ac-row"><div class="ac-who"><b>'+esc(p.email)+'</b>'+
        '<small>lands on '+esc(p.home)+(p.is_admin?' · admin':'')+'</small></div>'+
        '<div class="ac-roles">'+btns+'</div>'+rm+'</div>';
    }}).join('');
    $('list').querySelectorAll('.ac-roles button').forEach(function(b){{
      if(b.disabled)return; b.onclick=function(){{ setRole(b.dataset.e, b.dataset.r, b); }};
    }});
    $('list').querySelectorAll('.ac-rm[data-rm]').forEach(function(b){{
      b.onclick=function(){{ removePerson(b.dataset.rm); }};
    }});
    $('legend').innerHTML=(d.roles||[]).map(function(r){{
      return '<div class="ac-leg"><b>'+esc(r.label)+'</b><small>'+esc(r.blurb)+
        '<br>Lands on <code>'+esc(r.home)+'</code></small></div>';
    }}).join('');
  }}catch(e){{ if(e.message!=='auth') $('list').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}
async function invite(){{
  var email=($('inv-email').value||'').trim().toLowerCase(), role=$('inv-role').value;
  if(!email||email.indexOf('@')<0){{ toast('Enter an email address'); return; }}
  var btn=$('inv-add'); btn.disabled=true;
  try{{
    await api('/allowlist/add',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:email}})}});
    if(role) await api('/access/set',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:email,role:role}})}});
    toast(email+' invited as '+role);
    $('inv-email').value='';
    load();
  }}catch(e){{ toast(e.message); }}
  btn.disabled=false;
}}
async function setRole(email, role, btn){{
  var row=btn.closest('.ac-roles');
  row.querySelectorAll('button').forEach(function(x){{x.classList.remove('on');}});
  btn.classList.add('on');
  try{{
    var d=await api('/access/set',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:email,role:role}})}});
    toast(email+' → '+role+(d.gate_ok===false?' (gate: '+d.gate+')':''));
    load();
  }}catch(e){{ toast(e.message); load(); }}
}}
async function removePerson(email){{
  if(!confirm('Remove '+email+"? They lose access immediately.")) return;
  try{{
    await api('/allowlist/remove',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:email}})}});
    toast(email+' removed');
    load();
  }}catch(e){{ toast(e.message); }}
}}
$('inv-add').onclick=invite;
$('inv-email').addEventListener('keydown',function(e){{ if(e.key==='Enter') invite(); }});
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
