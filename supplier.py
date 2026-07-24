#!/usr/bin/env python3
"""Supplier build queue — renders /var/www/ops/supplier.html.

Designed phone-first, because that is genuinely where it will be used: the
supplier is at a bench, one-handed, replacing a WhatsApp group. Consequences
of taking that seriously rather than shrinking a desktop table:

  * One primary action per card — "Mark acknowledged" — because the pipeline
    is linear and the next step is knowable. A <select> would be two taps and
    a scroll wheel to do the thing they came to do.
  * Cards, not a table. A build brief is a photo + a spec, not a row of
    columns, and the photo is the part that actually communicates the job.
  * Sorted by what needs them: anything unacknowledged floats to the top and
    the rest collapses, so the queue answers "what do I do now", not "here
    is everything that ever existed".
  * A note box, because the group chat it replaces let them say "the brown
    dial is out of stock" and a status dropdown cannot.

Deliberately not built on hub_header()/_appnav(): those render the internal
app nav (Home, Drop, Ledger, Chat, Tools), which is meaningless — and wrong —
to show an external party. It also never loads the "Ask Labs" widget, which
talks to the agent backend that knows customer names and revenue.

Every safety property is server-side in agent_chat_server.py: the SELECT
behind /supplier/orders names no PII or price column, photos stream through
a role-checked endpoint (never a static URL), and nginx confines a
supplier-role account to this one page.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE

OUT = "/var/www/ops/supplier.html"

SUP_CSS = r"""
  :root{ --tap:46px; }
  body{ -webkit-tap-highlight-color:transparent; }
  .swrap{max-width:1240px;margin:0 auto;padding:0 16px env(safe-area-inset-bottom) 16px;}
  @media(max-width:520px){ .swrap{padding-left:12px;padding-right:12px;} }

  /* Column-based once there's room. One build per card, several cards per
     row on a laptop — the queue is scanned far more often than it's read. */
  .ogrid{display:grid;gap:12px;grid-template-columns:1fr;}
  @media(min-width:660px){ .ogrid{grid-template-columns:repeat(2,minmax(0,1fr));} }
  @media(min-width:1040px){ .ogrid{grid-template-columns:repeat(3,minmax(0,1fr));} }

  /* header — sticky, compact, with the counts that matter */
  .stop{position:sticky;top:0;z-index:40;background:var(--bg);
    padding:calc(12px + env(safe-area-inset-top)) 0 10px;
    border-bottom:1px solid var(--border);margin-bottom:14px;}
  .stop-row{display:flex;align-items:center;gap:10px;}
  .sdot{width:30px;height:30px;border-radius:8px;flex:none;
    background:linear-gradient(135deg,var(--accent),#d8a94c);
    color:#fff;font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center;}
  .stop b{font-size:16px;color:var(--ink);letter-spacing:-.01em;}
  .stop .sp{flex:1;}
  .swho{font-size:11.5px;color:var(--muted);max-width:38vw;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .sync{font-size:11.5px;color:var(--muted);}
  .sync.err{color:var(--bad);}

  /* tabs */
  .tabs{display:flex;gap:4px;margin-top:11px;}
  .tabs button{flex:none;border:none;background:none;color:var(--muted);font:inherit;
    font-size:14px;font-weight:650;padding:8px 2px;margin-right:16px;cursor:pointer;
    border-bottom:2px solid transparent;min-height:38px;}
  .tabs button.on{color:var(--ink);border-bottom-color:var(--accent);}
  .pane{display:none;} .pane.on{display:block;}

  /* Stage filter stays upfront — it's the question the supplier asks every
     time they open this ("what needs me?"), so it shouldn't cost a tap to
     see. Icons carry it so four stages fit a phone width without wrapping;
     the label is hidden on narrow screens but kept for screen readers. */
  .stagebar{display:flex;gap:6px;margin-top:10px;}
  .stg{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:2px;
    border:1px solid var(--border);background:var(--card);color:var(--muted);
    border-radius:var(--r-s);padding:7px 4px;font:inherit;font-size:11px;font-weight:650;
    cursor:pointer;min-height:46px;justify-content:center;
    transition:background .12s,color .12s,border-color .12s;}
  .stg svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.9;
    stroke-linecap:round;stroke-linejoin:round;}
  .stg b{font-size:13px;font-weight:800;line-height:1;}
  .stg .lbl{font-size:10px;opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;}
  .stg.on{background:var(--ink);color:var(--bg);border-color:var(--ink);}
  .stg.attn.on{background:var(--accent);border-color:var(--accent);color:#fff;}
  @media(max-width:380px){ .stg .lbl{display:none;} }

  /* Filters collapse behind a button so they never eat the space the queue
     itself needs — but the active count stays visible on the button, so a
     filtered view can't be mistaken for an empty one. */
  .fbar{display:flex;gap:8px;align-items:center;margin-top:10px;}
  .fbtn{flex:none;display:inline-flex;align-items:center;gap:7px;min-height:40px;
    border:1px solid var(--border);background:var(--card);color:var(--ink);
    border-radius:var(--r-s);padding:0 13px;font:inherit;font-size:13.5px;font-weight:650;
    cursor:pointer;}
  .fbtn.on{border-color:var(--accent);color:var(--accent);background:var(--accent-bg);}
  .fbtn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;
    stroke-linecap:round;stroke-linejoin:round;transition:transform .18s var(--ease);}
  .fbtn.open svg.chev{transform:rotate(180deg);}
  .fbtn .cnt{background:var(--accent);color:#fff;border-radius:999px;font-size:10.5px;
    font-weight:800;padding:1px 6px;}
  .ssearch{flex:1;min-width:0;font-size:16px;border:1px solid var(--border);
    border-radius:var(--r-s);background:var(--card);color:var(--ink);padding:10px 13px;
    -webkit-appearance:none;appearance:none;}
  .ssearch:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}

  .fpanel{display:none;margin-top:10px;padding:13px;background:var(--card);
    border:1px solid var(--border);border-radius:var(--r);}
  .fpanel.on{display:block;animation:fdown .16s var(--ease);}
  @keyframes fdown{from{opacity:0;transform:translateY(-4px);}to{opacity:1;transform:none;}}
  @media (prefers-reduced-motion:reduce){ .fpanel.on{animation:none;} }
  .flab{font-size:10.5px;font-weight:750;color:var(--muted);text-transform:uppercase;
    letter-spacing:.06em;margin:0 0 7px;}
  .frow{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:13px;}
  .frow:last-child{margin-bottom:0;}
  .schip{flex:none;border:1px solid var(--border);background:var(--bg);color:var(--muted);
    border-radius:999px;padding:8px 13px;font-size:13px;font-weight:650;cursor:pointer;
    white-space:nowrap;min-height:38px;}
  .schip.on{background:var(--ink);color:var(--bg);border-color:var(--ink);}
  .schip b{font-weight:800;}
  .fclear{border:none;background:none;color:var(--accent);font:inherit;font-size:13px;
    font-weight:650;cursor:pointer;padding:8px 4px;min-height:38px;}

  /* section heads */
  .shead{display:flex;align-items:center;gap:8px;margin:20px 2px 10px;}
  .shead h2{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;
    letter-spacing:.06em;margin:0;}
  .shead .n{font-size:11.5px;color:var(--muted);background:var(--card-2);border-radius:999px;
    padding:2px 8px;font-weight:700;}
  .shead .sp{flex:1;}
  .shead button{border:none;background:none;color:var(--accent);font:inherit;font-size:12.5px;
    font-weight:650;cursor:pointer;padding:6px;min-height:34px;}

  /* the card */
  .ocard{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column;
    transition:border-color .15s,box-shadow .15s;}
  .ocard:hover{box-shadow:var(--shadow-lg);}
  .ocard.attn{border-color:var(--accent);}
  .otop{display:flex;gap:12px;padding:13px;flex:1;}
  .oshot{width:74px;height:74px;border-radius:10px;flex:none;background:var(--card-2);
    border:1px solid var(--border);overflow:hidden;position:relative;cursor:zoom-in;
    touch-action:manipulation;}
  .oshot img{width:100%;height:100%;object-fit:cover;display:block;
    transition:transform .18s var(--ease);}
  .oshot:hover img{transform:scale(1.06);}
  .oshot .more{position:absolute;right:3px;bottom:3px;background:rgba(0,0,0,.68);color:#fff;
    font-size:10.5px;font-weight:700;border-radius:5px;padding:1px 5px;}
  .oshot.none{display:flex;align-items:center;justify-content:center;cursor:default;}
  .oshot.none svg{width:24px;height:24px;stroke:var(--muted);fill:none;stroke-width:1.6;opacity:.5;}

  /* Hover (mouse) or press-and-hold (touch) to enlarge without leaving the
     list — checking "is this the right dial" shouldn't cost a full-screen
     view and a trip back. */
  .peek{position:fixed;z-index:250;pointer-events:none;opacity:0;transform:scale(.94);
    transition:opacity .13s var(--ease),transform .13s var(--ease);
    border-radius:12px;overflow:hidden;box-shadow:0 18px 48px rgba(0,0,0,.42);
    border:1px solid rgba(255,255,255,.14);background:var(--card);}
  .peek.on{opacity:1;transform:none;}
  .peek img{display:block;width:300px;height:300px;object-fit:cover;}
  @media (prefers-reduced-motion:reduce){ .peek{transition:none;} }
  .omid{flex:1;min-width:0;}
  .onum{font-size:11.5px;font-weight:800;color:var(--accent);letter-spacing:.03em;}
  .oprod{font-size:15px;font-weight:700;color:var(--ink);line-height:1.32;margin:2px 0 0;
    word-break:break-word;}
  .oqty{display:inline-block;font-size:11.5px;font-weight:700;color:var(--ink);
    background:var(--accent-bg);border-radius:5px;padding:1px 7px;margin-left:6px;}
  .ospec{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px;}
  .ospec span{font-size:11.5px;color:var(--muted);background:var(--card-2);border-radius:6px;
    padding:2px 7px;}
  .onote{font-size:12.5px;color:var(--muted);line-height:1.5;margin-top:7px;
    padding-left:9px;border-left:2px solid var(--border-2);}

  /* tracking — a chip once set, a quiet "add" link until then */
  .otrk{display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap;}
  .otrk .chip{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:650;
    color:var(--ink);background:var(--card-2);border-radius:6px;padding:3px 8px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all;}
  .otrk .chip svg{width:12px;height:12px;stroke:var(--muted);fill:none;stroke-width:2;flex:none;}
  .otrk button{border:none;background:none;color:var(--accent);font:inherit;font-size:12px;
    font-weight:650;cursor:pointer;padding:4px 2px;min-height:30px;}
  .trkform{display:flex;gap:6px;margin-top:8px;}
  .trkform input{flex:1;min-width:0;font-size:16px;border:1px solid var(--border);
    border-radius:var(--r-s);background:var(--bg);color:var(--ink);padding:8px 10px;
    -webkit-appearance:none;appearance:none;}
  .trkform input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
  .trkform button{min-height:38px;padding:0 12px;border:1px solid var(--border);
    background:var(--card);color:var(--ink);border-radius:var(--r-s);font:inherit;
    font-size:13px;font-weight:650;cursor:pointer;}

  /* status + actions */
  .obar{display:flex;align-items:center;gap:9px;padding:0 14px 12px;flex-wrap:wrap;}
  .ost{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;
    text-transform:uppercase;letter-spacing:.04em;color:var(--muted);}
  .ost i{width:7px;height:7px;border-radius:50%;background:var(--muted);display:block;font-style:normal;}
  .ost.s-new i{background:var(--accent);} .ost.s-new{color:var(--accent);}
  .ost.s-delivered i{background:var(--good);} .ost.s-delivered{color:var(--good);}
  .ost .when{font-weight:500;text-transform:none;letter-spacing:0;opacity:.75;}
  .oact{display:flex;gap:8px;padding:0 14px 14px;}
  .obtn{flex:1;min-height:var(--tap);border-radius:var(--r-s);border:1px solid var(--ink);
    background:var(--ink);color:var(--bg);font:inherit;font-size:14.5px;font-weight:700;
    cursor:pointer;display:flex;align-items:center;justify-content:center;gap:7px;
    transition:transform .1s,opacity .15s;}
  .obtn:active{transform:scale(.985);}
  .obtn[disabled]{opacity:.5;cursor:wait;}
  .obtn.ghost{flex:none;width:var(--tap);background:var(--card);color:var(--muted);
    border-color:var(--border);font-size:19px;}
  .obtn svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:2.2;
    stroke-linecap:round;stroke-linejoin:round;}

  /* expandable detail */
  .odet{border-top:1px solid var(--border);padding:13px 14px;display:none;background:var(--card-2);}
  .odet.on{display:block;}
  .tl{list-style:none;margin:0 0 12px;padding:0;}
  .tl li{display:flex;gap:9px;font-size:12.5px;color:var(--muted);line-height:1.5;padding:3px 0;}
  .tl i{width:6px;height:6px;border-radius:50%;background:var(--border-2);flex:none;margin-top:6px;font-style:normal;}
  .tl b{color:var(--ink);font-weight:650;}
  .tl .note-ev b{color:var(--accent);}
  .noteform{display:flex;gap:8px;}
  .noteform input{flex:1;min-width:0;font-size:16px;border:1px solid var(--border);
    border-radius:var(--r-s);background:var(--bg);color:var(--ink);padding:10px 12px;
    -webkit-appearance:none;appearance:none;}
  .noteform input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
  .noteform button{min-height:var(--tap);padding:0 15px;border:1px solid var(--border);
    background:var(--card);color:var(--ink);border-radius:var(--r-s);font:inherit;
    font-size:13.5px;font-weight:650;cursor:pointer;}

  /* status sheet */
  .sheet{position:fixed;inset:0;z-index:200;display:none;}
  .sheet.on{display:block;}
  .sheet-bg{position:absolute;inset:0;background:rgba(0,0,0,.45);}
  .sheet-in{position:absolute;left:0;right:0;bottom:0;background:var(--card);
    border-radius:18px 18px 0 0;padding:8px 12px calc(14px + env(safe-area-inset-bottom));
    max-height:82vh;overflow-y:auto;animation:rise .2s var(--ease);}
  @keyframes rise{from{transform:translateY(100%);}to{transform:none;}}
  .sheet-grab{width:38px;height:4px;border-radius:2px;background:var(--border-2);margin:6px auto 12px;}
  .sheet h3{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;
    margin:0 4px 8px;font-weight:700;}
  .sheet button.opt{display:flex;width:100%;align-items:center;gap:10px;min-height:var(--tap);
    border:none;background:none;color:var(--ink);font:inherit;font-size:15.5px;text-align:left;
    padding:11px 8px;border-radius:var(--r-s);cursor:pointer;}
  .sheet button.opt:active{background:var(--card-2);}
  .sheet button.opt i{width:8px;height:8px;border-radius:50%;background:var(--border-2);
    flex:none;font-style:normal;}
  .sheet button.opt.cur i{background:var(--accent);}
  .sheet button.opt.cur{font-weight:700;}

  /* lightbox */
  .lb{position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.94);display:none;
    align-items:center;justify-content:center;flex-direction:column;}
  .lb.on{display:flex;}
  .lb img{max-width:96vw;max-height:82vh;object-fit:contain;border-radius:8px;}
  .lb-close{position:absolute;top:calc(10px + env(safe-area-inset-top));right:12px;
    width:42px;height:42px;border-radius:50%;border:none;background:rgba(255,255,255,.16);
    color:#fff;font-size:23px;cursor:pointer;}
  .lb-nav{display:flex;gap:8px;margin-top:14px;}
  .lb-nav button{min-width:var(--tap);min-height:38px;border-radius:var(--r-s);border:none;
    background:rgba(255,255,255,.16);color:#fff;font:inherit;font-size:14px;font-weight:650;cursor:pointer;}

  /* multi-select + the bar that appears once anything is picked */
  .ocard.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-bg);}
  .osel{position:absolute;top:9px;left:9px;z-index:2;width:24px;height:24px;
    border-radius:6px;border:1.5px solid var(--border-2);background:var(--card);
    cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;}
  .osel svg{width:14px;height:14px;stroke:#fff;fill:none;stroke-width:3;opacity:0;}
  .osel.on{background:var(--accent);border-color:var(--accent);}
  .osel.on svg{opacity:1;}
  .ocard{position:relative;}
  .ocard.selectable .oshot{margin-left:20px;}

  .bulkbar{position:sticky;bottom:0;z-index:60;display:none;gap:8px;align-items:center;
    background:var(--ink);color:var(--bg);border-radius:var(--r);padding:10px 12px;
    margin:12px 0 calc(10px + env(safe-area-inset-bottom));box-shadow:var(--shadow-lg);
    flex-wrap:wrap;}
  .bulkbar.on{display:flex;}
  .bulkbar b{font-size:13.5px;}
  .bulkbar .sp{flex:1;}
  .bulkbar button{min-height:38px;border:1px solid rgba(255,255,255,.28);background:transparent;
    color:var(--bg);border-radius:var(--r-s);padding:0 12px;font:inherit;font-size:13px;
    font-weight:650;cursor:pointer;}
  .bulkbar button.primary{background:var(--bg);color:var(--ink);border-color:var(--bg);}

  /* shipments */
  .shipcard{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    box-shadow:var(--shadow);padding:15px;margin-bottom:12px;}
  .shiptop{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
  .shipcode{font-size:15.5px;font-weight:750;color:var(--ink);
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}
  .shipmeta{font-size:12.5px;color:var(--muted);}
  .shipcost{margin-left:auto;text-align:right;}
  .shipcost b{display:block;font-size:17px;font-weight:750;color:var(--ink);letter-spacing:-.01em;}
  .shipcost span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:650;}
  .shiporders{margin-top:11px;border-top:1px solid var(--border);padding-top:10px;}
  .shiprow{display:flex;gap:9px;align-items:center;font-size:13px;padding:5px 0;color:var(--ink);}
  .shiprow .n{color:var(--muted);font-size:12px;min-width:38px;}
  .shiprow .st{margin-left:auto;font-size:11px;color:var(--muted);text-transform:uppercase;
    letter-spacing:.04em;font-weight:650;}
  .shipform{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:14px;
    background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px;}
  @media(max-width:560px){ .shipform{grid-template-columns:1fr;} }
  .shipform input{font-size:16px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:10px 12px;-webkit-appearance:none;appearance:none;}
  .shipform input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
  .shipform button{grid-column:1/-1;min-height:var(--tap);border:none;border-radius:var(--r-s);
    background:var(--ink);color:var(--bg);font:inherit;font-size:14.5px;font-weight:700;cursor:pointer;}

  .sempty{text-align:center;color:var(--muted);font-size:14px;line-height:1.6;padding:44px 20px;}
  .sempty svg{width:40px;height:40px;stroke:var(--border-2);fill:none;stroke-width:1.4;margin-bottom:12px;}
  .sfoot{text-align:center;color:var(--muted);font-size:11.5px;padding:26px 0 34px;}
"""

SUP_JS = r"""
'use strict';
var API='/ops/agent/api';
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
var toastT;
function toast(m){var t=$('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(function(){t.classList.remove('show');},3200);}
async function api(path,opts){
  var r=await fetch(API+path,opts);
  if(r.status===403){throw new Error('This account can\'t open the build queue.');}
  var d=await r.json().catch(function(){return {};});
  if(!r.ok)throw new Error(d.error||'Something went wrong');
  return d;
}
function jpost(p,b){return api(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b)});}

/* "2 days ago" reads faster than a timestamp when the question is
   "has this been sitting?" */
function ago(s){
  if(!s)return '';
  var t=Date.parse(String(s).replace(' ','T')+(String(s).indexOf('Z')<0?'Z':''));
  if(isNaN(t))return '';
  var d=Math.floor((Date.now()-t)/1000);
  if(d<60)return 'just now';
  if(d<3600)return Math.floor(d/60)+'m ago';
  if(d<86400)return Math.floor(d/3600)+'h ago';
  if(d<2592000)return Math.floor(d/86400)+'d ago';
  return new Date(t).toLocaleDateString();
}
function cls(s){return String(s||'').replace(/[^a-z]/gi,'');}

var STATUSES=[], ORDERS=[], filter='attn', q='', openDetail={}, sort='oldest', onlyPhotos=false;
var stageF='', caseF='', moveF='', selectMode=false, SEL={}, SHIPS=[];
/* Everything before "shipped" still wants something from them; the tail end
   is history. This split is what makes the queue a to-do list. */
var DONE=['shipped','delivered'];

function nextOf(st){
  var i=STATUSES.indexOf(st);
  if(i<0||i>=STATUSES.length-1)return null;
  var n=STATUSES[i+1];
  return n==='cancelled'?null:n;
}
function bucket(o){
  if(DONE.indexOf(o.status)>=0)return 'done';
  if(o.status==='new')return 'attn';
  return 'wip';
}
function matches(o){
  if(onlyPhotos&&!o.photos)return false;
  if(stageF&&o.status!==stageF)return false;
  if(caseF&&(o.case_style||'')!==caseF)return false;
  if(moveF&&(o.movement||'')!==moveF)return false;
  if(!q)return true;
  var hay=('#'+o.id+' '+(o.product||'')+' '+(o.notes||'')+' '+(o.tracking_code||'')+' '+
    ['case_style','dial_colour','dial_style','case_colour','movement','watch_size']
      .map(function(k){return o[k]||'';}).join(' ')).toLowerCase();
  return hay.indexOf(q)>=0;
}
function activeFilters(){
  var n=0;
  if(onlyPhotos)n++;
  if(sort!=='oldest')n++;
  if(q)n++;
  if(stageF)n++;
  if(caseF)n++;
  if(moveF)n++;
  return n;
}
/* Attribute filters are built from what's actually in the queue rather than
   the full catalogue vocabulary — offering "Daytona" when no Daytona is in
   the queue is a filter that can only ever return nothing. */
function distinct(key){
  var seen={};
  ORDERS.forEach(function(o){ if(o[key]) seen[o[key]]=(seen[o[key]]||0)+1; });
  return Object.keys(seen).sort();
}
function chipRow(host,key,cur,setter){
  var vals=distinct(key);
  var el=$(host);
  if(!vals.length){ el.innerHTML='<span class="shipmeta">None recorded yet</span>'; return; }
  el.innerHTML=vals.map(function(v){
    return '<button class="schip'+(v===cur?' on':'')+'" data-v="'+esc(v)+'">'+esc(v)+'</button>';
  }).join('');
  el.querySelectorAll('.schip').forEach(function(b){
    b.onclick=function(){ setter(b.dataset.v===cur?'':b.dataset.v); render(); };
  });
}
function sortOrders(list){
  var c=list.slice();
  if(sort==='newest')c.reverse();
  else if(sort==='stage')c.sort(function(a,b){
    return STATUSES.indexOf(a.status)-STATUSES.indexOf(b.status)||a.id-b.id;});
  return c;
}

function photoEl(o){
  if(!o.photos){
    return '<div class="oshot none"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/></svg></div>';
  }
  return '<div class="oshot" data-lb="'+o.id+'" data-n="'+o.photos+'">'+
    '<img src="'+API+'/supplier/photo?id='+o.id+'&n=0" alt="Reference photo for order '+o.id+'" loading="lazy">'+
    (o.photos>1?'<span class="more">+'+(o.photos-1)+'</span>':'')+'</div>';
}
function specEl(o){
  var keys=['case_style','dial_colour','dial_style','case_colour','movement','watch_size'];
  var got=keys.filter(function(k){return o[k];});
  if(!got.length)return '';
  return '<div class="ospec">'+got.map(function(k){return '<span>'+esc(o[k])+'</span>';}).join('')+'</div>';
}
function lastEvent(o){
  var tl=o.timeline||[];
  for(var i=tl.length-1;i>=0;i--){ if(tl[i].kind==='status')return tl[i]; }
  return tl.length?tl[tl.length-1]:null;
}
function timelineEl(o){
  var tl=o.timeline||[];
  if(!tl.length)return '<ul class="tl"><li><i></i>No activity yet.</li></ul>';
  return '<ul class="tl">'+tl.map(function(e){
    var txt = e.kind==='note' ? '<b>'+esc(e.by||'note')+':</b> '+esc(e.detail)
            : e.kind==='created' ? '<b>Added to the queue</b>'
            : '<b>'+esc(e.detail||'')+'</b>'+(e.by?' · '+esc(e.by):'');
    return '<li class="'+(e.kind==='note'?'note-ev':'')+'"><i></i><span>'+txt+
      ' <span style="opacity:.7">· '+esc(ago(e.at))+'</span></span></li>';
  }).join('')+'</ul>';
}

function trackEl(o){
  if(o.tracking_code){
    return '<div class="otrk"><span class="chip">'+
      '<svg viewBox="0 0 24 24"><path d="M3 7h13v10H3zM16 10h4l1 3v4h-5z"/><circle cx="7" cy="18" r="1.6"/><circle cx="18" cy="18" r="1.6"/></svg>'+
      esc(o.tracking_code)+'</span>'+
      '<button data-trk="'+o.id+'">Change</button></div>';
  }
  return '<div class="otrk"><button data-trk="'+o.id+'">+ Add tracking number</button></div>';
}
function cardEl(o){
  var nxt=nextOf(o.status), le=lastEvent(o), attn=bucket(o)==='attn';
  return '<article class="ocard'+(attn?' attn':'')+(selectMode?' selectable':'')+
      (SEL[o.id]?' sel':'')+'" data-id="'+o.id+'">'+
    (selectMode?'<button class="osel'+(SEL[o.id]?' on':'')+'" data-sel="'+o.id+
      '" aria-label="Select order '+o.id+'"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></button>':'')+
    '<div class="otop">'+photoEl(o)+
      '<div class="omid">'+
        '<div class="onum">'+(o.ref_code?'ORDER '+esc(o.ref_code):'ORDER #'+o.id)+'</div>'+
        '<h3 class="oprod">'+esc(o.product||'—')+
          (o.quantity>1?'<span class="oqty">x'+o.quantity+'</span>':'')+'</h3>'+
        specEl(o)+
        (o.notes?'<div class="onote">'+esc(o.notes)+'</div>':'')+
        trackEl(o)+
      '</div>'+
    '</div>'+
    '<div class="obar">'+
      '<span class="ost s-'+cls(o.status)+'"><i></i>'+esc(o.status)+
        (le?' <span class="when">· '+esc(ago(le.at))+'</span>':'')+'</span>'+
    '</div>'+
    '<div class="oact">'+
      (nxt?'<button class="obtn" data-adv="'+o.id+'" data-to="'+esc(nxt)+'">'+
        '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>Mark '+esc(nxt)+'</button>'
       :'<button class="obtn" data-sheet="'+o.id+'">Change status</button>')+
      (nxt?'<button class="obtn ghost" data-sheet="'+o.id+'" aria-label="Other status">&#8943;</button>':'')+
      '<button class="obtn ghost" data-share="'+o.id+'" aria-label="Share status">'+
        '<svg viewBox="0 0 24 24"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/><path d="M12 15V3M8 7l4-4 4 4"/></svg></button>'+
      '<button class="obtn ghost" data-det="'+o.id+'" aria-label="Details">&#9432;</button>'+
    '</div>'+
    '<div class="odet'+(openDetail[o.id]?' on':'')+'" id="det-'+o.id+'">'+
      timelineEl(o)+
      '<form class="noteform" data-note="'+o.id+'">'+
        '<input type="text" placeholder="Add a note — e.g. dial out of stock" aria-label="Add a note">'+
        '<button type="submit">Send</button>'+
      '</form>'+
    '</div>'+
  '</article>';
}

function render(){
  var vis=ORDERS.filter(matches);
  var groups={attn:[],wip:[],done:[]};
  vis.forEach(function(o){groups[bucket(o)].push(o);});
  var show = filter==='all' ? ['attn','wip','done'] : [filter];
  var titles={attn:'Needs acknowledging',wip:'In progress',done:'Done'};
  var html='';
  show.forEach(function(k){
    var list=sortOrders(groups[k]);
    if(!list.length)return;
    html+='<div class="shead"><h2>'+titles[k]+'</h2><span class="n">'+list.length+'</span></div>'+
      '<div class="ogrid">'+list.map(cardEl).join('')+'</div>';
  });
  if(!html){
    html='<div class="sempty"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg><div>'+
      (q||onlyPhotos?'Nothing matches the current filters.'
        :filter==='attn'?'Nothing waiting on you. Everything here has been acknowledged.'
        :'Nothing in the queue yet. New builds appear here as soon as they&rsquo;re shared.')+
      '</div></div>';
  }
  $('list').innerHTML=html;
  var counts={attn:groups.attn.length,wip:groups.wip.length,done:groups.done.length,all:vis.length};
  document.querySelectorAll('.stg').forEach(function(c){
    c.classList.toggle('on',c.dataset.f===filter);
  });
  ['attn','wip','done','all'].forEach(function(k){
    var el=$('c-'+k); if(el)el.textContent=counts[k];
  });
  document.querySelectorAll('.schip[data-sort]').forEach(function(c){
    c.classList.toggle('on',c.dataset.sort===sort);
  });
  var pc=$('f-photos'); if(pc)pc.classList.toggle('on',onlyPhotos);
  var sm=$('f-select'); if(sm)sm.classList.toggle('on',selectMode);
  chipRow('f-stages','status',stageF,function(v){stageF=v;});
  chipRow('f-case','case_style',caseF,function(v){caseF=v;});
  chipRow('f-move','movement',moveF,function(v){moveF=v;});
  var n=activeFilters(), badge=$('fcount');
  badge.textContent=n?String(n):'';
  badge.style.display=n?'':'none';
  $('fbtn').classList.toggle('on',n>0);
  drawBulk();
  wire();
}

/* ---- multi-select + bulk ---- */
function selIds(){ return Object.keys(SEL).filter(function(k){return SEL[k];}).map(Number); }
function drawBulk(){
  var ids=selIds();
  $('bulkbar').classList.toggle('on',selectMode&&ids.length>0);
  $('bulkn').textContent=ids.length+' selected';
}
function clearSel(){ SEL={}; render(); }
async function bulkDo(body,label){
  var ids=selIds();
  if(!ids.length)return;
  try{
    var d=await jpost('/supplier/bulk',Object.assign({ids:ids},body));
    toast(label+' — '+d.count+' order'+(d.count===1?'':'s'));
    SEL={};
    load(true);
  }catch(e){ toast(e.message); }
}

function wire(){
  var L=$('list');
  L.querySelectorAll('[data-sel]').forEach(function(b){
    b.onclick=function(e){
      e.stopPropagation();
      var id=b.dataset.sel;
      SEL[id]=!SEL[id];
      b.classList.toggle('on',SEL[id]);
      b.closest('.ocard').classList.toggle('sel',SEL[id]);
      drawBulk();
    };
  });
  L.querySelectorAll('[data-adv]').forEach(function(b){
    b.onclick=function(){ setStatus(+b.dataset.adv, b.dataset.to, b); };
  });
  L.querySelectorAll('[data-sheet]').forEach(function(b){
    b.onclick=function(){ openSheet(+b.dataset.sheet); };
  });
  L.querySelectorAll('[data-det]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.det, d=$('det-'+id);
      openDetail[id]=!openDetail[id];
      d.classList.toggle('on',openDetail[id]);
    };
  });
  L.querySelectorAll('[data-lb]').forEach(function(el){
    var id=+el.dataset.lb, n=+el.dataset.n;
    el.onclick=function(){ if(!heldOpen) openLb(id,n); heldOpen=false; };
    /* mouse: peek on hover. touch: peek on press-and-hold, and swallow the
       click that follows so holding doesn't also open the lightbox. */
    el.addEventListener('mouseenter',function(){ showPeek(id,el); });
    el.addEventListener('mouseleave',hidePeek);
    el.addEventListener('touchstart',function(){
      clearTimeout(holdT);
      holdT=setTimeout(function(){ heldOpen=true; showPeek(id,el); },350);
    },{passive:true});
    ['touchend','touchmove','touchcancel'].forEach(function(ev){
      el.addEventListener(ev,function(){ clearTimeout(holdT); hidePeek(); },{passive:true});
    });
  });
  L.querySelectorAll('[data-share]').forEach(function(b){
    b.onclick=function(){ openShare(+b.dataset.share); };
  });
  L.querySelectorAll('[data-trk]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.trk, o=byId(id);
      var host=b.closest('.otrk');
      host.outerHTML='<form class="trkform" data-trkf="'+id+'">'+
        '<input type="text" value="'+esc(o.tracking_code||'')+'" placeholder="Courier / tracking number" aria-label="Tracking number">'+
        '<button type="submit">Save</button></form>';
      var f=L.querySelector('[data-trkf="'+id+'"]');
      var inp=f.querySelector('input'); inp.focus(); inp.select();
      f.onsubmit=function(e){
        e.preventDefault();
        var v=inp.value.trim();
        inp.disabled=true;
        jpost('/supplier/tracking',{id:id,tracking_code:v}).then(function(){
          o.tracking_code=v;
          toast(v?'Tracking saved':'Tracking cleared');
          render();
        }).catch(function(err){ inp.disabled=false; toast(err.message); });
      };
    };
  });
  L.querySelectorAll('[data-note]').forEach(function(f){
    f.onsubmit=function(e){
      e.preventDefault();
      var id=+f.dataset.note, inp=f.querySelector('input'), v=inp.value.trim();
      if(!v)return;
      inp.disabled=true;
      jpost('/supplier/note',{id:id,note:v}).then(function(){
        inp.value='';inp.disabled=false;
        toast('Note added');
        openDetail[id]=true;
        load(true);
      }).catch(function(e){ inp.disabled=false; toast(e.message); });
    };
  });
}

/* Optimistic: the row moves the moment they tap, and rolls back only if the
   server disagrees. On a phone on bench wifi, waiting on a round trip before
   showing anything is what makes an app feel broken. */
async function setStatus(id,to,btn){
  var o=ORDERS.filter(function(x){return x.id===id;})[0];
  if(!o)return;
  var was=o.status;
  if(btn)btn.disabled=true;
  o.status=to;
  (o.timeline=o.timeline||[]).push({at:new Date().toISOString(),kind:'status',
    detail:was+' -> '+to,by:'you'});
  render();
  try{
    await jpost('/supplier/status',{id:id,status:to});
    toast('#'+id+' → '+to);
  }catch(e){
    o.status=was;
    if(o.timeline)o.timeline.pop();
    render();
    toast(e.message);
  }
}

/* ---- peek: hover on a mouse, press-and-hold on a touchscreen ---- */
var holdT=null, heldOpen=false;
function byId(id){ return ORDERS.filter(function(x){return x.id===id;})[0]; }
function showPeek(id,anchor){
  var p=$('peek'), img=$('peek-img');
  if(img.dataset.for!==String(id)){ img.src=API+'/supplier/photo?id='+id+'&n=0'; img.dataset.for=String(id); }
  p.classList.add('on');
  /* place it beside the thumb, then nudge back inside the viewport */
  var r=anchor.getBoundingClientRect(), w=300, h=300, m=10;
  var left=r.right+m, top=r.top+r.height/2-h/2;
  if(left+w>innerWidth-m) left=Math.max(m,r.left-w-m);
  if(left<m) left=Math.max(m,(innerWidth-w)/2);
  top=Math.min(Math.max(m,top),innerHeight-h-m);
  p.style.left=left+'px'; p.style.top=top+'px';
}
function hidePeek(){ $('peek').classList.remove('on'); }
window.addEventListener('scroll',hidePeek,{passive:true});

/* ---- share a status: copy, WhatsApp, or PDF ---- */
var shareFor=null;
async function openShare(id){
  shareFor=id;
  $('sheet-title').textContent='Share order #'+id;
  $('sheet-opts').innerHTML=
    '<button class="opt" data-a="post"><i></i>Post to the team WhatsApp</button>'+
    '<button class="opt" data-a="postpdf"><i></i>Post to WhatsApp with PDF</button>'+
    '<button class="opt" data-a="copy"><i></i>Copy status as text</button>'+
    '<button class="opt" data-a="wa"><i></i>Open WhatsApp to pick a chat</button>'+
    '<button class="opt" data-a="pdf"><i></i>Download PDF</button>';
  $('sheet-opts').querySelectorAll('.opt').forEach(function(b){
    b.onclick=function(){ closeSheet(); doShare(id,b.dataset.a); };
  });
  $('sheet').classList.add('on');
}
async function doShare(id,how){
  if(how==='pdf'){ window.open(API+'/supplier/card?id='+id,'_blank'); return; }
  if(how==='post'||how==='postpdf'){
    /* Straight into the team's WhatsApp from here — no app switch, and it
       lands where the team already looks rather than in a file someone has
       to be told about. */
    toast('Sending…');
    try{
      await jpost('/supplier/whatsapp',{id:id,with_pdf:how==='postpdf'});
      toast('Posted to WhatsApp');
      load(true);
    }catch(e){ toast(e.message); }
    return;
  }
  var d;
  try{ d=await api('/supplier/card?id='+id+'&text=1'); }
  catch(e){ toast(e.message); return; }
  if(how==='wa'){
    /* wa.me carries text only — a file can't be attached from a link, so the
       PDF stays a separate download. Text is what actually gets read anyway. */
    window.open('https://wa.me/?text='+encodeURIComponent(d.text),'_blank');
    return;
  }
  try{
    if(navigator.clipboard&&navigator.clipboard.writeText){
      await navigator.clipboard.writeText(d.text);
      toast('Status copied');
    }else{ throw new Error('no clipboard'); }
  }catch(e){
    /* older mobile browsers: show it so it can be selected by hand */
    prompt('Copy the status:',d.text);
  }
}

/* status sheet */
var sheetFor=null;
function openSheet(id){
  sheetFor=id;
  var o=ORDERS.filter(function(x){return x.id===id;})[0];
  if(!o)return;
  $('sheet-title').textContent='Order #'+id;
  $('sheet-opts').innerHTML=STATUSES.map(function(s){
    return '<button class="opt'+(s===o.status?' cur':'')+'" data-s="'+esc(s)+'"><i></i>'+esc(s)+'</button>';
  }).join('');
  $('sheet-opts').querySelectorAll('.opt').forEach(function(b){
    b.onclick=function(){ closeSheet(); if(b.dataset.s!==o.status) setStatus(id,b.dataset.s,null); };
  });
  $('sheet').classList.add('on');
}
function closeSheet(){ $('sheet').classList.remove('on'); sheetFor=null; }
$('sheet-bg').onclick=closeSheet;

/* lightbox */
var lbId=null, lbN=0, lbI=0;
function openLb(id,n){ lbId=id;lbN=n;lbI=0;drawLb();$('lb').classList.add('on'); }
function drawLb(){
  $('lb-img').src=API+'/supplier/photo?id='+lbId+'&n='+lbI;
  $('lb-count').textContent=(lbI+1)+' / '+lbN;
  $('lb-prev').style.visibility=lbN>1?'':'hidden';
  $('lb-next').style.visibility=lbN>1?'':'hidden';
}
function closeLb(){ $('lb').classList.remove('on'); $('lb-img').src=''; }
$('lb-close').onclick=closeLb;
$('lb').onclick=function(e){ if(e.target===$('lb'))closeLb(); };
$('lb-prev').onclick=function(){ lbI=(lbI-1+lbN)%lbN; drawLb(); };
$('lb-next').onclick=function(){ lbI=(lbI+1)%lbN; drawLb(); };
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){ closeLb(); closeSheet(); }
  else if($('lb').classList.contains('on')){
    if(e.key==='ArrowLeft')$('lb-prev').click();
    if(e.key==='ArrowRight')$('lb-next').click();
  }
});

/* ---- tabs ---- */
document.querySelectorAll('.tabs button').forEach(function(t){
  t.onclick=function(){
    var k=t.dataset.tab;
    document.querySelectorAll('.tabs button').forEach(function(x){x.classList.toggle('on',x===t);});
    $('pane-queue').classList.toggle('on',k==='queue');
    $('pane-ships').classList.toggle('on',k==='ships');
    $('queue-controls').style.display = k==='queue' ? '' : 'none';
    if(k==='ships')loadShips();
  };
});

/* ---- shipments ---- */
function money(n,cur){
  if(n===null||n===undefined||n==='')return '—';
  return (cur||'USD')+' '+Number(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
}
async function loadShips(){
  try{
    var d=await api('/supplier/shipments');
    SHIPS=d.shipments||[];
    var unassigned=d.unassigned||[];
    if(!SHIPS.length){
      $('shiplist').innerHTML='<div class="sempty">No shipments yet. Add one above, then '+
        'select builds in the queue and use &ldquo;Add to shipment&rdquo;.</div>';
      return;
    }
    $('shiplist').innerHTML=SHIPS.map(function(s){
      return '<div class="shipcard">'+
        '<div class="shiptop">'+
          '<span class="shipcode">'+esc(s.code||'')+'</span>'+
          '<span class="shipmeta">'+esc(s.carrier||'')+
            (s.order_count?' · '+s.order_count+' watch'+(s.order_count===1?'':'es'):' · empty')+'</span>'+
          '<span class="shipcost"><b>'+esc(money(s.per_watch,s.currency))+'</b>'+
            '<span>per watch</span></span>'+
        '</div>'+
        '<div class="shipmeta" style="margin-top:4px">Total '+esc(money(s.total_cost,s.currency))+
          (s.order_count?' ÷ '+s.order_count:'')+'</div>'+
        (s.orders&&s.orders.length?'<div class="shiporders">'+s.orders.map(function(o){
          return '<div class="shiprow"><span class="n">#'+o.id+'</span>'+
            '<span>'+esc((o.product||'').slice(0,46))+'</span>'+
            '<span class="st">'+esc(o.status)+'</span></div>';
        }).join('')+'</div>':'')+
      '</div>';
    }).join('')+
      (unassigned.length?'<p class="shipmeta" style="margin-top:6px">'+unassigned.length+
        ' build'+(unassigned.length===1?'':'s')+' not yet in a shipment.</p>':'');
  }catch(e){ $('shiplist').innerHTML='<div class="sempty">'+esc(e.message)+'</div>'; }
}
$('shipform').onsubmit=async function(e){
  e.preventDefault();
  var code=$('s-code').value.trim();
  if(!code){toast('A shipment number is needed');return;}
  try{
    await jpost('/supplier/shipment/save',{code:code,carrier:$('s-carrier').value.trim(),
      total_cost:$('s-cost').value,currency:$('s-cur').value.trim()||'USD'});
    toast('Shipment '+code+' saved');
    $('s-code').value='';$('s-carrier').value='';$('s-cost').value='';
    loadShips();
  }catch(err){ toast(err.message); }
};

/* ---- bulk buttons ---- */
$('bulk-clear').onclick=clearSel;
$('bulk-status').onclick=function(){
  $('sheet-title').textContent=selIds().length+' orders — set status';
  $('sheet-opts').innerHTML=STATUSES.map(function(s){
    return '<button class="opt" data-s="'+esc(s)+'"><i></i>'+esc(s)+'</button>';}).join('');
  $('sheet-opts').querySelectorAll('.opt').forEach(function(b){
    b.onclick=function(){ closeSheet(); bulkDo({status:b.dataset.s},'Status set'); };
  });
  $('sheet').classList.add('on');
};
$('bulk-track').onclick=function(){
  var v=prompt('Tracking number for '+selIds().length+' selected orders:');
  if(v===null)return;
  bulkDo({tracking_code:v.trim()},'Tracking set');
};
$('bulk-ship').onclick=async function(){
  if(!SHIPS.length){ try{ var d=await api('/supplier/shipments'); SHIPS=d.shipments||[]; }catch(e){} }
  if(!SHIPS.length){ toast('Create a shipment first, on the Shipments tab'); return; }
  $('sheet-title').textContent='Add '+selIds().length+' to a shipment';
  $('sheet-opts').innerHTML=SHIPS.map(function(s){
    return '<button class="opt" data-s="'+s.id+'"><i></i>'+esc(s.code)+
      ' <span style="opacity:.6">('+s.order_count+')</span></button>';}).join('')+
    '<button class="opt" data-s=""><i></i>Remove from shipment</button>';
  $('sheet-opts').querySelectorAll('.opt').forEach(function(b){
    b.onclick=async function(){
      closeSheet();
      try{
        var d=await jpost('/supplier/shipment/assign',
          {ids:selIds(),shipment_id:b.dataset.s||null});
        toast(d.count+' order'+(d.count===1?'':'s')+' updated');
        SEL={}; load(true); loadShips();
      }catch(e){ toast(e.message); }
    };
  });
  $('sheet').classList.add('on');
};
$('bulk-pdf').onclick=function(){
  var ids=selIds();
  if(!ids.length)return;
  window.open(API+'/supplier/ledger?ids='+ids.join(','),'_blank');
};

/* filters + search */
document.querySelectorAll('.stg').forEach(function(c){
  c.onclick=function(){ filter=c.dataset.f; render(); window.scrollTo({top:0,behavior:'smooth'}); };
});
$('f-select').onclick=function(){
  selectMode=!selectMode;
  if(!selectMode)SEL={};
  render();
};
document.querySelectorAll('.schip[data-sort]').forEach(function(c){
  c.onclick=function(){ sort=c.dataset.sort; render(); };
});
$('f-photos').onclick=function(){ onlyPhotos=!onlyPhotos; render(); };
$('fbtn').onclick=function(){
  var open=$('fpanel').classList.toggle('on');
  $('fbtn').classList.toggle('open',open);
  $('fbtn').setAttribute('aria-expanded',open?'true':'false');
};
$('fclear').onclick=function(){
  q='';onlyPhotos=false;sort='oldest';stageF='';caseF='';moveF='';
  $('q').value='';render();
};
var qT;
$('q').addEventListener('input',function(){
  clearTimeout(qT);
  var v=this.value;
  qT=setTimeout(function(){ q=v.trim().toLowerCase(); render(); },140);
});

/* load + keep fresh: refetch when they come back to the tab, and slowly in
   the background, so an order added while it sat open still shows up */
var loading=false;
async function load(quiet){
  if(loading)return;
  loading=true;
  if(!quiet)$('sync').textContent='Loading…';
  try{
    var d=await api('/supplier/orders');
    STATUSES=d.statuses||[];
    ORDERS=d.orders||[];
    $('sync').textContent='Updated '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    $('sync').classList.remove('err');
    render();
  }catch(e){
    $('sync').textContent=e.message;
    $('sync').classList.add('err');
    if(!ORDERS.length)$('list').innerHTML='<div class="sempty">'+esc(e.message)+'</div>';
  }
  loading=false;
}
document.addEventListener('visibilitychange',function(){ if(!document.hidden)load(true); });
setInterval(function(){ if(!document.hidden)load(true); },90000);
load();

fetch(API+'/whoami').then(function(r){return r.ok?r.json():null;}).then(function(i){
  if(i&&i.email){var w=$('who');if(w)w.textContent=i.email;}
}).catch(function(){});
"""


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Build queue">
<meta name="mobile-web-app-capable" content="yes">
<title>Build queue — Timelabs Co</title>
<style>{HUB_STYLE}{SUP_CSS}</style></head>
<body>
<div class="swrap">
  <div class="stop">
    <div class="stop-row">
      <span class="sdot">T</span><b>Build queue</b>
      <span class="sp"></span>
      <span class="swho" id="who"></span>
    </div>
    <div class="tabs">
      <button class="on" data-tab="queue">Build queue</button>
      <button data-tab="ships">Shipments</button>
    </div>

    <div id="queue-controls">
      <div class="stagebar">
        <button class="stg attn on" data-f="attn" aria-label="Needs you">
          <svg viewBox="0 0 24 24"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>
          <b id="c-attn">0</b><span class="lbl">Needs you</span></button>
        <button class="stg" data-f="wip" aria-label="In progress">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
          <b id="c-wip">0</b><span class="lbl">In progress</span></button>
        <button class="stg" data-f="done" aria-label="Done">
          <svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>
          <b id="c-done">0</b><span class="lbl">Done</span></button>
        <button class="stg" data-f="all" aria-label="Everything">
          <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
          <b id="c-all">0</b><span class="lbl">All</span></button>
      </div>
      <div class="fbar">
        <input id="q" class="ssearch" type="search" placeholder="Search number, product, spec, tracking"
               aria-label="Search the queue" autocomplete="off">
        <button class="fbtn" id="fbtn" aria-expanded="false" aria-controls="fpanel">
          <svg viewBox="0 0 24 24"><path d="M4 6h16M7 12h10M10 18h4"/></svg>
          Filters<span class="cnt" id="fcount" style="display:none"></span>
          <svg class="chev" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
        </button>
      </div>
      <div class="fpanel" id="fpanel">
        <p class="flab">Exact stage</p>
        <div class="frow" id="f-stages"></div>
        <p class="flab">Case style</p>
        <div class="frow" id="f-case"></div>
        <p class="flab">Movement</p>
        <div class="frow" id="f-move"></div>
        <p class="flab">Order by</p>
        <div class="frow">
          <button class="schip on" data-sort="oldest">Oldest first</button>
          <button class="schip" data-sort="newest">Newest first</button>
          <button class="schip" data-sort="stage">Stage</button>
        </div>
        <div class="frow">
          <button class="schip" id="f-photos">With a photo</button>
          <button class="schip" id="f-select">Select mode</button>
          <button class="fclear" id="fclear">Reset filters</button>
        </div>
      </div>
      <div style="margin-top:8px"><span class="sync" id="sync">Loading&hellip;</span></div>
    </div>
  </div>

  <section class="pane on" id="pane-queue">
    <main id="list"><div class="sempty">Loading&hellip;</div></main>
    <div class="bulkbar" id="bulkbar">
      <b id="bulkn">0 selected</b>
      <span class="sp"></span>
      <button id="bulk-status">Set status</button>
      <button id="bulk-track">Tracking</button>
      <button id="bulk-ship">Add to shipment</button>
      <button class="primary" id="bulk-pdf">Build request PDF</button>
      <button id="bulk-clear">Clear</button>
    </div>
  </section>

  <section class="pane" id="pane-ships">
    <form class="shipform" id="shipform">
      <input id="s-code" placeholder="Shipment number" aria-label="Shipment number" autocomplete="off" required>
      <input id="s-carrier" placeholder="Carrier (DHL, EMS&hellip;)" aria-label="Carrier" autocomplete="off">
      <input id="s-cost" type="number" step="0.01" min="0" placeholder="Total cost" aria-label="Total cost">
      <input id="s-cur" placeholder="USD" aria-label="Currency" value="USD" autocomplete="off">
      <button type="submit">Save shipment</button>
    </form>
    <div id="shiplist"><div class="sempty">Loading&hellip;</div></div>
  </section>

  <p class="sfoot">Timelabs Co &middot; build queue</p>
</div>

<div class="peek" id="peek" aria-hidden="true"><img id="peek-img" alt=""></div>

<div class="sheet" id="sheet" role="dialog" aria-modal="true">
  <div class="sheet-bg" id="sheet-bg"></div>
  <div class="sheet-in">
    <div class="sheet-grab"></div>
    <h3 id="sheet-title">Order</h3>
    <div id="sheet-opts"></div>
  </div>
</div>

<div class="lb" id="lb" role="dialog" aria-modal="true" aria-label="Reference photo">
  <button class="lb-close" id="lb-close" aria-label="Close">&times;</button>
  <img id="lb-img" alt="Reference photo">
  <div class="lb-nav">
    <button id="lb-prev" aria-label="Previous photo">&#8249;</button>
    <span id="lb-count" style="color:#fff;font-size:13px;align-self:center;padding:0 6px"></span>
    <button id="lb-next" aria-label="Next photo">&#8250;</button>
  </div>
</div>

<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>{SUP_JS}</script>
</body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
