"""Timelabs Hub — the shared shell (Face dashboard skeleton + OS app switcher).

This module owns the *chrome* of Hub: the brand bar, the mobile-first app
navigation (Face / Drop / Ledger / Chat / Key), the Face sub-tab bar, and the
page-level CSS/JS. generate.py builds the data fragments and calls page(...).

Design intent: mobile-first. On a phone the primary navigation is a fixed
bottom bar (thumb-reachable); on desktop the same nav sits inline at the top.
Kept deliberately dependency-free — it emits one self-contained HTML string.
"""
import datetime
import html

# Admin emails mirror /etc/oauth2-proxy/allowlist.txt admins. Used only to
# reveal the Key (invite) affordance client-side — real gating is server-side.
ADMIN_EMAILS = ("timelabs.inc@gmail.com", "schezan.m@gmail.com")


def _refund_pct(s):
    """Refunds as a share of gross — a one-glance leak indicator."""
    gross = s.get("gross_sales") or s.get("total_sales") or 0
    refunds = s.get("refund_total") or 0
    if gross <= 0:
        return ""
    return f"{100 * refunds / gross:.0f}% of gross"


def source_chip(src, name):
    """Honest per-source freshness chip. Reads each source's own `as_of` so a
    live-today Shopify pull doesn't make carried-forward GA4/Meta look fresh."""
    if not (isinstance(src, dict) and src.get("connected")):
        return (f'<div class="src"><span class="src-name">{html.escape(name)}</span>'
                f'<span class="src-dot off"></span><span class="src-when">not connected</span></div>')
    date = src.get("as_of") or src.get("via_bridge") or ""
    cls, when = "fresh", "live"
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(date)).days
        if age <= 0:
            when = "live · today"
        elif age == 1:
            when = "live · yesterday"
        else:
            when = f"live · {date}"
        if age > 3:
            cls, when = "stale", f"stale · {date}"
    except (ValueError, TypeError):
        when = "live"
    return (f'<div class="src"><span class="src-name">{html.escape(name)}</span>'
            f'<span class="src-dot {cls}"></span><span class="src-when {cls}">{html.escape(when)}</span></div>')


HUB_STYLE = r"""
  :root{
    --ground:#0f1512; --surface:#161f1a; --surface-2:#1d2822; --ink:#eef2ee; --muted:#9fb0a6;
    --line:#293831; --accent:#c9a35e; --accent-soft:rgba(201,163,94,.14);
    --good:#5cbf88; --good-soft:rgba(92,191,136,.14);
    --warn:#d9b45b; --warn-soft:rgba(217,180,91,.14);
    --crit:#e06a45; --crit-soft:rgba(224,106,69,.16);
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:"SF Mono","Cascadia Code","Consolas",ui-monospace,monospace;
    --appnav-h:64px; --radius:12px;
  }
  @media (prefers-color-scheme: light){
    :root{ --ground:#ecefe8; --surface:#ffffff; --surface-2:#f4f6f1; --ink:#16211b; --muted:#586b60;
      --line:#dde3dc; --accent:#8a6a2c; --accent-soft:rgba(138,106,44,.12);
      --good:#2f8f5b; --good-soft:rgba(47,143,91,.12);
      --warn:#9a7b2b; --warn-soft:rgba(154,123,43,.12);
      --crit:#c1502e; --crit-soft:rgba(193,80,46,.12); }
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.5;
    -webkit-font-smoothing:antialiased;}
  .num{font-variant-numeric:tabular-nums lining-nums}
  a{color:var(--accent);}
  .hub{max-width:960px;margin:0 auto;padding:0 1rem calc(var(--appnav-h) + env(safe-area-inset-bottom,0) + 1.4rem);}

  /* ---- brand bar (scrolls away; functional nav stays) ---- */
  .hub-top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;
    padding:.9rem .1rem .8rem;}
  .brand{display:flex;align-items:baseline;gap:.45rem;text-decoration:none;}
  .brand-mark{font-family:var(--mono);font-weight:700;letter-spacing:.2em;font-size:.82rem;color:var(--ink);}
  .brand-sub{font-family:var(--serif);font-style:italic;font-size:1.05rem;color:var(--accent);}
  .hub-actions{display:flex;align-items:center;gap:.5rem;}
  .who{font-family:var(--mono);font-size:.66rem;color:var(--muted);max-width:8.5rem;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .iconbtn{font-family:var(--mono);font-size:.95rem;line-height:1;cursor:pointer;border:1px solid var(--line);
    background:var(--surface);color:var(--ink);border-radius:9px;min-width:40px;min-height:40px;
    display:inline-flex;align-items:center;justify-content:center;transition:border-color .15s;}
  .iconbtn:hover{border-color:var(--accent);}
  .iconbtn:disabled{opacity:.5;cursor:wait;}

  /* ---- OS app switcher: fixed bottom on mobile, inline top on desktop ---- */
  .appnav{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;background:var(--surface);
    border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom,0);
    box-shadow:0 -6px 24px rgba(0,0,0,.22);}
  .appitem{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.15rem;
    padding:.5rem .2rem;min-height:var(--appnav-h);text-decoration:none;color:var(--muted);
    font-family:var(--mono);font-size:.6rem;letter-spacing:.03em;text-transform:uppercase;
    background:none;border:none;cursor:pointer;}
  .appitem .ic{font-size:1.25rem;line-height:1;}
  .appitem.active{color:var(--accent);}
  .appitem.active .ic{text-shadow:0 0 12px var(--accent-soft);}
  .appitem:active{background:var(--surface-2);}
  .appitem .soon{position:absolute;transform:translate(1.1rem,-.7rem);font-size:.5rem;color:var(--muted);
    text-transform:none;letter-spacing:0;}
  .admin-only{display:none;}
  body.is-admin .admin-only{display:flex;}

  /* ---- Face header + range ---- */
  .face-head{padding:.4rem .1rem 1rem;border-bottom:1px solid var(--line);margin-bottom:.2rem;}
  .face-title{font-family:var(--serif);font-weight:600;font-size:clamp(1.6rem,6vw,2.1rem);margin:0;}
  .face-sub{color:var(--muted);font-size:.9rem;margin:.2rem 0 .9rem;}
  .face-controls{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;}
  .rangebar{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;}
  .rangebar button{font-family:var(--mono);font-size:.74rem;border:none;background:var(--surface);
    color:var(--muted);padding:.55rem .85rem;cursor:pointer;min-height:40px;}
  .rangebar button.active{background:var(--accent-soft);color:var(--ink);font-weight:700;}
  .rangebar button:hover{color:var(--ink);}
  .refreshed{font-family:var(--mono);font-size:.7rem;color:var(--muted);}

  /* ---- Face sub-tab bar: horizontal scroll on mobile, sticky ---- */
  .face-nav{position:sticky;top:0;z-index:30;background:var(--ground);padding:.7rem 0 .55rem;
    margin:0 -1rem 1.1rem;border-bottom:1px solid var(--line);}
  .face-tabs{display:flex;gap:.4rem;overflow-x:auto;-webkit-overflow-scrolling:touch;
    scrollbar-width:none;padding:0 1rem;}
  .face-tabs::-webkit-scrollbar{display:none;}
  .tabbtn{position:relative;flex:0 0 auto;font-family:var(--mono);font-size:.75rem;border:1px solid var(--line);
    background:var(--surface);color:var(--muted);border-radius:999px;padding:.5rem .95rem;cursor:pointer;
    white-space:nowrap;min-height:38px;transition:color .12s,border-color .12s;}
  .tabbtn:hover{color:var(--ink);}
  .tabbtn.active{background:var(--accent-soft);border-color:var(--accent);color:var(--ink);font-weight:700;}
  .tabbadge{margin-left:.35rem;font-size:.62rem;color:var(--accent);}
  .searchrow{padding:0 1rem;margin-top:.55rem;}
  #q{font-family:var(--sans);font-size:.9rem;border:1px solid var(--line);border-radius:10px;
    background:var(--surface);color:var(--ink);padding:.6rem .8rem;width:100%;min-height:42px;}
  #q:focus{outline:2px solid var(--accent);border-color:var(--accent);}

  .tabpanel{display:none;}
  .tabpanel.active{display:block;animation:fadein .18s ease;}
  @keyframes fadein{from{opacity:.4;transform:translateY(3px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion: reduce){.tabpanel.active{animation:none;}}
  .srch-hidden{display:none!important;}

  h2{font-family:var(--serif);font-size:1.05rem;font-weight:600;margin:0 0 .8rem;}
  section{margin-bottom:1.6rem;}
  .panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:1.1rem 1.15rem;}

  /* ---- source status ---- */
  .sources{display:flex;gap:.55rem;flex-wrap:wrap;}
  .src{display:flex;align-items:center;gap:.4rem;background:var(--surface);border:1px solid var(--line);
    border-radius:999px;padding:.4rem .75rem;font-size:.76rem;}
  .src-name{font-weight:600;}
  .src-dot{width:.5rem;height:.5rem;border-radius:50%;background:var(--muted);}
  .src-dot.fresh{background:var(--good);box-shadow:0 0 8px var(--good-soft);}
  .src-dot.stale{background:var(--crit);}
  .src-dot.off{background:var(--muted);}
  .src-when{font-family:var(--mono);font-size:.68rem;color:var(--muted);}
  .src-when.fresh{color:var(--good);}
  .src-when.stale{color:var(--crit);}
  .healthrow{display:flex;gap:.45rem;flex-wrap:wrap;margin:.75rem 0 0;}
  .chiplet{font-family:var(--mono);font-size:.66rem;padding:.28rem .6rem;border-radius:999px;
    border:1px solid var(--line);color:var(--muted);}
  .chiplet.ok{border-color:var(--good);color:var(--good);}
  .chiplet.bad{border-color:var(--crit);color:var(--crit);font-weight:700;}

  /* ---- KPIs ---- */
  .kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:.7rem;}
  .kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:.9rem 1rem;}
  .kpi .label{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;}
  .kpi .val{font-family:var(--serif);font-size:1.55rem;margin:.35rem 0 .1rem;}
  .kpi .ctx{font-size:.74rem;color:var(--muted);}

  /* ---- verdict ---- */
  .verdict{margin:0 0 1.4rem;display:flex;gap:.9rem;background:var(--surface);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:var(--radius);padding:1rem 1.15rem;}
  .verdict b{font-family:var(--serif);font-weight:600;display:block;margin-bottom:.3rem;}
  .verdict p{margin:0;color:var(--muted);font-size:.88rem;line-height:1.55;}
  .verdict .stale{font-family:var(--mono);font-size:.68rem;color:var(--muted);display:block;margin-top:.5rem;}

  /* ---- tables / funnel / channels ---- */
  table{width:100%;border-collapse:collapse;font-size:.84rem;}
  th,td{text-align:left;padding:.55rem .6rem;border-bottom:1px solid var(--line);}
  th{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);}
  td.n,th.n{text-align:right;}
  .tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .empty{color:var(--muted);font-size:.9rem;}
  .f-note{color:var(--muted);font-size:.75rem;margin:.8rem 0 0;}
  .f-note a{color:var(--accent);}
  .srcrow{display:grid;grid-template-columns:minmax(90px,130px) 1fr 58px;align-items:center;gap:.6rem;margin:.5rem 0;}
  .s-name{font-size:.82rem;}
  .s-track{background:var(--surface-2);border-radius:6px;height:14px;overflow:hidden;border:1px solid var(--line);}
  .s-fill{height:100%;background:var(--accent);}
  .s-val{font-size:.8rem;color:var(--muted);text-align:right;}
  .fstep{display:grid;grid-template-columns:minmax(90px,130px) 1fr 60px;align-items:center;gap:.6rem;margin:.55rem 0;}
  .fname{font-size:.83rem;}
  .ftrack{background:var(--surface-2);border-radius:7px;height:24px;overflow:hidden;border:1px solid var(--line);}
  .ffill{height:100%;border-radius:6px;background:linear-gradient(90deg,#3a6b4b,var(--accent));
    display:flex;align-items:center;justify-content:flex-end;min-width:30px;}
  .ffill span{font-size:.72rem;color:#0f1512;font-weight:700;padding-right:.5rem;}
  .fpct{font-size:.76rem;color:var(--muted);text-align:right;}
  .oo-row{display:flex;align-items:center;gap:.8rem;padding:.55rem 0;border-bottom:1px solid var(--line);
    font-size:.88rem;flex-wrap:wrap;}
  .oo-row:last-of-type{border-bottom:none;}
  .oo-row b{flex:1;min-width:8rem;font-weight:600;}

  /* ---- pills ---- */
  .pill{font-size:.64rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;padding:.22rem .5rem;
    border-radius:999px;border:1px solid var(--line);color:var(--muted);}
  .pill.good{color:var(--good);background:var(--good-soft);border-color:var(--good);}
  .pill.warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn);}
  .pill.crit{color:var(--crit);background:var(--crit-soft);border-color:var(--crit);}

  /* ---- plan ---- */
  .a-project{font-family:var(--mono);font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;
    color:var(--accent);margin:1rem 0 .2rem;}
  .a-item{padding:.65rem 0;border-bottom:1px solid var(--line);}
  .a-item:last-of-type{border-bottom:none;}
  .a-head{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;font-size:.9rem;}
  .a-head b{flex:1;min-width:10rem;}
  .a-why{margin:.3rem 0 0;font-size:.8rem;color:var(--muted);line-height:1.5;}
  .a-blocked{margin:.3rem 0 0;font-size:.78rem;color:var(--crit);line-height:1.5;}
  .donebtn{font-family:var(--mono);font-size:.62rem;color:var(--muted);background:transparent;
    border:1px solid var(--line);border-radius:6px;padding:.35rem .6rem;cursor:pointer;min-height:34px;}
  .donebtn:hover{border-color:var(--good);color:var(--good);}

  /* ---- research / markdown ---- */
  .research-bar{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:.5rem;}
  #researchBtn{font-family:var(--mono);font-size:.72rem;border:1px solid var(--accent);color:var(--accent);
    background:transparent;border-radius:7px;padding:.5rem .85rem;cursor:pointer;min-height:38px;}
  #researchBtn:hover{background:var(--accent-soft);}
  #researchBtn:disabled{opacity:.55;cursor:wait;}
  .research-body summary{cursor:pointer;font-family:var(--mono);font-size:.72rem;color:var(--muted);margin-bottom:.4rem;}
  .md h1{font-family:var(--serif);font-size:1.15rem;margin:.8rem 0 .3rem;}
  .md h2{font-family:var(--serif);font-size:1rem;margin:.8rem 0 .3rem;}
  .md p,.md li{font-size:.87rem;line-height:1.55;}
  .md ul,.md ol{padding-left:1.3rem;}
  .md table{border-collapse:collapse;font-size:.8rem;display:block;overflow-x:auto;margin:.5rem 0;}
  .md th,.md td{border:1px solid var(--line);padding:.3rem .55rem;text-align:left;}
  .md th{background:var(--surface-2);}
  .md code{font-family:var(--mono);font-size:.82em;background:var(--surface-2);padding:.06em .3em;border-radius:3px;}

  .finding{padding:.75rem 0;border-bottom:1px solid var(--line);}
  .finding:last-of-type{border-bottom:none;}
  .finding p{margin:.25rem 0 0;font-size:.88rem;line-height:1.55;}
  .f-meta{font-family:var(--mono);font-size:.68rem;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;}

  /* ---- CTA row + toast ---- */
  .cta-row{display:flex;gap:.5rem;flex-wrap:wrap;margin:.2rem 0 1.4rem;}
  .cta{font-family:var(--mono);font-size:.74rem;text-decoration:none;border:1px solid var(--line);
    background:var(--surface);color:var(--ink);border-radius:9px;padding:.55rem .85rem;min-height:40px;
    display:inline-flex;align-items:center;gap:.4rem;}
  .cta:hover{border-color:var(--accent);}
  .cta.primary{background:var(--accent);border-color:var(--accent);color:#141310;font-weight:600;}
  .toast{position:fixed;left:50%;bottom:calc(var(--appnav-h) + env(safe-area-inset-bottom,0) + .9rem);
    transform:translateX(-50%) translateY(1rem);background:var(--surface-2);color:var(--ink);
    border:1px solid var(--accent);border-radius:10px;padding:.7rem 1rem;font-size:.82rem;z-index:80;
    opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;box-shadow:0 8px 30px rgba(0,0,0,.3);
    max-width:calc(100% - 2rem);text-align:center;}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
  footer{margin-top:2rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);
    font-family:var(--mono);font-size:.72rem;display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem;}

  /* ---- desktop: nav goes inline-top, wider gutters ---- */
  @media (min-width:760px){
    :root{--appnav-h:0px;}
    .hub{padding-bottom:3rem;}
    .appnav{position:sticky;top:0;bottom:auto;border-top:none;border-bottom:1px solid var(--line);
      box-shadow:none;border-radius:0 0 var(--radius) var(--radius);}
    .appitem{flex:0 0 auto;flex-direction:row;gap:.45rem;min-height:auto;padding:.85rem 1.15rem;font-size:.72rem;}
    .appitem .ic{font-size:1rem;}
    .appitem .soon{position:static;transform:none;}
    .kpis{grid-template-columns:repeat(auto-fit,minmax(160px,1fr));}
    .face-nav{margin-left:0;margin-right:0;}
    .face-tabs,.searchrow{padding-left:0;padding-right:0;}
    .toast{bottom:1.5rem;}
  }
"""


HUB_SCRIPT = r"""
  var ADMINS = ['timelabs.inc@gmail.com','schezan.m@gmail.com'];
  var TABS = ['overview','orders','plan','competition','content','findings'];
  var SEARCHABLE = {
    overview: {sel: '.kpi, .srcrow, .fstep, #tab-overview tbody tr', hide: false},
    orders: {sel: '#tab-orders tbody tr', hide: true},
    plan: {sel: '#tab-plan .a-item', hide: true},
    competition: {sel: '#tab-competition .md h2, #tab-competition .md p, #tab-competition .md li, #tab-competition .md td', hide: false},
    content: {sel: '#tab-content .finding', hide: true},
    findings: {sel: '#tab-findings .finding', hide: true}
  };
  function showTab(name){
    if(TABS.indexOf(name) === -1) name = 'overview';
    document.querySelectorAll('.tabpanel').forEach(function(p){ p.classList.toggle('active', p.id === 'tab-'+name); });
    document.querySelectorAll('.tabbtn').forEach(function(b){ b.classList.toggle('active', b.dataset.tab === name); });
    if(history.replaceState) history.replaceState(null, '', '#'+name);
    var active = document.querySelector('.tabbtn[data-tab="'+name+'"]');
    if(active && active.scrollIntoView) active.scrollIntoView({inline:'center', block:'nearest'});
    applySearch();
  }
  function applySearch(){
    var q = document.getElementById('q').value.trim().toLowerCase();
    TABS.forEach(function(tab){
      var cfg = SEARCHABLE[tab];
      var items = document.querySelectorAll(cfg.sel);
      var hits = 0;
      items.forEach(function(el){
        var match = !q || el.textContent.toLowerCase().indexOf(q) !== -1;
        if(match) hits++;
        if(cfg.hide) el.classList.toggle('srch-hidden', !match);
      });
      var badge = document.querySelector('.tabbtn[data-tab="'+tab+'"] .tabbadge');
      if(badge) badge.textContent = q ? (hits > 0 ? hits : '·') : '';
    });
  }
  var toastTimer;
  function toast(msg){
    var t = document.getElementById('toast');
    if(!t) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ t.classList.remove('show'); }, 2600);
  }
  async function markDone(id, btn){
    btn.disabled = true; btn.textContent = '…';
    try {
      var res = await fetch('/ops/agent/api/plan/toggle', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id})
      });
      if (res.ok) { setTimeout(function(){ location.reload(); }, 600); return; }
    } catch (e) {}
    btn.disabled = false; btn.textContent = '✓ done';
  }
  async function runResearch(btn){
    btn.disabled = true;
    var start = Date.now();
    btn.textContent = 'Researching…';
    var tick = setInterval(function(){ btn.textContent = 'Researching — ' + Math.round((Date.now()-start)/1000) + 's'; }, 1000);
    try {
      var res = await fetch('/ops/agent/api/research/run', {method:'POST'});
      clearInterval(tick);
      if (res.ok) { location.reload(); return; }
      var body = await res.json().catch(function(){ return {}; });
      btn.textContent = body.error || 'Failed — retry';
    } catch (e) { clearInterval(tick); btn.textContent = 'Failed — retry'; }
    setTimeout(function(){ btn.disabled = false; btn.textContent = 'Run fresh analysis'; }, 4000);
  }
  async function doRefresh(days){
    var btn = document.getElementById('refreshBtn');
    if(btn){ btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = '↻'; }
    var url = days ? ('/ops/refresh?days=' + days) : '/ops/refresh';
    try {
      var res = await fetch(url, {method:'POST'});
      if (res.ok) { location.reload(); return; }
      var body = await res.json().catch(function(){ return {}; });
      toast(body.error === 'refresh already running' ? 'Already refreshing…' : 'Refresh failed — retry');
    } catch (e) { toast('Refresh failed — retry'); }
    if(btn){ setTimeout(function(){ btn.disabled = false; btn.textContent = '↻'; }, 2500); }
  }
  async function initAccount(){
    try {
      var res = await fetch('/oauth2/userinfo', {headers: {'Accept':'application/json'}});
      if(!res.ok) return;
      var info = await res.json();
      var email = (info.email || info.preferredUsername || '').toLowerCase();
      if(email){
        var who = document.getElementById('who');
        if(who) who.textContent = email;
        if(ADMINS.indexOf(email) !== -1) document.body.classList.add('is-admin');
      }
    } catch (e) {}
  }
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.tabbtn').forEach(function(b){ b.addEventListener('click', function(){ showTab(b.dataset.tab); }); });
    var q = document.getElementById('q');
    if(q) q.addEventListener('input', applySearch);
    document.querySelectorAll('.appitem[data-soon]').forEach(function(a){
      a.addEventListener('click', function(e){ e.preventDefault(); toast(a.dataset.soon + ' is coming soon — building it next.'); });
    });
    document.addEventListener('keydown', function(e){
      if(e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA'){
        e.preventDefault(); if(q) q.focus();
      }
    });
    showTab((location.hash || '#overview').slice(1));
    initAccount();
  });
"""


def _appnav(active="face", drop_ready=False):
    """The OS app switcher. Items that aren't built yet use data-soon (a toast),
    so nothing is a dead link. Drop points to /drop/ once copyparty is live."""
    drop_attr = 'href="/drop/"' if drop_ready else 'href="#" data-soon="Drop"'
    items = [
        ('face', 'href="/ops/#overview"', '◷', 'Face', False),
        ('drop', drop_attr, '⬆', 'Drop', False),
        ('ledger', 'href="#" data-soon="Ledger"', '₹', 'Ledger', False),
        ('chat', 'href="/ops/agent/"', '✦', 'Chat', False),
        ('key', 'href="#" data-soon="Key"', '⚷', 'Key', True),
    ]
    out = []
    for key, attr, icon, label, admin in items:
        cls = "appitem" + (" active" if key == active else "") + (" admin-only" if admin else "")
        out.append(f'<a class="{cls}" {attr}><span class="ic">{icon}</span><span>{label}</span></a>')
    return '<nav class="appnav" aria-label="Apps">' + "".join(out) + '</nav>'


def page(*, generated_at, lookback_days, source_status_html, kpi_html, verdict_html,
         funnel_html, channel_html, campaign_html, overview_orders_html, orders_html,
         plan_html, research_html, content_html, findings_html, health_html,
         drop_ready=False):
    """Assemble the full Hub/Face HTML from data fragments built by generate.py."""
    def rb(days, label):
        active = " active" if lookback_days == days else ""
        return f'<button class="{active.strip() and "active" or ""}" onclick="doRefresh({days})">{label}</button>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1512">
<meta name="color-scheme" content="dark light">
<title>Timelabs Hub — Face</title>
<style>{HUB_STYLE}</style>
<script>{HUB_SCRIPT}</script>
</head>
<body>
<div class="hub">
  <header class="hub-top">
    <a class="brand" href="/ops/#overview">
      <span class="brand-mark">TIMELABS</span><span class="brand-sub">Hub</span>
    </a>
    <div class="hub-actions">
      <span id="who" class="who"></span>
      <button id="refreshBtn" class="iconbtn" title="Refresh now" aria-label="Refresh now" onclick="doRefresh()">↻</button>
    </div>
  </header>

  {_appnav(active="face", drop_ready=drop_ready)}

  <section id="app-face" class="app active">
    <div class="face-head">
      <h1 class="face-title">Face</h1>
      <p class="face-sub">Live view of the business — orders, funnel, plan, and findings at a glance.</p>
      <div class="face-controls">
        <div class="rangebar">{rb(7, "7d")}{rb(30, "30d")}{rb(90, "90d")}</div>
        <span class="refreshed num">refreshed {generated_at}</span>
      </div>
    </div>

    <div class="cta-row">
      <a class="cta primary" href="/ops/agent/">✦ Ask the team</a>
      <a class="cta" href="/ops/playbooks/community-playbook.pdf">Playbook PDF</a>
    </div>

    <nav class="face-nav" aria-label="Face sections">
      <div class="face-tabs" role="tablist">
        <button class="tabbtn" data-tab="overview">Overview<span class="tabbadge"></span></button>
        <button class="tabbtn" data-tab="orders">Orders<span class="tabbadge"></span></button>
        <button class="tabbtn" data-tab="plan">Plan<span class="tabbadge"></span></button>
        <button class="tabbtn" data-tab="competition">Competition<span class="tabbadge"></span></button>
        <button class="tabbtn" data-tab="content">Content<span class="tabbadge"></span></button>
        <button class="tabbtn" data-tab="findings">Findings<span class="tabbadge"></span></button>
      </div>
      <div class="searchrow"><input id="q" type="search" placeholder="Search everything…" aria-label="Search all sections"></div>
    </nav>

    <div class="tabpanel" id="tab-overview">
      {verdict_html}
      <section>
        <h2>Source status</h2>
        <div class="sources">{source_status_html}</div>
        <div class="healthrow">{health_html}</div>
      </section>
      <section>
        <h2>Key numbers</h2>
        <div class="kpis">{kpi_html}</div>
      </section>
      {overview_orders_html}
      {funnel_html}
      {channel_html}
      {campaign_html}
    </div>

    <div class="tabpanel" id="tab-orders">{orders_html}</div>
    <div class="tabpanel" id="tab-plan">{plan_html}</div>
    <div class="tabpanel" id="tab-competition">{research_html or '<section><div class="panel"><p class="empty">No research yet — run one from the agent.</p></div></section>'}</div>
    <div class="tabpanel" id="tab-content">{content_html or '<section><div class="panel"><p class="empty">Approved marketing copy will collect here.</p></div></section>'}</div>
    <div class="tabpanel" id="tab-findings">{findings_html}</div>

    <footer>
      <span>Timelabs Hub · Face</span>
      <span>daily refresh + on-demand</span>
    </footer>
  </section>
</div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
</body>
</html>
"""
