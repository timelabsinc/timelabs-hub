#!/usr/bin/env python3
"""Board — saved design/creative references. Renders /var/www/ops/board.html.

A place to keep "I like this style" references (screenshots, links) so they
don't get lost in chat or Drive — tagged, searchable, browsable. Deliberately
just a library for now: no "make content/images like this" hook is wired up
yet. That's still an open question (text-copy grounding, storefront/product
visual grounding, or actual AI image generation are all different builds —
see CONTEXT.md) and was left for a later, deliberate decision.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/board.html"

CATEGORIES = [
    "Storefront/Theme", "Social Post", "Product Photography",
    "Packaging", "Ads", "Other",
]

CSS = """
.bd-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;}
.bd-search{flex:1;min-width:160px;border:1px solid var(--border);border-radius:var(--r-s);
  background:var(--card);color:var(--ink);font:inherit;font-size:13.5px;padding:9px 12px;}
.bd-toolbar select{border:1px solid var(--border);border-radius:var(--r-s);background:var(--card);
  color:var(--ink);font:inherit;font-size:13.5px;padding:9px 10px;}
.bd-sp{flex:1;}
.bd-archtoggle{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted);
  white-space:nowrap;}
.bd-tagbar{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:16px;}
.bd-chip{border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:999px;
  padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer;transition:.15s;}
.bd-chip:hover{border-color:var(--border-2);}
.bd-chip.on{background:var(--accent);border-color:var(--accent);color:#fff;}

.bd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;}
.bd-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  overflow:hidden;box-shadow:var(--shadow);cursor:pointer;display:flex;flex-direction:column;}
.bd-card:hover{border-color:var(--border-2);}
.bd-thumb{aspect-ratio:4/3;background:var(--card-2);display:flex;align-items:center;
  justify-content:center;color:var(--muted);position:relative;}
.bd-thumb img{width:100%;height:100%;object-fit:cover;display:block;}
.bd-thumb svg{width:30px;height:30px;stroke:var(--muted);fill:none;stroke-width:1.6;}
.bd-card.archived{opacity:.55;}
.bd-arch-badge{position:absolute;top:7px;left:7px;background:rgba(0,0,0,.55);color:#fff;
  font-size:10px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;
  padding:2px 7px;border-radius:5px;}
.bd-body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px;flex:1;}
.bd-title{font-size:13.5px;font-weight:650;color:var(--ink);line-height:1.3;}
.bd-cat{font-size:11px;color:var(--accent);font-weight:600;}
.bd-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:auto;}
.bd-tag{font-size:10.5px;color:var(--muted);background:var(--card-2);border-radius:5px;
  padding:2px 7px;}
.bd-empty{color:var(--muted);font-size:13.5px;padding:40px 0;text-align:center;}

/* modal shell — same pattern as the Reddit tool's Drop picker */
.bd-modal{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;
  align-items:flex-end;justify-content:center;z-index:60;}
.bd-modal.open{display:flex;}
@media(min-width:640px){ .bd-modal{align-items:center;} }
.bd-sheet{background:var(--bg);width:100%;max-width:600px;max-height:88vh;
  border-radius:16px 16px 0 0;display:flex;flex-direction:column;overflow:hidden;
  box-shadow:var(--shadow-lg);}
@media(min-width:640px){ .bd-sheet{border-radius:16px;} }
.bd-sheet header{display:flex;align-items:center;gap:10px;padding:14px 16px;
  border-bottom:1px solid var(--border);}
.bd-sheet header b{flex:1;font-size:15px;}
.bd-sheet .x{border:none;background:none;font-size:22px;color:var(--muted);
  cursor:pointer;line-height:1;}
.bd-sheet footer{padding:12px 16px;border-top:1px solid var(--border);display:flex;
  gap:10px;align-items:center;}
.bd-formbody,.bd-viewbody{overflow:auto;padding:14px 16px;}
.bd-formbody label{display:block;font-size:12px;color:var(--muted);margin:12px 0 5px;}
.bd-formbody label:first-of-type{margin-top:0;}
.bd-hint{font-weight:400;color:var(--muted);}
.bd-formbody input,.bd-formbody select,.bd-formbody textarea{width:100%;border:1px solid var(--border);
  border-radius:var(--r-s);background:var(--card);color:var(--ink);font:inherit;font-size:13.5px;
  padding:9px 11px;}
.bd-formbody textarea{min-height:70px;resize:vertical;}

.bd-dz{border:1.5px dashed var(--border-2);border-radius:var(--r);padding:20px;text-align:center;
  color:var(--muted);font-size:13px;cursor:pointer;transition:.15s;}
.bd-dz.over{border-color:var(--accent);color:var(--accent);}
.bd-dz u{color:var(--accent);text-decoration:none;}
.bd-shots{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;}
.bd-shot{position:relative;width:72px;height:72px;border-radius:8px;overflow:hidden;
  background:var(--card-2);}
.bd-shot img{width:100%;height:100%;object-fit:cover;display:block;}
.bd-shot .up{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,.35);color:#fff;font-size:10.5px;}
.bd-shot .rm{position:absolute;top:2px;right:2px;width:18px;height:18px;border-radius:50%;
  border:none;background:rgba(0,0,0,.6);color:#fff;font-size:12px;line-height:1;cursor:pointer;}

.bd-view .bd-viewshots{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;}
.bd-view .bd-viewshots img{width:100%;max-width:220px;border-radius:var(--r-s);display:block;}
.bd-view dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:13px;}
.bd-view dt{color:var(--muted);}
.bd-view dd{margin:0;color:var(--ink);}
.bd-view a{color:var(--accent);}

.bd-del{color:var(--bad);border-color:var(--border);}
.bd-del:hover{background:var(--bad-bg);border-color:var(--bad);}
"""

JS = r"""
'use strict';
var API='/ops/agent/api';
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
var toastT;
function toast(m){
  var t=$('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(function(){t.classList.remove('show');},3400);
}
async function api(path,opts){
  var res=await fetch(API+path,opts);
  if(res.status===403){location.href='/oauth2/start?rd=/ops/board.html';throw new Error('auth');}
  var d=await res.json().catch(function(){return {};});
  if(!res.ok){throw new Error(d.error||'failed');}
  return d;
}
function jpost(path,body){
  return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}

var refs=[];
var activeTag='',activeCategory='',activeQ='';
var showArchived=false;
var editingId=null;

function photoUrl(id,n){return API+'/references/photo?id='+id+'&n='+n;}

async function loadRefs(){
  var qs='?';
  if(activeTag)qs+='tag='+encodeURIComponent(activeTag)+'&';
  if(activeCategory)qs+='category='+encodeURIComponent(activeCategory)+'&';
  if(activeQ)qs+='q='+encodeURIComponent(activeQ)+'&';
  if(showArchived)qs+='archived=1&';
  try{
    var d=await api('/references/list'+qs);
    refs=d.references||[];
  }catch(e){toast(e.message);refs=[];}
  drawTagbar();
  drawGrid();
}

function drawTagbar(){
  var all={};
  refs.forEach(function(r){(r.tags||[]).forEach(function(t){all[t]=1;});});
  var tags=Object.keys(all).sort();
  var html=tags.map(function(t){
    return '<button type="button" class="bd-chip'+(t===activeTag?' on':'')+'" data-tag="'+esc(t)+'">'+esc(t)+'</button>';
  }).join('');
  $('bd-tagbar').innerHTML=html;
  $('bd-tagbar').querySelectorAll('.bd-chip').forEach(function(el){
    el.onclick=function(){
      activeTag=(activeTag===el.getAttribute('data-tag'))?'':el.getAttribute('data-tag');
      loadRefs();
    };
  });
}

function drawGrid(){
  $('bd-empty').style.display=refs.length?'none':'block';
  $('bd-grid').innerHTML=refs.map(function(r){
    var thumb=r.photo_count>0
      ? '<img src="'+photoUrl(r.id,0)+'" alt="" loading="lazy">'
      : '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 14l3-3 2 2 3-4"/></svg>';
    var tags=(r.tags||[]).slice(0,4).map(function(t){return '<span class="bd-tag">'+esc(t)+'</span>';}).join('');
    return '<div class="bd-card'+(r.archived?' archived':'')+'" data-id="'+r.id+'">'
      +'<div class="bd-thumb">'+thumb+(r.archived?'<span class="bd-arch-badge">Archived</span>':'')+'</div>'
      +'<div class="bd-body"><div class="bd-title">'+esc(r.title||'(untitled)')+'</div>'
      +(r.category?'<div class="bd-cat">'+esc(r.category)+'</div>':'')
      +'<div class="bd-tags">'+tags+'</div></div></div>';
  }).join('');
  $('bd-grid').querySelectorAll('.bd-card').forEach(function(el){
    el.onclick=function(){openView(parseInt(el.getAttribute('data-id'),10));};
  });
}

/* ---------------- add / edit form ---------------- */
var shots=[];
function drawShots(){
  $('bd-shots').innerHTML=shots.map(function(s){
    return '<div class="bd-shot" data-key="'+s.key+'"><img src="'+s.url+'">'
      +(s.path?'':'<div class="up">…</div>')
      +'<button type="button" class="rm" data-key="'+s.key+'">&times;</button></div>';
  }).join('');
  $('bd-shots').querySelectorAll('.rm').forEach(function(b){
    b.onclick=function(e){e.stopPropagation();removeShot(b.getAttribute('data-key'));};
  });
}
function removeShot(key){
  shots=shots.filter(function(s){
    if(s.key===key&&s.url){try{URL.revokeObjectURL(s.url);}catch(e){}}
    return s.key!==key;
  });
  drawShots();
}
async function addFiles(files){
  var list=Array.prototype.slice.call(files||[]).filter(function(f){
    return f&&f.type&&f.type.indexOf('image/')===0;});
  if(!list.length)return;
  for(var i=0;i<list.length;i++){
    if(shots.length>=8){toast('8 photos is the limit');break;}
    var f=list[i];
    var rec={key:String(Date.now())+'-'+i+'-'+Math.random().toString(36).slice(2,7),
             url:URL.createObjectURL(f),path:null};
    shots.push(rec);drawShots();
    try{
      var fd=new FormData();
      fd.append('image',f,f.name||('pasted-'+Date.now()+'.png'));
      var d=await api('/upload',{method:'POST',body:fd});
      rec.path=d.path;
    }catch(err){
      shots=shots.filter(function(x){return x.key!==rec.key;});
      toast(err.message);
    }
    drawShots();
  }
}
$('bd-dz').onclick=function(){$('bd-file').click();};
$('bd-file').onchange=function(){addFiles(this.files);this.value='';};
['dragenter','dragover'].forEach(function(ev){
  $('bd-dz').addEventListener(ev,function(e){e.preventDefault();$('bd-dz').classList.add('over');});});
['dragleave','drop'].forEach(function(ev){
  $('bd-dz').addEventListener(ev,function(e){e.preventDefault();$('bd-dz').classList.remove('over');});});
$('bd-dz').addEventListener('drop',function(e){addFiles(e.dataTransfer&&e.dataTransfer.files);});
document.addEventListener('paste',function(e){
  if(!$('bd-form-modal').classList.contains('open'))return;
  if(!e.clipboardData)return;
  var f=e.clipboardData.files;
  if(f&&f.length){addFiles(f);e.preventDefault();}
});

function openAdd(){
  editingId=null;
  $('bd-form-title').textContent='Add reference';
  $('bd-title').value='';$('bd-catsel').value='';$('bd-tags').value='';
  $('bd-note').value='';$('bd-source').value='';
  $('bd-delete').style.display='none';$('bd-archive').style.display='none';
  shots=[];drawShots();
  $('bd-form-modal').classList.add('open');
  setTimeout(function(){$('bd-title').focus();},50);
}
function openEdit(r){
  editingId=r.id;
  $('bd-form-title').textContent='Edit reference';
  $('bd-title').value=r.title||'';$('bd-catsel').value=r.category||'';
  $('bd-tags').value=(r.tags||[]).join(', ');
  $('bd-note').value=r.note||'';$('bd-source').value=r.source_url||'';
  $('bd-delete').style.display='';
  $('bd-archive').style.display='';
  $('bd-archive').textContent=r.archived?'Unarchive':'Archive';
  shots=[];drawShots();  // editing photos isn't supported yet — only new refs attach photos
  $('bd-dz').style.display='none';
  $('bd-view-modal').classList.remove('open');
  $('bd-form-modal').classList.add('open');
}
$('bd-add').onclick=openAdd;
$('bd-form-x').onclick=function(){$('bd-form-modal').classList.remove('open');$('bd-dz').style.display='';};

$('bd-save').onclick=async function(){
  if(shots.filter(function(s){return !s.path;}).length){toast('A photo is still uploading — one moment');return;}
  var title=$('bd-title').value.trim();
  var tags=$('bd-tags').value.split(',').map(function(t){return t.trim();}).filter(Boolean);
  var body={title:title,category:$('bd-catsel').value,tags:tags,
    note:$('bd-note').value.trim(),source_url:$('bd-source').value.trim()};
  var btn=$('bd-save');btn.disabled=true;var label=btn.textContent;btn.textContent='Saving…';
  try{
    if(editingId){
      await jpost('/references/update',Object.assign({id:editingId},body));
    }else{
      body.photos=shots.map(function(s){return s.path;});
      if(!body.title&&!body.photos.length){toast('add a title or at least one photo');btn.disabled=false;btn.textContent=label;return;}
      await jpost('/references/save',body);
    }
    $('bd-form-modal').classList.remove('open');$('bd-dz').style.display='';
    toast('Saved');
    loadRefs();
  }catch(e){toast(e.message);}
  btn.disabled=false;btn.textContent=label;
};
$('bd-archive').onclick=async function(){
  if(!editingId)return;
  var r=refs.filter(function(x){return x.id===editingId;})[0];
  try{
    await jpost('/references/update',{id:editingId,archived:r&&r.archived?0:1});
    $('bd-form-modal').classList.remove('open');
    toast('Updated');loadRefs();
  }catch(e){toast(e.message);}
};
$('bd-delete').onclick=async function(){
  if(!editingId)return;
  if(!confirm('Delete this reference and its photos? This cannot be undone.'))return;
  try{
    await jpost('/references/delete',{id:editingId});
    $('bd-form-modal').classList.remove('open');
    toast('Deleted');loadRefs();
  }catch(e){toast(e.message);}
};

/* ---------------- lightbox ---------------- */
function openView(id){
  var r=refs.filter(function(x){return x.id===id;})[0];
  if(!r)return;
  $('bd-view-title').textContent=r.title||'(untitled)';
  var shotsHtml='';
  for(var i=0;i<r.photo_count;i++){shotsHtml+='<img src="'+photoUrl(r.id,i)+'" alt="">';}
  var rows='';
  if(r.category)rows+='<dt>Category</dt><dd>'+esc(r.category)+'</dd>';
  if((r.tags||[]).length)rows+='<dt>Tags</dt><dd>'+r.tags.map(esc).join(', ')+'</dd>';
  if(r.note)rows+='<dt>Note</dt><dd>'+esc(r.note)+'</dd>';
  if(r.source_url)rows+='<dt>Source</dt><dd><a href="'+esc(r.source_url)+'" target="_blank" rel="noopener">'+esc(r.source_url)+'</a></dd>';
  rows+='<dt>Added</dt><dd>'+esc(r.created_at||'')+(r.created_by?' by '+esc(r.created_by):'')+'</dd>';
  $('bd-viewbody').innerHTML='<div class="bd-viewshots">'+shotsHtml+'</div><dl>'+rows+'</dl>';
  $('bd-view-edit').onclick=function(){openEdit(r);};
  $('bd-view-modal').classList.add('open');
}
$('bd-view-x').onclick=function(){$('bd-view-modal').classList.remove('open');};

/* ---------------- filters ---------------- */
var qT;
$('bd-q').addEventListener('input',function(){
  clearTimeout(qT);var v=this.value;
  qT=setTimeout(function(){activeQ=v.trim();loadRefs();},300);
});
$('bd-cat').addEventListener('change',function(){activeCategory=this.value;loadRefs();});
$('bd-showarch').addEventListener('change',function(){showArchived=this.checked;loadRefs();});

loadRefs();
"""


def build():
    header = hub_header("board")
    footer = hub_footer()
    category_opts = "".join(f'<option value="{c}">{c}</option>' for c in CATEGORIES)
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Board — Labs OS</title>
<style>{HUB_STYLE}{CSS}</style></head>
<body>
<div class="wrap">
  {header}
  <main>
    <div class="page-head">
      <h1 class="page-title">Board</h1>
      <p class="page-sub">Save a design or creative reference you like, tag it, and keep it somewhere you can find it again.</p>
    </div>

    <div class="bd-toolbar">
      <input id="bd-q" class="bd-search" type="search" placeholder="Search title or note…" aria-label="Search references">
      <select id="bd-cat" aria-label="Filter by category"><option value="">All categories</option>{category_opts}</select>
      <span class="bd-sp"></span>
      <label class="bd-archtoggle"><input type="checkbox" id="bd-showarch"> Show archived</label>
      <button type="button" class="btn primary" id="bd-add">+ Add reference</button>
    </div>
    <div class="bd-tagbar" id="bd-tagbar"></div>

    <div class="bd-grid" id="bd-grid"></div>
    <p class="bd-empty" id="bd-empty" style="display:none">No references yet — add the first one.</p>
  </main>
  {footer}
</div>

<div class="bd-modal" id="bd-form-modal"><div class="bd-sheet">
  <header><b id="bd-form-title">Add reference</b><button type="button" class="x" id="bd-form-x">&times;</button></header>
  <div class="bd-formbody">
    <div class="bd-dz" id="bd-dz">
      <input type="file" id="bd-file" accept="image/jpeg,image/png,image/webp" multiple hidden>
      <span>Drag photos here, paste (⌘V), or <u>choose files</u></span>
    </div>
    <div class="bd-shots" id="bd-shots"></div>
    <label>Title</label>
    <input id="bd-title" maxlength="200" placeholder="e.g. Minimal gold-accent product page">
    <label>Category</label>
    <select id="bd-catsel"><option value="">—</option>{category_opts}</select>
    <label>Tags <span class="bd-hint">comma-separated</span></label>
    <input id="bd-tags" placeholder="minimal, gold accent, pinterest">
    <label>Note</label>
    <textarea id="bd-note" placeholder="What you like about it, where it's from…"></textarea>
    <label>Source link <span class="bd-hint">optional</span></label>
    <input id="bd-source" placeholder="https://…">
  </div>
  <footer>
    <button type="button" class="btn bd-del" id="bd-delete" style="display:none">Delete</button>
    <button type="button" class="btn" id="bd-archive" style="display:none">Archive</button>
    <span class="bd-sp"></span>
    <button type="button" class="btn primary" id="bd-save">Save reference</button>
  </footer>
</div></div>

<div class="bd-modal" id="bd-view-modal"><div class="bd-sheet bd-view">
  <header><b id="bd-view-title"></b><button type="button" class="x" id="bd-view-x">&times;</button></header>
  <div class="bd-viewbody" id="bd-viewbody"></div>
  <footer>
    <span class="bd-sp"></span>
    <button type="button" class="btn" id="bd-view-edit">Edit</button>
  </footer>
</div></div>

<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>{WHOAMI_JS}</script>
<script>{JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
