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
.bd-chip .c{margin-left:6px;font-size:10.5px;opacity:.6;font-variant-numeric:tabular-nums;}

/* align-items:start, or one tall card stretches every card beside it to
   match and the shorter ones grow a dead zone under their title. */
.bd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));
  gap:14px;align-items:start;}
.bd-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  overflow:hidden;box-shadow:var(--shadow);cursor:pointer;display:flex;flex-direction:column;}
.bd-card:hover{border-color:var(--border-2);}
.bd-thumb{aspect-ratio:4/3;background:var(--card-2);display:flex;align-items:center;
  justify-content:center;color:var(--muted);position:relative;overflow:hidden;}
/* Absolutely positioned, not height:100%. A percentage height against a
   parent sized only by aspect-ratio is indefinite, so it resolves to auto
   and a portrait image (652x1250 here) renders full height and bursts the
   tile. Same pattern as the Reddit picker's grid. */
.bd-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  display:block;}
.bd-thumb svg{width:30px;height:30px;stroke:var(--muted);fill:none;stroke-width:1.6;}
.bd-noimg{padding:12px 13px;font-size:12px;line-height:1.5;color:var(--muted);
  overflow:hidden;text-align:left;}
.bd-card.archived{opacity:.55;}
.bd-play{position:absolute;top:7px;right:7px;width:26px;height:26px;border-radius:50%;
  background:rgba(0,0,0,.58);display:flex;align-items:center;justify-content:center;}
.bd-play svg{width:13px;height:13px;fill:#fff;stroke:none;margin-left:1px;}
.bd-arch-badge{position:absolute;top:7px;left:7px;background:rgba(0,0,0,.55);color:#fff;
  font-size:10px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;
  padding:2px 7px;border-radius:5px;}
.bd-body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px;}
.bd-title{font-size:13.5px;font-weight:650;color:var(--ink);line-height:1.3;}
.bd-cat{font-size:11px;color:var(--accent);font-weight:600;}
.bd-tags{display:flex;flex-wrap:wrap;gap:5px;}
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

/* board rail + layout */
.bd-layout{display:grid;grid-template-columns:190px 1fr;gap:22px;align-items:start;}
@media(max-width:760px){ .bd-layout{grid-template-columns:1fr;gap:14px;} }
.bd-rail{display:flex;flex-direction:column;gap:2px;}
@media(max-width:760px){
  .bd-rail{flex-direction:row;overflow-x:auto;gap:7px;padding-bottom:4px;
    -webkit-overflow-scrolling:touch;}
  .bd-rail::-webkit-scrollbar{display:none;}
}
.bd-railitem{display:flex;align-items:center;gap:8px;border:none;background:none;
  color:var(--muted);font:inherit;font-size:13px;font-weight:600;text-align:left;
  padding:8px 10px;border-radius:var(--r-s);cursor:pointer;white-space:nowrap;
  max-width:100%;overflow:hidden;}
/* A board name is free text, so it must be allowed to run out of room
   gracefully instead of pushing the count out of the pill. */
.bd-railitem>.nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
.bd-railitem:hover{background:var(--card-2);color:var(--ink);}
.bd-railitem.on{background:var(--accent-bg);color:var(--accent);}
.bd-railitem .n{margin-left:auto;font-size:11px;opacity:.75;font-variant-numeric:tabular-nums;}
@media(max-width:760px){
  .bd-railitem{border:1px solid var(--border);border-radius:999px;padding:6px 12px;}
  .bd-railitem .n{margin-left:5px;}
}
.bd-railhead{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);padding:4px 10px;margin-top:6px;}
@media(max-width:760px){ .bd-railhead{display:none;} }
.bd-newboard{color:var(--accent);}

/* fetch state */
.bd-state{position:absolute;left:7px;bottom:7px;display:inline-flex;align-items:center;gap:5px;
  background:rgba(0,0,0,.62);color:#fff;font-size:10.5px;font-weight:600;
  padding:3px 8px;border-radius:999px;}
.bd-state.err{background:var(--bad);}
.bd-spin{width:9px;height:9px;border:1.5px solid rgba(255,255,255,.4);border-top-color:#fff;
  border-radius:50%;animation:bdspin .7s linear infinite;}
@keyframes bdspin{to{transform:rotate(360deg);}}
.bd-link{font-size:11.5px;color:var(--accent);word-break:break-all;}
.bd-analysis{background:var(--card-2);border-radius:var(--r-s);padding:11px 13px;
  font-size:13px;line-height:1.6;color:var(--ink);margin-bottom:12px;}
.bd-analysis b{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);margin-bottom:5px;}
.bd-caption{font-size:12.5px;color:var(--muted);line-height:1.55;white-space:pre-line;
  max-height:150px;overflow:auto;}
.bd-failbox{background:var(--bad-bg);color:var(--bad);border-radius:var(--r-s);
  padding:10px 12px;font-size:12.5px;margin-bottom:12px;line-height:1.5;}

/* right-click menu */
.bd-menu{position:fixed;z-index:80;min-width:190px;background:var(--card);
  border:1px solid var(--border);border-radius:var(--r-s);box-shadow:var(--shadow-lg);
  padding:5px;display:none;}
.bd-menu.open{display:block;}
.bd-menu button{display:block;width:100%;text-align:left;border:none;background:none;
  color:var(--ink);font:inherit;font-size:13px;padding:8px 11px;border-radius:6px;cursor:pointer;}
.bd-menu button:hover{background:var(--card-2);}
.bd-menu button.danger{color:var(--bad);}
.bd-menu hr{border:none;border-top:1px solid var(--border);margin:4px 0;}

/* running-jobs strip — the answer to "how do I know it's working" */
.bd-jobs{display:flex;flex-direction:column;gap:7px;margin-bottom:14px;}
.bd-job{display:flex;align-items:center;gap:10px;background:var(--card);
  border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:var(--r-s);padding:10px 13px;font-size:13px;box-shadow:var(--shadow);}
.bd-job.done{border-left-color:#3f7d4f;}
.bd-job.err{border-left-color:var(--bad);}
.bd-job b{font-weight:650;color:var(--ink);}
.bd-job .sub{color:var(--muted);font-size:12.5px;}
.bd-job .go{margin-left:auto;border:none;background:none;color:var(--accent);
  font:inherit;font-size:12.5px;font-weight:650;cursor:pointer;white-space:nowrap;}
.bd-jobspin{width:12px;height:12px;border:2px solid var(--border-2);
  border-top-color:var(--accent);border-radius:50%;animation:bdspin .8s linear infinite;
  flex:none;}

/* find inspiration */
.bd-findnote{margin:0 0 4px;font-size:13px;color:var(--muted);line-height:1.55;}
.bd-sources{display:flex;flex-direction:column;gap:8px;}
.bd-source{display:block;width:100%;text-align:left;border:1px solid var(--border);
  background:var(--card);color:var(--ink);font:inherit;font-size:13.5px;font-weight:650;
  border-radius:var(--r-s);padding:11px 13px;cursor:pointer;transition:.15s;}
.bd-source:hover{border-color:var(--border-2);}
.bd-source.on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);}
.bd-source small{display:block;font-weight:400;font-size:12px;color:var(--muted);
  margin-top:2px;}
.bd-source.on small{color:var(--accent);opacity:.85;}
.bd-railitem.finding{color:var(--accent);}
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

var refs=[],boards=[],unsortedCount=0;
var activeTag='',activeCategory='',activeQ='',activeBoard='';
var showArchived=false;
var editingId=null;
var pollT=null;

function photoUrl(id,n){return API+'/references/photo?id='+id+'&n='+n;}
/* Grid tiles get a ~640px thumbnail. A fetched Instagram image is often
   2160px/600KB, and fifty of those is a broken-feeling page, not a slow one. */
function thumbUrl(id,n){return photoUrl(id,n)+'&thumb=1';}

async function loadBoards(){
  try{
    var d=await api('/boards/list');
    boards=d.boards||[];unsortedCount=d.unsorted||0;
  }catch(e){boards=[];}
  drawRail();
  drawJobs();
}

async function loadRefs(){
  var qs='?';
  if(activeTag)qs+='tag='+encodeURIComponent(activeTag)+'&';
  if(activeCategory)qs+='category='+encodeURIComponent(activeCategory)+'&';
  if(activeQ)qs+='q='+encodeURIComponent(activeQ)+'&';
  if(activeBoard)qs+='board='+encodeURIComponent(activeBoard)+'&';
  if(showArchived)qs+='archived=1&';
  try{
    var d=await api('/references/list'+qs);
    refs=d.references||[];
  }catch(e){toast(e.message);refs=[];}
  drawTagbar();
  drawGrid();
  schedulePoll();
}

/* A link fetch runs for minutes in the background. Poll only while something
   is actually in flight, then stop — no permanent timer on an idle page. */
function schedulePoll(){
  var busy=refs.some(function(r){
    return r.fetch_status==='queued'||r.fetch_status==='running';})
    ||boards.some(function(b){
    return b.discover_status==='queued'||b.discover_status==='running';});
  clearTimeout(pollT);
  if(busy)pollT=setTimeout(function(){loadBoards();loadRefs();},6000);
}

/* A search runs for minutes in the background, so the page has to say so
   plainly. A "…" on a rail pill is not an answer to "is it working?". */
var jobSeen={};
function drawJobs(){
  var live=boards.filter(function(b){
    return b.discover_status==='queued'||b.discover_status==='running';});
  var recent=boards.filter(function(b){
    return (b.discover_status==='done'||b.discover_status==='failed')
      &&jobSeen[b.id]==='running';});
  recent.forEach(function(b){jobSeen[b.id]='shown';});
  boards.forEach(function(b){
    if(b.discover_status==='queued'||b.discover_status==='running')jobSeen[b.id]='running';
  });
  var rows=live.map(function(b,i){
    var running=b.discover_status==='running';
    return '<div class="bd-job"><span class="bd-jobspin"></span><span>'
      +'<b>'+esc(b.name)+'</b> — '
      +(running?'searching the internet now'
              :'waiting its turn (#'+(i+1)+' in the queue)')
      +'<br><span class="sub">This takes a few minutes. You can leave the page; '
      +'it keeps running.</span></span>'
      +'<button type="button" class="go" data-goboard="'+b.id+'">Open</button></div>';
  });
  rows=rows.concat(recent.map(function(b){
    var ok=b.discover_status==='done';
    return '<div class="bd-job '+(ok?'done':'err')+'"><span>'
      +'<b>'+esc(b.name)+'</b> — '
      +(ok?('found '+(b.discover_found||0)+' references')
         :('couldn\'t finish: '+esc(b.discover_error||'unknown')))
      +'</span><button type="button" class="go" data-goboard="'+b.id+'">Open</button></div>';
  }));
  var host=$('bd-jobs');
  host.innerHTML=rows.join('');
  host.hidden=!rows.length;
  host.querySelectorAll('[data-goboard]').forEach(function(el){
    el.onclick=function(){
      activeBoard=el.getAttribute('data-goboard');drawRail();loadRefs();};
  });
}

function drawRail(){
  var html='<button type="button" class="bd-railitem'+(activeBoard===''?' on':'')
    +'" data-board=""><span class="nm">All</span></button>';
  html+='<button type="button" class="bd-railitem'+(activeBoard==='unsorted'?' on':'')
    +'" data-board="unsorted"><span class="nm">Unsorted</span>'
    +'<span class="n">'+unsortedCount+'</span></button>';
  if(boards.length)html+='<div class="bd-railhead">Boards</div>';
  html+=boards.map(function(b){
    var busy=b.discover_status==='queued'||b.discover_status==='running';
    var badge=busy?'<span class="n">…</span>'
      :(b.discover_status==='failed'?'<span class="n">!</span>'
        :'<span class="n">'+(b.count||0)+'</span>');
    return '<button type="button" class="bd-railitem'+(activeBoard===String(b.id)?' on':'')
      +(busy?' finding':'')+'" data-board="'+b.id+'" title="'
      +esc(b.discover_status==='failed'?(b.discover_error||'Search failed'):b.name)
      +'"><span class="nm">'+esc(b.name)+'</span>'+badge+'</button>';
  }).join('');
  html+='<button type="button" class="bd-railitem bd-newboard" id="bd-newboard">+ New board</button>';
  $('bd-rail').innerHTML=html;
  $('bd-rail').querySelectorAll('.bd-railitem[data-board]').forEach(function(el){
    el.onclick=function(){activeBoard=el.getAttribute('data-board');drawRail();loadRefs();};
  });
  $('bd-newboard').onclick=async function(){
    var name=prompt('Name this board');
    if(!name||!name.trim())return;
    try{
      var d=await jpost('/boards/save',{name:name.trim()});
      toast(d.existing?'That board already exists':'Board created');
      activeBoard=String(d.id);
      await loadBoards();loadRefs();
    }catch(e){toast(e.message);}
  };
  drawBoardOptions();
}

function drawBoardOptions(){
  var sel=$('bd-boardsel');
  var current=sel.value;
  sel.innerHTML='<option value="">Unsorted</option>'+boards.map(function(b){
    return '<option value="'+b.id+'">'+esc(b.name)+'</option>';}).join('');
  sel.value=current;
}

/* A tag only earns a chip if it actually groups something. Showing every
   unique tag turned 7 references into 41 chips, 39 of them used exactly
   once — a wall that filters nothing. Tags on a single reference stay on
   the card and remain searchable; they just don't clutter the filter bar. */
function drawTagbar(){
  var count={};
  refs.forEach(function(r){
    (r.tags||[]).forEach(function(t){count[t]=(count[t]||0)+1;});
  });
  var shared=Object.keys(count).filter(function(t){
    return count[t]>1||t===activeTag;});
  shared.sort(function(a,b){return count[b]-count[a]||a.localeCompare(b);});
  var html=shared.map(function(t){
    return '<button type="button" class="bd-chip'+(t===activeTag?' on':'')
      +'" data-tag="'+esc(t)+'">'+esc(t)
      +'<span class="c">'+count[t]+'</span></button>';
  }).join('');
  $('bd-tagbar').innerHTML=html;
  $('bd-tagbar').style.display=shared.length?'':'none';
  $('bd-tagbar').querySelectorAll('.bd-chip').forEach(function(el){
    el.onclick=function(){
      activeTag=(activeTag===el.getAttribute('data-tag'))?'':el.getAttribute('data-tag');
      loadRefs();
    };
  });
}

/* Board stores a video's poster frame and its link, never the file — so
   say so on the tile rather than letting a still pose as the reference. */
function videoBadge(r){
  return r.is_video?'<span class="bd-play" title="Video — opens at the source">'
    +'<svg viewBox="0 0 24 24"><path d="M8 5l11 7-11 7z"/></svg></span>':'';
}

function stateBadge(r){
  if(r.fetch_status==='queued')return '<span class="bd-state"><i class="bd-spin"></i>Queued</span>';
  if(r.fetch_status==='running')return '<span class="bd-state"><i class="bd-spin"></i>Reading link…</span>';
  if(r.fetch_status==='failed')return '<span class="bd-state err">Couldn\'t read link</span>';
  return '';
}

function drawGrid(){
  $('bd-empty').style.display=refs.length?'none':'block';
  $('bd-grid').innerHTML=refs.map(function(r){
    /* An idea can be worth keeping without a picture. Show the opening of
       the write-up instead of an empty frame, so it reads as a note rather
       than a broken tile. */
    var thumb=r.photo_count>0
      ? '<img src="'+thumbUrl(r.id,0)+'" alt="" loading="lazy" decoding="async">'
      : (r.analysis
          ? '<span class="bd-noimg">'+esc(r.analysis.slice(0,150))+'…</span>'
          : '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 14l3-3 2 2 3-4"/></svg>');
    var tags=(r.tags||[]).slice(0,4).map(function(t){return '<span class="bd-tag">'+esc(t)+'</span>';}).join('');
    return '<div class="bd-card'+(r.archived?' archived':'')+'" data-id="'+r.id+'">'
      +'<div class="bd-thumb">'+thumb+(r.archived?'<span class="bd-arch-badge">Archived</span>':'')
      +videoBadge(r)+stateBadge(r)+'</div>'
      +'<div class="bd-body"><div class="bd-title">'+esc(r.title||'(untitled)')+'</div>'
      +(r.category?'<div class="bd-cat">'+esc(r.category)+'</div>':'')
      +'<div class="bd-tags">'+tags+'</div></div></div>';
  }).join('');
  $('bd-grid').querySelectorAll('.bd-card').forEach(function(el){
    var id=parseInt(el.getAttribute('data-id'),10);
    el.onclick=function(){openView(id);};
    el.oncontextmenu=function(e){e.preventDefault();openMenu(e,id);};
  });
}

/* ---------------- right-click menu ---------------- */
var menuId=null;
function openMenu(e,id){
  menuId=id;
  var r=refs.filter(function(x){return x.id===id;})[0];
  if(!r)return;
  var canRead=!!r.source_url&&r.fetch_status!=='queued'&&r.fetch_status!=='running';
  $('bd-menu').innerHTML=
    '<button type="button" data-act="share">Copy share link</button>'
    +'<button type="button" data-act="open">Open reference</button>'
    +(canRead?'<button type="button" data-act="fetch">'
      +(r.fetch_status?'Read the link again':'Read &amp; analyse link')+'</button>':'')
    +'<hr>'
    +'<button type="button" data-act="edit">Edit</button>'
    +'<button type="button" data-act="archive">'+(r.archived?'Unarchive':'Archive')+'</button>'
    +'<button type="button" class="danger" data-act="delete">Delete</button>';
  var m=$('bd-menu');
  m.classList.add('open');
  /* keep it on-screen when right-clicked near an edge */
  var w=m.offsetWidth,h=m.offsetHeight;
  m.style.left=Math.min(e.clientX,window.innerWidth-w-8)+'px';
  m.style.top=Math.min(e.clientY,window.innerHeight-h-8)+'px';
  m.querySelectorAll('button').forEach(function(b){
    b.onclick=function(){menuAction(b.getAttribute('data-act'),r);};
  });
}
function closeMenu(){$('bd-menu').classList.remove('open');menuId=null;}
document.addEventListener('click',function(e){
  if(!$('bd-menu').contains(e.target))closeMenu();
});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeMenu();});
window.addEventListener('scroll',closeMenu,{passive:true});

async function menuAction(act,r){
  closeMenu();
  if(act==='open'){openView(r.id);return;}
  if(act==='edit'){openEdit(r);return;}
  if(act==='share'){shareReference(r);return;}
  if(act==='fetch'){
    try{
      await jpost('/references/fetch',{id:r.id});
      toast('Reading the link — this takes a few minutes');
      loadRefs();
    }catch(e){toast(e.message);}
    return;
  }
  if(act==='archive'){
    try{
      await jpost('/references/update',{id:r.id,archived:r.archived?0:1});
      toast('Updated');loadRefs();loadBoards();
    }catch(e){toast(e.message);}
    return;
  }
  if(act==='delete'){
    if(!confirm('Delete "'+(r.title||'this reference')+'" and its photos? This cannot be undone.'))return;
    try{
      await jpost('/references/delete',{id:r.id});
      toast('Deleted');loadRefs();loadBoards();
    }catch(e){toast(e.message);}
  }
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
  /* default into whichever board you're looking at */
  $('bd-boardsel').value=(activeBoard&&activeBoard!=='unsorted')?activeBoard:'';
  $('bd-delete').style.display='none';$('bd-archive').style.display='none';
  $('bd-dz').style.display='';
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
  $('bd-boardsel').value=r.board_id?String(r.board_id):'';
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
    note:$('bd-note').value.trim(),source_url:$('bd-source').value.trim(),
    board_id:$('bd-boardsel').value||0};
  var btn=$('bd-save');btn.disabled=true;var label=btn.textContent;btn.textContent='Saving…';
  try{
    if(editingId){
      await jpost('/references/update',Object.assign({id:editingId},body));
      toast('Saved');
    }else{
      body.photos=shots.map(function(s){return s.path;});
      if(!body.title&&!body.photos.length&&!body.source_url){
        toast('add a title, a link, or at least one photo');
        btn.disabled=false;btn.textContent=label;return;
      }
      var d=await jpost('/references/save',body);
      toast(d.fetching?'Saved — reading that link now, takes a few minutes':'Saved');
    }
    $('bd-form-modal').classList.remove('open');$('bd-dz').style.display='';
    loadRefs();loadBoards();
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
function boardName(id){
  var b=boards.filter(function(x){return x.id===id;})[0];
  return b?b.name:'Unsorted';
}
function openView(id){
  var r=refs.filter(function(x){return x.id===id;})[0];
  if(!r)return;
  $('bd-view-title').textContent=r.title||'(untitled)';
  var shotsHtml='';
  for(var i=0;i<r.photo_count;i++){shotsHtml+='<img src="'+photoUrl(r.id,i)+'" alt="">';}
  var head='';
  if(r.fetch_status==='queued'||r.fetch_status==='running'){
    head+='<div class="bd-analysis"><b>Reading the link</b>Fetching the post and looking at '
      +'the image. This takes a few minutes — you can close this and come back.</div>';
  }else if(r.fetch_status==='failed'){
    head+='<div class="bd-failbox"><b>Couldn\'t read that link.</b> '+esc(r.fetch_error||'')
      +'<br>Right-click the card to try again, or add a screenshot yourself.</div>';
  }
  if(r.analysis){
    head+='<div class="bd-analysis"><b>Style read</b>'+esc(r.analysis)+'</div>';
  }
  var rows='';
  if(r.is_video)rows+='<dt>Type</dt><dd>Video — the frame below is a still; '
    +'open the source to watch it</dd>';
  rows+='<dt>Board</dt><dd>'+esc(boardName(r.board_id))+'</dd>';
  if(r.category)rows+='<dt>Category</dt><dd>'+esc(r.category)+'</dd>';
  if((r.tags||[]).length)rows+='<dt>Tags</dt><dd>'+r.tags.map(esc).join(', ')+'</dd>';
  if(r.note)rows+='<dt>Note</dt><dd>'+esc(r.note)+'</dd>';
  if(r.source_author)rows+='<dt>From</dt><dd>'+esc(r.source_author)+'</dd>';
  if(r.source_url)rows+='<dt>Source</dt><dd><a class="bd-link" href="'+esc(r.source_url)+'" target="_blank" rel="noopener noreferrer">'+esc(r.source_url)+'</a></dd>';
  if(r.source_caption)rows+='<dt>Caption</dt><dd><div class="bd-caption">'+esc(r.source_caption)+'</div></dd>';
  rows+='<dt>Added</dt><dd>'+esc(r.created_at||'')+(r.created_by?' by '+esc(r.created_by):'')+'</dd>';
  $('bd-viewbody').innerHTML=head+'<div class="bd-viewshots">'+shotsHtml+'</div><dl>'+rows+'</dl>';
  $('bd-view-edit').onclick=function(){openEdit(r);};
  $('bd-view-share').onclick=function(){shareReference(r);};
  $('bd-view-modal').classList.add('open');
}

/* ---------------- share ---------------- */
async function shareReference(r){
  try{
    var d=await jpost('/references/share',{id:r.id});
    var ok=false;
    try{
      await navigator.clipboard.writeText(d.url);
      ok=true;
    }catch(e){}
    if(ok){
      toast('Share link copied — anyone with it can view this reference');
    }else{
      prompt('Copy this share link:',d.url);
    }
  }catch(e){toast(e.message);}
}
$('bd-view-x').onclick=function(){$('bd-view-modal').classList.remove('open');};

/* ---------------- find inspiration ---------------- */
var findSource='ideas';
var SOURCE_COPY={
  ideas:{label:'What do you want the idea to help you do?',
         ph:'launch a new dial colour without sounding like an ad'},
  ads:{label:'What to search the ad library for',ph:'seiko mod watch'},
  web:{label:'What kind of thing to look for',ph:'pastel minimal watch product pages'},
  accounts:{label:'Which accounts (comma-separated)',ph:'@christopherwardlondon, @sternglasse'}
};
function drawSources(){
  $('bd-sources').querySelectorAll('.bd-source').forEach(function(b){
    b.classList.toggle('on',b.getAttribute('data-source')===findSource);
  });
  var c=SOURCE_COPY[findSource];
  $('bd-qlabel').textContent=c.label;
  $('bd-query').placeholder=c.ph;
  $('bd-countrywrap').style.display=findSource==='ads'?'':'none';
}
$('bd-sources').querySelectorAll('.bd-source').forEach(function(b){
  b.onclick=function(){findSource=b.getAttribute('data-source');drawSources();};
});
$('bd-find').onclick=function(){
  drawSources();
  $('bd-find-modal').classList.add('open');
  setTimeout(function(){$('bd-query').focus();},50);
};
$('bd-find-x').onclick=function(){$('bd-find-modal').classList.remove('open');};
$('bd-findgo').onclick=async function(){
  var q=$('bd-query').value.trim();
  if(!q){toast('Say what to look for');return;}
  var btn=this;btn.disabled=true;var label=btn.textContent;btn.textContent='Starting…';
  try{
    var d=await jpost('/boards/discover',{source:findSource,query:q,
      country:$('bd-country').value,name:$('bd-findname').value.trim()});
    $('bd-find-modal').classList.remove('open');
    $('bd-query').value='';$('bd-findname').value='';
    toast('Searching — "'+d.name+'" will fill in over the next few minutes');
    activeBoard=String(d.id);
    await loadBoards();loadRefs();
  }catch(e){toast(e.message);}
  btn.disabled=false;btn.textContent=label;
};

/* ---------------- filters ---------------- */
var qT;
$('bd-q').addEventListener('input',function(){
  clearTimeout(qT);var v=this.value;
  qT=setTimeout(function(){activeQ=v.trim();loadRefs();},300);
});
$('bd-cat').addEventListener('change',function(){activeCategory=this.value;loadRefs();});
$('bd-showarch').addEventListener('change',function(){showArchived=this.checked;loadRefs();});

loadBoards().then(loadRefs);
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
      <button type="button" class="btn" id="bd-find">Find inspiration</button>
      <button type="button" class="btn primary" id="bd-add">+ Add reference</button>
    </div>
    <div class="bd-layout">
      <nav class="bd-rail" id="bd-rail" aria-label="Boards"></nav>
      <div>
        <div class="bd-jobs" id="bd-jobs" hidden></div>
    <div class="bd-tagbar" id="bd-tagbar"></div>
        <div class="bd-grid" id="bd-grid"></div>
        <p class="bd-empty" id="bd-empty" style="display:none">Nothing here yet — add a reference, or paste a link and it'll read itself.</p>
      </div>
    </div>
  </main>
  {footer}
</div>

<div class="bd-menu" id="bd-menu" role="menu"></div>

<div class="bd-modal" id="bd-form-modal"><div class="bd-sheet">
  <header><b id="bd-form-title">Add reference</b><button type="button" class="x" id="bd-form-x">&times;</button></header>
  <div class="bd-formbody">
    <div class="bd-dz" id="bd-dz">
      <input type="file" id="bd-file" accept="image/jpeg,image/png,image/webp" multiple hidden>
      <span>Drag photos here, paste (⌘V), or <u>choose files</u></span>
    </div>
    <div class="bd-shots" id="bd-shots"></div>
    <label>Source link <span class="bd-hint">paste an Instagram/web link and it reads itself</span></label>
    <input id="bd-source" placeholder="https://instagram.com/p/…">
    <label>Title <span class="bd-hint">optional if you pasted a link</span></label>
    <input id="bd-title" maxlength="200" placeholder="e.g. Minimal gold-accent product page">
    <label>Board</label>
    <select id="bd-boardsel"><option value="">Unsorted</option></select>
    <label>Category</label>
    <select id="bd-catsel"><option value="">—</option>{category_opts}</select>
    <label>Tags <span class="bd-hint">comma-separated</span></label>
    <input id="bd-tags" placeholder="minimal, gold accent, pinterest">
    <label>Note</label>
    <textarea id="bd-note" placeholder="What you like about it, where it's from…"></textarea>
  </div>
  <footer>
    <button type="button" class="btn bd-del" id="bd-delete" style="display:none">Delete</button>
    <button type="button" class="btn" id="bd-archive" style="display:none">Archive</button>
    <span class="bd-sp"></span>
    <button type="button" class="btn primary" id="bd-save">Save reference</button>
  </footer>
</div></div>

<div class="bd-modal" id="bd-find-modal"><div class="bd-sheet">
  <header><b>Find inspiration</b><button type="button" class="x" id="bd-find-x">&times;</button></header>
  <div class="bd-formbody">
    <p class="bd-findnote">Searches the real internet and files what it finds into a new board.
      Takes a few minutes. You keep what's good and delete the rest.</p>
    <label>Where to look</label>
    <div class="bd-sources" id="bd-sources">
      <button type="button" class="bd-source on" data-source="ideas">Ideas worth stealing
        <small>Great brand moves from any industry, and how you'd run your own version</small></button>
      <button type="button" class="bd-source" data-source="ads">Competitor ads
        <small>Live ads from the public Meta Ad Library</small></button>
      <button type="button" class="bd-source" data-source="web">Around the web
        <small>Brand sites, campaigns, design galleries</small></button>
      <button type="button" class="bd-source" data-source="accounts">Named accounts
        <small>Specific Instagram/social handles you list</small></button>
    </div>
    <label id="bd-qlabel">What to search the ad library for</label>
    <input id="bd-query" maxlength="300" placeholder="seiko mod watch">
    <div id="bd-countrywrap">
      <label>Country</label>
      <select id="bd-country">
        <option value="IN" selected>India</option>
        <option value="US">United States</option>
        <option value="GB">United Kingdom</option>
        <option value="AE">UAE</option>
        <option value="SG">Singapore</option>
      </select>
    </div>
    <label>Board name <span class="bd-hint">optional</span></label>
    <input id="bd-findname" maxlength="80" placeholder="named automatically">
  </div>
  <footer>
    <span class="bd-sp"></span>
    <button type="button" class="btn primary" id="bd-findgo">Start searching</button>
  </footer>
</div></div>

<div class="bd-modal" id="bd-view-modal"><div class="bd-sheet bd-view">
  <header><b id="bd-view-title"></b><button type="button" class="x" id="bd-view-x">&times;</button></header>
  <div class="bd-viewbody" id="bd-viewbody"></div>
  <footer>
    <button type="button" class="btn" id="bd-view-share">Copy share link</button>
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
