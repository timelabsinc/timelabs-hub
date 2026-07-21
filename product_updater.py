#!/usr/bin/env python3
"""Quick product updater — Shopify tool. Renders /var/www/ops/product-updater.html.

Fast inline price/status edits, an expandable deep editor (title, type, tags,
description, images), and bulk activate. All data is fetched live client-side
from /ops/agent/api/shopify/* (admin-gated); this generator emits shell + JS.
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
<meta name="color-scheme" content="light dark"><title>Quick product updater — Timelabs OS</title>
<style>{HUB_STYLE}
  .pu-controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px;}}
  .pu-search{{flex:1;min-width:200px;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--card);color:var(--ink);padding:9px 12px;}}
  .pu-search:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  .seg button{{cursor:pointer;}}
  .pwrap{{border:1px solid var(--border);border-radius:var(--r);background:var(--card);margin-bottom:8px;
    box-shadow:var(--shadow);overflow:hidden;}}
  .prow{{display:grid;grid-template-columns:46px 1fr 108px 116px 40px;gap:12px;align-items:center;padding:10px 12px;}}
  .prow img{{width:46px;height:46px;object-fit:cover;border-radius:8px;background:var(--card-2);}}
  .prow .ph{{width:46px;height:46px;border-radius:8px;background:var(--card-2);display:flex;align-items:center;
    justify-content:center;color:var(--muted);font-size:18px;}}
  .prow .nm{{min-width:0;}}
  .prow .nm b{{font-size:13.5px;font-weight:600;color:var(--ink);display:block;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}}
  .prow .nm small{{font-size:11.5px;color:var(--muted);}}
  .pinput{{font-size:13.5px;border:1px solid var(--border);border-radius:7px;background:var(--bg);
    color:var(--ink);padding:7px 9px;width:100%;font-family:inherit;}}
  .pinput:focus{{outline:none;border-color:var(--accent);}}
  .pinput.dirty{{border-color:var(--accent);background:var(--accent-bg);}}
  .editbtn{{width:34px;height:34px;border-radius:8px;border:1px solid var(--border);background:var(--card);
    color:var(--muted);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;
    transition:transform .2s var(--ease);}}
  .editbtn:hover{{color:var(--ink);border-color:var(--border-2);}}
  .pwrap.open .editbtn{{transform:rotate(180deg);color:var(--accent);}}
  .pdetail{{display:none;border-top:1px solid var(--border);padding:14px;background:var(--card-2);}}
  .pwrap.open .pdetail{{display:block;animation:fadeup .2s var(--ease);}}
  @keyframes fadeup{{from{{opacity:0;transform:translateY(-4px)}}to{{opacity:1;transform:none}}}}
  .pfield{{margin-bottom:11px;}}
  .pfield label{{display:block;font-size:11.5px;font-weight:650;color:var(--muted);text-transform:uppercase;
    letter-spacing:.04em;margin-bottom:5px;}}
  .pfield textarea{{min-height:80px;resize:vertical;line-height:1.5;}}
  .pimgs{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}}
  .pimg{{position:relative;width:64px;height:64px;border-radius:8px;overflow:hidden;border:1px solid var(--border);}}
  .pimg img{{width:100%;height:100%;object-fit:cover;}}
  .pimg .rm{{position:absolute;top:2px;right:2px;width:20px;height:20px;border-radius:50%;border:none;
    background:rgba(0,0,0,.6);color:#fff;font-size:12px;cursor:pointer;display:flex;align-items:center;justify-content:center;}}
  .addimg{{display:flex;gap:6px;margin-top:8px;}}
  .addimg input{{flex:1;font-size:12.5px;}}
  .addimg button, .savebtn, .pu-bulk button{{font-size:12.5px;font-weight:650;border:none;border-radius:7px;
    background:var(--ink);color:var(--bg);padding:8px 14px;cursor:pointer;}}
  .savebtn{{margin-top:6px;}}
  .savebtn[disabled]{{opacity:.45;cursor:not-allowed;}}
  .pu-bulk{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0;padding:11px 14px;
    background:var(--accent-bg);border-radius:var(--r);font-size:13.5px;color:var(--ink);}}
  .pu-setup{{background:var(--accent-bg);border-radius:var(--r);padding:16px;text-align:center;color:var(--ink);}}
  .pu-more{{display:block;margin:14px auto 0;font-size:13px;font-weight:600;border:1px solid var(--border);
    background:var(--card);color:var(--ink);border-radius:var(--r-s);padding:9px 18px;cursor:pointer;}}
  .hint{{font-size:11.5px;color:var(--muted);margin-top:4px;}}
  @media (max-width:640px){{
    .prow{{grid-template-columns:40px 1fr 40px;gap:8px;}}
    .prow .price,.prow .status{{grid-column:2;}}
  }}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Quick product updater</h1>
      <p class="page-sub">Search, re-price, edit details &amp; images, and launch products. Changes go live on your store immediately.</p>
    </div>
    <div id="pu-body"></div>
    {hub_footer()}
  </main>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
var API = '/ops/agent/api';
var state = {{ q:'', filter:'all', cursor:null, hasMore:false }};
function $(id){{ return document.getElementById(id); }}
function esc(s){{ var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }}
var toastT;
function toast(m){{ var t=$('toast'); t.textContent=m; t.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(function(){{t.classList.remove('show');}},2800); }}
async function api(path, opts){{
  var res=await fetch(API+path, opts);
  if(res.status===403){{ location.href='/oauth2/start?rd=/ops/product-updater.html'; throw new Error('auth'); }}
  var d=await res.json().catch(function(){{return {{}};}});
  if(!res.ok) throw new Error(d.error||'failed');
  return d;
}}

function shell(){{
  $('pu-body').innerHTML =
    '<div class="pu-controls">' +
      '<input id="q" class="pu-search" type="search" placeholder="Search products, SKU…">' +
      '<div class="seg" id="filter">' +
        '<button data-f="all" class="active">All</button>' +
        '<button data-f="active">Active</button>' +
        '<button data-f="draft">Draft</button>' +
      '</div>' +
    '</div><div id="bulk"></div><div id="list"></div>' +
    '<button id="more" class="pu-more" style="display:none">Load more</button>';
  var t=null;
  $('q').addEventListener('input', function(){{ clearTimeout(t); t=setTimeout(function(){{ state.q=$('q').value.trim(); load(true); }},350); }});
  $('filter').querySelectorAll('button').forEach(function(b){{ b.onclick=function(){{
    $('filter').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
    b.classList.add('active'); state.filter=b.dataset.f; load(true);
  }}; }});
  $('more').onclick=function(){{ load(false); }};
}}
function buildQuery(){{ var p=[]; if(state.filter!=='all') p.push('status:'+state.filter); if(state.q) p.push(state.q); return p.join(' '); }}

async function load(reset){{
  if(reset){{ state.cursor=null; $('list').innerHTML='<p class="empty" style="padding:20px">Loading…</p>'; }}
  try{{
    var d=await api('/shopify/products?q='+encodeURIComponent(buildQuery())+(state.cursor?'&after='+encodeURIComponent(state.cursor):''));
    state.cursor=d.cursor; state.hasMore=d.has_more;
    if(reset) $('list').innerHTML='';
    if(reset && !d.products.length) $('list').innerHTML='<p class="empty" style="padding:20px">No products match.</p>';
    d.products.forEach(addRow);
    $('more').style.display=state.hasMore?'block':'none';
    renderBulk();
  }}catch(e){{ if(e.message!=='auth') $('list').innerHTML='<div class="pu-setup">'+esc(e.message)+'</div>'; }}
}}

function addRow(p){{
  var wrap=document.createElement('div'); wrap.className='pwrap'; wrap.dataset.pid=p.id;
  var img = p.image ? '<img loading="lazy" src="'+esc(p.image)+'" alt="">' : '<div class="ph">◷</div>';
  wrap.innerHTML =
    '<div class="prow">'+img+
    '<div class="nm"><b>'+esc(p.title)+'</b><small>'+esc(p.sku||p.type||'')+(p.inventory!=null?' · '+p.inventory+' in stock':'')+'</small></div>'+
    '<input class="pinput price" type="number" step="0.01" value="'+esc(p.price||'')+'" '+(p.variant_id?'':'disabled')+' title="Price">'+
    '<select class="pinput status">'+['ACTIVE','DRAFT','ARCHIVED'].map(function(s){{return '<option value="'+s+'"'+(s===p.status?' selected':'')+'>'+s.charAt(0)+s.slice(1).toLowerCase()+'</option>';}}).join('')+'</select>'+
    '<button class="editbtn" title="Edit details &amp; images">⌄</button>'+
    '</div><div class="pdetail"></div>';
  var priceI=wrap.querySelector('.price'), statusI=wrap.querySelector('.status');
  var orig={{price:p.price, status:p.status}};
  var qsT=null;
  function quickSave(){{ clearTimeout(qsT); qsT=setTimeout(async function(){{
    var body={{product_id:p.id, variant_id:p.variant_id, title:p.title}};
    if(priceI.value!==(orig.price||'')) body.price=priceI.value;
    if(statusI.value!==orig.status) body.status=statusI.value;
    try{{ await api('/shopify/product/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
      orig.price=priceI.value; orig.status=statusI.value; priceI.classList.remove('dirty'); statusI.classList.remove('dirty');
      toast(p.title+' saved'); renderBulk();
    }}catch(e){{ toast(e.message); }}
  }},700); }}
  function rowDirty(){{
    priceI.classList.toggle('dirty', priceI.value!==(orig.price||''));
    statusI.classList.toggle('dirty', statusI.value!==orig.status);
    if(priceI.value!==(orig.price||'') || statusI.value!==orig.status) quickSave();
  }}
  priceI.addEventListener('change', rowDirty);
  statusI.addEventListener('change', rowDirty);
  wrap.querySelector('.editbtn').onclick=function(){{ toggleDetail(wrap, p); }};
  $('list').appendChild(wrap);
}}

async function toggleDetail(wrap, p){{
  if(wrap.classList.contains('open')){{ wrap.classList.remove('open'); return; }}
  wrap.classList.add('open');
  var box=wrap.querySelector('.pdetail');
  if(box.dataset.loaded) return;
  box.innerHTML='<p class="empty">Loading details…</p>';
  try{{
    var d=await api('/shopify/product/detail?id='+encodeURIComponent(p.id));
    box.dataset.loaded='1';
    box.innerHTML=
      '<div class="pfield"><label>Title</label><input class="pinput f-title" value="'+esc(d.title)+'"></div>'+
      '<div class="pfield"><label>Product type</label><input class="pinput f-type" value="'+esc(d.type)+'" placeholder="Case, Dial, Movement…"></div>'+
      '<div class="pfield"><label>Tags (comma-separated)</label><input class="pinput f-tags" value="'+esc((d.tags||[]).join(', '))+'"></div>'+
      '<div class="pfield"><label>Description</label><textarea class="pinput f-desc">'+esc(d.description)+'</textarea></div>'+
      '<div class="pfield"><label>Images</label><div class="pimgs"></div>'+
        '<div class="addimg"><input class="pinput f-imgurl" placeholder="Paste an image URL (or a Drop share link)…"><button class="f-addimg">Add</button></div>'+
        '<div class="hint">Tip: in Drop, share an image and paste its link + <b>/raw</b> here.</div></div>'+
      '<button class="savebtn f-save" disabled>Save details</button>';
    renderImgs(box, p, d.images);
    var save=box.querySelector('.f-save');
    var dirty=function(){{ save.disabled=false; }};
    ['.f-title','.f-type','.f-tags','.f-desc'].forEach(function(sel){{ box.querySelector(sel).addEventListener('input', dirty); }});
    save.onclick=async function(){{
      save.disabled=true; save.textContent='Saving…';
      try{{
        await api('/shopify/product/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{
          product_id:p.id, title:box.querySelector('.f-title').value,
          type:box.querySelector('.f-type').value,
          description:box.querySelector('.f-desc').value,
          tags:box.querySelector('.f-tags').value.split(',').map(function(t){{return t.trim();}}).filter(Boolean)
        }})}});
        wrap.querySelector('.nm b').textContent=box.querySelector('.f-title').value;
        toast('Details saved'); save.textContent='Saved ✓';
        setTimeout(function(){{save.textContent='Save details';}},1500);
      }}catch(e){{ toast(e.message); save.disabled=false; save.textContent='Save details'; }}
    }};
    box.querySelector('.f-addimg').onclick=async function(){{
      var inp=box.querySelector('.f-imgurl'); var url=inp.value.trim(); if(!url) return;
      this.disabled=true; this.textContent='Adding…';
      try{{ await api('/shopify/product/media/add',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_id:p.id,url:url,title:d.title}})}});
        inp.value=''; toast('Image added — Shopify is processing it');
        var fresh=await api('/shopify/product/detail?id='+encodeURIComponent(p.id)); renderImgs(box, p, fresh.images);
      }}catch(e){{ toast(e.message); }}
      this.disabled=false; this.textContent='Add';
    }};
  }}catch(e){{ box.innerHTML='<div class="pu-setup">'+esc(e.message)+'</div>'; }}
}}

function renderImgs(box, p, images){{
  var strip=box.querySelector('.pimgs');
  strip.innerHTML=(images||[]).map(function(im){{
    return '<div class="pimg" data-mid="'+esc(im.id)+'"><img src="'+esc(im.url)+'" alt=""><button class="rm">✕</button></div>';
  }}).join('') || '<span class="empty" style="font-size:12.5px">No images yet.</span>';
  strip.querySelectorAll('.pimg .rm').forEach(function(b){{
    b.onclick=async function(){{
      var cell=b.closest('.pimg');
      if(!confirm('Remove this image from the product?')) return;
      try{{ await api('/shopify/product/media/remove',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_id:p.id, media_id:cell.dataset.mid, title:p.title}})}});
        cell.remove(); toast('Image removed');
      }}catch(e){{ toast(e.message); }}
    }};
  }});
}}

function renderBulk(){{
  var drafts=[].slice.call(document.querySelectorAll('.pwrap')).filter(function(w){{return w.querySelector('.status').value==='DRAFT';}});
  var box=$('bulk');
  if(!drafts.length){{ box.innerHTML=''; return; }}
  box.innerHTML='<div class="pu-bulk"><span>'+drafts.length+' draft'+(drafts.length>1?'s':'')+' loaded</span><button id="actAll">Activate all loaded drafts</button></div>';
  $('actAll').onclick=async function(){{
    if(!confirm('Set '+drafts.length+' draft product(s) to ACTIVE (live on the store)?')) return;
    this.textContent='Activating…'; this.disabled=true; var ok=0;
    for(var i=0;i<drafts.length;i++){{ var w=drafts[i];
      try{{ await api('/shopify/product/update',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_id:w.dataset.pid, status:'ACTIVE', title:w.querySelector('.nm b').textContent}})}});
        ok++; w.querySelector('.status').value='ACTIVE';
      }}catch(e){{}}
    }}
    toast('Activated '+ok+' product'+(ok===1?'':'s')); load(true);
  }};
}}

shell(); load(true);
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
