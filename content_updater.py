#!/usr/bin/env python3
"""Content updater — Shopify tool. Renders /var/www/ops/content-updater.html.

Two jobs, both in your brand voice:
  • Products — bulk-insert a reusable content block (shipping, warranty, care…)
    or find/replace across many product descriptions, with a live before/after
    preview before anything is written. Blocks are idempotent: re-running updates
    the block in place instead of stacking duplicates.
  • Pages — edit your store pages (About, policies, guides) directly.

All writes go through admin-gated /ops/agent/api/shopify/* endpoints.
"""
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/content-updater.html"

# Ready-made blocks in the Timelabs voice — one click to insert across products.
PRESETS = [
    ("Shipping & delivery", "<p><strong>Shipping.</strong> Dispatched within 24&ndash;48 hours and delivered across India in 5&ndash;10 days, fully insured with tracking.</p>"),
    ("2-year warranty", "<p><strong>Warranty.</strong> Every build is backed by a 2-year movement warranty. If anything skips a beat, we make it right.</p>"),
    ("Build & care", "<p><strong>Build &amp; care.</strong> Hand-assembled and timed in-house. Keep the crown pushed in, avoid magnets, and it will keep good time for years.</p>"),
]


def build():
    preset_js = ",".join(
        "{name:%r,html:%r}" % (n, h) for n, h in PRESETS
    )
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Content updater — Labs OS</title>
<style>{HUB_STYLE}
  .tabs{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;margin-bottom:16px;}}
  .tabs button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:13.5px;
    font-weight:600;padding:9px 18px;cursor:pointer;}}
  .tabs button.active{{background:var(--ink);color:var(--bg);}}
  .tabs button+button{{border-left:1px solid var(--border);}}
  .cu-controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px;}}
  .cu-search{{flex:1;min-width:200px;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--card);color:var(--ink);padding:9px 12px;}}
  .cu-search:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .seg{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;}}
  .seg button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:13px;
    font-weight:600;padding:8px 13px;cursor:pointer;}}
  .seg button.active{{background:var(--ink);color:var(--bg);}}
  .seg button+button{{border-left:1px solid var(--border);}}
  .selbar{{display:flex;align-items:center;gap:12px;font-size:13px;color:var(--muted);margin-bottom:8px;flex-wrap:wrap;}}
  .selbar a{{color:var(--accent);cursor:pointer;}}
  .plist{{border:1px solid var(--border);border-radius:var(--r);background:var(--card);overflow:hidden;
    box-shadow:var(--shadow);max-height:360px;overflow-y:auto;}}
  .pitem{{display:flex;align-items:center;gap:11px;padding:9px 13px;border-bottom:1px solid var(--border);cursor:pointer;}}
  .pitem:last-child{{border-bottom:none;}}
  .pitem:hover{{background:var(--card-2);}}
  .pitem input{{width:17px;height:17px;accent-color:var(--accent);flex-shrink:0;}}
  .pitem img{{width:36px;height:36px;object-fit:cover;border-radius:6px;background:var(--card-2);flex-shrink:0;}}
  .pitem .ph{{width:36px;height:36px;border-radius:6px;background:var(--card-2);display:flex;align-items:center;justify-content:center;color:var(--muted);flex-shrink:0;}}
  .pitem b{{font-size:13.5px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
  .pitem small{{font-size:11.5px;color:var(--muted);}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:16px;box-shadow:var(--shadow);margin-top:14px;}}
  .card h3{{font-size:14px;margin:0 0 12px;}}
  .field{{margin-bottom:12px;}}
  .field label{{display:block;font-size:11.5px;font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px;}}
  .pin{{width:100%;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);background:var(--bg);color:var(--ink);padding:10px 12px;font-family:inherit;}}
  .pin:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  textarea.pin{{min-height:96px;resize:vertical;line-height:1.5;font-size:13px;}}
  .presets{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:9px;}}
  .chip{{font-size:12px;font-weight:600;border:1px solid var(--border);background:var(--card);color:var(--ink);
    border-radius:20px;padding:5px 12px;cursor:pointer;transition:border-color .12s,background .12s;}}
  .chip:hover{{border-color:var(--accent);background:var(--accent-bg);}}
  .row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
  @media(max-width:520px){{.row2{{grid-template-columns:1fr;}}}}
  /* The line under a field that says what the operation will actually do.
     It had no rule, so it rendered at body size and read as instruction
     rather than as a note. */
  .hint{{font-size:12px;line-height:1.45;color:var(--muted);margin-top:8px;}}
  .btn{{font-size:13.5px;font-weight:650;border-radius:var(--r-s);padding:10px 16px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);transition:border-color .12s,transform .1s;}}
  .btn:hover{{border-color:var(--border-2);}}
  .btn:active{{transform:scale(.98);}}
  .btn.primary{{background:var(--ink);color:var(--bg);border-color:var(--ink);}}
  .btn[disabled]{{opacity:.45;cursor:not-allowed;}}
  .actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;}}
  .preview{{margin-top:14px;}}
  .pv-head{{font-size:13px;color:var(--muted);margin-bottom:8px;}}
  .pv-head b{{color:var(--ink);}}
  .diff{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
  @media(max-width:640px){{.diff{{grid-template-columns:1fr;}}}}
  .diff .box{{border:1px solid var(--border);border-radius:var(--r-s);padding:11px;font-size:12px;
    line-height:1.5;max-height:240px;overflow:auto;background:var(--bg);white-space:pre-wrap;word-break:break-word;}}
  .diff .box.before{{opacity:.7;}}
  .diff .lbl{{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:5px;}}
  .setup{{background:var(--accent-bg);border-radius:var(--r);padding:16px;color:var(--ink);}}
  .empty2{{padding:22px;text-align:center;color:var(--muted);font-size:13.5px;}}
  .pagebody{{min-height:320px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Content updater</h1>
      <p class="page-sub">Refresh product copy in bulk and edit your store pages — in your voice. Preview every change before it goes live.</p>
    </div>
    <div class="tabs" id="tabs">
      <button data-t="products" class="active">Products</button>
      <button data-t="pages">Pages</button>
    </div>
    <div id="tab-products"></div>
    <div id="tab-pages" style="display:none"></div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API='/ops/agent/api';
var PRESETS=[{preset_js}];
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},2800);}}
async function api(path,opts){{
  var res=await fetch(API+path,opts);
  if(res.status===403){{location.href='/oauth2/start?rd=/ops/content-updater.html';throw new Error('auth');}}
  var d=await res.json().catch(function(){{return {{}};}});
  if(!res.ok)throw new Error(d.error||'failed');
  return d;
}}

/* ---------------- tabs ---------------- */
$('tabs').querySelectorAll('button').forEach(function(b){{b.onclick=function(){{
  $('tabs').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
  b.classList.add('active');
  var t=b.dataset.t;
  $('tab-products').style.display=t==='products'?'':'none';
  $('tab-pages').style.display=t==='pages'?'':'none';
  if(t==='pages'&&!pagesLoaded)loadPages();
}};}});

/* ---------------- products ---------------- */
var P={{q:'',filter:'all',cursor:null,hasMore:false,sel:{{}},op:'append_block'}};
function productsShell(){{
  $('tab-products').innerHTML=
    '<div class="cu-controls">'+
      '<input id="pq" class="cu-search" type="search" placeholder="Search products…">'+
      '<div class="seg" id="pfilter"><button data-f="all" class="active">All</button>'+
        '<button data-f="active">Active</button><button data-f="draft">Draft</button></div>'+
    '</div>'+
    '<div class="selbar"><span id="selcount">0 selected</span>'+
      '<a id="selall">Select all loaded</a><a id="selnone">Clear</a>'+
      '<button id="pmore" class="btn" style="margin-left:auto;display:none;padding:6px 12px;font-size:12.5px">Load more</button></div>'+
    '<div class="plist" id="plist"></div>'+
    '<div class="card"><h3>What to change</h3>'+
      '<div class="seg" id="opseg" style="margin-bottom:12px">'+
        '<button data-o="append_block" class="active">Insert a block</button>'+
        '<button data-o="replace">Find &amp; replace</button></div>'+
      '<div id="opbody"></div>'+
      '<div class="actions"><button class="btn" id="preview">Preview changes</button>'+
        '<button class="btn primary" id="apply" disabled>Apply</button></div>'+
      '<div class="preview" id="pvout"></div>'+
    '</div>';
  var t=null;
  $('pq').addEventListener('input',function(){{clearTimeout(t);t=setTimeout(function(){{P.q=$('pq').value.trim();loadProducts(true);}},350);}});
  $('pfilter').querySelectorAll('button').forEach(function(b){{b.onclick=function(){{
    $('pfilter').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
    b.classList.add('active');P.filter=b.dataset.f;loadProducts(true);
  }};}});
  $('pmore').onclick=function(){{loadProducts(false);}};
  $('selall').onclick=function(){{document.querySelectorAll('#plist .pitem').forEach(function(el){{
    P.sel[el.dataset.id]=el.dataset.title;el.querySelector('input').checked=true;}});selCount();}};
  $('selnone').onclick=function(){{P.sel={{}};document.querySelectorAll('#plist input').forEach(function(i){{i.checked=false;}});selCount();}};
  $('opseg').querySelectorAll('button').forEach(function(b){{b.onclick=function(){{
    $('opseg').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
    b.classList.add('active');P.op=b.dataset.o;drawOp();$('pvout').innerHTML='';$('apply').disabled=true;
  }};}});
  $('preview').onclick=function(){{runBulk(true);}};
  $('apply').onclick=function(){{runBulk(false);}};
  drawOp();loadProducts(true);
}}
function buildQuery(){{var p=[];if(P.filter!=='all')p.push('status:'+P.filter);if(P.q)p.push(P.q);return p.join(' ');}}
function selCount(){{var n=Object.keys(P.sel).length;$('selcount').textContent=n+' selected';}}
async function loadProducts(reset){{
  if(reset){{P.cursor=null;$('plist').innerHTML='<div class="empty2">Loading…</div>';}}
  try{{
    var d=await api('/shopify/products?q='+encodeURIComponent(buildQuery())+(P.cursor?'&after='+encodeURIComponent(P.cursor):''));
    P.cursor=d.cursor;P.hasMore=d.has_more;
    if(reset)$('plist').innerHTML='';
    if(reset&&!d.products.length)$('plist').innerHTML='<div class="empty2">No products match.</div>';
    d.products.forEach(function(p){{
      var el=document.createElement('div');el.className='pitem';el.dataset.id=p.id;el.dataset.title=p.title;
      var img=p.image?'<img loading="lazy" src="'+esc(p.image)+'" alt="">':'<div class="ph">&#9678;</div>';
      el.innerHTML='<input type="checkbox"'+(P.sel[p.id]?' checked':'')+'>'+img+
        '<div style="min-width:0"><b>'+esc(p.title)+'</b><br><small>'+esc(p.status.toLowerCase())+(p.type?' · '+esc(p.type):'')+'</small></div>';
      var cb=el.querySelector('input');
      function toggle(v){{if(v){{P.sel[p.id]=p.title;}}else{{delete P.sel[p.id];}}cb.checked=v;selCount();}}
      el.onclick=function(e){{if(e.target!==cb)toggle(!cb.checked);}};
      cb.onclick=function(e){{e.stopPropagation();toggle(cb.checked);}};
      $('plist').appendChild(el);
    }});
    $('pmore').style.display=P.hasMore?'inline-block':'none';
  }}catch(e){{if(e.message!=='auth')$('plist').innerHTML='<div class="setup">'+esc(e.message)+'</div>';}}
}}
function drawOp(){{
  if(P.op==='replace'){{
    $('opbody').innerHTML=
      '<div class="row2"><div class="field"><label>Find this text</label><input id="o-find" class="pin" placeholder="old phrase or price"></div>'+
      '<div class="field"><label>Replace with</label><input id="o-rep" class="pin" placeholder="new phrase"></div></div>'+
      '<div class="hint">Exact, case-sensitive text match across each product&#39;s description.</div>';
  }}else{{
    $('opbody').innerHTML=
      '<div class="presets" id="presets"></div>'+
      '<div class="row2"><div class="field"><label>Block name</label><input id="o-name" class="pin" placeholder="e.g. Warranty"></div>'+
      '<div class="field"><label>Position</label><div class="seg" id="o-pos"><button data-p="append_block" class="active">End</button><button data-p="prepend_block">Start</button></div></div></div>'+
      '<div class="field"><label>Block content (HTML)</label><textarea id="o-block" class="pin" placeholder="<p>Your reusable copy…</p>"></textarea></div>'+
      '<div class="hint">Re-running the same-named block updates it everywhere instead of adding a duplicate.</div>';
    var pc=$('presets');
    PRESETS.forEach(function(pr){{var c=document.createElement('button');c.className='chip';c.textContent=pr.name;
      c.onclick=function(){{$('o-name').value=pr.name;$('o-block').value=pr.html;}};pc.appendChild(c);}});
    $('o-pos').querySelectorAll('button').forEach(function(b){{b.onclick=function(){{
      $('o-pos').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
      b.classList.add('active');P.op=b.dataset.p;}};}});
  }}
}}
function opPayload(){{
  var body={{ids:Object.keys(P.sel),op:P.op}};
  if(P.op==='replace'){{body.find=($('o-find')||{{}}).value||'';body.replace=($('o-rep')||{{}}).value||'';}}
  else{{body.op=$('o-pos')?document.querySelector('#o-pos button.active').dataset.p:'append_block';
    body.name=($('o-name')||{{}}).value||'';body.block=($('o-block')||{{}}).value||'';}}
  return body;
}}
async function runBulk(dry){{
  var ids=Object.keys(P.sel);
  if(!ids.length){{toast('Select at least one product');return;}}
  var body=opPayload();body.dry_run=dry;
  var btn=dry?$('preview'):$('apply');var lbl=btn.textContent;btn.disabled=true;btn.textContent=dry?'Previewing…':'Applying…';
  try{{
    if(!dry&&!confirm('Apply this change to '+ids.length+' product'+(ids.length>1?'s':'')+'? This edits live descriptions.'))
      {{btn.disabled=false;btn.textContent=lbl;return;}}
    var d=await api('/shopify/products/bulk-content',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    if(dry){{
      var s=d.sample;
      var h='<div class="pv-head"><b>'+d.changed+'</b> of '+d.total+' will change'+
        (d.total-d.changed>0?' · '+(d.total-d.changed)+' unaffected':'')+'.</div>';
      if(s){{h+='<div class="pv-head" style="margin-top:4px">Example — <b>'+esc(s.title)+'</b></div>'+
        '<div class="diff"><div><div class="lbl">Before</div><div class="box before">'+esc(s.before)+'</div></div>'+
        '<div><div class="lbl">After</div><div class="box">'+esc(s.after)+'</div></div></div>';}}
      $('pvout').innerHTML=h;
      $('apply').disabled=d.changed===0;
      $('apply').textContent='Apply to '+d.changed+' product'+(d.changed===1?'':'s');
    }}else{{
      var errs=d.results.filter(function(r){{return r.error;}});
      toast('Updated '+d.changed+' product'+(d.changed===1?'':'s')+(errs.length?' · '+errs.length+' failed':''));
      $('pvout').innerHTML='';$('apply').disabled=true;$('apply').textContent='Apply';
    }}
  }}catch(e){{toast(e.message);}}
  btn.disabled=false;if(dry)btn.textContent=lbl;
}}

/* ---------------- pages ---------------- */
var pagesLoaded=false;
async function loadPages(){{
  pagesLoaded=true;
  $('tab-pages').innerHTML='<div class="pagebody" id="pagebody"><div class="empty2">Loading pages…</div></div>';
  try{{
    var d=await api('/shopify/pages');
    if(!d.pages.length){{$('pagebody').innerHTML='<div class="empty2">No pages yet.</div>';return;}}
    var h='<div class="plist">';
    d.pages.forEach(function(p){{
      h+='<div class="pitem" data-id="'+esc(p.id)+'" data-title="'+esc(p.title)+'"><div class="ph">&#9632;</div>'+
        '<div style="min-width:0;flex:1"><b>'+esc(p.title)+'</b><br><small>/'+esc(p.handle)+(p.published?'':' · hidden')+'</small></div>'+
        '<span style="color:var(--muted);font-size:16px">&rsaquo;</span></div>';
    }});
    h+='</div>';
    $('pagebody').innerHTML=h;
    $('pagebody').querySelectorAll('.pitem').forEach(function(el){{el.onclick=function(){{openPage(el.dataset.id,el.dataset.title);}};}});
  }}catch(e){{if(e.message!=='auth')$('pagebody').innerHTML='<div class="setup">'+esc(e.message)+'</div>';}}
}}
async function openPage(id,title){{
  $('pagebody').innerHTML='<div class="empty2">Loading '+esc(title)+'…</div>';
  try{{
    var d=await api('/shopify/page/detail?id='+encodeURIComponent(id));
    $('pagebody').innerHTML=
      '<button class="btn" id="pgback" style="margin-bottom:12px;padding:7px 13px;font-size:12.5px">&larr; All pages</button>'+
      '<div class="card" style="margin-top:0"><div class="field"><label>Title</label><input id="pg-title" class="pin"></div>'+
      '<div class="field"><label>Body (HTML)</label><textarea id="pg-body" class="pin" style="min-height:300px"></textarea></div>'+
      '<div class="actions"><button class="btn primary" id="pg-save" disabled>Save page</button>'+
      '<a class="btn" href="https://timelabsco.in/pages/'+esc(d.handle)+'" target="_blank" rel="noopener">View live</a></div></div>';
    $('pg-title').value=d.title;$('pg-body').value=d.body;
    var save=$('pg-save');
    function dirty(){{save.disabled=false;}}
    $('pg-title').addEventListener('input',dirty);$('pg-body').addEventListener('input',dirty);
    $('pgback').onclick=function(){{loadPages();}};
    save.onclick=async function(){{
      save.disabled=true;save.textContent='Saving…';
      try{{
        await api('/shopify/page/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:id,title:$('pg-title').value,body:$('pg-body').value}})}});
        toast('Page saved');save.textContent='Saved ✓';setTimeout(function(){{save.textContent='Save page';}},1500);
      }}catch(e){{toast(e.message);save.disabled=false;save.textContent='Save page';}}
    }};
  }}catch(e){{if(e.message!=='auth')$('pagebody').innerHTML='<div class="setup">'+esc(e.message)+'</div>';}}
}}

productsShell();
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
