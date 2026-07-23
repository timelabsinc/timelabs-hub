#!/usr/bin/env python3
"""Order form — log an order in seconds. Renders /var/www/ops/order-form.html.

Built around how orders actually arrive: staff copy a labelled block from
WhatsApp/email and paste it in — the parser splits Name/Email/Phone/Address/
Pincode into fields, the server fills city/state from the address and pulls
product attributes (case style, dial colour, movement…) out of the free text,
so the whole record structures itself from one paste.

Four tabs: New order, Orders, Customers, What's selling. hermes.db is the
source of truth; each save mirrors to the Orders and Customers sheet tabs and
pushes photos to Drive.

Notes on two things that are easy to get wrong:
  * Inputs are 16px. Below that, iOS Safari zooms the page on focus and won't
    zoom back out — that's the "page jumps when I tap a field" bug.
  * Clipboard has two halves. The paste EVENT (⌘V) needs no permission and is
    the desktop path; navigator.clipboard.read/readText needs a user gesture
    and a permission grant, and is the only path on a phone with no keyboard.
    Both are wired to the same handlers, and the buttons hide themselves when
    the API isn't available.

CSS and JS live in raw string constants, not the f-string, so regex braces
survive intact.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, _appnav, hub_header, hub_footer, WHOAMI_JS

OUT = "/var/www/ops/order-form.html"

STATUSES = ["new", "confirmed", "in build", "shipped", "delivered", "cancelled"]
DEFAULT_SOURCES = ["CC", "TLC", "Offkicks"]

OF_CSS = r"""
  /* 16px inputs — anything smaller makes iOS Safari zoom on focus */
  .pin{width:100%;font-size:16px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:11px 13px;font-family:inherit;
    transition:border-color .13s,box-shadow .13s;-webkit-appearance:none;appearance:none;}
  .pin:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
  textarea.pin{min-height:64px;resize:vertical;line-height:1.5;}
  select.pin{background-image:none;}
  input,select,textarea,button{font-size:16px;}

  /* tabs */
  .tabs{display:flex;gap:4px;margin-bottom:16px;overflow-x:auto;-webkit-overflow-scrolling:touch;
    scrollbar-width:none;padding-bottom:2px;}
  .tabs::-webkit-scrollbar{display:none;}
  .tab{flex:none;border:1px solid var(--border);background:var(--card);color:var(--muted);
    border-radius:999px;padding:8px 15px;font-size:13.5px;font-weight:650;cursor:pointer;
    white-space:nowrap;transition:background .13s,color .13s,border-color .13s;}
  .tab.on{background:var(--ink);color:var(--bg);border-color:var(--ink);}
  .pane{display:none;}
  .pane.on{display:block;}

  .of-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:20px;box-shadow:var(--shadow);margin-bottom:16px;}
  @media(max-width:600px){.of-card{padding:15px;}}
  .of-card.pastecard{padding:16px 18px;}

  /* paste-to-fill */
  .paste-head{display:flex;align-items:center;gap:9px;margin-bottom:10px;flex-wrap:wrap;}
  .paste-head svg{width:17px;height:17px;stroke:var(--accent);fill:none;stroke-width:1.8;
    stroke-linecap:round;stroke-linejoin:round;flex:none;}
  .paste-head b{font-size:14.5px;color:var(--ink);}
  #paste{width:100%;min-height:76px;resize:vertical;font-size:16px;line-height:1.55;
    border:1.5px dashed var(--border-2);border-radius:var(--r-s);background:var(--bg);
    color:var(--ink);padding:12px 14px;font-family:inherit;transition:border-color .15s;}
  #paste:focus{outline:none;border-style:solid;border-color:var(--accent);
    box-shadow:0 0 0 3px var(--accent-bg);}
  .caught{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}
  .caught span{font-size:11.5px;font-weight:650;color:var(--good);background:var(--good-bg);
    border-radius:6px;padding:3px 9px;}
  .caught span.miss{color:var(--muted);background:var(--card-2);}

  /* fields */
  .of-field{margin-bottom:15px;}
  .of-field:last-child{margin-bottom:0;}
  .of-field label{display:block;font-size:11.5px;font-weight:650;color:var(--muted);
    text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}
  .of-field .req{color:var(--accent);}
  .of-row{display:grid;grid-template-columns:1fr 1fr;gap:13px;}
  .of-row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:13px;}
  @media(max-width:600px){.of-row,.of-row3{grid-template-columns:1fr;gap:0;}
    .of-row>.of-field,.of-row3>.of-field{margin-bottom:15px;}}
  .price-wrap{position:relative;}
  .price-wrap span{position:absolute;left:13px;top:50%;transform:translateY(-50%);
    color:var(--muted);font-size:15px;}
  .price-wrap input{padding-left:28px;}
  .of-sep{height:1px;background:var(--border);margin:19px -20px;}
  @media(max-width:600px){.of-sep{margin:17px -15px;}}
  .of-legend{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;
    letter-spacing:.06em;margin:0 0 13px;}

  /* source chips */
  .srcs{display:flex;flex-wrap:wrap;gap:8px;}
  .src{border:1px solid var(--border);background:var(--card);color:var(--ink);
    border-radius:999px;padding:9px 16px;font-size:14px;font-weight:600;cursor:pointer;
    transition:background .12s,border-color .12s,color .12s;}
  .src.on{background:var(--accent-bg);border-color:var(--accent);color:var(--accent);}
  .src.add{color:var(--muted);border-style:dashed;}

  /* attribute chips */
  .attrs{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px;min-height:4px;}
  .attr{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;
    background:var(--card-2);color:var(--ink);border-radius:7px;padding:5px 9px;}
  .attr i{font-style:normal;color:var(--muted);font-weight:500;}
  .attr button{border:none;background:none;color:var(--muted);cursor:pointer;font-size:14px;
    line-height:1;padding:0 0 0 2px;}
  .attr-hint{font-size:11.5px;color:var(--muted);margin-top:7px;line-height:1.5;}

  /* photos */
  .dz{border:1.5px dashed var(--border-2);border-radius:var(--r-s);padding:18px 14px;
    text-align:center;transition:border-color .15s,background .15s;cursor:pointer;}
  .dz:hover{border-color:var(--accent);}
  .dz.over{border-color:var(--accent);background:var(--accent-bg);border-style:solid;}
  .dz-t{font-size:14px;color:var(--ink);font-weight:600;}
  .dz-s{font-size:12.5px;color:var(--muted);margin-top:4px;line-height:1.55;}
  .dz-btns{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:11px;}
  .kbd{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
    border:1px solid var(--border-2);border-radius:4px;padding:1px 5px;color:var(--ink);}
  .shots{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:11px;}
  .shot{position:relative;width:78px;height:78px;border-radius:10px;overflow:hidden;
    border:1px solid var(--border);background:var(--card-2);}
  .shot img{width:100%;height:100%;object-fit:cover;display:block;}
  .shot.up img{opacity:.45;}
  .shot .spin{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-size:11px;font-weight:650;color:var(--muted);}
  .shot .rm{position:absolute;top:3px;right:3px;width:22px;height:22px;border-radius:50%;
    border:none;background:rgba(0,0,0,.62);color:#fff;font-size:13px;cursor:pointer;
    display:flex;align-items:center;justify-content:center;line-height:1;}
  input[type=file]{display:none;}

  .btn{font-size:13.5px;font-weight:650;border-radius:var(--r-s);padding:10px 15px;cursor:pointer;
    border:1px solid var(--border);background:var(--card);color:var(--ink);
    display:inline-flex;align-items:center;gap:7px;transition:border-color .12s,transform .1s;}
  .btn:hover{border-color:var(--border-2);}
  .btn:active{transform:scale(.97);}
  .btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;
    stroke-linecap:round;stroke-linejoin:round;}
  .btn.sm{padding:6px 11px;font-size:12.5px;}
  .btn.primary{background:var(--ink);color:var(--bg);border-color:var(--ink);width:100%;
    justify-content:center;padding:15px;font-size:16px;margin-top:2px;}
  .btn.primary[disabled]{opacity:.42;cursor:not-allowed;}
  .savehint{text-align:center;font-size:12px;color:var(--muted);margin-top:9px;}
  .warnbox{background:var(--accent-bg);color:var(--accent);border-radius:var(--r-s);
    padding:11px 13px;font-size:13px;line-height:1.55;margin-bottom:14px;}
  .okbox{background:var(--good-bg);color:var(--good);border-radius:var(--r-s);
    padding:11px 13px;font-size:13px;line-height:1.55;margin-bottom:14px;}

  /* lists */
  .sec-head{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;flex-wrap:wrap;}
  .sec-head h2{font-size:15.5px;margin:0;}
  .sec-head .sp{flex:1;}
  .sec-head a{font-size:12.5px;color:var(--accent);}
  .tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  table.dt{width:100%;border-collapse:collapse;font-size:13.5px;min-width:700px;}
  table.dt th{text-align:left;font-size:11px;font-weight:650;color:var(--muted);
    text-transform:uppercase;letter-spacing:.04em;padding:0 10px 9px;white-space:nowrap;}
  table.dt td{padding:11px 10px;border-top:1px solid var(--border);vertical-align:top;}
  .o-strong{font-weight:650;color:var(--ink);}
  .o-sub{font-size:12px;color:var(--muted);line-height:1.45;}
  .o-when{color:var(--muted);font-size:12.5px;white-space:nowrap;}
  .o-num{white-space:nowrap;}
  .pill{display:inline-block;font-size:10.5px;font-weight:650;text-transform:uppercase;
    letter-spacing:.04em;padding:3px 8px;border-radius:6px;color:var(--muted);background:var(--card-2);}
  .pill.new{color:var(--accent);background:var(--accent-bg);}
  .pill.delivered{color:var(--good);background:var(--good-bg);}
  .pill.cancelled{opacity:.6;}
  .pill.vip{color:var(--accent);background:var(--accent-bg);}
  .empty{color:var(--muted);font-size:13.5px;padding:16px 2px;}

  /* the same rows as stacked cards on a phone */
  @media(max-width:720px){
    table.dt{min-width:0;display:block;}
    table.dt thead{display:none;}
    table.dt tbody,table.dt tr,table.dt td{display:block;width:100%;}
    table.dt tr{border:1px solid var(--border);border-radius:var(--r-s);padding:11px 13px;
      margin-bottom:10px;background:var(--bg);}
    table.dt td{border:none;padding:3px 0;}
    table.dt td:before{content:attr(data-l);display:block;font-size:10.5px;font-weight:650;
      color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:1px;}
    table.dt td:empty{display:none;}
  }

  /* what's selling */
  .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px;}
  @media(max-width:520px){.kpis{grid-template-columns:1fr;}}
  .kpi{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:15px 16px;}
  .kpi b{display:block;font-size:23px;font-weight:700;color:var(--ink);letter-spacing:-.02em;}
  .kpi span{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:650;}
  .bars{margin-bottom:18px;}
  .bars:last-child{margin-bottom:0;}
  .bars h3{font-size:12.5px;font-weight:650;color:var(--muted);text-transform:uppercase;
    letter-spacing:.05em;margin:0 0 10px;}
  .bar{display:grid;grid-template-columns:130px 1fr 44px;gap:10px;align-items:center;margin-bottom:7px;}
  @media(max-width:520px){.bar{grid-template-columns:96px 1fr 36px;gap:8px;}}
  .bar .nm{font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .bar .tr{background:var(--card-2);border-radius:5px;height:9px;overflow:hidden;}
  .bar .fl{height:100%;background:var(--accent);border-radius:5px;}
  .bar .vl{font-size:12.5px;color:var(--muted);text-align:right;}

  /* product combobox — a suggestion overlay, never a locked-in enum */
  .combo-wrap{position:relative;}
  .combo-list{position:absolute;top:calc(100% + 4px);left:0;right:0;z-index:30;
    background:var(--card);border:1px solid var(--border-2);border-radius:var(--r-s);
    box-shadow:var(--shadow-lg);max-height:230px;overflow-y:auto;padding:5px;}
  .combo-opt{padding:9px 11px;font-size:14px;color:var(--ink);border-radius:6px;
    cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .combo-opt.hi{background:var(--accent-bg);color:var(--accent);}

  /* editable status pill */
  select.pill-select{border:none;cursor:pointer;font:inherit;font-size:10.5px;font-weight:650;
    text-transform:uppercase;letter-spacing:.04em;padding:3px 20px 3px 8px;border-radius:6px;
    color:var(--muted);background-color:var(--card-2);-webkit-appearance:none;appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 5px center;background-size:10px;}
  select.pill-select.new{color:var(--accent);background-color:var(--accent-bg);}
  select.pill-select.delivered{color:var(--good);background-color:var(--good-bg);}
  select.pill-select.cancelled{opacity:.6;}
  select.pill-select[disabled]{opacity:.5;cursor:wait;}

  /* what's selling — sortable table */
  .sellhead{display:flex;align-items:baseline;gap:8px;margin:22px 0 10px;}
  .sellhead h3{font-size:12.5px;font-weight:650;color:var(--muted);text-transform:uppercase;
    letter-spacing:.05em;margin:0;}
  table.dt th.sortable{cursor:pointer;user-select:none;}
  table.dt th.sortable:hover{color:var(--ink);}
  table.dt th.sortable .arr{opacity:.4;margin-left:3px;}
  table.dt th.sortable.on .arr{opacity:1;color:var(--accent);}
  .pname{cursor:text;border-bottom:1px dashed var(--border-2);}
  .pname:hover{border-color:var(--accent);}
  .pname input{font:inherit;font-size:13.5px;font-weight:650;color:var(--ink);border:1px solid var(--accent);
    border-radius:5px;padding:3px 6px;width:100%;background:var(--bg);}
"""

OF_JS = r"""
'use strict';
var API='/ops/agent/api';
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
var toastT;
function toast(m){var t=$('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(function(){t.classList.remove('show');},3400);}
async function api(path,opts){
  var res=await fetch(API+path,opts);
  if(res.status===403){location.href='/oauth2/start?rd=/ops/order-form.html';throw new Error('auth');}
  var d=await res.json().catch(function(){return {};});
  if(!res.ok)throw new Error(d.error||'failed');
  return d;
}
function jpost(path,body){return api(path,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}

/* ---------------- tabs ---------------- */
var loaded={};
document.querySelectorAll('.tab').forEach(function(t){
  t.onclick=function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on');});
    document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('on');});
    t.classList.add('on');
    var pane=$('pane-'+t.dataset.p);
    pane.classList.add('on');
    if(!loaded[t.dataset.p]){loaded[t.dataset.p]=1;
      if(t.dataset.p==='orders')loadOrders();
      if(t.dataset.p==='customers')loadCustomers();
      if(t.dataset.p==='selling')loadSelling();
    }
  };
});

/* ---------------- paste-to-fill ----------------
   Orders arrive as a labelled block. Match a label per line; any line that
   isn't a new label continues the one before it, which is what makes
   multi-line addresses come through whole. */
var FIELD_MAP=[
  ['f-cust',    /^(name|customer|customer\s*name|full\s*name)$/i,                                    'Name'],
  ['f-email',   /^(e-?mail|e-?mail\s*id|mail)$/i,                                                    'Email'],
  ['f-phone',   /^(phone|phone\s*no\.?|phone\s*number|mobile|mobile\s*no\.?|contact|contact\s*no\.?|ph|ph\s*no\.?|whatsapp)$/i, 'Phone'],
  ['f-address', /^(address|addr|shipping\s*address|delivery\s*address|full\s*address)$/i,            'Address'],
  ['f-pincode', /^(pin|pin\s*code|pincode|postal\s*code|zip|zip\s*code)$/i,                          'Pincode'],
  ['f-city',    /^(city|town)$/i,                                                                    'City'],
  ['f-state',   /^(state|province)$/i,                                                               'State'],
  ['f-product', /^(product|item|order|reference|ref|model)$/i,                                       'Product'],
  ['f-qty',     /^(qty|quantity|nos?|pcs)$/i,                                                        'Qty'],
  ['f-price',   /^(price|amount|total|value|cost)$/i,                                                'Price'],
  ['f-notes',   /^(notes?|remarks?|comments?|message)$/i,                                            'Notes']
];

function parseBlock(text){
  var out={}, cur=null;
  text.replace(/\r/g,'').split('\n').forEach(function(raw){
    var line=raw.trim();
    if(!line)return;
    var m=line.match(/^([A-Za-z][A-Za-z .\/_-]{0,28}?)\s*[:–-]\s*(.*)$/);
    var key=null;
    if(m){
      for(var i=0;i<FIELD_MAP.length;i++){
        if(FIELD_MAP[i][1].test(m[1].trim())){key=FIELD_MAP[i][0];break;}
      }
    }
    if(key){cur=key;out[key]=(m[2]||'').trim();}
    else if(cur){out[cur]=(out[cur]?out[cur]+', ':'')+line;}
  });
  /* Loose fallbacks so an unlabelled or differently-labelled block still lands. */
  if(!out['f-email']){var e=text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);if(e)out['f-email']=e[0];}
  if(!out['f-phone']){var p=text.match(/(?:\+?91[\s-]*)?[6-9]\d{9}/);if(p)out['f-phone']=p[0];}
  if(!out['f-pincode']){var z=text.match(/\b[1-9]\d{5}\b/);if(z)out['f-pincode']=z[0];}
  if(out['f-address']&&out['f-pincode']){
    out['f-address']=out['f-address'].replace(new RegExp('[,\\s]*'+out['f-pincode']+'\\b'),'').trim();
  }
  if(out['f-qty'])out['f-qty']=(out['f-qty'].match(/\d+/)||['1'])[0];
  if(out['f-price'])out['f-price']=(out['f-price'].match(/[\d.]+/)||[''])[0];
  return out;
}

function applyPaste(){
  var text=$('paste').value;
  if(!text.trim()){$('caught').innerHTML='';return;}
  var got=parseBlock(text), chips='', n=0;
  FIELD_MAP.forEach(function(f){
    var v=got[f[0]];
    if(v){$(f[0]).value=v;n++;chips+='<span>'+esc(f[2])+'</span>';}
  });
  if(!n)chips='<span class="miss">Nothing recognised — type it in below</span>';
  $('caught').innerHTML=chips;
  canSave();
  if(n){toast('Filled '+n+' field'+(n===1?'':'s')+' — check and save');enrich();}
}
$('paste').addEventListener('input',applyPaste);
$('clear-paste').onclick=function(){$('paste').value='';$('caught').innerHTML='';};

/* Read the clipboard on demand — the only way in on a phone, where there's
   no ⌘V. Needs a user gesture, which the click provides. */
$('pull-clip').onclick=async function(){
  try{
    var txt=await navigator.clipboard.readText();
    if(!txt||!txt.trim()){toast('Clipboard is empty');return;}
    $('paste').value=txt;
    applyPaste();
  }catch(e){
    $('paste').focus();
    toast("Couldn't read the clipboard — paste into the box instead");
  }
};
if(!(navigator.clipboard&&navigator.clipboard.readText))$('pull-clip').style.display='none';

/* server fills city/state from the address and attributes from the product,
   so the vocabulary lives in exactly one place */
var enrichT;
function enrich(){
  clearTimeout(enrichT);
  enrichT=setTimeout(async function(){
    var addr=$('f-address').value.trim(), prod=$('f-product').value.trim();
    if(!addr&&!prod)return;
    try{
      var d=await jpost('/orders/parse',{address:addr,pincode:$('f-pincode').value.trim(),
        product:prod,notes:$('f-notes').value.trim()});
      if(d.city&&!$('f-city').value)$('f-city').value=d.city;
      if(d.state&&!$('f-state').value)$('f-state').value=d.state;
      /* honour chips the user deliberately removed — re-extracting on every
         keystroke would otherwise keep putting them back */
      attrs={};
      var got=d.attributes||{};
      Object.keys(got).forEach(function(k){if(!dropped[k])attrs[k]=got[k];});
      attrLabels=d.labels||attrLabels;drawAttrs();
    }catch(e){}
  },350);
}
['f-address','f-pincode','f-product','f-notes'].forEach(function(id){
  $(id).addEventListener('input',enrich);
});

/* ---------------- attributes ---------------- */
var attrs={}, attrLabels={}, dropped={};
function drawAttrs(){
  var keys=Object.keys(attrs).filter(function(k){return attrs[k];});
  $('attrs').innerHTML=keys.length?keys.map(function(k){
    return '<span class="attr"><i>'+esc(attrLabels[k]||k)+'</i>'+esc(attrs[k])+
      '<button data-k="'+k+'" title="Remove">&times;</button></span>';
  }).join(''):'';
  $('attr-hint').style.display=keys.length?'none':'';
  $('attrs').querySelectorAll('button').forEach(function(b){
    b.onclick=function(){dropped[b.dataset.k]=1;delete attrs[b.dataset.k];drawAttrs();};
  });
}

/* ---------------- product combobox ----------------
   A filter-as-you-type overlay, not a locked-in list — picking a suggestion
   just fills the field, and anything typed that matches nothing is saved
   exactly as typed, same as before this existed. */
var PRODUCTS=[], comboHi=-1, comboItems=[];
function comboFilter(q){
  q=q.trim().toLowerCase();
  if(!q)return [];
  return PRODUCTS.filter(function(p){return p.toLowerCase().indexOf(q)>=0;}).slice(0,8);
}
function comboRender(items){
  comboItems=items;comboHi=-1;
  var list=$('prod-list');
  if(!items.length){list.hidden=true;$('f-product').setAttribute('aria-expanded','false');return;}
  list.innerHTML=items.map(function(p,i){return '<div class="combo-opt" data-i="'+i+'">'+esc(p)+'</div>';}).join('');
  list.hidden=false;
  $('f-product').setAttribute('aria-expanded','true');
  list.querySelectorAll('.combo-opt').forEach(function(el){
    el.onmousedown=function(e){e.preventDefault();comboPick(items[+el.dataset.i]);};
  });
}
function comboHighlight(i){
  comboHi=i;
  $('prod-list').querySelectorAll('.combo-opt').forEach(function(el,j){el.classList.toggle('hi',j===i);});
}
function comboPick(v){
  $('f-product').value=v;
  comboRender([]);
  canSave();enrich();
}
$('f-product').addEventListener('input',function(){comboRender(comboFilter(this.value));});
$('f-product').addEventListener('keydown',function(e){
  if($('prod-list').hidden)return;
  if(e.key==='ArrowDown'){e.preventDefault();comboHighlight(Math.min(comboHi+1,comboItems.length-1));}
  else if(e.key==='ArrowUp'){e.preventDefault();comboHighlight(Math.max(comboHi-1,0));}
  else if(e.key==='Enter'&&comboHi>=0){e.preventDefault();comboPick(comboItems[comboHi]);}
  else if(e.key==='Escape'){comboRender([]);}
});
$('f-product').addEventListener('blur',function(){setTimeout(function(){comboRender([]);},120);});

/* ---------------- source ---------------- */
var source='';
function drawSources(list){
  $('srcs').innerHTML=list.map(function(s){
    return '<button type="button" class="src'+(s===source?' on':'')+'" data-s="'+esc(s)+'">'+esc(s)+'</button>';
  }).join('')+'<button type="button" class="src add" id="src-add">+ New</button>';
  $('srcs').querySelectorAll('.src[data-s]').forEach(function(b){
    b.onclick=function(){source=(source===b.dataset.s)?'':b.dataset.s;drawSources(list);};
  });
  $('src-add').onclick=function(){
    var v=(prompt('Name the new source')||'').trim();
    if(!v)return;
    if(list.indexOf(v)<0)list.push(v);
    source=v;drawSources(list);
  };
}

/* ---------------- photos ----------------
   Three ways in, because all three happen: the paste EVENT (desktop ⌘V, no
   permission needed), the clipboard API behind a button (the phone path), and
   the file picker (which on iPhone offers camera + photo library). */
var shots=[];
function drawShots(){
  $('shots').innerHTML=shots.map(function(p){
    return '<div class="shot'+(p.path?'':' up')+'"><img src="'+p.url+'" alt="">'+
      (p.path?'':'<span class="spin">…</span>')+
      '<button class="rm" data-k="'+p.key+'" title="Remove">&times;</button></div>';
  }).join('');
  $('shots').querySelectorAll('.rm').forEach(function(b){
    b.onclick=function(ev){ev.stopPropagation();
      shots=shots.filter(function(x){return x.key!==b.dataset.k;});drawShots();};
  });
}
async function addFiles(files){
  var list=Array.prototype.slice.call(files||[]).filter(function(f){
    return f&&f.type&&f.type.indexOf('image/')===0;});
  if(!list.length)return;
  for(var i=0;i<list.length;i++){
    if(shots.length>=10){toast('10 photos is the limit');break;}
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
$('dz').onclick=function(e){if(e.target.id!=='paste-img')$('ph-file').click();};
$('ph-file').onchange=function(){addFiles(this.files);this.value='';};
['dragenter','dragover'].forEach(function(ev){
  $('dz').addEventListener(ev,function(e){e.preventDefault();$('dz').classList.add('over');});});
['dragleave','drop'].forEach(function(ev){
  $('dz').addEventListener(ev,function(e){e.preventDefault();$('dz').classList.remove('over');});});
$('dz').addEventListener('drop',function(e){addFiles(e.dataTransfer&&e.dataTransfer.files);});
document.addEventListener('paste',function(e){
  if(!e.clipboardData)return;
  var f=e.clipboardData.files;
  if(f&&f.length){addFiles(f);e.preventDefault();}
});
/* explicit button: reads image blobs out of the clipboard on a tap */
$('paste-img').onclick=async function(ev){
  ev.stopPropagation();
  try{
    var items=await navigator.clipboard.read();
    var files=[];
    for(var i=0;i<items.length;i++){
      var types=items[i].types.filter(function(t){return t.indexOf('image/')===0;});
      if(types.length){
        var blob=await items[i].getType(types[0]);
        files.push(new File([blob],'pasted-'+Date.now()+'.'+types[0].split('/')[1],{type:types[0]}));
      }
    }
    if(!files.length){toast('No image in the clipboard');return;}
    addFiles(files);
  }catch(e){toast("Couldn't read the clipboard — try ⌘V, or pick the file");}
};
if(!(navigator.clipboard&&navigator.clipboard.read))$('paste-img').style.display='none';

/* ---------------- save ---------------- */
function canSave(){
  $('save').disabled=!($('f-cust').value.trim()&&$('f-product').value.trim());
}
['f-cust','f-product'].forEach(function(id){$(id).addEventListener('input',canSave);});

$('save').onclick=async function(){
  if(shots.filter(function(p){return !p.path;}).length){toast('A photo is still uploading — one moment');return;}
  var btn=$('save');btn.disabled=true;var label=btn.textContent;btn.textContent='Saving…';
  try{
    var body={customer_name:$('f-cust').value.trim(),customer_phone:$('f-phone').value.trim(),
      customer_email:$('f-email').value.trim(),address:$('f-address').value.trim(),
      pincode:$('f-pincode').value.trim(),city:$('f-city').value.trim(),
      state:$('f-state').value.trim(),source:source,product:$('f-product').value.trim(),
      quantity:$('f-qty').value||1,price_inr:$('f-price').value,
      notes:$('f-notes').value.trim(),status:$('f-status').value,
      photo_paths:shots.map(function(p){return p.path;})};
    Object.keys(attrs).forEach(function(k){body[k]=attrs[k];});
    var d=await jpost('/orders/create',body);
    var msg='Order #'+d.id+' saved';
    if(d.customer&&d.customer.orders>1)msg+=' — '+d.customer.orders+' orders from this customer'+
      (d.customer.tags?' ('+d.customer.tags+')':'');
    $('warn').innerHTML='<div class="okbox">'+esc(msg)+'</div>';
    reset();
    loaded={};                     /* other tabs are stale now */
    if($('pane-orders').classList.contains('on'))loadOrders();
    toast('Order #'+d.id+' saved');
    if((d.warnings||[]).length)showWarnings(d.warnings);
  }catch(e){toast(e.message);}
  btn.textContent=label;canSave();
};
document.addEventListener('keydown',function(e){
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&!$('save').disabled)$('save').click();
});

function reset(){
  ['f-cust','f-phone','f-email','f-address','f-pincode','f-city','f-state',
   'f-product','f-price','f-notes','paste'].forEach(function(id){$(id).value='';});
  $('f-qty').value='1';$('f-status').value='new';
  $('caught').innerHTML='';
  attrs={};dropped={};drawAttrs();
  shots=[];drawShots();
  window.scrollTo({top:0,behavior:'smooth'});
  $('paste').focus();
}
function showWarnings(w){
  $('warn').innerHTML+='<div class="warnbox">'+w.map(esc).join('<br>')+'</div>';
}

/* ---------------- orders ---------------- */
function when(s){return s?String(s).replace('T',' ').slice(0,16):'';}
function money(n){return (n===null||n===undefined||n==='')?'':('₹'+Number(n).toLocaleString('en-IN'));}
function photoCell(o){
  var links=[];
  try{links=o.photo_links?JSON.parse(o.photo_links):(o.drive_link?[o.drive_link]:[]);}
  catch(e){links=o.drive_link?[o.drive_link]:[];}
  if(!links.length)return '';
  return '<a href="'+esc(links[0])+'" target="_blank" rel="noopener">View</a>'+
    (links.length>1?'<span class="o-sub"> +'+(links.length-1)+'</span>':'');
}
function specOf(o){
  return ['case_style','dial_colour','dial_style','case_colour','movement','watch_size']
    .map(function(k){return o[k];}).filter(Boolean).join(' · ');
}
function stClass(s){return String(s||'').replace(/[^a-z]/gi,'');}
async function loadOrders(){
  try{
    var d=await api('/orders/list');
    if(d.sheet_url){var a=$('sheet-link');a.href=d.sheet_url;a.style.display='';}
    var rows=d.orders||[];
    if(!rows.length){$('list').innerHTML='<div class="empty">No orders yet — the first one you save shows up here.</div>';return;}
    $('list').innerHTML='<table class="dt"><thead><tr>'+
      '<th>#</th><th>Logged</th><th>Source</th><th>Customer</th><th>Ship to</th><th>Product</th>'+
      '<th>Qty</th><th>Price</th><th>Status</th><th>Photos</th></tr></thead><tbody>'+
      rows.map(function(o){
        var st=String(o.status||'new'), spec=specOf(o);
        return '<tr>'+
          '<td data-l="Order" class="o-num">#'+o.id+'</td>'+
          '<td data-l="Logged" class="o-when">'+esc(when(o.received_at))+'</td>'+
          '<td data-l="Source">'+(o.source?'<span class="pill">'+esc(o.source)+'</span>':'')+
            (o.shopify_name?'<div class="o-sub">'+esc(o.shopify_name)+'</div>':'')+'</td>'+
          '<td data-l="Customer"><div class="o-strong">'+esc(o.customer_name||'')+'</div>'+
            (o.customer_phone?'<div class="o-sub">'+esc(o.customer_phone)+'</div>':'')+
            (o.customer_email?'<div class="o-sub">'+esc(o.customer_email)+'</div>':'')+'</td>'+
          '<td data-l="Ship to"><div class="o-sub">'+esc(o.address||'')+
            (o.city?'<br>'+esc(o.city):'')+(o.state?', '+esc(o.state):'')+
            (o.pincode?' '+esc(o.pincode):'')+'</div></td>'+
          '<td data-l="Product">'+esc(o.product||'')+
            (spec?'<div class="o-sub">'+esc(spec)+'</div>':'')+
            (o.notes?'<div class="o-sub">'+esc(o.notes)+'</div>':'')+'</td>'+
          '<td data-l="Qty" class="o-num">'+(o.quantity||1)+'</td>'+
          '<td data-l="Price" class="o-num">'+esc(money(o.price_inr))+'</td>'+
          '<td data-l="Status"><select class="pill-select '+stClass(st)+'" data-id="'+o.id+'">'+
            STATUSES_LIST.map(function(s){return '<option value="'+esc(s)+'"'+(s===st?' selected':'')+'>'+esc(s)+'</option>';}).join('')+
            '</select></td>'+
          '<td data-l="Photos">'+photoCell(o)+'</td>'+
        '</tr>';
      }).join('')+'</tbody></table>';
    $('list').querySelectorAll('.pill-select').forEach(function(sel){
      sel.onchange=async function(){
        var id=sel.dataset.id, was=sel.dataset.was||sel.value, next=sel.value;
        sel.disabled=true;
        try{
          await jpost('/orders/update',{id:+id,status:next});
          sel.className='pill-select '+stClass(next);
          sel.dataset.was=next;
          toast('Order #'+id+' → '+next);
        }catch(e){sel.value=was;toast(e.message);}
        sel.disabled=false;
      };
    });
  }catch(e){$('list').innerHTML='<div class="empty">'+esc(e.message)+'</div>';}
}

/* ---------------- customers ---------------- */
async function loadCustomers(){
  try{
    var d=await api('/customers/list');
    var rows=d.customers||[];
    if(!rows.length){$('clist').innerHTML='<div class="empty">No customers yet — they build up as you log orders.</div>';return;}
    $('clist').innerHTML='<table class="dt"><thead><tr>'+
      '<th>Customer</th><th>Where</th><th>Orders</th><th>Spent</th><th>Avg</th>'+
      '<th>First</th><th>Last</th><th>Tags</th></tr></thead><tbody>'+
      rows.map(function(c){
        return '<tr>'+
          '<td data-l="Customer"><div class="o-strong">'+esc(c.name||'')+'</div>'+
            (c.phone?'<div class="o-sub">'+esc(c.phone)+'</div>':'')+
            (c.email?'<div class="o-sub">'+esc(c.email)+'</div>':'')+'</td>'+
          '<td data-l="Where"><div class="o-sub">'+esc([c.city,c.state].filter(Boolean).join(', '))+
            (c.pincode?' '+esc(c.pincode):'')+'</div></td>'+
          '<td data-l="Orders" class="o-num">'+(c.orders_count||0)+'</td>'+
          '<td data-l="Spent" class="o-num o-strong">'+esc(money(c.total_spent))+'</td>'+
          '<td data-l="Avg" class="o-num">'+esc(money(c.avg_order_value))+'</td>'+
          '<td data-l="First order" class="o-when">'+esc(when(c.first_order_at))+'</td>'+
          '<td data-l="Last order" class="o-when">'+esc(when(c.last_order_at))+'</td>'+
          '<td data-l="Tags">'+(c.tags?c.tags.split(',').map(function(t){
            t=t.trim();return t?'<span class="pill '+(t==='VIP'?'vip':'')+'">'+esc(t)+'</span> ':'';
          }).join(''):'')+'</td>'+
        '</tr>';
      }).join('')+'</tbody></table>';
  }catch(e){$('clist').innerHTML='<div class="empty">'+esc(e.message)+'</div>';}
}

/* ---------------- what's selling ---------------- */
function barBlock(title,rows){
  if(!rows||!rows.length)return '';
  var max=Math.max.apply(null,rows.map(function(r){return r.units;}));
  return '<div class="bars"><h3>'+esc(title)+'</h3>'+rows.map(function(r){
    return '<div class="bar"><span class="nm">'+esc(r.name)+'</span>'+
      '<span class="tr"><span class="fl" style="width:'+Math.max(4,Math.round(r.units/max*100))+'%"></span></span>'+
      '<span class="vl">'+r.units+'</span></div>';
  }).join('')+'</div>';
}
var sellData=[], sellSort={key:'units',dir:-1};
var SELL_COLS=[
  {key:'name',label:'Product'},{key:'units',label:'Units'},
  {key:'orders',label:'Orders'},{key:'revenue',label:'Revenue'},
  {key:'last_sold',label:'Last sold'}
];
function sellSorted(){
  var k=sellSort.key,dir=sellSort.dir;
  return sellData.slice().sort(function(a,b){
    var av=a[k]||(k==='name'||k==='last_sold'?'':0), bv=b[k]||(k==='name'||k==='last_sold'?'':0);
    if(k==='name'||k==='last_sold')return dir*String(av).localeCompare(String(bv));
    return dir*(av-bv);
  });
}
function renderProductsTable(){
  var box=$('prod-table');
  if(!sellData.length){
    box.innerHTML='<div class="empty">No orders yet. This fills in from the storefront and the order form together.</div>';
    return;
  }
  var rows=sellSorted();
  box.innerHTML='<div class="tbl-wrap"><table class="dt"><thead><tr>'+
    SELL_COLS.map(function(c){
      var on=c.key===sellSort.key, arr=on?(sellSort.dir===1?'&#8593;':'&#8595;'):'&#8597;';
      return '<th class="sortable'+(on?' on':'')+'" data-k="'+c.key+'">'+esc(c.label)+' <span class="arr">'+arr+'</span></th>';
    }).join('')+'</tr></thead><tbody>'+
    rows.map(function(r){
      return '<tr>'+
        '<td data-l="Product"><span class="pname" data-name="'+esc(r.name)+'" title="Click to rename">'+esc(r.name)+'</span></td>'+
        '<td data-l="Units" class="o-num">'+(r.units||0)+'</td>'+
        '<td data-l="Orders" class="o-num">'+(r.orders||0)+'</td>'+
        '<td data-l="Revenue" class="o-num">'+esc(money(r.revenue||0))+'</td>'+
        '<td data-l="Last sold" class="o-when">'+esc(when(r.last_sold))+'</td>'+
      '</tr>';
    }).join('')+'</tbody></table></div>';
  box.querySelectorAll('th.sortable').forEach(function(th){
    th.onclick=function(){
      var k=th.dataset.k;
      if(sellSort.key===k)sellSort.dir=-sellSort.dir; else sellSort={key:k,dir:(k==='name'?1:-1)};
      renderProductsTable();
    };
  });
  box.querySelectorAll('.pname').forEach(function(el){
    el.onclick=function(){
      var old=el.dataset.name;
      el.innerHTML='<input type="text" value="'+esc(old)+'">';
      var inp=el.querySelector('input');inp.focus();inp.select();
      var done=false;
      function commit(){
        if(done)return;done=true;
        var val=inp.value.trim();
        if(!val||val===old){renderProductsTable();return;}
        jpost('/orders/items/relabel',{old:old,new:val}).then(function(r){
          toast('Renamed — '+r.updated+' line item'+(r.updated!==1?'s':'')+' updated');
          loadSelling();
        }).catch(function(e){toast(e.message);renderProductsTable();});
      }
      inp.addEventListener('blur',commit);
      inp.addEventListener('keydown',function(e){
        if(e.key==='Enter')commit();
        else if(e.key==='Escape'){done=true;renderProductsTable();}
      });
    };
  });
}
async function loadSelling(){
  try{
    var d=await api('/orders/meta');
    var t=d.totals||{}, ch=d.channels||{};
    var mix=(ch.website!=null)?' <small style="opacity:.6;font-weight:400">('+(ch.website||0)+' web · '+(ch.logged||0)+' logged)</small>':'';
    $('kpis').innerHTML=
      '<div class="kpi"><span>Orders</span><b>'+(t.orders||0)+'</b>'+mix+'</div>'+
      '<div class="kpi"><span>Revenue</span><b>'+esc(money(t.revenue||0))+'</b></div>'+
      '<div class="kpi"><span>Customers</span><b>'+(t.customers||0)+'</b></div>';
    sellData=d.top_products||[];
    var top6=sellData.slice(0,6).map(function(p){return {name:p.name,units:p.units};});
    var s=d.selling||{};
    var attrHtml=barBlock('Case style · logged orders',s.case_style)+
             barBlock('Dial colour · logged orders',s.dial_colour)+
             barBlock('Dial style · logged orders',s.dial_style)+
             barBlock('Movement · logged orders',s.movement);
    $('selling').innerHTML=(top6.length?barBlock('Top movers',top6):'')+
      '<div class="sellhead"><h3>Every product &middot; click a name to rename it, click a column to sort</h3></div>'+
      '<div id="prod-table"></div>'+attrHtml;
    renderProductsTable();
  }catch(e){$('selling').innerHTML='<div class="empty">'+esc(e.message)+'</div>';}
}

/* ---------------- boot ---------------- */
var STATUSES_LIST=Array.prototype.map.call($('f-status').options,function(o){return o.value;});
(async function(){
  drawShots();drawAttrs();
  try{
    var m=await api('/orders/meta');
    drawSources(m.sources||[]);
    attrLabels=(m.vocab&&m.vocab.labels)||{};
  }catch(e){drawSources([]);}
  try{
    var pd=await api('/orders/products');
    PRODUCTS=pd.products||[];
  }catch(e){}
})();
"""


def build():
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    status_opts = "".join(f'<option value="{s}">{s}</option>' for s in STATUSES)
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Order form — Labs OS</title>
<style>{HUB_STYLE}{OF_CSS}</style></head>
<body>
<div class="wrap">
  {hub_header("orders")}
  <main>
    <div class="page-head">
      <h1 class="page-title">Order form</h1>
      <p class="page-sub">Paste the customer's details, add a photo, save. It lands here, in the Orders sheet, and the photos go to Drive.</p>
    </div>

    <div class="tabs">
      <button class="tab on" data-p="new">New order</button>
      <button class="tab" data-p="orders">Orders</button>
      <button class="tab" data-p="customers">Customers</button>
      <button class="tab" data-p="selling">What's selling</button>
    </div>

    <section class="pane on" id="pane-new">
      <div class="of-card pastecard">
        <div class="paste-head">
          <svg viewBox="0 0 24 24"><rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/><path d="M9 13h6M9 17h4"/></svg>
          <b>Paste customer details</b>
          <span style="flex:1"></span>
          <button class="btn sm" id="pull-clip"><svg viewBox="0 0 24 24"><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/><path d="M14 4h7v7"/><path d="M10 14L20 4"/></svg>Paste from clipboard</button>
          <button class="btn sm" id="clear-paste">Clear</button>
        </div>
        <textarea id="paste" placeholder="Name: Anurag Mavi&#10;Email: name@example.com&#10;Phone no: 98765 43210&#10;Address: Village Morna, Sector 168&#10;            Gautam Buddha Nagar&#10;            Uttar Pradesh&#10;Pincode: 201301"></textarea>
        <div class="caught" id="caught"></div>
      </div>

      <div class="of-card">
        <div id="warn"></div>
        <div class="of-legend">Reference photos</div>
        <div class="of-field">
          <div class="shots" id="shots"></div>
          <div class="dz" id="dz">
            <div class="dz-t">Add photos</div>
            <div class="dz-s">Tap to use the camera roll &middot; drag them here &middot; or press <span class="kbd">&#8984;V</span></div>
            <div class="dz-btns"><button type="button" class="btn sm" id="paste-img"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/></svg>Paste image from clipboard</button></div>
          </div>
          <input type="file" id="ph-file" accept="image/jpeg,image/png,image/webp" multiple>
        </div>

        <div class="of-sep"></div>
        <div class="of-legend">Customer</div>
        <div class="of-row">
          <div class="of-field"><label>Name <span class="req">*</span></label>
            <input id="f-cust" class="pin" autocomplete="off"></div>
          <div class="of-field"><label>Phone</label>
            <input id="f-phone" class="pin" inputmode="tel" autocomplete="off"></div>
        </div>
        <div class="of-field"><label>Email</label>
          <input id="f-email" class="pin" inputmode="email" autocomplete="off"></div>
        <div class="of-field"><label>Address</label>
          <textarea id="f-address" class="pin"></textarea></div>
        <div class="of-row3">
          <div class="of-field"><label>City</label>
            <input id="f-city" class="pin" autocomplete="off"></div>
          <div class="of-field"><label>State</label>
            <input id="f-state" class="pin" autocomplete="off"></div>
          <div class="of-field"><label>Pincode</label>
            <input id="f-pincode" class="pin" inputmode="numeric" autocomplete="off"></div>
        </div>

        <div class="of-sep"></div>
        <div class="of-legend">Order</div>
        <div class="of-field"><label>Where did it come from?</label>
          <div class="srcs" id="srcs"></div></div>
        <div class="of-field combo-wrap"><label>Product <span class="req">*</span></label>
          <input id="f-product" class="pin" placeholder="e.g. datejust arabic light blue dial 36mm NH35" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false">
          <div class="combo-list" id="prod-list" hidden></div>
          <div class="attrs" id="attrs"></div>
          <div class="attr-hint" id="attr-hint">Case style, dial colour, movement and size are picked out of what you type — they drive the What's selling tab. Start typing to see what's sold before.</div></div>
        <div class="of-row3">
          <div class="of-field"><label>Qty</label>
            <input id="f-qty" class="pin" type="number" min="1" step="1" value="1"></div>
          <div class="of-field"><label>Price</label>
            <div class="price-wrap"><span>&#8377;</span><input id="f-price" class="pin" type="number" step="0.01" min="0" placeholder="0.00"></div></div>
          <div class="of-field"><label>Status</label>
            <select id="f-status" class="pin">{status_opts}</select></div>
        </div>
        <div class="of-field"><label>Notes</label>
          <textarea id="f-notes" class="pin" placeholder="Sizing, deadline, anything to remember…"></textarea></div>

        <button class="btn primary" id="save" disabled>Save order</button>
        <div class="savehint">or press <span class="kbd">&#8984;</span> + <span class="kbd">Enter</span></div>
      </div>
    </section>

    <section class="pane" id="pane-orders">
      <div class="of-card">
        <div class="sec-head"><h2>Recent orders</h2><span class="sp"></span>
          <a id="sheet-link" href="#" target="_blank" rel="noopener" style="display:none">Open the sheet &#8599;</a></div>
        <div class="tbl-wrap"><div id="list"><div class="empty">Loading…</div></div></div>
      </div>
    </section>

    <section class="pane" id="pane-customers">
      <div class="of-card">
        <div class="sec-head"><h2>Customers</h2><span class="sp"></span>
          <span class="o-sub">Built from your orders &middot; repeat and VIP tag themselves</span></div>
        <div class="tbl-wrap"><div id="clist"><div class="empty">Loading…</div></div></div>
      </div>
    </section>

    <section class="pane" id="pane-selling">
      <div class="kpis" id="kpis"></div>
      <div class="of-card"><div id="selling"><div class="empty">Loading…</div></div></div>
    </section>

    {hub_footer()}
  </main>
</div>

<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>{OF_JS}
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
