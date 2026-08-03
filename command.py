#!/usr/bin/env python3
"""Labs Command — the primary Hermes workspace.

This is deliberately additive while the interface proves itself: the existing
/ops/agent/ chat remains available as a fallback. The page reuses the current
role-checked chat, upload, session and event endpoints; no second agent backend
or parallel source of business truth is introduced.
"""
import json
import os
from pathlib import Path
from hub_shell import HUB_STYLE, WHOAMI_JS, hub_header

OUT = "/var/www/ops/command.html"
BASE = Path("/root/ops-dashboard")

CMO_DOCUMENTS = [
    ("product", "Product Information", "Product, customer, pricing and platform context", BASE / "docs/cmo/product-information.md"),
    ("strategy", "Marketing Strategy", "ICP, positioning, channels and 30-day direction", BASE / "docs/cmo/marketing-strategy.md"),
    ("competition", "Competitive Intelligence", "Market map, direct rivals and channel gaps", BASE / "docs/competitive-intelligence-audit.md"),
    ("voice", "Brand Voice", "How TimeLabs should sound and prove claims", BASE / "docs/cmo/brand-voice.md"),
    ("system", "How the CMO works", "Context, specialists, coordination and approval logic", BASE / "docs/cmo/system.md"),
]


def _cmo_documents_json():
    docs = []
    for key, title, summary, path in CMO_DOCUMENTS:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = f"# {title}\n\nThis context document is currently unavailable."
        docs.append({"key": key, "title": title, "summary": summary, "body": body})
    # A literal </script> inside a document must not terminate the inert JSON tag.
    return json.dumps(docs, ensure_ascii=False).replace("<", "\\u003c")


def build():
    cmo_documents = _cmo_documents_json()
    doc = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Command — Labs OS</title>
<style>""" + HUB_STYLE + r"""
html,body{height:100%;overflow:hidden}
[hidden]{display:none!important}
.wrap{max-width:none;width:100%;height:100vh;height:100dvh;margin:0;padding:0;display:flex;flex-direction:column}
.topbar{padding-left:24px;padding-right:24px}
.appnav{flex:none;margin:0 24px;display:flex}
.cmd{position:relative;flex:1;min-height:0;display:grid;grid-template-columns:280px minmax(0,1fr) 320px;
  border-top:1px solid var(--border);background:var(--bg);overflow:hidden}
.rail{min-width:0;background:var(--card);display:flex;flex-direction:column;overflow:hidden}
.rail.left{border-right:1px solid var(--border)}.rail.right{border-left:1px solid var(--border)}
.rail-head{height:56px;flex:none;display:flex;align-items:center;justify-content:space-between;padding:0 15px;
  border-bottom:1px solid var(--border)}
.rail-title{font-size:11px;font-weight:750;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
.new-btn,.icon-btn{border:1px solid var(--border);background:var(--card);color:var(--ink);cursor:pointer;
  border-radius:9px;font:inherit}.new-btn{padding:7px 10px;font-size:12px;font-weight:700}
.new-btn:hover,.icon-btn:hover{border-color:var(--accent);background:var(--accent-bg)}
.session-list{padding:8px;overflow:auto;flex:1}.session{width:100%;display:block;text-align:left;border:1px solid transparent;
  background:transparent;color:var(--ink);border-radius:9px;padding:10px;cursor:pointer;margin-bottom:3px}
.session:hover{background:var(--card-2)}.session.on{background:var(--accent-bg);border-color:var(--accent)}
.session b{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session span{display:block;font-size:11px;color:var(--muted);margin-top:4px}
.rail-tabs{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:8px;border-bottom:1px solid var(--border)}
.rail-tab{border:0;border-radius:8px;background:transparent;color:var(--muted);font:inherit;font-size:11px;font-weight:700;
  padding:7px;cursor:pointer}.rail-tab.on{background:var(--accent-bg);color:var(--accent)}
.cmo-context{padding:11px;overflow:auto;flex:1}.company-card{padding:12px;border:1px solid var(--border);border-radius:11px;background:var(--card-2)}
.company-card b{display:block;font-size:14px}.company-card p{font-size:11px;line-height:1.5;color:var(--muted);margin:6px 0 0}
.context-title{margin:17px 2px 7px;font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.doc-list{display:grid;gap:5px}.doc-btn{display:grid;grid-template-columns:30px minmax(0,1fr) auto;align-items:center;gap:8px;
  width:100%;padding:8px;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--ink);font:inherit;text-align:left;cursor:pointer}
.doc-btn:hover{background:var(--card-2);border-color:var(--border)}.doc-icon{width:28px;height:28px;border-radius:8px;background:var(--accent-bg);
  color:var(--accent);display:grid;place-items:center;font-size:12px;font-weight:850}.doc-btn b{display:block;font-size:11.5px}
.doc-btn small{display:block;color:var(--muted);font-size:9.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}.doc-open{color:var(--muted)}
.competitors{display:flex;flex-wrap:wrap;gap:5px}.competitor{border:1px solid var(--border);background:var(--card);border-radius:999px;
  padding:5px 7px;font-size:9.5px;color:var(--muted)}
.rail-foot{padding:10px;border-top:1px solid var(--border)}
.fallback{display:block;text-align:center;color:var(--muted);font-size:11px;text-decoration:none;padding:7px}
.fallback:hover{color:var(--accent)}
.workspace{min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;background:var(--bg);margin:0}
.work-head{height:56px;flex:none;display:flex;align-items:center;gap:10px;padding:0 16px;border-bottom:1px solid var(--border);
  background:color-mix(in srgb,var(--bg) 88%,transparent)}
.work-title{min-width:0;flex:1}.work-title b{font-size:14px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.work-title span{font-size:11px;color:var(--muted)}
.status{display:inline-flex;align-items:center;gap:6px;color:var(--good);font-size:11px;font-weight:650}
.status i{width:7px;height:7px;border-radius:50%;background:currentColor}
.mobile-toggle,.context-toggle{display:none;width:38px;height:38px;place-items:center}
.thread{flex:1;min-height:0;overflow:auto;padding:24px clamp(14px,4vw,48px);scroll-behavior:smooth}
.empty{max-width:720px;margin:8vh auto 0}.empty-mark{width:48px;height:48px;border-radius:13px;
  background:linear-gradient(135deg,var(--accent),#d8a94c);display:grid;place-items:center;color:white;
  font-size:20px;font-weight:850;box-shadow:var(--shadow-lg)}
.empty h1{font-size:clamp(25px,3vw,38px);letter-spacing:-.035em;margin:18px 0 8px;color:var(--ink)}
.empty>p{color:var(--muted);font-size:14px;line-height:1.6;max-width:580px}
.cmo-desk{margin-top:22px;padding:16px;border:1px solid color-mix(in srgb,var(--accent) 55%,var(--border));
  border-radius:13px;background:linear-gradient(135deg,var(--accent-bg),var(--card))}
.cmo-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}
.cmo-head b{font-size:14px}.cmo-head span{display:block;color:var(--muted);font-size:12px;line-height:1.45;margin-top:3px}
.cmo-badge{flex:none;border-radius:999px;padding:4px 8px;background:var(--accent);color:white;font-size:10px;font-weight:800;
  letter-spacing:.06em;text-transform:uppercase}.cmo-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.cmo-action{border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--ink);padding:11px;text-align:left;
  font:inherit;cursor:pointer}.cmo-action b{display:block;font-size:12px}.cmo-action small{display:block;color:var(--muted);font-size:10.5px;line-height:1.35;margin-top:4px}
.cmo-action:hover{border-color:var(--accent);transform:translateY(-1px)}
.starts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:24px}
.start{padding:13px;text-align:left;border:1px solid var(--border);border-radius:11px;background:var(--card);
  color:var(--ink);font:inherit;font-size:13px;line-height:1.35;cursor:pointer}
.start small{display:block;color:var(--muted);margin-top:5px}.start:hover{border-color:var(--accent);transform:translateY(-1px)}
.msg{max-width:900px;margin:0 auto 18px}.msg-label{font-size:10px;font-weight:750;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);margin:0 0 6px 2px}
.msg.user .bubble{margin-left:auto;max-width:78%;background:var(--ink);color:var(--bg);border-radius:14px 14px 4px 14px;
  padding:11px 14px;white-space:pre-wrap;font-size:14px;line-height:1.5}
.msg.agent .bubble{background:var(--card);border:1px solid var(--border);border-radius:4px 14px 14px 14px;
  padding:16px 18px;color:var(--ink);font-size:14px;line-height:1.6;box-shadow:var(--shadow)}
.md h1,.md h2,.md h3{margin:12px 0 6px;line-height:1.25}.md h1{font-size:20px}.md h2{font-size:17px}.md h3{font-size:15px}
.md p{margin:7px 0}.md ul,.md ol{margin:7px 0;padding-left:20px}.md pre{overflow:auto;background:var(--bg);
  border:1px solid var(--border);padding:10px;border-radius:8px}.md code{font-size:.9em;background:var(--bg);padding:1px 4px;border-radius:4px}
.md table{display:block;overflow:auto;border-collapse:collapse;margin:10px 0}.md th,.md td{border:1px solid var(--border);padding:6px 9px;text-align:left}
.md a{color:var(--accent)}.msg-tools{display:flex;gap:5px;margin-top:10px;padding-top:9px;border-top:1px solid var(--border)}
.msg-tools button{min-height:32px;border:0;background:none;color:var(--muted);font:inherit;font-size:11px;cursor:pointer;padding:5px 8px}
.msg-tools button:hover{color:var(--accent)}
.thinking{max-width:900px;margin:0 auto 18px;color:var(--muted);font-size:12px;display:flex;gap:8px;align-items:center}
.dots{display:flex;gap:3px}.dots i{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:pulse 1s infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes pulse{50%{opacity:.25;transform:translateY(-2px)}}
.compose-wrap{flex:none;padding:10px clamp(12px,4vw,48px) calc(12px + env(safe-area-inset-bottom));
  border-top:1px solid transparent}
.compose-wrap.drag{background:var(--accent-bg);border-top-color:var(--accent)}
.attachments{max-width:900px;margin:0 auto 7px;display:flex;gap:6px;flex-wrap:wrap}
.attachment{display:flex;align-items:center;gap:6px;padding:5px 7px;border:1px solid var(--border);background:var(--card);
  border-radius:8px;font-size:11px}.attachment img{width:28px;height:28px;border-radius:5px;object-fit:cover}
.attachment button{border:0;background:none;color:var(--bad);cursor:pointer}
.compose{max-width:900px;margin:0 auto;background:var(--card);border:1px solid var(--border);border-radius:14px;
  box-shadow:var(--shadow-lg);padding:8px;display:grid;grid-template-columns:auto 1fr auto;align-items:end;gap:7px}
.compose:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg),var(--shadow-lg)}
.compose textarea{border:0;outline:0;resize:none;background:transparent;color:var(--ink);font:inherit;font-size:14px;
  line-height:1.45;padding:8px 4px;min-height:38px;max-height:150px}
.compose button{width:38px;height:38px;border-radius:10px}.attach-btn{border:0;background:transparent;color:var(--muted)}
.send-btn{border:0;background:var(--ink);color:var(--bg)}.send-btn:disabled{opacity:.4}
.compose-note{max-width:900px;margin:6px auto 0;text-align:center;font-size:10.5px;color:var(--muted)}
.context{overflow:auto;padding:13px}.context-block{margin-bottom:20px}.context h3{font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--muted);margin:0 0 8px}
.analytics{display:grid;grid-template-columns:1fr 1fr;gap:6px}.metric{border:1px solid var(--border);border-radius:9px;background:var(--card);padding:9px}
.metric b{display:block;font-size:16px;font-variant-numeric:tabular-nums}.metric span{display:block;color:var(--muted);font-size:9.5px;margin-top:2px}
.agent-feed{display:grid;gap:6px}.agent-card{width:100%;display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:9px;align-items:center;
  border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--ink);padding:9px;text-align:left;font:inherit;cursor:pointer}
.agent-card:hover{border-color:var(--accent);background:var(--accent-bg)}.agent-icon{width:29px;height:29px;border-radius:9px;display:grid;
  place-items:center;background:var(--accent-bg);color:var(--accent);font-size:11px;font-weight:900}.agent-card b{display:block;font-size:11.5px}
.agent-card small{display:block;font-size:9.5px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agent-state{font-size:9px;color:var(--good);font-weight:750}.doc-modal{position:absolute;inset:0;z-index:60;background:rgba(10,15,12,.38);
  display:grid;place-items:center;padding:20px}.doc-sheet{width:min(780px,96vw);max-height:min(820px,92%);display:flex;flex-direction:column;
  border:1px solid var(--border);border-radius:14px;background:var(--bg);box-shadow:var(--shadow-lg);overflow:hidden}.doc-head{height:55px;flex:none;
  display:flex;align-items:center;justify-content:space-between;padding:0 15px;border-bottom:1px solid var(--border)}.doc-head b{font-size:13px}
.doc-body{overflow:auto;padding:20px}.doc-body .md{max-width:700px;margin:0 auto}.doc-close{width:34px;height:34px}
.mode{border:1px solid var(--accent);background:var(--accent-bg);border-radius:10px;padding:10px}
.mode b{display:block;font-size:13px}.mode span{display:block;font-size:11px;color:var(--muted);margin-top:3px;line-height:1.4}
.cap-list{display:grid;gap:5px}.cap{display:flex;align-items:center;justify-content:space-between;padding:8px 9px;
  border:1px solid var(--border);border-radius:8px;background:var(--card);font-size:12px}.cap span{color:var(--muted);font-size:10px}
.event{padding:9px 1px;border-bottom:1px solid var(--border)}.event:last-child{border-bottom:0}
.event b{font-size:11.5px;display:block}.event p{font-size:11px;color:var(--muted);line-height:1.35;margin:3px 0}
.event time{font-size:9.5px;color:var(--muted)}
.notice{max-width:900px;margin:0 auto 12px;border:1px solid var(--border);border-radius:10px;
  background:var(--card);padding:10px 12px;font-size:12px;color:var(--muted)}
.notice.bad{border-color:var(--bad);background:var(--bad-bg);color:var(--bad)}
.skeleton{height:52px;margin:4px 2px;border-radius:8px;background:linear-gradient(90deg,var(--card-2),var(--border),var(--card-2));
  background-size:200% 100%;animation:shimmer 1.2s infinite}
@keyframes shimmer{to{background-position:-200% 0}}
.shade{display:none;position:absolute;inset:0;background:rgba(0,0,0,.35);z-index:29}
button:focus-visible,a:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(max-width:1180px){
  .cmd{grid-template-columns:250px minmax(0,1fr)}.context-toggle{display:grid}
  .rail.right{display:flex;position:absolute;top:0;right:0;bottom:0;width:min(88vw,340px);z-index:31;
    box-shadow:var(--shadow-lg);transform:translateX(101%);transition:transform .18s var(--ease)}
  .rail.right.open{transform:none}.shade.on{display:block}
}
@media(max-width:720px){
  html,body{overflow:hidden}.wrap{padding-bottom:calc(var(--nav-h) + env(safe-area-inset-bottom,0))}
  .topbar{display:none}.appnav{display:flex;margin:0}.cmd{display:block;position:relative}
  .workspace{height:100%}.mobile-toggle{display:inline-grid;place-items:center}
  .rail.left{position:absolute;inset:0 auto 0 0;width:min(84vw,300px);z-index:30;transform:translateX(-101%);
    transition:transform .18s}.rail.left.open{transform:none}
  .thread{padding:18px 12px}.empty{margin-top:3vh}.starts,.cmo-actions{grid-template-columns:1fr}
  .compose-wrap{padding-left:8px;padding-right:8px}.msg.user .bubble{max-width:90%}
  .compose button,.mobile-toggle,.context-toggle{width:44px;height:44px}
  .work-head{height:58px;padding:0 8px}.work-title span{display:none}.status{font-size:0}.status i{width:8px;height:8px}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}.dots i,.skeleton{animation:none}.rail{transition:none!important}}
</style></head><body>
<div class="wrap" data-no-assist>""" + hub_header("chat") + r"""
<div class="cmd">
  <aside class="rail left" id="leftRail" aria-label="Conversations">
    <div class="rail-head"><span class="rail-title">TimeLabs CMO</span><button type="button" class="new-btn" id="newBtn">+ New chat</button></div>
    <div class="rail-tabs" role="tablist" aria-label="Command sidebar">
      <button type="button" class="rail-tab" id="chatsTab" role="tab" aria-selected="false">Chats</button>
      <button type="button" class="rail-tab on" id="companyTab" role="tab" aria-selected="true">Company</button>
    </div>
    <div class="session-list" id="sessions" hidden><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>
    <div class="cmo-context" id="cmoContext">
      <div class="company-card"><b>TimeLabs Co</b><p>Hand-built custom Seiko-movement watches. Company context is loaded and shared across every CMO specialist workflow.</p></div>
      <div class="context-title">Documents</div>
      <div class="doc-list" id="documentList"></div>
      <div class="context-title">Competitors</div>
      <div class="competitors"><span class="competitor">IndiaModWatches</span><span class="competitor">AG WatchStudio</span><span class="competitor">SeikoMods India</span><span class="competitor">WatchModCustom</span><span class="competitor">Indian microbrands</span></div>
      <div class="context-title">Context policy</div>
      <div class="company-card"><p>Current metrics stay connected to their systems of record. Documents hold durable strategy, positioning and operating rules.</p></div>
    </div>
    <div class="rail-foot"><a class="fallback" href="/ops/agent/">Open classic Labs Chat</a></div>
  </aside>
  <div class="shade" id="shade" aria-hidden="true"></div>
  <section class="workspace">
    <div class="work-head">
      <button type="button" class="icon-btn mobile-toggle" id="menuBtn" aria-label="Show conversations" aria-controls="leftRail" aria-expanded="false">☰</button>
      <div class="work-title"><b id="threadTitle">New command</b><span>Hermes · business operator and CMO</span></div>
      <span class="status" aria-live="polite"><i></i><span id="agentStatus">Ready</span></span>
      <button type="button" class="icon-btn context-toggle" id="contextBtn" aria-label="Show business context" aria-controls="contextRail" aria-expanded="false">ⓘ</button>
    </div>
    <div class="thread" id="thread" role="log" aria-live="polite" aria-relevant="additions">
      <div class="empty" id="empty">
        <div class="empty-mark">L</div>
        <h1>What should we move forward?</h1>
        <p>Ask a question or give Hermes a job. It can inspect the business and work across the Labs OS tools available to your account.</p>
        <div class="cmo-desk">
          <div class="cmo-head"><div><b>CMO desk</b><span>Research, positioning, channels and measurable growth. External actions remain review-gated.</span></div><div class="cmo-badge">Active</div></div>
          <div class="cmo-actions">
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Read /root/ops-dashboard/docs/hermes-cmo-charter.md and /root/ops-dashboard/docs/competitive-intelligence-audit.md completely. Inspect current memory_facts categories state, business and preference; use labs sales and products for current commercial context; inspect the latest Instagram audit snapshot; and run fresh public research where facts may have changed. Produce a decision-ready weekly CMO brief: executive verdict, commercial and channel scorecard using only measured data, customer and market signals, meaningful competitor changes, website/offer/content gaps, the three highest-leverage priorities, tasks you can prepare now, owner decisions required, and KPI, guardrail and review date for each recommendation. Label every important claim as first-party measured, public verified, competitor self-claim, community anecdote or inference. Save durable decisions to memory_facts and concrete work to action_items. Do not publish, spend, message, contact competitors or change Shopify without explicit approval."><b>Run CMO brief</b><small>Full evidence-backed weekly review</small></button>
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Read the CMO charter and latest competitive intelligence audit, then run a fresh source-cited market watch across direct Indian mod sellers, adjacent Indian microbrands, official-brand substitutes, global custom-watch benchmarks, communities and discovery channels. Report only changes and opportunities affecting a TimeLabs decision. Compare positioning, price ladder, product proof, warranty, lead time, content formats, conversion path and channel use. Separate verified facts, self-claims, anecdotes and inference. End with at most five prioritized moves, each with expected effect, effort, dependency, KPI and stop/scale rule. No external writes or outreach."><b>Market watch</b><small>Competitors and category signals</small></button>
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Audit our acquisition and retention path across Instagram, WhatsApp, Shopify, email, search, Reddit, YouTube and creator or collector partnerships using available first-party data and public evidence. Map discovery to proof, consultation, order, build, delivery and review/referral. Identify broken or unmeasured handoffs, privacy/access constraints, channels competitors use better, and the smallest 30-day plan to improve qualified demand. For every action include owner, prerequisite, KPI, guardrail and review date. Prepare drafts and internal tasks only; do not publish, spend or message anyone without approval."><b>Channel plan</b><small>Build the 30-day growth system</small></button>
          </div>
        </div>
        <div class="starts">
          <button type="button" class="start" data-prompt="Give me today's operating brief: new orders, anything blocked, and the three actions that matter most."><b>Run today’s brief</b><small>Orders, blockers and priorities</small></button>
          <button type="button" class="start" data-prompt="Show me every order that needs attention and explain why."><b>Review orders</b><small>Find work that needs intervention</small></button>
          <button type="button" class="start" data-prompt="What changed across Labs OS since yesterday?"><b>What changed?</b><small>Read the business activity feed</small></button>
          <button type="button" class="start" data-prompt="Check the health of Hermes and Labs OS. Report problems only, with the safest next action."><b>Check the system</b><small>Services, jobs and integrations</small></button>
        </div>
      </div>
    </div>
    <div class="compose-wrap">
      <div class="attachments" id="attachments"></div>
      <div class="compose">
        <button type="button" class="attach-btn" id="attachBtn" aria-label="Attach photos" title="Attach photos">＋</button>
        <input id="fileInput" type="file" accept="image/jpeg,image/png,image/webp" aria-label="Attach photos" multiple hidden>
        <textarea id="input" rows="1" placeholder="Ask Hermes anything…" aria-label="Command"></textarea>
        <button type="button" class="send-btn" id="sendBtn" aria-label="Send">↑</button>
      </div>
      <div class="compose-note">Ask Hermes for a dry run before any business change. Existing tool safeguards still apply.</div>
    </div>
  </section>
  <aside class="rail right" id="contextRail" aria-label="Business context">
    <div class="rail-head"><span class="rail-title">CMO intelligence</span><button type="button" class="icon-btn context-toggle" id="contextClose" aria-label="Close business context">×</button></div>
    <div class="context">
      <div class="context-block"><h3>Measured Instagram snapshot</h3><div class="analytics">
        <div class="metric"><b>3,117</b><span>followers</span></div><div class="metric"><b>5</b><span>posts / 90 days</span></div>
        <div class="metric"><b>4,399</b><span>top Reel views</span></div><div class="metric"><b>101</b><span>top interactions</span></div>
      </div></div>
      <div class="context-block"><h3>Specialist agents</h3><div class="agent-feed">
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Instagram specialist under the Hermes CMO charter. Read all CMO context documents and the latest Instagram snapshot. Audit cadence, formats, creative themes, proof, captions, conversion handoff and measurement. Return the next five evidence-led content briefs with hook, shot list, proof, CTA, KPI and review rule. Prepare internal tasks only; do not publish."><span class="agent-icon">IG</span><span><b>Instagram Agent</b><small>Cadence, creative and conversion</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Content specialist under the Hermes CMO charter. Read Product Information, Marketing Strategy, Brand Voice and the current competitive audit. Build a four-week cross-channel content system from the three strongest evidence-backed themes. Include Reel, carousel, WhatsApp, email and search repurposing, production owner and acceptance criteria. Draft only; do not publish."><span class="agent-icon">CO</span><span><b>Content Agent</b><small>Campaigns and repurposing</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs SEO and GEO specialist under the Hermes CMO charter. Audit the live storefront, product information and competitor sources for technical, on-page, search-intent and AI-answer visibility gaps. Prioritize queries that can lead to qualified custom-watch consultations. Return fixes and content briefs with evidence, effort, expected effect and measurement. Do not change Shopify without approval."><span class="agent-icon">SE</span><span><b>SEO &amp; GEO Agent</b><small>Search and AI visibility</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Reddit and community specialist. Use the existing Reddit listener and public web research to identify genuine watch-community questions where TimeLabs expertise could help. Separate listening insights from reply opportunities, follow subreddit rules, avoid promotion and astroturfing, and prepare drafts for review only."><span class="agent-icon">RD</span><span><b>Reddit Agent</b><small>Listening and genuine replies</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs WhatsApp and Email lifecycle specialist. Map consent-safe messages from consultation through order, build updates, delivery, care, review and referral. Use the CMO context and current operational reality. Identify missing data and CRM requirements, then prepare the smallest lifecycle plan and drafts. Do not send or broadcast anything."><span class="agent-icon">WA</span><span><b>WhatsApp &amp; Email</b><small>Consultation and lifecycle</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs UGC and owner-proof specialist. Design a permissioned system for build photos, wrist shots, reviews and referral proof from delivery through reuse. Include consent, asset fields, request timing, incentive guardrails and how proof maps back to products. Prepare the workflow and drafts only."><span class="agent-icon">UG</span><span><b>UGC Agent</b><small>Owner proof and referrals</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs influencer and collector-partnership specialist. Research a small, relevant India and diaspora watch-creator set using public evidence. Score fit, audience relevance, authenticity, likely format and risk. Propose a disclosed pilot with selection rules, deliverables, source tracking and stop/scale criteria. Do not contact anyone."><span class="agent-icon">IN</span><span><b>Influencer Agent</b><small>Collector and creator partnerships</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs competitor-intelligence specialist. Read the current audit, then verify only facts likely to have changed. Update the direct, adjacent, substitute and benchmark market map; identify meaningful price, offer, proof, content or channel changes; and state the TimeLabs decision each signal affects. Public research only, no block evasion or contact."><span class="agent-icon">CI</span><span><b>Competitor Agent</b><small>Market changes and threats</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs storefront conversion specialist. Audit the live site against Product Information, Marketing Strategy, Brand Voice and current customer trust gaps. Review navigation, disclosure, proof, product content, mobile friction and consultation handoff. Return a prioritized change plan and dry-run copy; do not modify Shopify."><span class="agent-icon">CV</span><span><b>Storefront Agent</b><small>Proof and conversion</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs marketing analytics specialist. Inspect only available first-party sales, product, Instagram, channel and operational data. Define the source-to-order measurement contract, identify data-quality gaps, and produce a weekly scorecard with metric definitions and owners. Never treat missing data as zero."><span class="agent-icon">AN</span><span><b>Analytics Agent</b><small>Attribution and scorecards</small></span><span class="agent-state">Ready</span></button>
      </div></div>
      <div class="context-block"><h3>Active operator</h3><div class="mode"><b>Hermes · CMO + operator</b><span>Owns research, positioning, channel plans and measurement. External actions remain review-gated.</span></div></div>
      <div class="context-block"><h3>CMO cadence</h3><div class="cap-list">
        <div class="cap">Market watch <span>Weekly</span></div>
        <div class="cap">Growth review <span>Weekly</span></div>
        <div class="cap">Positioning &amp; offer <span>Monthly</span></div>
      </div></div>
      <div class="context-block"><h3>Control policy</h3><div class="cap-list">
        <div class="cap">Inspect &amp; analyse <span>Direct</span></div>
        <div class="cap">Prepare changes <span>Ask first</span></div>
        <div class="cap">Business actions <span>Tool policy</span></div>
      </div></div>
      <div class="context-block"><h3>Recent activity</h3><div id="events"><div class="event"><p>Loading activity…</p></div></div></div>
    </div>
  </aside>
  <div class="doc-modal" id="docModal" role="dialog" aria-modal="true" aria-labelledby="docTitle" hidden>
    <div class="doc-sheet"><div class="doc-head"><b id="docTitle">Context document</b><button type="button" class="icon-btn doc-close" id="docClose" aria-label="Close document">×</button></div><div class="doc-body"><div class="md" id="docBody"></div></div></div>
  </div>
</div>
</div>
<script type="application/json" id="cmoDocuments">""" + cmo_documents + r"""</script>
<script>
(function(){
const API='/ops/agent/api', threadEl=document.getElementById('thread'), empty=document.getElementById('empty');
const input=document.getElementById('input'), sendBtn=document.getElementById('sendBtn');
const statusEl=document.getElementById('agentStatus'), sessionsEl=document.getElementById('sessions');
const attachEl=document.getElementById('attachments'), composeWrap=document.querySelector('.compose-wrap');
const cmoContext=document.getElementById('cmoContext'), chatsTab=document.getElementById('chatsTab'), companyTab=document.getElementById('companyTab');
const docModal=document.getElementById('docModal'), docTitle=document.getElementById('docTitle'), docBody=document.getElementById('docBody');
let busy=false, attachments=[], uploading=0, lastMessageId=0, historyGen=0, sessionsGen=0, runToken=0;
function syncSendState(){sendBtn.disabled=busy||uploading>0}
function stored(k){try{return localStorage.getItem(k)}catch(e){return null}}
function remember(k,v){try{localStorage.setItem(k,v)}catch(e){}}
function setSessionInUrl(id, push){
  try{
    var u=new URL(location.href);
    u.searchParams.set('session', String(id));
    var next=u.toString();
    if(next===location.href){
      return;
    }
    if(push){
      history.pushState({},'',next);
    }else{
      history.replaceState({},'',next);
    }
  }catch(e){}
}
function getStoredSessionId(){
  let v=parseInt(stored('labs_command_session')||stored('tl_session')||'1',10);
  return Number.isFinite(v)?v:1;
}
function getSessionFromUrl(){
  let v=parseInt(new URLSearchParams(location.search).get('session')||'',10);
  return Number.isFinite(v)?v:getStoredSessionId();
}
function syncSession(id, push){
  sid=Number.isFinite(id)?id:1;
  rememberSession();
  setSessionInUrl(sid,push);
}
let selectedSessionButton=null;
function focusComposer(){if(!matchMedia('(pointer:coarse)').matches&&document.visibilityState==='visible')input.focus()}
let sid=getSessionFromUrl();
function rememberSession(){remember('labs_command_session',sid);remember('tl_session',sid)}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function inline(s){return s.replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
 .replace(/\[([^\]]+)\]\((https?:[^)\s]+|\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')}
function md(src){let ls=esc(src).split('\n'),h='',i=0;while(i<ls.length){let l=ls[i];
 if(/^```/.test(l)){let b=[];i++;while(i<ls.length&&!/^```/.test(ls[i]))b.push(ls[i++]);i++;h+='<pre><code>'+b.join('\n')+'</code></pre>';continue}
 if(!l.trim()){i++;continue}if(/^#{1,3}\s/.test(l)){let n=l.match(/^#+/)[0].length;h+='<h'+n+'>'+inline(l.replace(/^#+\s*/,''))+'</h'+n+'>';i++;continue}
 if(/^\s*([-*]|\d+\.)\s+/.test(l)){let o=/^\s*\d+\./.test(l),a=[];while(i<ls.length&&/^\s*([-*]|\d+\.)\s+/.test(ls[i]))a.push('<li>'+inline(ls[i++].replace(/^\s*([-*]|\d+\.)\s+/,''))+'</li>');h+=(o?'<ol>':'<ul>')+a.join('')+(o?'</ol>':'</ul>');continue}
 if(/\|/.test(l)&&i+1<ls.length&&/^\s*\|?[\s:|-]+\|/.test(ls[i+1])){let row=x=>x.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(x=>inline(x.trim()));let hd=row(l);i+=2;let b=[];while(i<ls.length&&/\|/.test(ls[i]))b.push(row(ls[i++]));h+='<table><thead><tr>'+hd.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody>'+b.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table>';continue}
 let b=[l];i++;while(i<ls.length&&ls[i].trim()&&!/^(#{1,3}\s|```|\s*([-*]|\d+\.)\s)/.test(ls[i]))b.push(ls[i++]);h+='<p>'+inline(b.join('<br>'))+'</p>'}return h}
let cmoDocs=[];try{cmoDocs=JSON.parse(document.getElementById('cmoDocuments').textContent||'[]')}catch(e){}
function renderDocuments(){let el=document.getElementById('documentList');el.innerHTML='';cmoDocs.forEach(d=>{let b=document.createElement('button');b.type='button';b.className='doc-btn';b.innerHTML='<span class="doc-icon">D</span><span><b>'+esc(d.title)+'</b><small>'+esc(d.summary)+'</small></span><span class="doc-open">›</span>';b.onclick=()=>openDocument(d);el.appendChild(b)})}
let docTrigger=null;function openDocument(d){docTrigger=document.activeElement;docTitle.textContent=d.title;docBody.innerHTML=md(d.body);docModal.hidden=false;closeMenu(false);document.getElementById('docClose').focus()}
function closeDocument(){if(docModal.hidden)return;docModal.hidden=true;if(docTrigger&&docTrigger.focus)docTrigger.focus();docTrigger=null}
function setSidebar(view){let chats=view==='chats';sessionsEl.hidden=!chats;cmoContext.hidden=chats;chatsTab.classList.toggle('on',chats);companyTab.classList.toggle('on',!chats);chatsTab.setAttribute('aria-selected',String(chats));companyTab.setAttribute('aria-selected',String(!chats))}
function bubble(role,text){if(empty&&empty.parentNode)empty.remove();let m=document.createElement('div');m.className='msg '+role;
 m.innerHTML='<div class="msg-label">'+(role==='user'?'You':'Hermes')+'</div><div class="bubble"></div>';let b=m.querySelector('.bubble');
 if(role==='agent'){b.innerHTML='<div class="md">'+md(text)+'</div><div class="msg-tools"><button type="button">Copy</button></div>';b.querySelector('button').onclick=e=>navigator.clipboard.writeText(text).then(()=>{e.target.textContent='Copied';setTimeout(()=>e.target.textContent='Copy',1000)})}
 else b.textContent=text;threadEl.appendChild(m);threadEl.scrollTop=threadEl.scrollHeight;return m}
function thinking(){let d=document.createElement('div');d.className='thinking';d.innerHTML='<span class="dots"><i></i><i></i><i></i></span><span>Hermes is working with live business context…</span>';threadEl.appendChild(d);threadEl.scrollTop=threadEl.scrollHeight;return d}
function notice(text,bad){let n=document.createElement('div');n.className='notice'+(bad?' bad':'');n.textContent=text;threadEl.appendChild(n);threadEl.scrollTop=threadEl.scrollHeight;return n}
async function api(path,opt){let r=await fetch(API+path,opt);let ct=r.headers.get('content-type')||'';
 if((r.status===401||r.status===403)&&ct.indexOf('application/json')<0){location.href='/oauth2/start?rd='+encodeURIComponent(location.pathname+location.search);throw new Error('Sign-in required')}
 let d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||('Request failed ('+r.status+')'));return d}
async function loadSessions(){
 let gen=++sessionsGen;
 try{
  let d=await api('/sessions');
  if(gen!==sessionsGen)return;
  sessionsEl.innerHTML='';
  if(!d.sessions.length){
    let n=await api('/session/new',{method:'POST'});
    if(gen!==sessionsGen)return;
    sid=n.id;
    d=await api('/sessions');
    if(gen!==sessionsGen)return;
  }
  if(d.sessions.length&&!d.sessions.some(x=>x.id===sid))sid=d.sessions[0].id;
  if(gen!==sessionsGen)return;
  syncSession(sid);
  selectedSessionButton=null;
  d.sessions.forEach(s=>{
    let b=document.createElement('button');
    b.className='session'+(s.id===sid?' on':'');
    b.innerHTML='<b>'+esc(s.title)+'</b><span>'+s.n+' messages · '+esc((s.updated_at||'').slice(0,16).replace('T',' '))+'</span>';
    b.onclick=()=>switchSession(s.id);sessionsEl.appendChild(b);
    if(s.id===sid)selectedSessionButton=b;
  });
  if(selectedSessionButton)selectedSessionButton.scrollIntoView({block:'nearest'});
  let cur=d.sessions.find(x=>x.id===sid);
  document.getElementById('threadTitle').textContent=cur?cur.title:'New command';
}catch(e){sessionsEl.innerHTML='<div class="notice bad">'+esc(e.message)+'</div>'}}
async function loadHistory(){let gen=++historyGen,target=sid;statusEl.textContent='Loading';try{let d=await api('/history?session='+target);if(gen!==historyGen||target!==sid)return;threadEl.innerHTML='';if(!d.messages.length){threadEl.appendChild(empty)}else d.messages.forEach(m=>bubble(m.role,m.text));lastMessageId=d.messages.reduce((max,m)=>Math.max(max,Number(m.id)||0),0)
 }catch(e){if(gen===historyGen&&target===sid){threadEl.innerHTML='';if((e.message||'').toLowerCase()==='no such conversation'){try{let created=await api('/session/new',{method:'POST'});sid=created.id;syncSession(sid);await loadSessions();await loadHistory();return}catch(e2){notice('Could not load this conversation: '+(e2.message||e.message),true)};return;}notice('Could not load this conversation: '+e.message,true)}}finally{if(gen===historyGen)statusEl.textContent=busy?'Working':'Ready'}}
async function switchSession(id){
  runToken++;
  syncSession(id,true);
  closeMenu();
  await loadHistory();
  await loadSessions();
  focusComposer();
}
async function newSession(){runToken++;setSidebar('chats');try{let d=await api('/session/new',{method:'POST'});await switchSession(d.id)}catch(e){notice(e.message,true)}}
async function send(){let text=input.value.trim();if(busy||uploading||(!text&&!attachments.length))return;let at=attachments.slice(),runSid=sid,baseline=lastMessageId,accepted=false;attachments=[];renderAttachments();input.value='';autosize();let optimistic=bubble('user',text||(at.length+' photo'+(at.length===1?'':'s')));let token=++runToken;busy=true;syncSendState();statusEl.textContent='Working';let wait=thinking();
 try{let d=await api('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:runSid,message:text,images:at.map(x=>({path:x.path,name:x.name}))})});accepted=true;
  if(d.reply){wait.remove();if(runSid===sid){bubble('agent',d.reply);lastMessageId=Math.max(lastMessageId,Number(d.message_id)||0)}}
  else if(d.pending){await poll(runSid,wait,baseline,token)}
  else{wait.remove();if(runSid===sid)notice('Hermes returned no result. Your message remains in this conversation.',true)}
  await loadSessions();loadEvents()
 }catch(e){wait.remove();if(accepted){if(runSid===sid)notice('Your message was sent, but Command lost connection while waiting. The result will remain in this conversation.',false)}else if(runSid===sid&&/already working with another task|still working on the previous task|agent is busy/.test(String(e.message).toLowerCase())){notice('Hermes is already working on another request. Tap send again when it finishes.',false);input.value=text;attachments=at;renderAttachments();autosize()}else if(runSid===sid){optimistic.remove();notice('Not sent: '+e.message,true);input.value=text;attachments=at;renderAttachments();autosize()}
}
 finally{busy=false;syncSendState();statusEl.textContent='Ready';focusComposer()}
}
async function poll(runSid,wait,baseline,token){for(let n=0;n<500;n++){if(token!==runToken){wait.remove();return;}await new Promise(r=>setTimeout(r,6000));if(token!==runToken){wait.remove();return;}let d=await api('/history?session='+runSid),agents=d.messages.filter(m=>m.role==='agent'&&(Number(m.id)||0)>baseline);if(agents.length){wait.remove();if(runSid===sid){let reply=agents[agents.length-1];bubble('agent',reply.text);lastMessageId=Math.max(lastMessageId,Number(reply.id)||0)}else await loadSessions();return}}wait.remove();if(token===runToken&&runSid===sid)notice('The job is still running. Its result will remain in this conversation.',false)}
async function upload(f){if(!f)return;if(attachments.length+uploading>=8){notice('Up to 8 photos per message.',true);return}
 if(f.size>32*1024*1024){notice((f.name||'This image')+' is larger than 32 MB.',true);return}
 if(!/\.(jpe?g|png|webp)$/i.test(f.name||'')){let ext=(f.type||'').includes('png')?'.png':(f.type||'').includes('webp')?'.webp':'.jpg';f=new File([f],'pasted-'+Date.now()+ext,{type:f.type||'image/jpeg'})}
let form=new FormData();form.append('image',f);uploading++;syncSendState();try{let d=await api('/upload',{method:'POST',body:form}),a={path:d.path,name:f.name,url:''};attachments.push(a);renderAttachments();let rd=new FileReader();rd.onload=e=>{a.url=e.target.result;renderAttachments()};rd.readAsDataURL(f)}catch(e){notice(e.message,true)}finally{uploading--;syncSendState()}}
function renderAttachments(){attachEl.innerHTML='';attachments.forEach((a,i)=>{let c=document.createElement('div');c.className='attachment';c.innerHTML=(a.url?'<img src="'+a.url+'" alt="">':'')+'<span>'+esc(a.name)+'</span><button type="button" aria-label="Remove">×</button>';c.querySelector('button').onclick=()=>{attachments.splice(i,1);renderAttachments()};attachEl.appendChild(c)})}
async function loadEvents(){try{let d=await api('/events'),el=document.getElementById('events');el.innerHTML='';(d.events||[]).slice(0,8).forEach(x=>{let v=document.createElement('div');v.className='event';v.innerHTML='<b>'+esc((x.app||'Labs')+' · '+(x.kind||'activity').replace(/_/g,' '))+'</b><p>'+esc(x.detail||'')+'</p><time>'+esc((x.created_at||'').slice(0,16).replace('T',' '))+'</time>';el.appendChild(v)});if(!el.children.length)el.innerHTML='<div class="event"><p>No recent activity.</p></div>'}catch(e){}}
function autosize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,150)+'px'}
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{closeMenu(false);input.value=b.dataset.prompt;send()});document.getElementById('newBtn').onclick=newSession;
chatsTab.onclick=()=>setSidebar('chats');companyTab.onclick=()=>setSidebar('company');document.getElementById('docClose').onclick=closeDocument;
docModal.onclick=e=>{if(e.target===docModal)closeDocument()};renderDocuments();setSidebar('company');
document.getElementById('attachBtn').onclick=()=>document.getElementById('fileInput').click();document.getElementById('fileInput').onchange=async e=>{for(let f of e.target.files)await upload(f);e.target.value=''};
sendBtn.onclick=send;input.oninput=autosize;input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}};
document.addEventListener('paste',e=>{for(let x of (e.clipboardData&&e.clipboardData.items)||[])if(x.kind==='file'&&x.type.indexOf('image/')===0)upload(x.getAsFile())});
document.addEventListener('dragover',e=>{e.preventDefault();composeWrap.classList.add('drag')});document.addEventListener('dragleave',e=>{if(!e.relatedTarget)composeWrap.classList.remove('drag')});document.addEventListener('drop',e=>{e.preventDefault();composeWrap.classList.remove('drag');for(let f of (e.dataTransfer&&e.dataTransfer.files)||[])if((f.type||'').indexOf('image/')===0)upload(f)});
let rail=document.getElementById('leftRail'),contextRail=document.getElementById('contextRail'),shade=document.getElementById('shade'),menuBtn=document.getElementById('menuBtn'),contextBtn=document.getElementById('contextBtn');
let leftMq=matchMedia('(max-width:720px)'),contextMq=matchMedia('(max-width:1180px)'),drawerTrigger=null;
function setDrawerHidden(el,hidden){
 if(hidden){el.inert=true;el.setAttribute('inert','');el.setAttribute('aria-hidden','true')}
 else{el.inert=false;el.removeAttribute('inert');el.removeAttribute('aria-hidden')}
}
function syncDrawers(){
 setDrawerHidden(rail,leftMq.matches&&!rail.classList.contains('open'));
 setDrawerHidden(contextRail,contextMq.matches&&!contextRail.classList.contains('open'))
}
function closeMenu(restore=true){
 rail.classList.remove('open');contextRail.classList.remove('open');shade.classList.remove('on');
 menuBtn.setAttribute('aria-expanded','false');contextBtn.setAttribute('aria-expanded','false');
 let active=document.activeElement,target=drawerTrigger;drawerTrigger=null;
 if(restore&&target&&target.focus)target.focus();
 else if(rail.contains(active)&&menuBtn.offsetParent!==null)menuBtn.focus();
 else if(contextRail.contains(active)&&contextBtn.offsetParent!==null)contextBtn.focus();
 syncDrawers()
}
function openDrawer(el,trigger,focus){
 closeMenu(false);drawerTrigger=trigger;el.classList.add('open');shade.classList.add('on');
 trigger.setAttribute('aria-expanded','true');syncDrawers();requestAnimationFrame(()=>focus.focus())
}
menuBtn.onclick=()=>openDrawer(rail,menuBtn,document.getElementById('newBtn'));
contextBtn.onclick=()=>openDrawer(contextRail,contextBtn,document.getElementById('contextClose'));
document.getElementById('contextClose').onclick=()=>closeMenu();shade.onclick=()=>closeMenu();
window.addEventListener('popstate',()=>{
 let target=getSessionFromUrl();
 if(target===sid)return;
 runToken++;
 syncSession(target,false);
 closeMenu(false);
 loadHistory();
 loadSessions();
 focusComposer();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!docModal.hidden){closeDocument();return}if(e.key==='Escape'&&(rail.classList.contains('open')||contextRail.classList.contains('open')))closeMenu()});
for(let mq of [leftMq,contextMq]){if(mq.addEventListener)mq.addEventListener('change',()=>closeMenu(false));else mq.addListener(()=>closeMenu(false))}
syncDrawers();
loadSessions().then(loadHistory);loadEvents();focusComposer();
})();
""" + WHOAMI_JS + r"""
</script></body></html>"""
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
