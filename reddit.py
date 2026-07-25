#!/usr/bin/env python3
"""Reddit listener — admin/full tool. Renders /var/www/ops/reddit.html.

Phase 1 only: a read-only inbox of watch-hobbyist threads worth a genuine
reply, ranked and tagged. It makes no writes to Reddit — nothing here posts
or comments on its own. Phase 2 (draft-assist, human-approved) is scoped but
not built; see the reddit_threads/reddit_drafts schema in
_ensure_reddit_schema() (agent_chat_server.py) and reference_reddit_timelabs
memory for the full plan.

Data is fetched client-side from /reddit/threads (and synced via
/reddit/sync) rather than baked in at generate time, since a daily rebuild
would otherwise show hours-stale threads.
"""
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/reddit.html"

TAGS = [
    ("all", "All"),
    ("buying_intent", "Buying intent"),
    ("answerable_question", "Question"),
    ("competitor_complaint", "Complaint"),
    ("style_trend", "Style / trend"),
    ("other", "Other"),
]

CSS = """
.rd-sub{color:var(--muted);font-size:13.5px;margin:-6px 0 18px;}
.rd-banner{display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:14px 16px;margin-bottom:16px;font-size:13.5px;color:var(--ink);}
.rd-banner.on{display:block;}
.rd-banner b{display:block;margin-bottom:3px;}
.rd-banner p{margin:4px 0 0;color:var(--muted);}
.rd-kpirow{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px;}
.rd-kpi{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow);}
.rd-kpi .v{font-size:24px;font-weight:750;color:var(--ink);font-variant-numeric:tabular-nums;}
.rd-kpi .l{font-size:12px;color:var(--muted);}
.rd-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
.rd-chip{border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:999px;
  padding:6px 13px;font-size:12.5px;font-weight:600;cursor:pointer;transition:.15s;}
.rd-chip:hover{border-color:var(--border-2);}
.rd-chip.on{background:var(--accent);border-color:var(--accent);color:#fff;}
.rd-sp{flex:1;}
.rd-sync{border:none;background:var(--accent);color:#fff;border-radius:var(--r-s);padding:8px 15px;
  font-size:13px;font-weight:650;cursor:pointer;}
.rd-sync:disabled{opacity:.5;cursor:default;}
.rd-list{display:flex;flex-direction:column;gap:9px;}
.rd-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:13px 15px;
  box-shadow:var(--shadow);display:flex;flex-direction:column;gap:6px;}
.rd-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.rd-sr{font-weight:650;color:var(--accent);font-size:12.5px;}
.rd-meta{color:var(--muted);font-size:12px;}
.rd-tag{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;
  padding:2px 7px;border-radius:5px;}
.rd-tag.buying_intent{color:#3f7d4f;background:var(--good-bg);}
.rd-tag.answerable_question{color:#8a6a2c;background:#f3e4bc33;}
.rd-tag.competitor_complaint{color:var(--bad);background:var(--bad-bg);}
.rd-tag.style_trend{color:#5468c4;background:#5468c41a;}
.rd-tag.other{color:var(--muted);background:var(--card-2);}
.rd-title{font-size:14.5px;color:var(--ink);text-decoration:none;font-weight:600;line-height:1.35;}
.rd-title:hover{text-decoration:underline;}
.rd-score{margin-left:auto;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums;}
.rd-empty{color:var(--muted);font-size:13.5px;padding:30px 0;text-align:center;}

/* composer */
.rd-tabs{display:flex;gap:20px;margin:-4px 0 16px;border-bottom:1px solid var(--border);}
.rd-tabs button{border:none;background:none;color:var(--muted);font:inherit;font-size:14px;
  font-weight:650;padding:9px 2px;cursor:pointer;border-bottom:2px solid transparent;}
.rd-tabs button.on{color:var(--ink);border-bottom-color:var(--accent);}
.rd-pane{display:none;} .rd-pane.on{display:block;}
.rd-form{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:16px;margin-bottom:16px;}
.rd-form label{display:block;font-size:12px;color:var(--muted);margin:0 0 5px;}
.rd-form textarea,.rd-form select{width:100%;font:inherit;font-size:14px;
  border:1px solid var(--border);border-radius:var(--r-s);background:var(--bg);
  color:var(--ink);padding:10px 12px;-webkit-appearance:none;appearance:none;}
.rd-form textarea{min-height:92px;resize:vertical;}
.rd-form select{background-image:url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23888\' stroke-width=\'2\'%3E%3Cpath d=\'M6 9l6 6 6-6\'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;background-size:12px;padding-right:34px;}
.rd-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:12px;}
.rd-shots{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0;}
.rd-shot{position:relative;width:64px;height:64px;border-radius:8px;overflow:hidden;
  background:var(--card-2);border:1px solid var(--border);}
.rd-shot img{width:100%;height:100%;object-fit:cover;display:block;}
.rd-shot button{position:absolute;top:2px;right:2px;width:18px;height:18px;border:none;
  border-radius:50%;background:rgba(0,0,0,.6);color:#fff;font-size:12px;line-height:1;
  cursor:pointer;padding:0;}
.rd-add{width:64px;height:64px;border:1px dashed var(--border-2);border-radius:8px;
  background:none;color:var(--muted);font-size:22px;cursor:pointer;}
.rd-go{margin-top:14px;min-height:var(--tap);border:none;border-radius:var(--r-s);
  background:var(--accent);color:#fff;font:inherit;font-size:14.5px;font-weight:700;
  padding:0 18px;cursor:pointer;}
.rd-go:disabled{opacity:.5;cursor:default;}
.rd-draft{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:15px 16px;margin-bottom:11px;box-shadow:var(--shadow);}
.rd-draft.ready{border-color:var(--accent);}
.rd-dhead{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:8px;}
.rd-dstat{font-size:10px;font-weight:750;text-transform:uppercase;letter-spacing:.05em;
  padding:3px 8px;border-radius:999px;}
.rd-dstat.running{color:#8a6a2c;background:var(--accent-bg);}
.rd-dstat.ready{color:var(--good);background:var(--good-bg);}
.rd-dstat.failed{color:var(--bad);background:var(--bad-bg);}
.rd-dstat.posted{color:var(--muted);background:var(--card-2);}
.rd-dstat.needs_input{color:#8a6a2c;background:var(--accent-bg);}
.rd-ask{background:var(--accent-bg);border:1px solid var(--accent);border-radius:var(--r-s);
  padding:12px 14px;margin:8px 0 0;}
.rd-ask p{margin:0 0 9px;font-size:13.5px;color:var(--ink);line-height:1.55;}
.rd-ask textarea{width:100%;font:inherit;font-size:13.5px;border:1px solid var(--border);
  border-radius:var(--r-s);background:var(--bg);color:var(--ink);padding:9px 11px;
  min-height:64px;resize:vertical;}
.rd-ask button{margin-top:9px;min-height:38px;border:none;border-radius:var(--r-s);
  background:var(--accent);color:#fff;font:inherit;font-size:13.5px;font-weight:700;
  padding:0 15px;cursor:pointer;}
.rd-dwhen{font-size:11.5px;color:var(--muted);margin-left:auto;}
.rd-dtitle{width:100%;font:inherit;font-size:15px;font-weight:700;color:var(--ink);
  border:1px solid transparent;border-radius:var(--r-s);background:none;padding:6px 8px;
  margin:0 0 6px;-webkit-appearance:none;appearance:none;}
.rd-dbody{width:100%;font:inherit;font-size:13.5px;line-height:1.6;color:var(--ink);
  border:1px solid transparent;border-radius:var(--r-s);background:none;padding:6px 8px;
  min-height:120px;resize:vertical;}
.rd-dtitle:focus,.rd-dbody:focus{outline:none;border-color:var(--accent);background:var(--bg);}
.rd-dact{display:flex;gap:8px;margin-top:11px;flex-wrap:wrap;padding-top:12px;
  border-top:1px solid var(--border);}
.rd-dact button,.rd-dact a{flex:none;min-height:38px;padding:0 14px;border-radius:var(--r-s);
  border:1px solid var(--border-2);background:var(--card);color:var(--ink);font:inherit;
  font-size:13px;font-weight:650;cursor:pointer;display:inline-flex;align-items:center;
  text-decoration:none;white-space:nowrap;}
.rd-dact .go{background:var(--accent);color:#fff;border-color:var(--accent);}
.rd-dact .danger{color:var(--bad);margin-left:auto;}
.rd-passes{margin-top:10px;}
.rd-passes summary{cursor:pointer;font-size:12px;color:var(--muted);list-style:none;padding:5px 0;}
.rd-passes summary::-webkit-details-marker{display:none;}
.rd-pass{margin-top:8px;padding:9px 11px;background:var(--card-2);border-radius:var(--r-s);
  font-size:12px;line-height:1.55;color:var(--muted);white-space:pre-wrap;}
.rd-pass b{display:block;color:var(--ink);font-size:11px;text-transform:uppercase;
  letter-spacing:.05em;margin-bottom:4px;}
"""

JS = """
var API='/ops/agent/api';
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
var toastT;
function toast(m){var t=$('toast');if(!t)return;t.textContent=m;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(function(){t.classList.remove('show');},3200);}
async function api(path,opts){
  var r=await fetch(API+path,opts);
  if(r.status===403){throw new Error("This account can't open the Reddit listener.");}
  var d=await r.json().catch(function(){return {};});
  if(!r.ok)throw new Error(d.error||'Something went wrong');
  return d;
}
function jpost(p,b){return api(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b||{})});}
function agoEpoch(sec){
  if(!sec)return '';
  var d=Math.floor(Date.now()/1000-sec);
  if(d<60)return 'just now';
  if(d<3600)return Math.floor(d/60)+'m ago';
  if(d<86400)return Math.floor(d/3600)+'h ago';
  if(d<2592000)return Math.floor(d/86400)+'d ago';
  return new Date(sec*1000).toLocaleDateString();
}
var TAG_LABEL={buying_intent:'Buying intent',answerable_question:'Question',
  competitor_complaint:'Complaint',style_trend:'Style / trend',other:'Other'};
var THREADS=[], tagF='all';

function draw(){
  var rows=THREADS.filter(function(t){return tagF==='all'||t.tag===tagF;});
  $('rd-total').textContent=THREADS.length;
  $('rd-buying').textContent=THREADS.filter(function(t){return t.tag==='buying_intent';}).length;
  $('rd-question').textContent=THREADS.filter(function(t){return t.tag==='answerable_question';}).length;
  $('rd-hot').textContent=THREADS.filter(function(t){return t.opportunity_score>=0.7;}).length;
  if(!rows.length){
    $('list').innerHTML='<div class="rd-empty">'+(THREADS.length?'No threads match this filter.':
      'No threads yet — click Sync to pull the first batch.')+'</div>';
    return;
  }
  $('list').innerHTML=rows.map(function(t){
    var upvotes = t.score==null ? '&mdash;' : t.score;
    var comments = t.num_comments==null ? '&mdash;' : t.num_comments;
    return '<div class="rd-card">'
      +'<div class="rd-top">'
      +'<span class="rd-sr">r/'+esc(t.subreddit)+'</span>'
      +'<span class="rd-tag '+esc(t.tag)+'">'+esc(TAG_LABEL[t.tag]||t.tag)+'</span>'
      +'<span class="rd-meta">'+agoEpoch(t.created_utc)+' &middot; u/'+esc(t.author)+'</span>'
      +'<span class="rd-score">score '+ (t.opportunity_score||0).toFixed(2) +'</span>'
      +'</div>'
      +'<a class="rd-title" href="'+esc(t.permalink)+'" target="_blank" rel="noopener">'+esc(t.title)+'</a>'
      +'<div class="rd-meta">'+upvotes+' upvotes &middot; '+comments+' comments</div>'
      +'</div>';
  }).join('');
}

var BANNER={
  rss:['Running on Reddit\\u2019s public feed \\u2014 limited data for now',
    'No Reddit app is connected yet, so threads come from Reddit\\u2019s public RSS feed '
    +'instead of the full API: real titles and links, but no upvote or comment counts. '
    +'A Developer Support ticket is the way to unlock full data \\u2014 once approved and '
    +'the credentials are added to .env, this switches over automatically.'],
  oauth:['','']
};

async function load(){
  try{
    var d=await api('/reddit/threads');
    THREADS=d.threads||[];
    var b=BANNER[d.mode]||BANNER.rss;
    if(d.mode==='oauth'){
      $('rd-banner').classList.remove('on');
    } else {
      $('rd-banner-t').textContent=b[0];
      $('rd-banner-p').innerHTML=b[1];
      $('rd-banner').classList.add('on');
    }
    $('rd-sync').disabled=false;
    $('rd-sync').title='';
    draw();
  }catch(e){
    $('list').innerHTML='<div class="rd-empty">'+esc(e.message)+'</div>';
  }
}

document.querySelectorAll('.rd-chip').forEach(function(b){
  b.onclick=function(){
    document.querySelectorAll('.rd-chip').forEach(function(x){x.classList.remove('on');});
    b.classList.add('on'); tagF=b.dataset.tag; draw();
  };
});
$('rd-sync').onclick=async function(){
  $('rd-sync').disabled=true; $('rd-sync').textContent='Syncing\\u2026';
  try{
    var d=await jpost('/reddit/sync',{});
    toast(d.added+' new of '+d.fetched+' fetched');
    await load();
  }catch(e){ toast(e.message); }
  $('rd-sync').textContent='Sync now';
  $('rd-sync').disabled=false;
};
load();

/* ---------------- our subreddit: composer ---------------- */
var SHOTS=[], DRAFTS=[], pollT=null;

document.querySelectorAll('.rd-tabs button').forEach(function(t){
  t.onclick=function(){
    var k=t.dataset.rt;
    document.querySelectorAll('.rd-tabs button').forEach(function(x){x.classList.toggle('on',x===t);});
    $('rp-listen').classList.toggle('on',k==='listen');
    $('rp-compose').classList.toggle('on',k==='compose');
    document.querySelector('.rd-toolbar').style.display = k==='listen'?'':'none';
    document.querySelector('.rd-kpirow').style.display = k==='listen'?'':'none';
    if(k==='compose')loadDrafts();
  };
});

function drawShots(){
  var box=$('c-shots');
  box.innerHTML=SHOTS.map(function(s,i){
    return '<div class="rd-shot"><img src="'+s.url+'" alt="">'+
      '<button data-rm="'+i+'" aria-label="Remove">&times;</button></div>';
  }).join('')+'<button class="rd-add" id="c-add" aria-label="Add photos">+</button>';
  box.querySelectorAll('[data-rm]').forEach(function(b){
    b.onclick=function(){
      var i=+b.dataset.rm;
      if(SHOTS[i]&&SHOTS[i].url){ try{URL.revokeObjectURL(SHOTS[i].url);}catch(e){} }
      SHOTS.splice(i,1); drawShots();
    };
  });
  $('c-add').onclick=function(){ $('c-file').click(); };
}
drawShots();

$('c-file').onchange=async function(){
  var files=Array.prototype.slice.call(this.files||[]);
  this.value='';
  for(var i=0;i<files.length;i++){
    if(SHOTS.length>=12){ toast('12 photos is plenty'); break; }
    var f=files[i];
    if(!f.type||f.type.indexOf('image/')!==0)continue;
    var rec={url:URL.createObjectURL(f),path:null};
    SHOTS.push(rec); drawShots();
    try{
      var fd=new FormData(); fd.append('image',f,f.name||('shot-'+Date.now()+'.jpg'));
      var r=await fetch(API+'/upload',{method:'POST',body:fd});
      var d=await r.json();
      if(!r.ok)throw new Error(d.error||'upload failed');
      rec.path=d.path;
    }catch(e){ toast(e.message); }
  }
};

$('c-go').onclick=async function(){
  var brief=$('c-brief').value.trim();
  var paths=SHOTS.filter(function(s){return s.path;}).map(function(s){return s.path;});
  if(!brief&&!paths.length){ toast('Add a photo or describe the build'); return; }
  $('c-go').disabled=true; $('c-go').textContent='Starting…';
  try{
    await jpost('/reddit/post/create',{kind:$('c-kind').value,brief:brief,photos:paths});
    toast("Drafting — you will get a message when it is ready");
    $('c-brief').value=''; SHOTS=[]; drawShots();
    loadDrafts();
  }catch(e){ toast(e.message); }
  $('c-go').textContent='Draft it'; $('c-go').disabled=false;
};

function agoTxt(s){
  if(!s)return '';
  var t=Date.parse(String(s).replace(' ','T')+'Z');
  if(isNaN(t))return '';
  var d=Math.floor((Date.now()-t)/1000);
  if(d<60)return 'just now';
  if(d<3600)return Math.floor(d/60)+'m ago';
  if(d<86400)return Math.floor(d/3600)+'h ago';
  return Math.floor(d/86400)+'d ago';
}

function draftCard(p){
  var running=p.status==='queued'||p.status==='running';
  var stat=running?'running':p.status;
  var label=running?(p.stage?('pass: '+p.stage):'queued'):p.status;
  var passes=p.passes||{};
  var order=['research','draft','audit','humanise','verify'];
  var plist=order.filter(function(k){return passes[k];}).map(function(k){
    return '<div class="rd-pass"><b>'+k+'</b>'+esc(passes[k])+'</div>';
  }).join('');
  /* Opens Reddit's own composer with the text already in it. No API needed,
     which matters because write access is still waiting on approval — and
     photos have to be attached by hand there anyway. */
  var url='https://www.reddit.com/r/'+encodeURIComponent(SUBREDDIT)+'/submit?title='+
    encodeURIComponent(p.title||'')+'&text='+encodeURIComponent(p.body||'');
  return '<div class="rd-draft'+(p.status==='ready'?' ready':'')+'" data-p="'+p.id+'">'+
    '<div class="rd-dhead">'+
      '<span class="rd-dstat '+stat+'">'+esc(label)+'</span>'+
      '<span class="rd-meta">'+esc(p.kind||'')+(p.photos&&p.photos.length?' · '+p.photos.length+' photo(s)':'')+'</span>'+
      '<span class="rd-dwhen">'+agoTxt(p.created_at)+'</span>'+
    '</div>'+
    (p.status==='needs_input'&&p.question
      ? '<div class="rd-ask"><p>'+esc(p.question)+'</p>'+
        '<textarea data-ans="'+p.id+'" placeholder="Answer it and the run picks up from here"></textarea>'+
        '<button data-send="'+p.id+'">Answer and continue</button></div>'
    : running
      ? '<div class="rd-empty" style="padding:14px 0">Working through the passes…</div>'
      : p.status==='failed'
        ? '<div class="rd-empty" style="padding:14px 0;text-align:left">'+esc(p.error||'failed')+'</div>'
        : '<input class="rd-dtitle" value="'+esc(p.title||'')+'" data-t="'+p.id+'">'+
          '<textarea class="rd-dbody" data-b="'+p.id+'">'+esc(p.body||'')+'</textarea>'+
          (plist?'<details class="rd-passes"><summary>What each pass did</summary>'+plist+'</details>':'')+
          '<div class="rd-dact">'+
            (p.status!=='posted'
              ? '<a class="go" href="'+url+'" target="_blank" rel="noopener" data-open="'+p.id+'">Open in Reddit</a>'+
                '<button data-save="'+p.id+'">Save edits</button>'+
                '<button data-posted="'+p.id+'">Mark posted</button>'
              : '<span class="rd-meta">Posted '+agoTxt(p.posted_at)+'</span>')+
            '<button class="danger" data-del="'+p.id+'">Discard</button>'+
          '</div>')+
  '</div>';
}

function drawDrafts(){
  var L=$('draftlist');
  if(!DRAFTS.length){
    L.innerHTML='<div class="rd-empty">No drafts yet. Describe a build above and it writes one.</div>';
    return;
  }
  L.innerHTML=DRAFTS.map(draftCard).join('');
  L.querySelectorAll('[data-send]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.send;
      var v=L.querySelector('[data-ans="'+id+'"]').value.trim();
      if(!v){ toast('Type an answer first'); return; }
      b.disabled=true;
      jpost('/reddit/post/answer',{id:id,answer:v})
        .then(function(){ toast('Picking up where it stopped'); loadDrafts(); })
        .catch(function(e){ toast(e.message); b.disabled=false; });
    };
  });
  L.querySelectorAll('[data-save]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.save, c=b.closest('.rd-draft');
      jpost('/reddit/post/update',{id:id,
        title:c.querySelector('[data-t]').value, body:c.querySelector('[data-b]').value})
        .then(function(){ toast('Saved'); loadDrafts(); })
        .catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-posted]').forEach(function(b){
    b.onclick=function(){
      var url=prompt('Link to the post (optional):')||'';
      jpost('/reddit/post/update',{id:+b.dataset.posted,posted:true,url:url})
        .then(function(){ toast('Marked as posted'); loadDrafts(); })
        .catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-del]').forEach(function(b){
    b.onclick=function(){
      if(!confirm('Discard this draft?'))return;
      jpost('/reddit/post/update',{id:+b.dataset.del,discard:true})
        .then(function(){ loadDrafts(); }).catch(function(e){ toast(e.message); });
    };
  });
  /* Save whatever is on screen before handing it to Reddit, so an edit made
     and then immediately opened isn't silently left behind. */
  L.querySelectorAll('[data-open]').forEach(function(a){
    a.onclick=function(){
      var id=+a.dataset.open, c=a.closest('.rd-draft');
      jpost('/reddit/post/update',{id:id,
        title:c.querySelector('[data-t]').value, body:c.querySelector('[data-b]').value})
        .catch(function(){});
    };
  });
}

var SUBREDDIT='IndiaWatchMods';
async function loadDrafts(){
  try{
    var d=await api('/reddit/posts');
    DRAFTS=d.posts||[]; SUBREDDIT=d.subreddit||SUBREDDIT;
    drawDrafts();
    /* poll only while something is actually being written */
    var busy=DRAFTS.some(function(p){return p.status==='queued'||p.status==='running';});
    clearTimeout(pollT);
    if(busy&&$('rp-compose').classList.contains('on'))pollT=setTimeout(loadDrafts,6000);
  }catch(e){ $('draftlist').innerHTML='<div class="rd-empty">'+esc(e.message)+'</div>'; }
}
"""


def build():
    chips = "".join(
        f'<button class="rd-chip{" on" if key == "all" else ""}" data-tag="{key}">{label}</button>'
        for key, label in TAGS)
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Reddit listener — Labs OS</title>
<style>{HUB_STYLE}{CSS}</style></head>
<body>
<div class="wrap">
  {hub_header("tools")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Reddit listener</h1>
      <p class="page-sub">Genuine watch-hobbyist threads worth a reply. Read-only — nothing here
        ever posts or comments on its own. Refreshed {generated}.</p>
    </div>
    <div class="rd-banner" id="rd-banner"><b id="rd-banner-t"></b><p id="rd-banner-p"></p></div>
    <div class="rd-kpirow">
      <div class="rd-kpi"><div class="v" id="rd-total">&mdash;</div><div class="l">threads tracked</div></div>
      <div class="rd-kpi"><div class="v" id="rd-buying">&mdash;</div><div class="l">buying intent</div></div>
      <div class="rd-kpi"><div class="v" id="rd-question">&mdash;</div><div class="l">answerable questions</div></div>
      <div class="rd-kpi"><div class="v" id="rd-hot">&mdash;</div><div class="l">high opportunity</div></div>
    </div>
    <div class="rd-toolbar">
      {chips}
      <span class="rd-sp"></span>
      <button class="rd-sync" id="rd-sync">Sync now</button>
    </div>
    <div class="rd-tabs">
      <button class="on" data-rt="listen">Listening</button>
      <button data-rt="compose">Our subreddit</button>
    </div>

    <div class="rd-pane on" id="rp-listen">
      <div class="rd-list" id="list"><div class="rd-empty">Loading&hellip;</div></div>
    </div>

    <div class="rd-pane" id="rp-compose">
      <div class="rd-form">
        <div class="rd-row">
          <div>
            <label for="c-kind">What kind of post</label>
            <select id="c-kind">
              <option value="showcase">Build showcase</option>
              <option value="discussion">Discussion / poll</option>
              <option value="drop">Product drop or restock</option>
            </select>
          </div>
        </div>
        <label for="c-brief">Tell it about the build</label>
        <textarea id="c-brief" placeholder="Movement, dial, bezel, bracelet, anything that was awkward to source or fiddly to fit. Rough notes are fine &mdash; it turns these into the post."></textarea>
        <div class="rd-shots" id="c-shots"></div>
        <input id="c-file" type="file" accept="image/*" multiple hidden>
        <button class="rd-go" id="c-go">Draft it</button>
        <p class="rd-empty" style="padding:10px 0 0;text-align:left">
          Five passes on Hermes &mdash; research, draft, audit, humanise, verify.
          Takes a minute or two; you&rsquo;ll get a message when it&rsquo;s ready.</p>
      </div>
      <div id="draftlist"><div class="rd-empty">Loading&hellip;</div></div>
    </div>
    {hub_footer()}
  </main>
</div>
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
