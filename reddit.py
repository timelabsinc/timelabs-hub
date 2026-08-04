#!/usr/bin/env python3
"""Reddit listener — admin/full tool. Renders /var/www/ops/reddit.html.

The listening inbox is read-only against Reddit: it ranks and explains fresh
watch-community threads. A separate draft-assist queue can prepare replies,
but a person still reviews and manually posts every one.

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
    ("priority", "Worth replying"),
    ("all", "All surfaced"),
    ("buying_intent", "Buying intent"),
    ("build_help", "Build help"),
    ("answerable_question", "Question"),
    ("identification", "ID / legit check"),
    ("competitor_complaint", "Complaint"),
    ("style_trend", "Style / trend"),
    ("experience", "Wear / review"),
    ("showcase", "Showcase"),
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
/* The card's bottom line. It carries a button, so it has to be a flex row:
   .rd-reply asks for margin-left:auto and on a plain block .rd-meta that
   computes to 0, which glued "Draft a reply" to the end of the counts with
   no gap and left ~680px of empty card to its right on a desktop. */
.rd-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px;}
.rd-tag{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.03em;
  padding:2px 7px;border-radius:5px;}
.rd-tag.buying_intent{color:#3f7d4f;background:var(--good-bg);}
.rd-tag.build_help{color:#267067;background:#2b9f8d1a;}
.rd-tag.answerable_question{color:#8a6a2c;background:#f3e4bc33;}
.rd-tag.identification{color:#7b4ca0;background:#8a55af1a;}
.rd-tag.competitor_complaint{color:var(--bad);background:var(--bad-bg);}
.rd-tag.style_trend{color:#5468c4;background:#5468c41a;}
.rd-tag.experience{color:#98662f;background:#98662f1a;}
.rd-tag.showcase{color:#39759a;background:#39759a1a;}
.rd-tag.other{color:var(--muted);background:var(--card-2);}
.rd-title{font-size:14.5px;color:var(--ink);text-decoration:none;font-weight:600;line-height:1.35;}
.rd-title:hover{text-decoration:underline;}
.rd-score{margin-left:auto;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums;}
.rd-context{margin:2px 0 1px;color:var(--ink);font-size:13px;line-height:1.55;white-space:pre-line;}
.rd-context.missing{color:var(--muted);font-style:italic;}
.rd-why{display:flex;gap:7px;align-items:flex-start;flex-wrap:wrap;color:var(--muted);font-size:11.5px;line-height:1.45;}
.rd-why b{color:var(--ink);font-weight:650;white-space:nowrap;}
.rd-why>span:not(.rd-source){flex:1;min-width:180px;}
.rd-source{font-size:10.5px;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:2px 7px;white-space:nowrap;}
.rd-open{color:var(--accent);text-decoration:none;font-weight:650;}
.rd-open:hover{text-decoration:underline;}
.rd-more{align-self:center;border:1px solid var(--border-2);background:var(--card);color:var(--ink);
  border-radius:var(--r-s);padding:9px 16px;font:inherit;font-size:12.5px;font-weight:650;cursor:pointer;}
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
.rd-adddrop{width:64px;height:64px;border:1px dashed var(--border-2);border-radius:8px;
  background:none;color:var(--muted);font-size:10.5px;font-weight:650;cursor:pointer;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
  line-height:1.2;}
.rd-adddrop svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.8;}

/* Drop photo picker — same modal/grid pattern as the Product builder's, so a
   photo picked from Drop looks and behaves the same wherever it's offered. */
.rd-modal{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;
  align-items:flex-end;justify-content:center;z-index:60;}
.rd-modal.open{display:flex;}
@media(min-width:640px){ .rd-modal{align-items:center;} }
.rd-sheet{background:var(--bg);width:100%;max-width:640px;max-height:85vh;
  border-radius:16px 16px 0 0;display:flex;flex-direction:column;overflow:hidden;
  box-shadow:var(--shadow-lg);}
@media(min-width:640px){ .rd-sheet{border-radius:16px;} }
.rd-sheet header{display:flex;align-items:center;gap:10px;padding:14px 16px;
  border-bottom:1px solid var(--border);}
.rd-sheet header b{flex:1;font-size:15px;}
.rd-sheet .x{border:none;background:none;font-size:22px;color:var(--muted);
  cursor:pointer;line-height:1;}
.rd-crumbs{font-size:12.5px;color:var(--muted);padding:9px 16px;
  border-bottom:1px solid var(--border);}
.rd-crumbs a{color:var(--accent);cursor:pointer;}
.rd-pickgrid{overflow:auto;padding:12px 16px;display:grid;align-items:start;
  grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;}
.rd-pick{position:relative;border-radius:9px;overflow:hidden;border:2px solid transparent;
  cursor:pointer;aspect-ratio:1;background:var(--card-2);}
.rd-pick.sel{border-color:var(--accent);}
.rd-pick.folder{display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:var(--card-2);border-color:var(--border);color:var(--muted);gap:5px;padding:6px;
  text-align:center;}
.rd-pick.folder svg{width:34px;height:34px;stroke:var(--accent);fill:var(--accent-bg);stroke-width:1.6;}
.rd-pick.folder small{font-size:10.5px;line-height:1.2;overflow:hidden;max-height:24px;}
.rd-pick img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;}
.rd-pick .chk{position:absolute;top:4px;right:4px;width:19px;height:19px;border-radius:50%;
  background:var(--accent);color:#fff;font-size:12px;display:none;align-items:center;
  justify-content:center;}
.rd-pick.sel .chk{display:flex;}
.rd-sheet footer{padding:12px 16px;border-top:1px solid var(--border);display:flex;
  gap:10px;align-items:center;}
.rd-sheet footer .rd-go{margin:0;flex:1;text-align:center;}
.rd-go{margin-top:14px;min-height:var(--tap);border:none;border-radius:var(--r-s);
  background:var(--accent);color:#fff;font:inherit;font-size:14.5px;font-weight:700;
  padding:0 18px;cursor:pointer;}
.rd-go:disabled{opacity:.5;cursor:default;}
.rd-draft{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:15px 16px;margin-bottom:11px;box-shadow:var(--shadow);}
.rd-draft.ready{border-color:var(--accent);}
.rd-dphotos{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;}
.rd-dphotos a{display:block;width:68px;height:68px;border:1px solid var(--border);
  border-radius:8px;overflow:hidden;background:var(--card-2);}
.rd-dphotos img{width:100%;height:100%;object-fit:cover;display:block;}
.rd-dphotos-note{font-size:11.5px;color:var(--muted);align-self:center;max-width:180px;}
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

/* the rota: seven days, so a gap is visible rather than discovered */
.rd-week{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:1px;
  background:var(--border);border:1px solid var(--border);border-radius:var(--r-s);
  overflow:hidden;margin-bottom:16px;}
.rd-day{background:var(--card);padding:9px 10px;min-height:74px;}
.rd-day.today{background:var(--accent-bg);}
.rd-day .d{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;}
.rd-day .n{font-size:15px;font-weight:750;color:var(--ink);line-height:1.2;}
.rd-day .who{font-size:11px;color:var(--accent);margin-top:4px;line-height:1.35;
  overflow:hidden;text-overflow:ellipsis;}
.rd-day .gap{font-size:11px;color:var(--muted);margin-top:4px;font-style:italic;}
.rd-slot{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:9px;}
.rd-slot input{font:inherit;font-size:12.5px;border:1px solid var(--border);
  border-radius:var(--r-s);background:var(--bg);color:var(--ink);padding:6px 9px;}
.rd-slot input[type=date]{max-width:150px;}
.rd-slot button{font:inherit;font-size:12.5px;font-weight:650;cursor:pointer;
  border:1px solid var(--border-2);border-radius:var(--r-s);background:var(--card);
  color:var(--ink);padding:6px 11px;min-height:34px;}
.rd-reply{margin-left:auto;font:inherit;font-size:12px;font-weight:650;cursor:pointer;
  border:1px solid var(--accent);border-radius:var(--r-s);background:var(--accent-bg);
  color:var(--accent);padding:5px 11px;min-height:32px;white-space:nowrap;}
.rd-reply:disabled{opacity:.55;cursor:default;}
.rd-rbody{width:100%;font:inherit;font-size:13.5px;line-height:1.6;color:var(--ink);
  border:1px solid transparent;border-radius:var(--r-s);background:none;padding:6px 8px;
  min-height:110px;resize:vertical;}
.rd-rbody:focus{outline:none;border-color:var(--accent);background:var(--bg);}
.rd-setup{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:15px 16px;margin-bottom:16px;}
.rd-setup > summary{cursor:pointer;font-size:13.5px;font-weight:650;color:var(--ink);
  list-style:none;}
.rd-setup > summary::-webkit-details-marker{display:none;}
.rd-setup > summary::before{content:'+';display:inline-block;width:16px;color:var(--accent);
  font-weight:700;}
.rd-setup[open] > summary::before{content:'\2212';}
.rd-piece{margin-top:14px;}
.rd-piece h4{font-size:11px;color:var(--muted);margin:0 0 6px;font-weight:650;
  text-transform:uppercase;letter-spacing:.04em;}
.rd-piece textarea{width:100%;font:inherit;font-size:13px;line-height:1.55;
  border:1px solid var(--border);border-radius:var(--r-s);background:var(--bg);
  color:var(--ink);padding:10px 12px;min-height:130px;resize:vertical;}
.rd-piece textarea:focus{outline:none;border-color:var(--accent);}
.rd-pact{display:flex;gap:7px;margin-top:7px;flex-wrap:wrap;}
.rd-pact button{font:inherit;font-size:12.5px;font-weight:650;cursor:pointer;
  border:1px solid var(--border-2);border-radius:var(--r-s);background:var(--card);
  color:var(--ink);padding:6px 12px;min-height:34px;}
.rd-pact button:disabled{opacity:.55;cursor:default;}
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
var API='/ops/agent/api', DROP='/drop/api';
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
var TAG_LABEL={buying_intent:'Buying intent',build_help:'Build help',
  answerable_question:'Question',identification:'ID / legit check',
  competitor_complaint:'Complaint',style_trend:'Style / trend',
  experience:'Wear / review',showcase:'Showcase',other:'Other'};
var THREADS=[], tagF='priority', visibleThreads=60;

function draw(){
  var filtered=THREADS.filter(function(t){
    return tagF==='all'||(tagF==='priority'&&t.opportunity_score>=0.65)||t.tag===tagF;
  });
  var rows=filtered.slice(0,visibleThreads);
  $('rd-total').textContent=THREADS.length;
  $('rd-buying').textContent=THREADS.filter(function(t){return t.tag==='buying_intent';}).length;
  $('rd-question').textContent=THREADS.filter(function(t){return ['build_help','answerable_question','identification'].indexOf(t.tag)>=0;}).length;
  $('rd-hot').textContent=THREADS.filter(function(t){return t.opportunity_score>=0.65;}).length;
  if(!filtered.length){
    $('list').innerHTML='<div class="rd-empty">'+(THREADS.length?'No threads match this filter.':
      'No threads yet — click Sync to pull the first batch.')+'</div>';
    return;
  }
  $('list').innerHTML=rows.map(function(t){
    /* On the RSS feed — which is what runs until the OAuth ticket clears —
       Reddit gives us neither count, so every card used to read
       "— upvotes · — comments": two blanks per row saying nothing the banner
       at the top of the page doesn't already say. Show the counts only when
       there are counts; this lights up on its own once the app is connected. */
    var counts = [];
    if(t.score!=null) counts.push(t.score+' upvotes');
    if(t.num_comments!=null) counts.push(t.num_comments+' comments');
    var countTxt = counts.join(' &middot; ');
    var body=(t.body||'').trim(), excerpt=body.length>520?body.slice(0,517)+'...':body;
    var context=excerpt||('No text body was supplied for this '+(t.post_type||'media')+
      ' post. Open it before drafting; Hermes will not guess from the title.');
    var priority=t.opportunity_score>=0.82?'High':(t.opportunity_score>=0.65?'Good':'Low');
    var links='<a class="rd-open" href="'+esc(t.permalink)+'" target="_blank" rel="noopener">Open thread</a>';
    if(t.content_url)links+=' <a class="rd-open" href="'+esc(t.content_url)+'" target="_blank" rel="noopener">Open media/link</a>';
    return '<div class="rd-card">'
      +'<div class="rd-top">'
      +'<span class="rd-sr">r/'+esc(t.subreddit)+'</span>'
      +'<span class="rd-tag '+esc(t.tag)+'">'+esc(TAG_LABEL[t.tag]||t.tag)+'</span>'
      +(t.flair?'<span class="rd-source">'+esc(t.flair)+'</span>':'')
      +'<span class="rd-meta">'+agoEpoch(t.created_utc)+' &middot; u/'+esc(t.author)+'</span>'
      +'<span class="rd-score">'+priority+' reply fit</span>'
      +'</div>'
      +'<a class="rd-title" href="'+esc(t.permalink)+'" target="_blank" rel="noopener">'+esc(t.title)+'</a>'
      +'<div class="rd-context'+(body?'':' missing')+'">'+esc(context)+'</div>'
      +'<div class="rd-why"><b>Why surfaced</b><span>'+esc(t.match_reason||'No strong reply signal detected')+'</span>'
      +'<span class="rd-source">'+(body?'Post text captured':'Title only')+'</span></div>'
      +'<div class="rd-foot">'+links+(countTxt?'<span>'+countTxt+'</span>':'')
      +'<button class="rd-reply" data-reply="'+t.id+'">Draft a reply</button></div>'
      +'</div>';
  }).join('')+(filtered.length>visibleThreads?
    '<button type="button" class="rd-more" id="rd-more">Show 60 more ('+
    (filtered.length-visibleThreads)+' remaining)</button>':'');
  if($('rd-more'))$('rd-more').onclick=function(){visibleThreads+=60;draw();};
  $('list').querySelectorAll('[data-reply]').forEach(function(b){
    b.onclick=function(){
      var thread=THREADS.find(function(t){return +t.id===+b.dataset.reply;}),
        missing=!thread||!(thread.body||'').trim();
      var raw=prompt(missing?
        'This is a title-only image/link post. Open it first, then paste the relevant visual or discussion detail so Hermes does not guess:':
        'Anything you want said? Leave blank and Hermes will use the complete post text:');
      if(raw===null)return;
      var note=(raw||'').trim();
      if(missing&&!note){toast('Add the missing image or link context before drafting');return;}
      b.disabled=true; b.textContent='Drafting…';
      jpost('/reddit/reply/create',{thread_id:+b.dataset.reply,note:note})
        .then(function(){
          toast('Reply queued — progress stays in the Compose tab');
          document.querySelector('.rd-tabs button[data-rt="compose"]').click();
        })
        .catch(function(e){ toast(e.message); b.disabled=false; b.textContent='Draft a reply'; });
    };
  });
}

var BANNER={
  rss:['Running on Reddit\\u2019s public feed \\u2014 post text, but no discussion metrics',
    'No Reddit app is connected yet, so threads come from Reddit\\u2019s public RSS feed. '
    +'The Listener now captures the full post body when Reddit includes one, but it cannot see vote counts, comment counts, flair or comment text. '
    +'A Developer Support ticket is the way to unlock full data \\u2014 once approved and '
    +'the credentials are added to .env, this switches over automatically.'],
  oauth:['','']
};

async function load(){
  try{
    var d=await api('/reddit/threads');
    THREADS=d.threads||[];
    visibleThreads=60;
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
    b.classList.add('on'); tagF=b.dataset.tag; visibleThreads=60; draw();
  };
});
$('rd-sync').onclick=async function(){
  $('rd-sync').disabled=true; $('rd-sync').textContent='Syncing\\u2026';
  try{
    var d=await jpost('/reddit/sync',{});
    toast('r/'+d.subreddit+': '+d.added+' new of '+d.fetched+' fetched');
    await load();
  }catch(e){ toast(e.message); }
  $('rd-sync').textContent='Sync now';
  $('rd-sync').disabled=false;
};
load();

/* ---------------- our subreddit: composer ---------------- */
var SHOTS=[], DRAFTS=[], pollT=null, DRAFT_EDITS={};

document.querySelectorAll('.rd-tabs button').forEach(function(t){
  t.onclick=function(){
    var k=t.dataset.rt;
    document.querySelectorAll('.rd-tabs button').forEach(function(x){x.classList.toggle('on',x===t);});
    $('rp-listen').classList.toggle('on',k==='listen');
    $('rp-compose').classList.toggle('on',k==='compose');
    document.querySelector('.rd-toolbar').style.display = k==='listen'?'':'none';
    document.querySelector('.rd-kpirow').style.display = k==='listen'?'':'none';
    if(k==='compose'){ loadDrafts(); loadSetup(); }
  };
});

function drawShots(){
  var box=$('c-shots');
  box.innerHTML=SHOTS.map(function(s,i){
    return '<div class="rd-shot"><img src="'+s.url+'" alt="">'+
      '<button data-rm="'+i+'" aria-label="Remove">&times;</button></div>';
  }).join('')+'<button class="rd-add" id="c-add" aria-label="Add photos from this device">+</button>'+
    '<button class="rd-adddrop" id="c-adddrop" aria-label="Add photos from Drop">'+
    '<svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>Drop</button>';
  box.querySelectorAll('[data-rm]').forEach(function(b){
    b.onclick=function(){
      var i=+b.dataset.rm;
      if(SHOTS[i]&&SHOTS[i].url){ try{URL.revokeObjectURL(SHOTS[i].url);}catch(e){} }
      SHOTS.splice(i,1); drawShots();
    };
  });
  $('c-add').onclick=function(){ $('c-file').click(); };
  $('c-adddrop').onclick=openPicker;
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
    }catch(e){
      var at=SHOTS.indexOf(rec);
      if(at>=0)SHOTS.splice(at,1);
      try{URL.revokeObjectURL(rec.url);}catch(ignore){}
      drawShots();
      toast(e.message);
    }
  }
};

/* ---- Drop photo picker — same pattern as the Product builder's ---- */
var pk={path:'',sel:{}};
function openPicker(){ pk.sel={}; $('rd-picker').classList.add('open'); loadDrop(''); }
$('pk-x').onclick=function(){ $('rd-picker').classList.remove('open'); };
$('rd-picker').addEventListener('click',function(e){
  if(e.target===$('rd-picker'))$('rd-picker').classList.remove('open');
});
async function dropApi(url,opts){
  var r=await fetch(url,opts);
  if(r.status===403){ throw new Error("This account can't open Drop."); }
  var d=await r.json().catch(function(){return {};});
  if(!r.ok)throw new Error(d.error||'Something went wrong');
  return d;
}
async function loadDrop(path){
  pk.path=path;
  $('pk-grid').innerHTML='<span class="rd-empty" style="grid-column:1/-1">Loading&hellip;</span>';
  var crumbs='<a data-p="">Drop</a>', acc='';
  path.split('/').filter(Boolean).forEach(function(seg){
    acc=(acc?acc+'/':'')+seg;
    crumbs+=' / <a data-p="'+esc(acc)+'">'+esc(seg)+'</a>';
  });
  $('pk-crumbs').innerHTML=crumbs;
  $('pk-crumbs').querySelectorAll('a').forEach(function(a){
    a.onclick=function(){ loadDrop(a.dataset.p); };
  });
  try{
    var d=await dropApi(DROP+'/list?path='+encodeURIComponent(path));
    var html='';
    (d.dirs||[]).forEach(function(dir){
      html+='<div class="rd-pick folder" data-dir="'+esc(dir.name)+'">'+
        '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>'+
        '<small>'+esc(dir.name)+'</small></div>';
    });
    (d.files||[]).filter(function(f){ return f.kind==='image'; }).forEach(function(f){
      var full=(path?path+'/':'')+f.name;
      html+='<div class="rd-pick" data-name="'+esc(f.name)+'">'+
        '<img loading="lazy" src="'+DROP+'/thumb?path='+encodeURIComponent(full)+'&big=1" alt="">'+
        '<span class="chk">&#10003;</span></div>';
    });
    $('pk-grid').innerHTML=html||'<span class="rd-empty" style="grid-column:1/-1">No photos in this folder.</span>';
    $('pk-grid').querySelectorAll('.rd-pick.folder').forEach(function(el){
      el.onclick=function(){ loadDrop((path?path+'/':'')+el.dataset.dir); };
    });
    $('pk-grid').querySelectorAll('.rd-pick:not(.folder)').forEach(function(el){
      el.onclick=function(){
        var key=(path?path+'/':'')+el.dataset.name;
        if(pk.sel[key]){ delete pk.sel[key]; el.classList.remove('sel'); }
        else{ pk.sel[key]={path:path,name:el.dataset.name}; el.classList.add('sel'); }
        updatePkCount();
      };
    });
  }catch(e){ $('pk-grid').innerHTML='<span class="rd-empty" style="grid-column:1/-1">'+esc(e.message)+'</span>'; }
}
function updatePkCount(){
  var n=Object.keys(pk.sel).length;
  $('pk-count').textContent=n?(n+' selected'):'';
  $('pk-add').textContent=n?('Add '+n+' photo'+(n>1?'s':'')):'Add photos';
}
$('pk-add').onclick=async function(){
  var items=Object.values(pk.sel);
  if(!items.length){ $('rd-picker').classList.remove('open'); return; }
  this.disabled=true; this.textContent='Adding\\u2026';
  var added=0;
  for(var i=0;i<items.length;i++){
    if(SHOTS.length>=12){ toast('12 photos is plenty'); break; }
    try{
      var d=await api('/reddit/drop-photo',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(items[i])});
      SHOTS.push({url:DROP+'/thumb?path='+encodeURIComponent((items[i].path?items[i].path+'/':'')+items[i].name)+'&big=1',
        path:d.path});
      added++;
    }catch(e){ toast(e.message); }
  }
  this.disabled=false; this.textContent='Add photos';
  $('rd-picker').classList.remove('open');
  drawShots();
  if(added)toast(added+' photo'+(added===1?'':'s')+' added');
};

$('c-go').onclick=async function(){
  var brief=$('c-brief').value.trim();
  var paths=SHOTS.filter(function(s){return s.path;}).map(function(s){return s.path;});
  if(!brief&&!paths.length){ toast('Add a photo or describe the build'); return; }
  $('c-go').disabled=true; $('c-go').textContent='Starting…';
  try{
    await jpost('/reddit/post/create',{kind:$('c-kind').value,brief:brief,photos:paths});
    toast("Draft queued — progress stays here if you leave and return");
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
  var passes=p.passes||{}, edit=DRAFT_EDITS[p.id]||{};
  var shownTitle=Object.prototype.hasOwnProperty.call(edit,'title')?edit.title:(p.title||'');
  var shownBody=Object.prototype.hasOwnProperty.call(edit,'body')?edit.body:(p.body||'');
  var photoStrip=(p.photos||[]).length
    ? '<div class="rd-dphotos">'+p.photos.map(function(url,i){
        return '<a href="'+esc(url)+'&download=1" title="Download photo '+(i+1)+'">'+
          '<img src="'+esc(url)+'" loading="lazy" alt="Draft photo '+(i+1)+'"></a>';
      }).join('')+
      '<span class="rd-dphotos-note">Download these, then attach them in Reddit.</span></div>'
    : '';
  var order=['image_observations','research','draft','audit','humanise','verify'];
  var plist=order.filter(function(k){return passes[k];}).map(function(k){
    return '<div class="rd-pass"><b>'+k+'</b>'+esc(passes[k])+'</div>';
  }).join('');
  /* Opens Reddit's own composer with the text already in it. No API needed,
     which matters because write access is still waiting on approval — and
     photos have to be attached by hand there anyway. */
  return '<div class="rd-draft'+(p.status==='ready'?' ready':'')+'" data-p="'+p.id+'">'+
    '<div class="rd-dhead">'+
      '<span class="rd-dstat '+stat+'">'+esc(label)+'</span>'+
      '<span class="rd-meta">'+esc(p.kind||'')+(p.photos&&p.photos.length?' · '+p.photos.length+' photo(s)':'')+'</span>'+
      '<span class="rd-dwhen">'+agoTxt(p.created_at)+'</span>'+
    '</div>'+photoStrip+
    (p.status==='needs_input'&&p.question
      ? '<div class="rd-ask"><p>'+esc(p.question)+'</p>'+
        '<textarea data-ans="'+p.id+'" placeholder="Answer it and the run picks up from here"></textarea>'+
        '<button data-send="'+p.id+'">Answer and continue</button></div>'
    : running
      ? '<div class="rd-empty" style="padding:14px 0">Working through the passes…</div>'
      : p.status==='failed'
        ? '<div class="rd-empty" style="padding:14px 0;text-align:left">'+
          esc(p.error||'failed')+' <button data-retry="'+p.id+'">Retry</button></div>'
        : '<input class="rd-dtitle" value="'+esc(shownTitle)+'" data-t="'+p.id+
          '" aria-label="Reddit draft title">'+
          '<textarea class="rd-dbody" data-b="'+p.id+
          '" aria-label="Reddit draft body">'+esc(shownBody)+'</textarea>'+
          (plist?'<details class="rd-passes"><summary>What each pass did</summary>'+plist+'</details>':'')+
          '<div class="rd-dact">'+
            (p.status!=='posted'
              ? '<a class="go" href="#" data-open="'+p.id+'">Open in Reddit</a>'+
                '<button data-save="'+p.id+'">Save edits</button>'+
                '<button data-posted="'+p.id+'">Mark posted</button>'
              : '<span class="rd-meta">Posted '+agoTxt(p.posted_at)+'</span>')+
            '<button class="danger" data-del="'+p.id+'">Discard</button>'+
          '</div>'+
          '<div class="rd-slot">'+
            '<input type="date" data-date="'+p.id+'" value="'+esc(p.slot_date||'')+'" aria-label="Day to post">'+
            '<input type="text" data-who="'+p.id+'" value="'+esc(p.assigned_to||'')+'" placeholder="Who posts it" aria-label="Who posts it">'+
            '<button data-slot="'+p.id+'">Put on the rota</button>'+
          '</div>')+
  '</div>';
}

/* A week at a glance. Colleagues posting on alternate days only works if
   everyone can see which days already have something and which do not. */
function drawWeek(){
  var box=$('week'); if(!box)return;
  var byDay={};
  DRAFTS.forEach(function(p){ if(p.slot_date)(byDay[p.slot_date]=byDay[p.slot_date]||[]).push(p); });
  var out='', today=new Date();
  for(var i=0;i<7;i++){
    var d=new Date(today.getFullYear(),today.getMonth(),today.getDate()+i);
    var key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+
      String(d.getDate()).padStart(2,'0');
    var list=byDay[key]||[];
    var names=list.map(function(p){return p.assigned_to||'unassigned';});
    out+='<div class="rd-day'+(i===0?' today':'')+'">'+
      '<div class="d">'+d.toLocaleDateString(undefined,{weekday:'short'})+'</div>'+
      '<div class="n">'+d.getDate()+'</div>'+
      (list.length
        ? '<div class="who">'+esc(names.join(', '))+'</div>'
        : '<div class="gap">nothing</div>')+
    '</div>';
  }
  box.innerHTML=out;
}

function drawDrafts(){
  var L=$('draftlist');
  if(!DRAFTS.length){
    L.innerHTML='<div class="rd-empty">No drafts yet. Describe a build above and it writes one.</div>';
    return;
  }
  L.innerHTML=DRAFTS.map(draftCard).join('');
  L.querySelectorAll('[data-t]').forEach(function(el){
    el.oninput=function(){
      var id=+el.dataset.t;
      (DRAFT_EDITS[id]=DRAFT_EDITS[id]||{}).title=el.value;
    };
  });
  L.querySelectorAll('[data-b]').forEach(function(el){
    el.oninput=function(){
      var id=+el.dataset.b;
      (DRAFT_EDITS[id]=DRAFT_EDITS[id]||{}).body=el.value;
    };
  });
  L.querySelectorAll('[data-slot]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.slot;
      jpost('/reddit/post/update',{id:id,
        slot_date:L.querySelector('[data-date="'+id+'"]').value,
        assigned_to:L.querySelector('[data-who="'+id+'"]').value})
        .then(function(){ toast('On the rota'); loadDrafts(); })
        .catch(function(e){ toast(e.message); });
    };
  });
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
  L.querySelectorAll('[data-retry]').forEach(function(b){
    b.onclick=function(){
      b.disabled=true;
      jpost('/reddit/post/retry',{id:+b.dataset.retry})
        .then(function(){ toast('Retry started'); loadDrafts(); })
        .catch(function(e){ toast(e.message); b.disabled=false; });
    };
  });
  L.querySelectorAll('[data-save]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.save, c=b.closest('.rd-draft');
      jpost('/reddit/post/update',{id:id,
        title:c.querySelector('[data-t]').value, body:c.querySelector('[data-b]').value})
        .then(function(){ delete DRAFT_EDITS[id]; toast('Saved'); loadDrafts(); })
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
    a.onclick=function(e){
      e.preventDefault();
      var id=+a.dataset.open, c=a.closest('.rd-draft');
      var title=c.querySelector('[data-t]').value;
      var body=c.querySelector('[data-b]').value;
      var win=window.open('about:blank','_blank');
      if(win)win.opener=null;
      jpost('/reddit/post/update',{id:id,title:title,body:body})
        .then(function(){
          delete DRAFT_EDITS[id];
          var url='https://www.reddit.com/r/'+encodeURIComponent(SUBREDDIT)+
            '/submit?title='+encodeURIComponent(title)+'&text='+encodeURIComponent(body);
          if(win)win.location.replace(url);else location.href=url;
        })
        .catch(function(err){
          if(win)win.close();
          toast('Save failed — Reddit was not opened: '+err.message);
        });
    };
  });
}

var SUBREDDIT='IndiaWatchMods';
var SETUP={};
var PIECES=[['rules','Rules'],['sidebar','Sidebar description'],['flair','Post flairs']];
function drawSetup(){
  var L=$('setupbody'); if(!L)return;
  L.innerHTML=PIECES.map(function(pc){
    var k=pc[0], cur=SETUP[k]||{};
    return '<div class="rd-piece"><h4>'+esc(pc[1])+
      (cur.updated_at?' &middot; saved '+esc(cur.updated_at.slice(0,10)):'')+'</h4>'+
      '<textarea data-sk="'+k+'" placeholder="Not drafted yet">'+esc(cur.text||'')+'</textarea>'+
      '<div class="rd-pact">'+
        '<button data-sdraft="'+k+'">'+(cur.text?'Draft again':'Draft it')+'</button>'+
        '<button data-ssave="'+k+'">Save</button>'+
        '<button data-scopy="'+k+'">Copy</button>'+
      '</div></div>';
  }).join('')+
  '<p class="rd-empty" style="padding:10px 0 0;text-align:left">Paste these into Reddit '+
  'under Mod Tools: Rules, Community appearance, and Post flair.</p>';
  L.querySelectorAll('[data-sdraft]').forEach(function(b){
    b.onclick=function(){
      b.disabled=true; b.textContent='Drafting…';
      jpost('/reddit/setup/draft',{kind:b.dataset.sdraft})
        .then(function(){ toast('Drafting, about twenty seconds'); setTimeout(loadSetup,22000); })
        .catch(function(e){ toast(e.message); b.disabled=false; b.textContent='Draft it'; });
    };
  });
  L.querySelectorAll('[data-ssave]').forEach(function(b){
    b.onclick=function(){
      var k=b.dataset.ssave;
      jpost('/reddit/setup/save',{kind:k,text:L.querySelector('[data-sk="'+k+'"]').value})
        .then(function(){ toast('Saved'); loadSetup(); }).catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-scopy]').forEach(function(b){
    b.onclick=function(){
      var v=L.querySelector('[data-sk="'+b.dataset.scopy+'"]').value;
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(v).then(function(){ toast('Copied'); })
          .catch(function(){ prompt('Copy this:',v); });
      } else { prompt('Copy this:',v); }
    };
  });
}
async function loadSetup(){
  try{ var d=await api('/reddit/setup'); SETUP=d.setup||{}; drawSetup(); }catch(e){}
}

var REPLIES=[];
function drawReplies(){
  var L=$('replylist'); if(!L)return;
  if(!REPLIES.length){ L.innerHTML=''; return; }
  L.innerHTML='<h3 style="font-size:13px;color:var(--muted);margin:0 0 9px;font-weight:650">'+
    'Replies to other people</h3>'+REPLIES.map(function(r){
    var running=r.status==='queued'||r.status==='running';
    return '<div class="rd-draft'+(r.status==='ready'?' ready':'')+'">'+
      '<div class="rd-dhead"><span class="rd-dstat '+(running?'running':r.status)+'">'+
        esc(running?(r.stage||'queued'):r.status)+'</span>'+
        '<a class="rd-title" href="'+esc(r.permalink||'#')+'" target="_blank" rel="noopener">'+
        esc((r.title||'').slice(0,70))+'</a></div>'+
      (r.status==='needs_input'&&r.question
        ? '<div class="rd-ask"><p>'+esc(r.question)+'</p>'+
          '<textarea data-rans="'+r.id+'"></textarea>'+
          '<button data-rsend="'+r.id+'">Answer and continue</button></div>'
      : running
        ? '<div class="rd-empty" style="padding:12px 0">Working&hellip;</div>'
        : r.status==='failed'
          ? '<div class="rd-empty" style="padding:12px 0;text-align:left">'+
            esc(r.error||'failed')+' <button data-rretry="'+r.id+'">Retry</button></div>'
        : '<textarea class="rd-rbody" data-rt="'+r.id+'">'+esc(r.draft_text||'')+'</textarea>'+
          '<div class="rd-dact">'+
            '<a class="go" href="'+esc(r.permalink||'#')+'" target="_blank" rel="noopener">Open the thread</a>'+
            '<button data-rcopy="'+r.id+'">Copy reply</button>'+
            '<button data-rsave="'+r.id+'">Save edits</button>'+
            '<button class="danger" data-rdel="'+r.id+'">Discard</button>'+
          '</div>')+
    '</div>';
  }).join('');
  L.querySelectorAll('[data-rsave]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.rsave;
      jpost('/reddit/reply/update',{id:id,text:L.querySelector('[data-rt="'+id+'"]').value})
        .then(function(){ toast('Saved'); }).catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-rcopy]').forEach(function(b){
    b.onclick=function(){
      var v=L.querySelector('[data-rt="'+b.dataset.rcopy+'"]').value;
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(v).then(function(){ toast('Copied, paste it into the thread'); })
          .catch(function(){ prompt('Copy this:',v); });
      } else { prompt('Copy this:',v); }
    };
  });
  L.querySelectorAll('[data-rdel]').forEach(function(b){
    b.onclick=function(){
      if(!confirm('Discard this reply?'))return;
      jpost('/reddit/reply/update',{id:+b.dataset.rdel,discard:true})
        .then(loadDrafts).catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-rsend]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.rsend, v=L.querySelector('[data-rans="'+id+'"]').value.trim();
      if(!v){ toast('Type an answer first'); return; }
      b.disabled=true;
      jpost('/reddit/reply/update',{id:id,answer:v})
        .then(function(){ toast('Picking up where it stopped'); loadDrafts(); })
        .catch(function(e){ toast(e.message); b.disabled=false; });
    };
  });
  L.querySelectorAll('[data-rretry]').forEach(function(b){
    b.onclick=function(){
      b.disabled=true;
      jpost('/reddit/reply/update',{id:+b.dataset.rretry,retry:true})
        .then(function(){ toast('Retry started'); loadDrafts(); })
        .catch(function(e){ toast(e.message); b.disabled=false; });
    };
  });
}

async function loadDrafts(){
  try{
    var d=await api('/reddit/posts');
    DRAFTS=d.posts||[]; SUBREDDIT=d.subreddit||SUBREDDIT;
    try{ var rr=await api('/reddit/replies'); REPLIES=rr.replies||[]; }catch(e){}
    drawWeek(); drawReplies(); drawDrafts();
    /* poll only while something is actually being written */
    var busy=DRAFTS.concat(REPLIES).some(function(p){
      return p.status==='queued'||p.status==='running';});
    clearTimeout(pollT);
    if(busy&&$('rp-compose').classList.contains('on'))pollT=setTimeout(loadDrafts,6000);
  }catch(e){ $('draftlist').innerHTML='<div class="rd-empty">'+esc(e.message)+'</div>'; }
}
"""


def build():
    chips = "".join(
        f'<button class="rd-chip{" on" if key == "priority" else ""}" data-tag="{key}">{label}</button>'
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
      <p class="page-sub">Fresh watch-community threads from the last 14 days, with the post context
        and reason each one was surfaced. Read-only — nothing here posts or comments. Refreshed {generated}.</p>
    </div>
    <div class="rd-banner" id="rd-banner"><b id="rd-banner-t"></b><p id="rd-banner-p"></p></div>
    <div class="rd-kpirow">
      <div class="rd-kpi"><div class="v" id="rd-total">&mdash;</div><div class="l">surfaced threads</div></div>
      <div class="rd-kpi"><div class="v" id="rd-buying">&mdash;</div><div class="l">buying intent</div></div>
      <div class="rd-kpi"><div class="v" id="rd-question">&mdash;</div><div class="l">answerable questions</div></div>
      <div class="rd-kpi"><div class="v" id="rd-hot">&mdash;</div><div class="l">worth replying</div></div>
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
          It runs in the background and survives a service restart. Progress and the
          finished draft stay in this queue; private owner alerts are sent when configured.</p>
      </div>
      <details class="rd-setup" id="setup">
        <summary>Set the subreddit up: rules, sidebar, flair</summary>
        <div id="setupbody"></div>
      </details>
      <div id="replylist"></div>
      <div class="rd-week" id="week"></div>
      <div id="draftlist"><div class="rd-empty">Loading&hellip;</div></div>
    </div>
    {hub_footer()}
  </main>
</div>

<div class="rd-modal" id="rd-picker"><div class="rd-sheet">
  <header><b>Pick photos from Drop</b><button class="x" id="pk-x">&times;</button></header>
  <div class="rd-crumbs" id="pk-crumbs"></div>
  <div class="rd-pickgrid" id="pk-grid"></div>
  <footer>
    <span class="rd-empty" id="pk-count" style="margin:0;flex:1;padding:0"></span>
    <button class="rd-go" id="pk-add">Add photos</button>
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
