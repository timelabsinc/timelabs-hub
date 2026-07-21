#!/usr/bin/env python3
"""Quick product updater — first Shopify tool. Renders /var/www/ops/product-updater.html.

Search the catalog, change price / status inline, bulk-activate drafts. All
data is fetched live client-side from /ops/agent/api/shopify/* (admin-gated),
so this generator only emits the shell + JS.
"""
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/product-updater.html"


def build():
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Quick product updater — Timelabs Hub</title>
<style>{HUB_STYLE}
  .pu-controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px;}}
  .pu-search{{flex:1;min-width:200px;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--card);color:var(--ink);padding:9px 12px;}}
  .pu-search:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .seg button{{cursor:pointer;}}
  .prow{{display:grid;grid-template-columns:44px 1fr 120px 120px 84px;gap:12px;align-items:center;
    padding:11px 12px;border:1px solid var(--border);border-radius:var(--r);background:var(--card);
    margin-bottom:8px;box-shadow:var(--shadow);}}
  .prow img{{width:44px;height:44px;object-fit:cover;border-radius:8px;background:var(--card-2);}}
  .prow .ph{{width:44px;height:44px;border-radius:8px;background:var(--card-2);display:flex;align-items:center;
    justify-content:center;color:var(--muted);font-size:18px;}}
  .prow .nm{{min-width:0;}}
  .prow .nm b{{font-size:13.5px;font-weight:600;color:var(--ink);display:block;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}}
  .prow .nm small{{font-size:11.5px;color:var(--muted);}}
  .pinput{{font-size:13.5px;border:1px solid var(--border);border-radius:7px;background:var(--bg);
    color:var(--ink);padding:7px 9px;width:100%;}}
  .pinput:focus{{outline:none;border-color:var(--accent);}}
  .pinput.dirty{{border-color:var(--accent);background:var(--accent-bg);}}
  .psave{{font-size:12.5px;font-weight:650;border:1px solid var(--border);border-radius:7px;background:var(--card);
    color:var(--muted);padding:7px 0;cursor:not-allowed;}}
  .psave.on{{background:var(--ink);color:var(--bg);border-color:var(--ink);cursor:pointer;}}
  .pu-bulk{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0;padding:11px 14px;
    background:var(--accent-bg);border-radius:var(--r);font-size:13.5px;color:var(--ink);}}
  .pu-bulk button{{font-size:12.5px;font-weight:650;border:none;border-radius:7px;background:var(--ink);
    color:var(--bg);padding:7px 13px;cursor:pointer;}}
  .pu-setup{{background:var(--warn-bg,rgba(224,173,81,.12));border-radius:var(--r);padding:16px;text-align:center;}}
  .pu-more{{display:block;margin:14px auto 0;font-size:13px;font-weight:600;border:1px solid var(--border);
    background:var(--card);color:var(--ink);border-radius:var(--r-s);padding:9px 18px;cursor:pointer;}}
  @media (max-width:640px){{
    .prow{{grid-template-columns:40px 1fr;gap:8px;}}
    .prow img,.prow .ph{{width:40px;height:40px;}}
    .prow .pinput,.prow .psave{{grid-column:2;}}
  }}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Quick product updater</h1>
      <p class="page-sub">Search, re-price, and launch products fast. Changes go live on your store immediately.</p>
    </div>
    <div id="pu-body"></div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API = '/ops/agent/api';
var state = {{ q: '', filter: 'all', cursor: null, hasMore: false, selected: {{}} }};
function $(id){{ return document.getElementById(id); }}
function esc(s){{ var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }}
var toastT;
function toast(m){{ var t=$('toast'); t.textContent=m; t.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(function(){{t.classList.remove('show');}},2600); }}

function shell(){{
  $('pu-body').innerHTML =
    '<div class="pu-controls">' +
      '<input id="q" class="pu-search" type="search" placeholder="Search products, SKU…">' +
      '<div class="seg" id="filter">' +
        '<button data-f="all" class="active">All</button>' +
        '<button data-f="active">Active</button>' +
        '<button data-f="draft">Draft</button>' +
      '</div>' +
    '</div>' +
    '<div id="bulk"></div><div id="list"></div>' +
    '<button id="more" class="pu-more" style="display:none">Load more</button>';
  var t=null;
  $('q').addEventListener('input', function(){{ clearTimeout(t); t=setTimeout(function(){{ state.q=$('q').value.trim(); load(true); }}, 350); }});
  $('filter').querySelectorAll('button').forEach(function(b){{ b.onclick=function(){{
    $('filter').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
    b.classList.add('active'); state.filter=b.dataset.f; load(true);
  }}; }});
  $('more').onclick=function(){{ load(false); }};
}}

function buildQuery(){{
  var parts=[];
  if(state.filter!=='all') parts.push('status:'+state.filter);
  if(state.q) parts.push(state.q);
  return parts.join(' ');
}}

async function load(reset){{
  if(reset){{ state.cursor=null; state.selected={{}}; $('list').innerHTML='<p class="empty" style="padding:20px">Loading…</p>'; }}
  try{{
    var url=API+'/shopify/products?q='+encodeURIComponent(buildQuery())+(state.cursor?'&after='+encodeURIComponent(state.cursor):'');
    var res=await fetch(url);
    if(res.status===403){{ location.href='/oauth2/start?rd=/ops/product-updater.html'; return; }}
    var d=await res.json();
    if(!res.ok) throw new Error(d.error||'failed');
    state.cursor=d.cursor; state.hasMore=d.has_more;
    if(reset) $('list').innerHTML='';
    if(reset && !d.products.length){{ $('list').innerHTML='<p class="empty" style="padding:20px">No products match.</p>'; }}
    d.products.forEach(addRow);
    $('more').style.display=state.hasMore?'block':'none';
    renderBulk();
  }}catch(e){{ $('list').innerHTML='<div class="pu-setup">'+esc(e.message)+'</div>'; }}
}}

function addRow(p){{
  var row=document.createElement('div'); row.className='prow'; row.dataset.pid=p.id; row.dataset.vid=p.variant_id||'';
  var img = p.image ? '<img loading="lazy" src="'+esc(p.image)+'" alt="">' : '<div class="ph">◷</div>';
  row.innerHTML = img +
    '<div class="nm"><b>'+esc(p.title)+'</b><small>'+esc(p.sku||p.type||'')+(p.inventory!=null?' · '+p.inventory+' in stock':'')+'</small></div>' +
    '<input class="pinput price" type="number" step="0.01" value="'+esc(p.price||'')+'" '+(p.variant_id?'':'disabled')+'>' +
    '<select class="pinput status">' +
      ['ACTIVE','DRAFT','ARCHIVED'].map(function(s){{return '<option value="'+s+'"'+(s===p.status?' selected':'')+'>'+s.charAt(0)+s.slice(1).toLowerCase()+'</option>';}}).join('') +
    '</select>' +
    '<button class="psave">Save</button>';
  var priceI=row.querySelector('.price'), statusI=row.querySelector('.status'), save=row.querySelector('.psave');
  var orig={{price:p.price, status:p.status}};
  function dirty(){{
    var d=(priceI.value!==(orig.price||'')) || (statusI.value!==orig.status);
    save.classList.toggle('on', d);
    priceI.classList.toggle('dirty', priceI.value!==(orig.price||''));
    statusI.classList.toggle('dirty', statusI.value!==orig.status);
  }}
  priceI.addEventListener('input', dirty); statusI.addEventListener('change', function(){{ dirty(); state.selected[p.id]=(statusI.value==='ACTIVE'&&orig.status==='DRAFT'); }});
  save.onclick=async function(){{
    if(!save.classList.contains('on')) return;
    save.textContent='…'; save.classList.remove('on');
    try{{
      var body={{product_id:p.id, variant_id:p.variant_id, title:p.title}};
      if(priceI.value!==(orig.price||'')) body.price=priceI.value;
      if(statusI.value!==orig.status) body.status=statusI.value;
      var res=await fetch(API+'/shopify/product/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
      var d=await res.json();
      if(!res.ok) throw new Error(d.error||'failed');
      orig.price=priceI.value; orig.status=statusI.value; dirty();
      save.textContent='Saved ✓'; toast(p.title+' updated');
      setTimeout(function(){{save.textContent='Save';}},1500);
    }}catch(e){{ toast(e.message); save.textContent='Retry'; save.classList.add('on'); }}
  }};
  $('list').appendChild(row);
}}

function renderBulk(){{
  var drafts=[].slice.call(document.querySelectorAll('.prow')).filter(function(r){{return r.querySelector('.status').value==='DRAFT';}});
  var box=$('bulk');
  if(!drafts.length){{ box.innerHTML=''; return; }}
  box.innerHTML='<div class="pu-bulk"><span>'+drafts.length+' draft'+(drafts.length>1?'s':'')+' loaded</span>'+
    '<button id="actAll">Activate all loaded drafts</button></div>';
  $('actAll').onclick=async function(){{
    if(!confirm('Set '+drafts.length+' draft product(s) to ACTIVE (live on the store)?')) return;
    this.textContent='Activating…'; this.disabled=true;
    var ok=0;
    for(var i=0;i<drafts.length;i++){{
      var r=drafts[i];
      try{{
        await fetch(API+'/shopify/product/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{product_id:r.dataset.pid, status:'ACTIVE', title:r.querySelector('.nm b').textContent}})}});
        ok++; r.querySelector('.status').value='ACTIVE';
      }}catch(e){{}}
    }}
    toast('Activated '+ok+' product'+(ok===1?'':'s')); load(true);
  }};
}}

shell(); load(true);
</script>
<script>{WHOAMI_JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
