#!/usr/bin/env python3
"""Product builder — Shopify tool. Renders /var/www/ops/product-builder.html.

Compose a brand-new product from a title, spec, price and photos, then push it
live (or as a draft) to the store in one click. Photos can be pulled straight
from Labs Drop — the picker mints a public share link per image so Shopify can
fetch it — or pasted as URLs. All calls are admin-gated server-side.
"""
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS, RTE_JS

OUT = "/var/www/ops/product-builder.html"

# Common watch-mod part categories, offered as type suggestions.
TYPES = ["Case", "Dial", "Hands", "Movement", "Bezel", "Bezel insert",
         "Crystal", "Chapter ring", "Crown", "Strap", "Bracelet", "Gasket",
         "Case back", "Complete build", "Tool", "Accessory"]


def build():
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    type_opts = "".join(f'<option value="{t}">' for t in TYPES)
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Product builder — Labs OS</title>
<style>{HUB_STYLE}
  .pb-grid{{display:grid;grid-template-columns:1fr 320px;gap:20px;align-items:start;}}
  @media(max-width:840px){{.pb-grid{{grid-template-columns:1fr;}}}}
  .pb-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:18px;box-shadow:var(--shadow);}}
  .pb-field{{margin-bottom:15px;}}
  .pb-field:last-child{{margin-bottom:0;}}
  .pb-field label{{display:block;font-size:11.5px;font-weight:650;color:var(--muted);
    text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}}
  .pb-field .req{{color:var(--accent);}}
  .pin{{width:100%;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:10px 12px;font-family:inherit;}}
  .pin:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}}
  textarea.pin{{min-height:110px;resize:vertical;line-height:1.55;}}
  .pb-row{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
  @media(max-width:520px){{.pb-row{{grid-template-columns:1fr;}}}}
  .price-wrap{{position:relative;}}
  .price-wrap span{{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:14px;}}
  .price-wrap input{{padding-left:26px;}}
  .seg{{display:inline-flex;border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;}}
  .seg button{{border:none;background:var(--card);color:var(--muted);font:inherit;font-size:13px;
    font-weight:600;padding:9px 16px;cursor:pointer;transition:background .12s,color .12s;}}
  .seg button.active{{background:var(--ink);color:var(--bg);}}
  .seg button+button{{border-left:1px solid var(--border);}}
  .imgstrip{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px;}}
  .imgcell{{position:relative;width:72px;height:72px;border-radius:9px;overflow:hidden;
    border:1px solid var(--border);background:var(--card-2);}}
  .imgcell img{{width:100%;height:100%;object-fit:cover;}}
  .imgcell .rm{{position:absolute;top:2px;right:2px;width:20px;height:20px;border-radius:50%;
    border:none;background:rgba(0,0,0,.62);color:#fff;font-size:12px;cursor:pointer;
    display:flex;align-items:center;justify-content:center;}}
  .imgcell.first::after{{content:"Cover";position:absolute;bottom:0;left:0;right:0;
    background:var(--accent);color:#fff;font-size:9px;font-weight:700;text-align:center;
    text-transform:uppercase;letter-spacing:.04em;padding:1px 0;}}
  .img-actions{{display:flex;gap:8px;flex-wrap:wrap;}}
  .btn{{font-size:13px;font-weight:650;border-radius:var(--r-s);padding:9px 15px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);
    display:inline-flex;align-items:center;gap:7px;transition:border-color .12s,transform .1s;}}
  .btn:hover{{border-color:var(--border-2);}}
  .btn:active{{transform:scale(.97);}}
  .btn svg{{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;
    stroke-linecap:round;stroke-linejoin:round;}}
  .btn.ai{{width:100%;justify-content:center;margin-top:9px;background:var(--accent-bg);
    border-color:transparent;color:var(--accent);font-weight:700;}}
  .btn.ai:hover{{border-color:var(--accent);}}
  .btn.ai svg{{stroke:var(--accent);}}
  .btn.primary{{background:var(--ink);color:var(--bg);border-color:var(--ink);width:100%;
    justify-content:center;padding:13px;font-size:14.5px;}}
  .btn.primary[disabled]{{opacity:.45;cursor:not-allowed;}}
  .urlrow{{display:flex;gap:8px;margin-top:8px;}}
  .urlrow input{{flex:1;font-size:13px;}}
  .hint{{font-size:11.5px;color:var(--muted);margin-top:7px;line-height:1.5;}}
  /* live preview */
  /* Sticky has to sit on the grid item, not on something inside it. .pb-grid
     is align-items:start, so .pv-wrap is exactly as tall as the preview and
     the old `position:sticky` on .pv had no room to travel in — the preview
     scrolled away the moment the form got long, which is the one time it is
     worth having. Only in the two-column layout: stacked on a phone, a
     pinned preview would just cover the field being typed into. */
  .pv-wrap{{min-width:0;}}
  @media(min-width:841px){{.pv-wrap{{position:sticky;top:14px;}}}}
  .pv{{min-width:0;}}
  .pv-label{{font-size:11px;font-weight:650;color:var(--muted);text-transform:uppercase;
    letter-spacing:.05em;margin-bottom:9px;}}
  .pv-img{{width:100%;aspect-ratio:1;border-radius:var(--r-s);object-fit:cover;background:var(--card-2);
    display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:26px;}}
  .pv-body{{padding:13px 2px 2px;}}
  .pv-type{{font-size:11px;font-weight:650;color:var(--accent);text-transform:uppercase;letter-spacing:.04em;}}
  .pv-title{{font-size:16px;font-weight:700;color:var(--ink);margin:3px 0 4px;line-height:1.25;}}
  .pv-price{{font-size:15px;font-weight:650;color:var(--ink);}}
  .pv-tags{{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px;}}
  .pv-tag{{font-size:11px;background:var(--card-2);color:var(--muted);border-radius:5px;padding:2px 7px;}}
  .pv-status{{display:inline-block;margin-top:10px;font-size:10.5px;font-weight:650;text-transform:uppercase;
    letter-spacing:.04em;padding:3px 8px;border-radius:6px;}}
  .pv-status.active{{color:var(--good);background:var(--good-bg);}}
  .pv-status.draft{{color:var(--accent);background:var(--accent-bg);}}
  /* success */
  .done{{text-align:center;padding:22px;}}
  .done .tick{{width:56px;height:56px;border-radius:50%;background:var(--good-bg);color:var(--good);
    display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 14px;}}
  .done h2{{font-size:19px;margin:0 0 4px;}}
  .done p{{color:var(--muted);font-size:14px;margin:0 0 18px;}}
  .done .row{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;}}
  /* drop picker */
  .modal{{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:flex-end;
    justify-content:center;z-index:60;}}
  .modal.open{{display:flex;}}
  @media(min-width:640px){{.modal{{align-items:center;}}}}
  .sheet{{background:var(--bg);width:100%;max-width:640px;max-height:85vh;border-radius:16px 16px 0 0;
    display:flex;flex-direction:column;overflow:hidden;box-shadow:var(--shadow-lg);}}
  @media(min-width:640px){{.sheet{{border-radius:16px;}}}}
  .sheet header{{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--border);
    position:static;background:var(--bg);}}
  .sheet header b{{flex:1;font-size:15px;}}
  .sheet .x{{border:none;background:none;font-size:22px;color:var(--muted);cursor:pointer;line-height:1;}}
  .crumbs{{font-size:12.5px;color:var(--muted);padding:9px 16px;border-bottom:1px solid var(--border);}}
  .crumbs a{{color:var(--accent);cursor:pointer;}}
  .pick-grid{{overflow:auto;padding:12px 16px;display:grid;align-items:start;
    grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;}}
  .pick{{position:relative;border-radius:9px;overflow:hidden;border:2px solid transparent;cursor:pointer;
    aspect-ratio:1;background:var(--card-2);}}
  .pick.sel{{border-color:var(--accent);}}
  .pick.folder{{display:flex;flex-direction:column;align-items:center;justify-content:center;
    color:var(--muted);gap:4px;padding:6px;text-align:center;}}
  .pick.folder svg{{width:26px;height:26px;stroke:var(--accent);fill:none;stroke-width:1.6;}}
  .pick.folder small{{font-size:10.5px;line-height:1.2;overflow:hidden;max-height:24px;}}
  .pick img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;}}
  .pick .chk{{position:absolute;top:4px;right:4px;width:19px;height:19px;border-radius:50%;
    background:var(--accent);color:#fff;font-size:12px;display:none;align-items:center;justify-content:center;}}
  .pick.sel .chk{{display:flex;}}
  .sheet footer{{padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:10px;align-items:center;}}
  .sheet footer .btn.primary{{width:auto;flex:1;padding:11px;}}
</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Product builder</h1>
      <p class="page-sub">Compose a new product, drop in photos from Labs Drop, and publish it to your store. Start it as a draft and go live when it's ready.</p>
    </div>
    <div id="pb-root"></div>
    {hub_footer()}
  </main>
</div>

<div class="modal" id="picker"><div class="sheet">
  <header><b>Pick photos from Drop</b><button class="x" id="pk-x">&times;</button></header>
  <div class="crumbs" id="pk-crumbs"></div>
  <div class="pick-grid" id="pk-grid"></div>
  <footer>
    <span class="hint" id="pk-count" style="margin:0;flex:1"></span>
    <button class="btn primary" id="pk-add" style="width:auto">Add photos</button>
  </footer>
</div></div>

<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>{RTE_JS}</script>
<script>
'use strict';
var API='/ops/agent/api', DROP='/drop/api';
function $(id){{return document.getElementById(id);}}
function esc(s){{var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}}
var toastT;
function toast(m){{var t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(function(){{t.classList.remove('show');}},2800);}}
async function api(path,opts){{
  var res=await fetch(API+path,opts);
  if(res.status===403){{location.href='/oauth2/start?rd=/ops/product-builder.html';throw new Error('auth');}}
  var d=await res.json().catch(function(){{return {{}};}});
  if(!res.ok)throw new Error(d.error||'failed');
  return d;
}}

var form={{title:'',type:'',price:'',tags:'',desc:'',status:'DRAFT',images:[]}};
var descRTE=null;

function render(){{
  $('pb-root').innerHTML=
  '<div class="pb-grid">'+
    '<div class="pb-card">'+
      '<div class="pb-field"><label>Title <span class="req">*</span></label>'+
        '<input id="f-title" class="pin" placeholder="e.g. NH35 Sapphire Double-Dome Crystal" autofocus></div>'+
      '<div class="pb-row">'+
        '<div class="pb-field"><label>Type</label>'+
          '<input id="f-type" class="pin" list="types" placeholder="Case, Dial, Movement…"><datalist id="types">{type_opts}</datalist></div>'+
        '<div class="pb-field"><label>Price</label>'+
          '<div class="price-wrap"><span>&#8377;</span><input id="f-price" class="pin" type="number" step="0.01" min="0" placeholder="0.00"></div></div>'+
      '</div>'+
      '<div class="pb-field"><label>Tags <small style="text-transform:none;font-weight:400">(comma-separated)</small></label>'+
        '<input id="f-tags" class="pin" placeholder="seiko-mod, nh35, sapphire"></div>'+
      '<div class="pb-field"><label>Description</label>'+
        '<div id="f-desc"></div></div>'+
      '<div class="pb-field"><label>Photos</label>'+
        '<div class="imgstrip" id="strip"></div>'+
        '<div class="img-actions">'+
          '<button class="btn" id="from-drop"><svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>Add from Drop</button>'+
          '<button class="btn" id="from-url"><svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>Paste URL</button>'+
        '</div>'+
        '<div class="urlrow" id="urlrow" style="display:none"><input id="f-url" class="pin" placeholder="https://… image link"><button class="btn" id="url-add">Add</button></div>'+
        '<button class="btn ai" id="ai-draft"><svg viewBox="0 0 24 24"><path d="M9 3l1.2 3.3L13.5 7.5 10.2 8.7 9 12 7.8 8.7 4.5 7.5l3.3-1.2z"/><path d="M17 12l.8 2.2L20 15l-2.2.8L17 18l-.8-2.2L14 15l2.2-.8z"/></svg>Draft the details with AI</button>'+
        '<div class="hint">Add photos, then let AI read them and write the title, type, tags &amp; description. Photos from Drop are shared automatically so Shopify can fetch them; the first is the cover.</div>'+
      '</div>'+
      '<div class="pb-field"><label>Publish as</label>'+
        '<div class="seg" id="status-seg">'+
          '<button data-s="DRAFT" class="active">Draft</button>'+
          '<button data-s="ACTIVE">Live now</button>'+
        '</div>'+
        '<div class="hint">Draft is saved to Shopify but hidden from shoppers until you activate it.</div>'+
      '</div>'+
      '<button class="btn primary" id="create" disabled>Create product</button>'+
    '</div>'+
    '<div class="pv-wrap"><div class="pv pb-card"><div class="pv-label">Live preview</div>'+
      '<div class="pv-img" id="pv-img">&#9678;</div>'+
      '<div class="pv-body">'+
        '<div class="pv-type" id="pv-type"></div>'+
        '<div class="pv-title" id="pv-title">Untitled product</div>'+
        '<div class="pv-price" id="pv-price"></div>'+
        '<div class="pv-tags" id="pv-tags"></div>'+
        '<span class="pv-status draft" id="pv-status">Draft</span>'+
      '</div></div></div>'+
  '</div>';

  bind('f-title','title');bind('f-type','type');bind('f-price','price');bind('f-tags','tags');
  descRTE=LabsRTE.mount({{mount:'f-desc',html:form.desc,placeholder:'What it is, what it fits, materials, sizing…',onChange:function(h){{form.desc=h;}}}});
  $('status-seg').querySelectorAll('button').forEach(function(b){{b.onclick=function(){{
    $('status-seg').querySelectorAll('button').forEach(function(x){{x.classList.remove('active');}});
    b.classList.add('active');form.status=b.dataset.s;sync();
  }};}});
  $('from-drop').onclick=openPicker;
  $('from-url').onclick=function(){{var r=$('urlrow');r.style.display=r.style.display==='none'?'flex':'none';if(r.style.display==='flex')$('f-url').focus();}};
  $('url-add').onclick=function(){{var u=$('f-url').value.trim();if(!u)return;if(!/^https?:\\/\\//.test(u)){{toast('Give a full https:// link');return;}}form.images.push(u);$('f-url').value='';drawImgs();sync();}};
  $('f-url').addEventListener('keydown',function(e){{if(e.key==='Enter'){{e.preventDefault();$('url-add').click();}}}});
  $('ai-draft').onclick=aiDraft;
  $('create').onclick=submit;
  drawImgs();sync();
}}

async function aiDraft(){{
  var hint=($('f-title').value||'').trim();
  if(!form.images.length && !hint){{ toast('Add a photo first, or type a rough idea in the title'); return; }}
  var btn=$('ai-draft'); var lbl=btn.innerHTML; btn.disabled=true; btn.textContent='Reading the photos…';
  try{{
    var d=await api('/shopify/product/ai-draft',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{images:form.images,hint:hint}})}});
    if(d.title){{ form.title=d.title; $('f-title').value=d.title; }}
    if(d.type){{ form.type=d.type; $('f-type').value=d.type; }}
    if(d.tags&&d.tags.length){{ form.tags=d.tags.join(', '); $('f-tags').value=form.tags; }}
    if(d.description){{ form.desc=d.description; if(descRTE)descRTE.setHTML(d.description); }}
    if(d.price){{ form.price=String(d.price); $('f-price').value=form.price; }}
    sync();
    toast('Drafted with AI — review and tweak, then create');
  }}catch(e){{ toast(e.message); }}
  btn.disabled=false; btn.innerHTML=lbl;
}}
function bind(id,key){{$(id).addEventListener('input',function(){{form[key]=$(id).value;sync();}});}}

function drawImgs(){{
  var s=$('strip');
  if(!form.images.length){{s.innerHTML='<span class="hint" style="margin:0">No photos yet.</span>';return;}}
  s.innerHTML=form.images.map(function(u,i){{
    return '<div class="imgcell'+(i===0?' first':'')+'"><img src="'+esc(u)+'" alt=""><button class="rm" data-i="'+i+'">&times;</button></div>';
  }}).join('');
  s.querySelectorAll('.rm').forEach(function(b){{b.onclick=function(){{form.images.splice(+b.dataset.i,1);drawImgs();sync();}};}});
}}

function sync(){{
  $('pv-title').textContent=form.title||'Untitled product';
  $('pv-type').textContent=form.type||'';
  $('pv-price').textContent=form.price?('\\u20B9'+Number(form.price).toLocaleString('en-IN')):'';
  var tags=form.tags.split(',').map(function(t){{return t.trim();}}).filter(Boolean);
  $('pv-tags').innerHTML=tags.slice(0,6).map(function(t){{return '<span class="pv-tag">'+esc(t)+'</span>';}}).join('');
  var img=$('pv-img');
  if(form.images.length){{img.innerHTML='<img src="'+esc(form.images[0])+'" style="width:100%;height:100%;object-fit:cover;border-radius:inherit" alt="">';}}
  else img.innerHTML='&#9678;';
  var st=$('pv-status');
  st.textContent=form.status==='ACTIVE'?'Live':'Draft';
  st.className='pv-status '+(form.status==='ACTIVE'?'active':'draft');
  $('create').disabled=!form.title.trim();
  $('create').textContent=form.status==='ACTIVE'?'Create & publish product':'Create draft product';
}}

async function submit(){{
  var btn=$('create');btn.disabled=true;var label=btn.textContent;btn.textContent='Creating…';
  try{{
    var d=await api('/shopify/product/create',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{
      title:form.title.trim(),type:form.type.trim(),price:form.price||null,
      tags:form.tags,description:form.desc,status:form.status,images:form.images
    }})}});
    done(d);
  }}catch(e){{toast(e.message);btn.disabled=false;btn.textContent=label;}}
}}

function done(d){{
  var live=d.status==='ACTIVE';
  $('pb-root').innerHTML=
    '<div class="pb-card done">'+
      '<div class="tick">&#10003;</div>'+
      '<h2>'+esc(form.title)+' created</h2>'+
      '<p>'+(live?'It\\'s live on your store now.':'Saved as a draft — activate it when you\\'re ready.')+'</p>'+
      '<div class="row">'+
        '<a class="btn" href="'+esc(d.admin_url)+'" target="_blank" rel="noopener">Open in Shopify</a>'+
        (live?'':'<a class="btn" href="/ops/product-updater.html">Manage in Quick updater</a>')+
        '<button class="btn primary" id="again" style="width:auto">Create another</button>'+
      '</div>'+
    '</div>';
  $('again').onclick=function(){{form={{title:'',type:'',price:'',tags:'',desc:'',status:'DRAFT',images:[]}};render();}};
}}

/* ---- Drop photo picker ---- */
var pk={{path:'',sel:{{}}}};
function openPicker(){{pk.sel={{}};$('picker').classList.add('open');loadDrop('');}}
$('pk-x').onclick=function(){{$('picker').classList.remove('open');}};
$('picker').addEventListener('click',function(e){{if(e.target===$('picker'))$('picker').classList.remove('open');}});
async function loadDrop(path){{
  pk.path=path;
  $('pk-grid').innerHTML='<span class="hint" style="grid-column:1/-1">Loading…</span>';
  var crumbs='<a data-p="">Drop</a>';var acc='';
  path.split('/').filter(Boolean).forEach(function(seg){{acc=(acc?acc+'/':'')+seg;crumbs+=' / <a data-p="'+esc(acc)+'">'+esc(seg)+'</a>';}});
  $('pk-crumbs').innerHTML=crumbs;
  $('pk-crumbs').querySelectorAll('a').forEach(function(a){{a.onclick=function(){{loadDrop(a.dataset.p);}};}});
  try{{
    var d=await api2(DROP+'/list?path='+encodeURIComponent(path));
    var html='';
    (d.dirs||[]).forEach(function(dir){{
      html+='<div class="pick folder" data-dir="'+esc(dir.name)+'"><svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg><small>'+esc(dir.name)+'</small></div>';
    }});
    (d.files||[]).filter(function(f){{return f.kind==='image';}}).forEach(function(f){{
      var full=(path?path+'/':'')+f.name;
      html+='<div class="pick" data-name="'+esc(f.name)+'"><img loading="lazy" src="'+DROP+'/thumb?path='+encodeURIComponent(full)+'&big=1" alt=""><span class="chk">&#10003;</span></div>';
    }});
    $('pk-grid').innerHTML=html||'<span class="hint" style="grid-column:1/-1">No photos in this folder.</span>';
    $('pk-grid').querySelectorAll('.pick.folder').forEach(function(el){{el.onclick=function(){{loadDrop((path?path+'/':'')+el.dataset.dir);}};}});
    $('pk-grid').querySelectorAll('.pick:not(.folder)').forEach(function(el){{el.onclick=function(){{
      var key=(path?path+'/':'')+el.dataset.name;
      if(pk.sel[key]){{delete pk.sel[key];el.classList.remove('sel');}}
      else{{pk.sel[key]={{path:path,name:el.dataset.name}};el.classList.add('sel');}}
      updatePkCount();
    }};}});
  }}catch(e){{$('pk-grid').innerHTML='<span class="hint" style="grid-column:1/-1">'+esc(e.message)+'</span>';}}
}}
function updatePkCount(){{var n=Object.keys(pk.sel).length;$('pk-count').textContent=n?(n+' selected'):'';$('pk-add').textContent=n?('Add '+n+' photo'+(n>1?'s':'')):'Add photos';}}
async function api2(url,opts){{
  var res=await fetch(url,opts);
  if(res.status===403){{location.href='/oauth2/start?rd=/ops/product-builder.html';throw new Error('auth');}}
  var d=await res.json().catch(function(){{return {{}};}});
  if(!res.ok)throw new Error(d.error||'failed');
  return d;
}}
$('pk-add').onclick=async function(){{
  var items=Object.values(pk.sel);
  if(!items.length){{$('picker').classList.remove('open');return;}}
  this.disabled=true;this.textContent='Sharing…';var added=0;
  for(var i=0;i<items.length;i++){{
    try{{
      var s=await api2(DROP+'/share',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(items[i])}});
      if(s.url){{form.images.push(s.url+'/raw');added++;}}
    }}catch(e){{}}
  }}
  this.disabled=false;this.textContent='Add photos';
  $('picker').classList.remove('open');
  drawImgs();sync();
  toast(added+' photo'+(added===1?'':'s')+' added');
}};

render();
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
