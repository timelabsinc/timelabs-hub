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
      'No threads yet. Once connected, click Sync to pull the first batch.')+'</div>';
    return;
  }
  $('list').innerHTML=rows.map(function(t){
    return '<div class="rd-card">'
      +'<div class="rd-top">'
      +'<span class="rd-sr">r/'+esc(t.subreddit)+'</span>'
      +'<span class="rd-tag '+esc(t.tag)+'">'+esc(TAG_LABEL[t.tag]||t.tag)+'</span>'
      +'<span class="rd-meta">'+agoEpoch(t.created_utc)+' &middot; u/'+esc(t.author)+'</span>'
      +'<span class="rd-score">score '+ (t.opportunity_score||0).toFixed(2) +'</span>'
      +'</div>'
      +'<a class="rd-title" href="'+esc(t.permalink)+'" target="_blank" rel="noopener">'+esc(t.title)+'</a>'
      +'<div class="rd-meta">'+ (t.score||0) +' upvotes &middot; '+ (t.num_comments||0) +' comments</div>'
      +'</div>';
  }).join('');
}

async function load(){
  try{
    var d=await api('/reddit/threads');
    THREADS=d.threads||[];
    if(!d.connected){
      $('rd-banner').classList.add('on');
      $('rd-sync').disabled=true;
      $('rd-sync').title="Waiting on Reddit API credentials";
    } else {
      $('rd-banner').classList.remove('on');
      $('rd-sync').disabled=false;
      $('rd-sync').title='';
    }
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
    <div class="rd-banner" id="rd-banner">
      <b>Waiting on Reddit API access</b>
      <p>Reddit closed self-service app creation — a Developer Support ticket was filed
        2026-07-24 requesting manual approval (usually about a week). Once the client ID
        and secret are added to .env, Sync will start pulling threads automatically.</p>
    </div>
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
    <div class="rd-list" id="list"><div class="rd-empty">Loading&hellip;</div></div>
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
