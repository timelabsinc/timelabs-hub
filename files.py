#!/usr/bin/env python3
"""System files — admin tool. Renders /var/www/ops/files.html.

A clean, read-only browser over the Labs OS source tree: navigate folders,
preview text inline, and download safe project files. Runtime data, backups,
credentials, hidden paths and symlinks are refused by the server.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/files.html"


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>System files — Labs OS</title>
<style>{HUB_STYLE}
  .fb-bar{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px;}}
  .crumbs{{flex:1;min-width:0;font-size:13.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:2px;align-items:center;}}
  .crumbs a{{color:var(--accent);cursor:pointer;}} .crumbs span.sep{{color:var(--muted);opacity:.5;}}
  .fb-toggle{{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--muted);cursor:pointer;user-select:none;}}
  .fb-toggle input{{width:15px;height:15px;accent-color:var(--accent);}}
  .flist{{border:1px solid var(--border);border-radius:var(--r);background:var(--card);overflow:hidden;box-shadow:var(--shadow);}}
  .frow{{display:flex;align-items:center;gap:12px;padding:10px 13px;border-bottom:1px solid var(--border);cursor:pointer;}}
  .frow:last-child{{border-bottom:none;}} .frow:hover{{background:var(--card-2);}}
  .fic{{width:22px;height:22px;flex-shrink:0;stroke:var(--muted);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;}}
  .frow.dir .fic{{stroke:var(--accent);}}
  .fnm{{flex:1;min-width:0;font-size:13.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
  .frow.hidden .fnm{{opacity:.6;}}
  .fmeta{{font-size:11.5px;color:var(--muted);flex-shrink:0;font-variant-numeric:tabular-nums;}}
  .badge{{font-size:9.5px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;padding:2px 6px;border-radius:5px;flex-shrink:0;}}
  .badge.lock{{color:var(--accent);background:var(--accent-bg);}}
  .badge.link{{color:var(--muted);background:var(--card-2);}}
  .chev{{color:var(--muted);flex-shrink:0;font-size:16px;}}
  .empty2{{padding:22px;text-align:center;color:var(--muted);font-size:13.5px;}}
  .note{{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.5;}}
  /* preview modal */
  .modal{{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center;z-index:60;padding:16px;}}
  .modal.on{{display:flex;}}
  .sheet{{background:var(--bg);width:100%;max-width:820px;max-height:88vh;border-radius:14px;display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-lg);}}
  .sheet header{{display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--border);}}
  .sheet header b{{flex:1;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:ui-monospace,Menlo,Consolas,monospace;}}
  .sheet .dl{{font-size:12.5px;font-weight:650;text-decoration:none;color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:7px 12px;}}
  .sheet .dl:hover{{border-color:var(--border-2);}}
  .sheet .x{{border:none;background:none;font-size:22px;color:var(--muted);cursor:pointer;line-height:1;}}
  .sheet pre{{margin:0;overflow:auto;padding:16px;font-family:ui-monospace,Menlo,Consolas,monospace;
    font-size:12.5px;line-height:1.55;color:var(--ink);white-space:pre;tab-size:4;}}
  .sheet .msg{{padding:26px;text-align:center;color:var(--muted);font-size:13.5px;line-height:1.6;}}
  .sheet .msg .lk{{width:40px;height:40px;stroke:var(--accent);fill:none;stroke-width:1.6;margin:0 auto 10px;display:block;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">System files</h1>
      <p class="page-sub">Browse the Labs OS project source — read-only. Runtime data, backups and credentials stay outside this browser. (Drop is separate; that's your product media.)</p>
    </div>
    <div class="fb-bar">
      <div class="crumbs" id="crumbs"></div>
      <label class="fb-toggle"><input type="checkbox" id="hid"> Show hidden</label>
    </div>
    <div class="flist" id="list"><div class="empty2">Loading…</div></div>
    <div class="note">Rooted at <code>/root/ops-dashboard</code>. Files over 512&nbsp;KB and binaries open as downloads.</div>
    {hub_footer()}
  </main>
</div>
<div class="modal" id="modal" role="dialog" aria-modal="true" aria-labelledby="mv-name" hidden><div class="sheet">
  <header><b id="mv-name"></b><a class="dl" id="mv-dl" href="#" style="display:none">Download</a><button class="x" id="mv-x" aria-label="Close preview">&times;</button></header>
  <div id="mv-body"></div>
</div></div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API='/ops/agent/api';
var state={{path:'/root/ops-dashboard',hidden:false,root:'/root/ops-dashboard'}};
var loadSeq=0,fileSeq=0,lastFocus=null;
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},2800);}}
async function api(p){{
  var r=await fetch(API+p);
  if(r.status===403){{location.href='/oauth2/start?rd=/ops/files.html';throw new Error('auth');}}
  var d=await r.json().catch(function(){{return {{}};}});
  if(!r.ok)throw new Error(d.error||'failed');
  return d;
}}
function fmtSize(n){{ if(n==null)return ''; if(n<1024)return n+' B'; if(n<1048576)return (n/1024).toFixed(0)+' KB'; if(n<1073741824)return (n/1048576).toFixed(1)+' MB'; return (n/1073741824).toFixed(1)+' GB'; }}
function fmtDate(t){{ var d=new Date(t*1000); return d.toLocaleDateString('en-GB',{{day:'2-digit',month:'short'}})+' '+d.toLocaleTimeString('en-GB',{{hour:'2-digit',minute:'2-digit'}}); }}
var DIR='<svg class="fic" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
var FILE='<svg class="fic" viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
var LOCK='<svg class="fic" viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>';

async function load(){{
  var seq=++loadSeq, requested=state.path;
  $('list').innerHTML='<div class="empty2">Loading…</div>';
  try{{
    var d=await api('/fs/list?hidden='+(state.hidden?'1':'0')+'&path='+encodeURIComponent(requested));
    if(seq!==loadSeq)return;
    state.path=d.path; state.root=d.root;
    crumbs(d);
    if(!d.entries.length){{ $('list').innerHTML='<div class="empty2">Empty folder.</div>'; return; }}
    var html='';
    if(d.parent){{ html+='<div class="frow dir" role="button" tabindex="0" data-up="'+esc(d.parent)+'">'+DIR+'<div class="fnm">..</div><span class="chev">&rsaquo;</span></div>'; }}
    d.entries.forEach(function(e){{
      var icon = e.is_dir?DIR:(e.protected?LOCK:FILE);
      var badge = e.protected?'<span class="badge lock">protected</span>':(e.link?'<span class="badge link">link</span>':'');
      var meta = e.is_dir?'':('<span class="fmeta">'+fmtSize(e.size)+' &middot; '+fmtDate(e.mtime)+'</span>');
      var cls='frow'+(e.is_dir?' dir':'')+(e.hidden?' hidden':'');
      html+='<div class="'+cls+'" role="button" tabindex="0" data-dir="'+(e.is_dir?'1':'0')+'" data-path="'+esc(e.path)+'" data-name="'+esc(e.name)+'" data-prot="'+(e.protected?'1':'0')+'">'+
        icon+'<div class="fnm">'+esc(e.name)+'</div>'+badge+meta+(e.is_dir?'<span class="chev">&rsaquo;</span>':'')+'</div>';
    }});
    $('list').innerHTML=html;
    $('list').querySelectorAll('.frow').forEach(function(row){{
      row.onclick=function(){{
        if(row.dataset.up){{ state.path=row.dataset.up; load(); return; }}
        if(row.dataset.dir==='1'){{ state.path=row.dataset.path; load(); }}
        else openFile(row.dataset.path, row.dataset.name, row.dataset.prot==='1');
      }};
      row.onkeydown=function(e){{ if(e.key==='Enter'||e.key===' '){{e.preventDefault();row.click();}} }};
    }});
  }}catch(e){{ if(seq===loadSeq&&e.message!=='auth') $('list').innerHTML='<div class="empty2">'+esc(e.message)+'</div>'; }}
}}

function crumbs(d){{
  var c=$('crumbs'); var rel=d.path===d.root?'':d.path.slice(d.root.length).replace(/^\\//,'');
  var html='<a role="button" tabindex="0" data-p="'+esc(d.root)+'">root</a>';
  var acc=d.root;
  rel.split('/').filter(Boolean).forEach(function(seg){{ acc=acc+'/'+seg; html+='<span class="sep">/</span><a role="button" tabindex="0" data-p="'+esc(acc)+'">'+esc(seg)+'</a>'; }});
  c.innerHTML=html;
  c.querySelectorAll('a').forEach(function(a){{ a.onclick=function(){{ state.path=a.dataset.p; load(); }}; a.onkeydown=function(e){{if(e.key==='Enter'||e.key===' '){{e.preventDefault();a.click();}}}}; }});
}}

async function openFile(path, name, prot){{
  var seq=++fileSeq;
  lastFocus=document.activeElement;
  $('mv-name').textContent=name;
  var dl=$('mv-dl'); dl.style.display='none';
  $('mv-body').innerHTML='<div class="msg">Loading…</div>';
  $('modal').hidden=false;$('modal').classList.add('on');$('mv-x').focus();
  if(prot){{
    $('mv-body').innerHTML='<div class="msg"><svg class="lk" viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>'+
      'This file holds credentials, so its contents are protected from the browser.<br>Open it over SSH if you truly need it.</div>';
    return;
  }}
  try{{
    var d=await api('/fs/read?path='+encodeURIComponent(path));
    if(seq!==fileSeq)return;
    dl.href=API+'/fs/download?path='+encodeURIComponent(path); dl.style.display='';
    if(d.content!=null){{ var pre=document.createElement('pre'); pre.textContent=d.content; $('mv-body').innerHTML=''; $('mv-body').appendChild(pre); }}
    else if(d.binary){{ $('mv-body').innerHTML='<div class="msg">Binary file ('+fmtSize(d.size)+') — no inline preview.<br>Use Download to grab it.</div>'; }}
    else if(d.too_large){{ $('mv-body').innerHTML='<div class="msg">Large file ('+fmtSize(d.size)+') — too big to preview inline.<br>Use Download to grab it.</div>'; }}
    else{{ $('mv-body').innerHTML='<div class="msg">Nothing to show.</div>'; }}
  }}catch(e){{ if(seq===fileSeq)$('mv-body').innerHTML='<div class="msg">'+esc(e.message)+'</div>'; }}
}}
function closeModal(){{fileSeq++;$('modal').classList.remove('on');$('modal').hidden=true;if(lastFocus&&lastFocus.focus)lastFocus.focus();}}
$('mv-x').onclick=closeModal;
$('modal').addEventListener('click',function(e){{ if(e.target===$('modal')) closeModal(); }});
document.addEventListener('keydown',function(e){{
  if(!$('modal').classList.contains('on'))return;
  if(e.key==='Escape'){{closeModal();return;}}
  if(e.key==='Tab'){{
    var f=Array.prototype.slice.call($('modal').querySelectorAll('a[href],button:not([disabled])')).filter(function(x){{return x.offsetParent!==null;}});
    if(!f.length)return;
    var i=f.indexOf(document.activeElement),n=e.shiftKey?(i<=0?f.length-1:i-1):(i===f.length-1?0:i+1);
    e.preventDefault();f[n].focus();
  }}
}});
$('hid').addEventListener('change',function(){{ state.hidden=$('hid').checked; load(); }});

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
