#!/usr/bin/env python3
"""Supplier build queue — renders /var/www/ops/supplier.html.

Deliberately not built on hub_header()/_appnav(): those render the internal
app nav (Home, Drop, Ledger, Chat, Tools), which is meaningless — and wrong
to expose — to an external party. This page gets its own minimal header and
never loads the "Ask Labs" chat widget, which talks to the same backend that
knows customer names, phones and revenue.

The page is pure presentation; every safety property lives server-side in
agent_chat_server.py's /supplier/orders and /supplier/status — the SELECT
that endpoint runs never names a PII or price column, and nginx's role gate
confines a supplier-role account to this one page. This file has nothing to
get wrong that would leak anything, which is the point.
"""
import os
import sys

sys.path.insert(0, "/root/ops-dashboard")
from hub_shell import HUB_STYLE, hub_footer

OUT = "/var/www/ops/supplier.html"

SUP_CSS = r"""
  .sup-top{display:flex;align-items:center;justify-content:space-between;padding:16px 0 22px;}
  .sup-brand{display:flex;align-items:center;gap:10px;}
  .sup-dot{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#d8a94c);
    color:#fff;font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center;}
  .sup-brand b{font-size:16px;color:var(--ink);letter-spacing:-.01em;}
  .sup-who{font-size:12.5px;color:var(--muted);}

  /* self-contained — this page intentionally doesn't share order_form.py's
     CSS, so its own copy of the generic table/card styles lives here */
  .of-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:20px;box-shadow:var(--shadow);margin-bottom:16px;}
  @media(max-width:600px){.of-card{padding:15px;}}
  .tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  table.dt{width:100%;border-collapse:collapse;font-size:13.5px;min-width:700px;}
  table.dt th{text-align:left;font-size:11px;font-weight:650;color:var(--muted);
    text-transform:uppercase;letter-spacing:.04em;padding:0 10px 9px;white-space:nowrap;}
  table.dt td{padding:11px 10px;border-top:1px solid var(--border);vertical-align:top;}
  .o-strong{font-weight:650;color:var(--ink);}
  .o-sub{font-size:12px;color:var(--muted);line-height:1.45;}
  .o-when{color:var(--muted);font-size:12.5px;white-space:nowrap;}
  .o-num{white-space:nowrap;}
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

  table.dt th.spec{min-width:220px;}
  .spec-chip{display:inline-block;font-size:11.5px;color:var(--muted);background:var(--card-2);
    border-radius:6px;padding:2px 7px;margin:1px 3px 1px 0;}
  select.stsel{font:inherit;font-size:13px;font-weight:650;border:1px solid var(--border);
    border-radius:var(--r-s);background:var(--card);color:var(--ink);padding:7px 26px 7px 10px;
    cursor:pointer;-webkit-appearance:none;appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 8px center;background-size:11px;}
  select.stsel[disabled]{opacity:.5;cursor:wait;}
  select.stsel.delivered{border-color:var(--good);color:var(--good);}
  select.stsel.cancelled{opacity:.55;}
"""

SUP_JS = r"""
'use strict';
var API='/ops/agent/api';
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
var toastT;
function toast(m){var t=$('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastT);toastT=setTimeout(function(){t.classList.remove('show');},3400);}
async function api(path,opts){
  var r=await fetch(API+path,opts);
  if(r.status===403){$('list').innerHTML='<div class="empty">This account can\'t open the build queue.</div>';throw new Error('auth');}
  var d=await r.json().catch(function(){return {};});
  if(!r.ok)throw new Error(d.error||'failed');
  return d;
}
function when(s){return s?String(s).replace('T',' ').slice(0,16):'';}
function specOf(o){
  return ['case_style','dial_colour','dial_style','case_colour','movement','watch_size']
    .filter(function(k){return o[k];}).map(function(k){return '<span class="spec-chip">'+esc(o[k])+'</span>';}).join('');
}
var STATUSES=[];
async function load(){
  try{
    var d=await api('/supplier/orders');
    STATUSES=d.statuses||[];
    var rows=d.orders||[];
    if(!rows.length){
      $('list').innerHTML='<div class="empty">Nothing in the queue right now — new builds show up here as soon as they\'re shared.</div>';
      return;
    }
    $('list').innerHTML='<div class="tbl-wrap"><table class="dt"><thead><tr>'+
      '<th>#</th><th>Received</th><th>Product</th><th class="spec">Spec</th><th>Qty</th><th>Status</th>'+
      '</tr></thead><tbody>'+rows.map(function(o){
        return '<tr>'+
          '<td data-l="Order" class="o-num">#'+o.id+'</td>'+
          '<td data-l="Received" class="o-when">'+esc(when(o.received_at))+'</td>'+
          '<td data-l="Product" class="o-strong">'+esc(o.product||'')+'</td>'+
          '<td data-l="Spec">'+(specOf(o)||'<span class="o-sub">—</span>')+'</td>'+
          '<td data-l="Qty" class="o-num">'+(o.quantity||1)+'</td>'+
          '<td data-l="Status"><select class="stsel '+esc((o.status||'').replace(/[^a-z]/gi,''))+'" data-id="'+o.id+'">'+
            STATUSES.map(function(s){return '<option value="'+esc(s)+'"'+(s===o.status?' selected':'')+'>'+esc(s)+'</option>';}).join('')+
          '</select></td>'+
        '</tr>';
      }).join('')+'</tbody></table></div>';
    $('list').querySelectorAll('.stsel').forEach(function(sel){
      sel.onchange=async function(){
        var id=sel.dataset.id, was=sel.dataset.was||sel.value, next=sel.value;
        sel.disabled=true;
        try{
          await api('/supplier/status',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({id:+id,status:next})});
          sel.className='stsel '+next.replace(/[^a-z]/gi,'');
          sel.dataset.was=next;
          toast('Order #'+id+' → '+next);
        }catch(e){sel.value=was;toast(e.message);}
        sel.disabled=false;
      };
    });
  }catch(e){ if(e.message!=='auth') $('list').innerHTML='<div class="empty">'+esc(e.message)+'</div>'; }
}
load();
fetch(API+'/whoami').then(function(r){return r.ok?r.json():null;}).then(function(i){
  if(i&&i.email){var w=$('who');if(w)w.textContent=i.email;}
}).catch(function(){});
"""


def build():
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Build queue — Timelabs Co</title>
<style>{HUB_STYLE}{SUP_CSS}</style></head>
<body>
<div class="wrap">
  <div class="sup-top">
    <div class="sup-brand"><span class="sup-dot">T</span><b>Build queue</b></div>
    <span class="sup-who" id="who"></span>
  </div>
  <main>
    <div class="page-head">
      <p class="page-sub">Every order shared with you, oldest first. Update the status as you go — acknowledge it, mark it paid once you've received payment, then in transit, assembled and shipped.</p>
    </div>
    <div class="of-card"><div id="list"><div class="empty">Loading…</div></div></div>
    {hub_footer()}
  </main>
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
