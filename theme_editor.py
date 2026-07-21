#!/usr/bin/env python3
"""Theme editor — Shopify tool. Renders /var/www/ops/theme-editor.html.

Prompt-driven storefront theming. You describe the change in plain language
("make the buttons deep navy and use a serif heading font"); the request is
routed through Claude against your live theme's real settings, which returns a
validated diff you review — with colour swatches — before anything is written.
Every apply is snapshotted so one click reverts it.

Needs the Shopify read_themes / write_themes scopes. If they're missing the page
shows an honest unlock path instead of a dead tool.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/theme-editor.html"

EXAMPLES = ["Make the primary buttons deep navy",
            "Use a serif heading font", "Round the button corners more",
            "Make the layout wider", "Warmer, cream background"]


def build():
    example_js = ",".join('"%s"' % e.replace('"', '\\"') for e in EXAMPLES)
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Theme editor — Labs OS</title>
<style>{HUB_STYLE}
  .te-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px;box-shadow:var(--shadow);}}
  .te-top{{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap;}}
  .te-top .nm{{font-size:14px;font-weight:650;color:var(--ink);}}
  .te-top .tag{{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;
    color:var(--good);background:var(--good-bg);padding:3px 8px;border-radius:6px;}}
  .te-top .rev{{margin-left:auto;font-size:12.5px;color:var(--muted);background:none;border:1px solid var(--border);
    border-radius:var(--r-s);padding:7px 12px;cursor:pointer;}}
  .te-top .rev:hover{{border-color:var(--border-2);color:var(--ink);}}
  .te-top .rev[disabled]{{opacity:.4;cursor:not-allowed;}}
  .prompt label{{display:block;font-size:11.5px;font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}}
  .prompt textarea{{width:100%;font-size:14.5px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:12px;font-family:inherit;min-height:70px;resize:vertical;line-height:1.5;}}
  .prompt textarea:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .egs{{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0;}}
  .eg{{font-size:12px;font-weight:550;border:1px solid var(--border);background:var(--card);color:var(--muted);
    border-radius:20px;padding:5px 12px;cursor:pointer;transition:border-color .12s,color .12s;}}
  .eg:hover{{border-color:var(--accent);color:var(--ink);}}
  .btn{{font-size:13.5px;font-weight:650;border-radius:var(--r-s);padding:10px 16px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);text-decoration:none;
    display:inline-flex;align-items:center;gap:7px;transition:border-color .12s,transform .1s;}}
  .btn:hover{{border-color:var(--border-2);text-decoration:none;}}
  .btn:active{{transform:scale(.98);}}
  .btn.primary{{background:var(--ink);color:var(--bg);border-color:var(--ink);}}
  .btn[disabled]{{opacity:.5;cursor:not-allowed;}}
  .btn svg{{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}}
  .go{{margin-top:4px;}}
  .prev{{margin-top:16px;}}
  .prev-head{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
  .prev-head b{{font-size:14px;}}
  .prev-head .sub{{font-size:12.5px;color:var(--muted);}}
  .chg{{display:flex;align-items:center;gap:11px;padding:9px 11px;border:1px solid var(--border);
    border-radius:var(--r-s);margin-bottom:7px;background:var(--bg);}}
  .chg input{{width:17px;height:17px;accent-color:var(--accent);flex-shrink:0;}}
  .chg .k{{flex:1;min-width:0;font-size:13px;color:var(--ink);}}
  .chg .k small{{display:block;color:var(--muted);font-size:11px;}}
  .chg .vv{{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--muted);flex-shrink:0;}}
  .sw{{width:16px;height:16px;border-radius:4px;border:1px solid var(--border-2);flex-shrink:0;display:inline-block;}}
  .arw{{color:var(--muted);}}
  .newv{{color:var(--ink);font-weight:600;}}
  .prev-actions{{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;}}
  .empty2{{padding:20px;text-align:center;color:var(--muted);font-size:13.5px;}}
  .note{{font-size:12.5px;color:var(--muted);margin-top:10px;background:var(--accent-bg);border-radius:var(--r-s);padding:11px 13px;line-height:1.5;}}
  /* lock state (kept for when access is missing) */
  .te-lock{{display:flex;gap:14px;align-items:flex-start;margin-bottom:6px;}}
  .te-lock .ic{{flex-shrink:0;width:44px;height:44px;border-radius:12px;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;}}
  .te-lock .ic svg{{width:22px;height:22px;stroke:var(--accent);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;}}
  .te-lock h2{{font-size:17px;margin:0 0 4px;}} .te-lock p{{margin:0;color:var(--muted);font-size:14px;line-height:1.55;}}
  .steps{{list-style:none;padding:0;margin:18px 0 0;counter-reset:s;}}
  .steps li{{position:relative;padding:0 0 16px 40px;counter-increment:s;font-size:14px;line-height:1.55;color:var(--ink);}}
  .steps li::before{{content:counter(s);position:absolute;left:0;top:-1px;width:26px;height:26px;border-radius:50%;background:var(--ink);color:var(--bg);font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;}}
  .steps code{{background:var(--card-2);border:1px solid var(--border);border-radius:5px;padding:1px 6px;font-size:12.5px;}}
  .diag{{margin-top:14px;font-size:12px;color:var(--muted);}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Theme editor</h1>
      <p class="page-sub">Describe how you want the storefront to look — Claude turns it into real theme settings you approve before they go live.</p>
    </div>
    <div id="te-root"><div class="te-card"><p class="empty">Checking theme access…</p></div></div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API='/ops/agent/api', EGS=[{example_js}];
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},3200);}}
async function api(path,opts){{
  var res=await fetch(API+path,opts);
  if(res.status===403){{location.href='/oauth2/start?rd=/ops/theme-editor.html';throw new Error('auth');}}
  var d=await res.json().catch(function(){{return {{}};}});
  if(!res.ok)throw new Error(d.error||'failed');
  return d;
}}

function isColor(v){{return typeof v==='string' && (/^#([0-9a-f]{{3,8}})$/i.test(v) || /^rgba?\\(/i.test(v));}}
function humanize(key){{
  if(key.indexOf('color_schemes.')===0){{
    var p=key.split('.');
    return {{main:p[3].replace(/_/g,' '), sub:'Scheme '+p[1].replace('scheme-','')}};
  }}
  return {{main:key.replace(/_/g,' '), sub:''}};
}}
function valCell(v){{
  if(isColor(v))return '<span class="sw" style="background:'+esc(v)+'"></span>'+esc(v);
  return esc(String(v));
}}

/* ---------------- editor ---------------- */
var current={{theme:'',changes:[]}};
function editor(theme){{
  current.theme=theme.name||'your theme';
  $('te-root').innerHTML=
    '<div class="te-card">'+
      '<div class="te-top"><span class="nm">'+esc(current.theme)+'</span><span class="tag">Live</span>'+
        '<button class="rev" id="revert" disabled>Undo last change</button></div>'+
      '<div class="prompt"><label>Describe the change</label>'+
        '<textarea id="req" placeholder="e.g. make the primary buttons deep navy and use a serif heading font"></textarea>'+
        '<div class="egs" id="egs"></div>'+
        '<button class="btn primary go" id="suggest"><svg viewBox="0 0 24 24"><path d="M5 3l1.5 4L11 8.5 6.5 10 5 14l-1.5-4L-1 8.5 3.5 7z" transform="translate(4 1)"/><path d="M18 13l.9 2.4L21 16l-2.1.6L18 19l-.9-2.4L15 16l2.1-.6z"/></svg>Suggest changes</button>'+
      '</div>'+
      '<div class="prev" id="prev"></div>'+
      '<div class="note">Nothing changes until you press <b>Apply</b>. Every apply is snapshotted, so <b>Undo</b> restores the previous version instantly.</div>'+
    '</div>';
  var egs=$('egs');
  EGS.forEach(function(e){{var b=document.createElement('button');b.className='eg';b.textContent=e;
    b.onclick=function(){{$('req').value=e;$('req').focus();}};egs.appendChild(b);}});
  $('suggest').onclick=suggest;
  $('req').addEventListener('keydown',function(ev){{if((ev.metaKey||ev.ctrlKey)&&ev.key==='Enter')suggest();}});
  $('revert').onclick=revert;
  refreshRevert();
}}
async function refreshRevert(){{
  try{{var d=await api('/shopify/theme/settings');$('revert').disabled=!d.can_revert;}}catch(e){{}}
}}
async function suggest(){{
  var req=$('req').value.trim();
  if(!req){{toast('Describe the change first');return;}}
  var btn=$('suggest');btn.disabled=true;btn.textContent='Thinking…';
  $('prev').innerHTML='<div class="empty2">Claude is reading your theme and working out the settings…</div>';
  try{{
    var d=await api('/shopify/theme/suggest',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{request:req}})}});
    current.changes=d.changes||[];
    renderPreview(d);
  }}catch(e){{$('prev').innerHTML='';toast(e.message);}}
  btn.disabled=false;btn.innerHTML='<svg viewBox="0 0 24 24"><path d="M5 3l1.5 4L11 8.5 6.5 10 5 14l-1.5-4L-1 8.5 3.5 7z" transform="translate(4 1)"/><path d="M18 13l.9 2.4L21 16l-2.1.6L18 19l-.9-2.4L15 16l2.1-.6z"/></svg>Suggest changes';
}}
function renderPreview(d){{
  var ch=current.changes;
  if(!ch.length){{$('prev').innerHTML='<div class="empty2">No matching settings for that — try naming a colour, font, size or the buttons.</div>';return;}}
  var rows=ch.map(function(c,i){{
    var h=humanize(c.key);
    return '<div class="chg"><input type="checkbox" data-i="'+i+'" checked>'+
      '<div class="k">'+esc(h.main)+(h.sub?'<small>'+esc(h.sub)+'</small>':'')+'</div>'+
      '<div class="vv">'+valCell(c.old)+'<span class="arw">&rarr;</span><span class="newv">'+valCell(c.new)+'</span></div></div>';
  }}).join('');
  $('prev').innerHTML=
    '<div class="prev-head"><b>'+ch.length+' change'+(ch.length>1?'s':'')+' proposed</b>'+
      (d.unmatched?'<span class="sub">'+d.unmatched+' ignored</span>':'')+'</div>'+
    rows+
    '<div class="prev-actions"><button class="btn primary" id="apply">Apply</button>'+
      '<button class="btn" id="discard">Discard</button></div>';
  updateApplyLabel();
  $('prev').querySelectorAll('.chg input').forEach(function(cb){{cb.onchange=updateApplyLabel;}});
  $('apply').onclick=applyChanges;
  $('discard').onclick=function(){{$('prev').innerHTML='';current.changes=[];}};
}}
function checkedPatch(){{
  var patch={{}};
  $('prev').querySelectorAll('.chg input:checked').forEach(function(cb){{
    var c=current.changes[+cb.dataset.i];patch[c.key]=c.new;}});
  return patch;
}}
function updateApplyLabel(){{
  var n=$('prev').querySelectorAll('.chg input:checked').length;
  var a=$('apply');a.disabled=n===0;a.textContent='Apply '+n+' change'+(n===1?'':'s');
}}
async function applyChanges(){{
  var patch=checkedPatch();var n=Object.keys(patch).length;
  if(!n)return;
  if(!confirm('Apply '+n+' change'+(n>1?'s':'')+' to your live theme? You can undo it in one click.'))return;
  var a=$('apply');a.disabled=true;a.textContent='Applying…';
  try{{
    var d=await api('/shopify/theme/apply',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{patch:patch}})}});
    toast('Applied '+d.applied+' change'+(d.applied===1?'':'s')+' to '+current.theme);
    $('prev').innerHTML='';current.changes=[];$('req').value='';
    $('revert').disabled=false;
  }}catch(e){{toast(e.message);a.disabled=false;updateApplyLabel();}}
}}
async function revert(){{
  if(!confirm('Undo the last theme change?'))return;
  var b=$('revert');b.disabled=true;b.textContent='Undoing…';
  try{{
    var d=await api('/shopify/theme/revert',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{}})}});
    toast('Reverted. '+(d.remaining>0?d.remaining+' earlier snapshot'+(d.remaining===1?'':'s')+' left.':'No earlier snapshots.'));
    b.disabled=d.remaining<=0;
  }}catch(e){{toast(e.message);b.disabled=false;}}
  b.textContent='Undo last change';
}}

/* ---------------- locked (no theme access) ---------------- */
function locked(reason){{
  $('te-root').innerHTML=
    '<div class="te-card">'+
      '<div class="te-lock"><div class="ic"><svg viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg></div>'+
        '<div><h2>Theme access needed</h2><p>Editing the theme needs Shopify&#39;s <code>read_themes</code> &amp; <code>write_themes</code> permissions. Add them to the app and reconnect, and this editor turns on.</p></div></div>'+
      '<ol class="steps">'+
        '<li>Shopify admin &rarr; <b>Settings &rarr; Apps &rarr; Develop apps</b> &rarr; your app &rarr; <b>Configuration</b>.</li>'+
        '<li>Under <b>Admin API scopes</b> tick <code>read_themes</code> and <code>write_themes</code>, then <b>Save</b>.</li>'+
        '<li>Reconnect at <a href="/ops/agent/api/shopify/connect">the Shopify connect link</a> to refresh the token.</li>'+
      '</ol>'+
      '<div class="te-actions" style="margin-top:18px"><button class="btn" id="recheck">Re-check access</button></div>'+
      (reason?'<div class="diag">Shopify said: '+esc(reason)+'</div>':'')+
    '</div>';
  $('recheck').onclick=function(){{$('recheck').textContent='Checking…';$('recheck').disabled=true;init();}};
}}

async function init(){{
  try{{
    var st=await api('/shopify/theme/status');
    if(st&&st.available)editor(st.theme||{{}}); else locked(st&&st.reason);
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
