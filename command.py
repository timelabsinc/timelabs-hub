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
    ("writing", "Human Writing Standard", "Channel rules, evidence, rhythm and final review", BASE / "docs/human-writing-standard.md"),
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


def _instagram_metrics():
    fallback = {"followers": 3117, "posts": 5, "top_views": 4399,
                "top_interactions": 101}
    try:
        snap = json.loads((BASE / "data/instagram_audit_snapshot.json").read_text(encoding="utf-8"))
        account = snap.get("account") or {}
        posts = snap.get("posts") or []
        return {
            "followers": int(account.get("followers") or fallback["followers"]),
            "posts": len(posts),
            "top_views": max((int(p.get("views") or 0) for p in posts), default=0),
            "top_interactions": max((int(p.get("interactions") or 0) for p in posts), default=0),
        }
    except (OSError, ValueError, TypeError):
        return fallback


def build():
    cmo_documents = _cmo_documents_json()
    instagram = _instagram_metrics()
    doc = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Command — Labs OS</title>
<style>""" + HUB_STYLE + r"""
html,body{height:100%;overflow:hidden}
[hidden]{display:none !important}
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
.session-viewbar{display:flex;gap:5px;padding:7px 8px;border-bottom:1px solid var(--border)}
.session-viewbar button{flex:1;border:0;border-radius:7px;background:transparent;color:var(--muted);font:inherit;font-size:10.5px;
  font-weight:700;padding:6px;cursor:pointer}.session-viewbar button.on{background:var(--card-2);color:var(--ink)}
.session-list{padding:8px;overflow:auto;flex:1}.session-row{position:relative;margin-bottom:3px}.session{width:100%;display:block;text-align:left;border:1px solid transparent;
  background:transparent;color:var(--ink);border-radius:9px;padding:10px 38px 10px 10px;cursor:pointer}
.session:hover{background:var(--card-2)}.session.on{background:var(--accent-bg);border-color:var(--accent)}
.session b{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session-meta{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted);margin-top:4px;white-space:nowrap;overflow:hidden}
.session-engine{margin-left:auto;max-width:90px;overflow:hidden;text-overflow:ellipsis;color:var(--accent);font-size:9px;font-style:normal;font-weight:750}
.session-more{position:absolute;right:5px;top:50%;transform:translateY(-50%);width:30px;height:30px;border:0;border-radius:7px;
  background:transparent;color:var(--muted);font:inherit;font-size:17px;line-height:1;cursor:pointer;opacity:0}
.session-row:hover .session-more,.session-more:focus-visible,.session-row:focus-within .session-more{opacity:1}.session-more:hover{background:var(--card);color:var(--ink)}
.session-empty{padding:22px 10px;text-align:center;color:var(--muted);font-size:11px;line-height:1.5}
.session-menu{position:fixed;z-index:80;width:244px;max-height:calc(100dvh - 16px);overflow:auto;padding:6px;border:1px solid var(--border);border-radius:11px;background:var(--card);
  box-shadow:var(--shadow-lg)}.session-menu button{display:block;width:100%;border:0;border-radius:7px;background:transparent;color:var(--ink);
  font:inherit;font-size:12px;text-align:left;padding:9px 10px;cursor:pointer}.session-menu button:hover:not(:disabled),.session-menu button:focus-visible{background:var(--card-2)}
.session-menu button:disabled{cursor:not-allowed;opacity:.52}.session-menu .danger{color:var(--bad)}.session-menu .on{background:var(--accent-bg);color:var(--accent)}
.session-menu-head{padding:8px 9px 7px}.session-menu-head b{display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.session-menu-head span,.session-menu button small{display:block;color:var(--muted);font-size:9.5px;line-height:1.35;margin-top:2px}
.session-menu-label{border-top:1px solid var(--border);margin-top:5px;padding:9px 9px 4px;color:var(--muted);font-size:9px;font-weight:850;letter-spacing:.09em;text-transform:uppercase}
.session-engine-action{min-height:48px}
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
.engine-pill{flex:none;max-width:145px;border:1px solid var(--border);border-radius:999px;background:var(--card);color:var(--accent);font:inherit;
  font-size:10px;font-weight:750;padding:6px 9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}.engine-pill:hover{border-color:var(--accent);background:var(--accent-bg)}
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
.sent-media{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.sent-media:first-child{margin-top:0}
.sent-media-item{width:118px;min-height:42px;margin:0;padding:5px;border:1px solid color-mix(in srgb,currentColor 28%,transparent);
  border-radius:9px;display:flex;align-items:center;gap:6px;box-sizing:border-box;overflow:hidden}
.sent-media-item img{width:44px;height:44px;flex:none;border-radius:6px;object-fit:cover;background:var(--bg)}
.sent-media-icon{width:30px;height:30px;flex:none;border:1px solid color-mix(in srgb,currentColor 28%,transparent);border-radius:6px;
  display:grid;place-items:center;font-size:8px;font-weight:850;letter-spacing:.06em}
.sent-media-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;line-height:1.2}
.msg.agent .bubble{background:var(--card);border:1px solid var(--border);border-radius:4px 14px 14px 14px;
  padding:16px 18px;color:var(--ink);font-size:14px;line-height:1.6;box-shadow:var(--shadow)}
.md h1,.md h2,.md h3{margin:12px 0 6px;line-height:1.25}.md h1{font-size:20px}.md h2{font-size:17px}.md h3{font-size:15px}
.md p{margin:7px 0}.md ul,.md ol{margin:7px 0;padding-left:20px}.md pre{overflow:auto;background:var(--bg);
  border:1px solid var(--border);padding:10px;border-radius:8px;white-space:pre-wrap}.msg .md pre{position:relative;padding:42px 10px 10px}.md code{font-size:.9em;background:var(--bg);padding:1px 4px;border-radius:4px}
.md pre code{padding:0;background:transparent}.block-copy{position:absolute;top:7px;right:7px;min-height:28px;border:1px solid var(--border);
  border-radius:7px;background:var(--card);color:var(--accent);font:inherit;font-size:10.5px;font-weight:750;padding:4px 8px;cursor:pointer}
.md table{display:block;overflow:auto;border-collapse:collapse;margin:10px 0}.md th,.md td{border:1px solid var(--border);padding:6px 9px;text-align:left}
.md a{color:var(--accent)}.msg-tools{display:flex;gap:5px;margin-top:10px;padding-top:9px;border-top:1px solid var(--border)}
.msg-tools button{min-height:32px;border:0;background:none;color:var(--muted);font:inherit;font-size:11px;cursor:pointer;padding:5px 8px}
.copy-all{font-weight:700}
.msg-tools button:hover{color:var(--accent)}
.thinking{max-width:900px;margin:0 auto 18px;color:var(--muted);font-size:12px;display:flex;gap:8px;align-items:center}
.dots{display:flex;gap:3px}.dots i{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:pulse 1s infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes pulse{50%{opacity:.25;transform:translateY(-2px)}}
.compose-wrap{flex:none;padding:10px clamp(12px,4vw,48px) calc(12px + env(safe-area-inset-bottom));
  border-top:1px solid transparent}
.compose-wrap.drag{background:var(--accent-bg);border-top-color:var(--accent)}
.creator-workbar{max-width:900px;margin:0 auto 8px;padding:10px;border:1px solid color-mix(in srgb,var(--accent) 45%,var(--border));
  border-radius:12px;background:linear-gradient(135deg,var(--accent-bg),var(--card));box-shadow:var(--shadow)}
.creator-work-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px}
.creator-work-head b{display:block;font-size:12px;color:var(--ink);margin-top:2px}.creator-kicker{display:block;font-size:9px;
  color:var(--accent);font-weight:850;letter-spacing:.1em;text-transform:uppercase}
.creator-new{flex:none;min-height:34px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--ink);
  font:inherit;font-size:10.5px;font-weight:750;padding:5px 9px;cursor:pointer}
.creator-progress{height:3px;border-radius:99px;background:var(--border);overflow:hidden;margin-bottom:12px}
.creator-progress i{display:block;height:100%;width:14%;border-radius:inherit;background:var(--accent);transition:width .2s var(--ease)}
.creator-question{font-size:16px;line-height:1.3;margin:0;color:var(--ink)}.creator-help{font-size:11px;line-height:1.45;
  color:var(--muted);margin:5px 0 10px}.creator-choices{display:flex;gap:6px;flex-wrap:wrap}
.creator-choice{min-height:38px;border:1px solid var(--border);border-radius:9px;background:var(--card);color:var(--ink);
  font:inherit;font-size:11px;font-weight:700;padding:7px 10px;cursor:pointer;text-align:left}
.creator-choice:hover{border-color:var(--accent);background:var(--accent-bg);color:var(--accent)}
.creator-answer{display:grid;gap:8px}.creator-answer textarea{width:100%;min-height:68px;max-height:150px;resize:vertical;
  border:1px solid var(--border);border-radius:9px;background:var(--card);color:var(--ink);font:inherit;font-size:13px;
  line-height:1.45;padding:9px 10px;box-sizing:border-box}.creator-answer textarea:focus{border-color:var(--accent);outline:2px solid var(--accent-bg)}
.creator-actions,.creator-foot{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.creator-actions{justify-content:flex-end}
.creator-action{min-height:36px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--ink);
  font:inherit;font-size:10.5px;font-weight:750;padding:6px 10px;cursor:pointer}.creator-action.primary{border-color:var(--ink);
  background:var(--ink);color:var(--bg)}.creator-action:disabled{opacity:.45;cursor:default}.creator-foot{justify-content:space-between;
  border-top:1px solid var(--border);margin-top:11px;padding-top:8px}.creator-link{border:0;background:none;color:var(--muted);
  font:inherit;font-size:10.5px;padding:5px 2px;cursor:pointer}.creator-link:hover{color:var(--accent)}
.creator-status{min-height:15px;margin:6px 1px 0;color:var(--muted);font-size:10px}.creator-status.bad{color:var(--bad)}
.creator-trail{display:grid;gap:4px;margin-bottom:9px}.creator-trail-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;
  padding:6px 8px;border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--card) 70%,transparent)}
.creator-trail-row span{min-width:0;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.creator-trail-row span b{color:var(--ink)}
.creator-trail-row button{border:0;background:none;color:var(--accent);font:inherit;font-size:9.5px;font-weight:750;padding:4px;cursor:pointer}
.creator-summary{display:grid;gap:5px;max-height:130px;overflow:auto}.creator-summary-row{display:grid;grid-template-columns:minmax(90px,.35fr) 1fr;
  gap:9px;border-bottom:1px solid var(--border);padding:5px 1px;font-size:10.5px}.creator-summary-row b{color:var(--muted)}
.creator-direct{display:flex;align-items:center;justify-content:space-between;gap:10px}.creator-direct span{display:block;color:var(--muted);
  font-size:10.5px;margin-top:2px}.compose-wrap.creator-guided .compose,.compose-wrap.creator-guided .compose-note{display:none}
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
.prompt-action{font:inherit}
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
  body.is-admin .topbar,body.is-role-preview .topbar{display:flex;padding:6px 8px}body.is-admin .brand-name,body.is-role-preview .brand-name{display:none}
  .workspace{height:100%}.mobile-toggle{display:inline-grid;place-items:center}
  .rail.left{position:absolute;inset:0 auto 0 0;width:min(84vw,300px);z-index:30;transform:translateX(-101%);
    transition:transform .18s}.rail.left.open{transform:none}
  .thread{padding:18px 12px}.empty{margin-top:3vh}.starts,.cmo-actions{grid-template-columns:1fr}
  .compose-wrap{padding-left:8px;padding-right:8px}.msg.user .bubble{max-width:90%}
  .creator-workbar{padding:10px}.creator-choice{min-height:44px;font-size:12px;flex:1 1 calc(50% - 6px)}
  .creator-question{font-size:15px}.creator-answer textarea{font-size:16px}.creator-summary-row{grid-template-columns:1fr}.creator-foot{align-items:flex-end}
  .compose button,.mobile-toggle,.context-toggle{width:44px;height:44px}
  .work-head{height:58px;padding:0 8px}.work-title span{display:none}.engine-pill{max-width:102px;padding:6px 8px}.status{font-size:0}.status i{width:8px;height:8px}
}
@media(pointer:coarse){.session-more{opacity:1}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}.dots i,.skeleton{animation:none}.rail{transition:none!important}}
</style></head><body>
<div class="wrap" data-no-assist>""" + hub_header("chat") + r"""
<div class="cmd">
  <aside class="rail left" id="leftRail" aria-label="Conversations">
    <div class="rail-head"><span class="rail-title" id="leftRailTitle">TimeLabs CMO</span><button type="button" class="new-btn" id="newBtn">+ New chat</button></div>
    <div class="rail-tabs" role="tablist" aria-label="Command sidebar">
      <button type="button" class="rail-tab" id="chatsTab" role="tab" aria-selected="false">Chats</button>
      <button type="button" class="rail-tab on" id="companyTab" role="tab" aria-selected="true">Company</button>
    </div>
    <div class="session-viewbar" id="sessionViewbar" hidden>
      <button type="button" class="on" id="activeChatsBtn">Active</button>
      <button type="button" id="archivedChatsBtn">Archived</button>
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
  <div class="session-menu" id="sessionMenu" role="menu" hidden>
    <div class="session-menu-head"><b id="sessionMenuTitle">Chat settings</b><span id="sessionMenuEngine">Writing engine</span></div>
    <button type="button" id="sessionOpenAction" role="menuitem">Open chat</button>
    <button type="button" id="sessionRenameAction" role="menuitem">Rename chat</button>
    <button type="button" id="sessionCopyLinkAction" role="menuitem">Copy chat link</button>
    <button type="button" id="sessionArchiveAction" role="menuitem">Archive</button>
    <div class="session-menu-label">Writing engine</div>
    <button type="button" class="session-engine-action" data-engine="claude" role="menuitem"><b>Claude Sonnet 4.6</b><small>Recommended for writing · connected</small></button>
    <button type="button" class="session-engine-action" data-engine="minimax" role="menuitem"><b>MiniMax M3</b><small>Connected alternative</small></button>
    <button type="button" class="session-engine-action" data-engine="auto" role="menuitem"><b>Hermes Auto</b><small>Let routing choose and fail over</small></button>
    <button type="button" disabled><b>ChatGPT</b><small>Not connected to Hermes yet</small></button>
    <div class="session-menu-label">Danger zone</div>
    <button type="button" class="danger" id="sessionDeleteAction" role="menuitem">Delete permanently</button>
  </div>
  <div class="shade" id="shade" aria-hidden="true"></div>
  <section class="workspace">
    <div class="work-head">
      <button type="button" class="icon-btn mobile-toggle" id="menuBtn" aria-label="Show conversations" aria-controls="leftRail" aria-expanded="false">☰</button>
      <div class="work-title"><b id="threadTitle">New command</b><span id="operatorMode">Hermes · business operator and CMO</span></div>
      <button type="button" class="engine-pill" id="currentEngineBtn" aria-label="Change writing engine">Claude Sonnet 4.6</button>
      <span class="status" aria-live="polite"><i></i><span id="agentStatus">Ready</span></span>
      <button type="button" class="icon-btn context-toggle" id="contextBtn" aria-label="Show business context" aria-controls="contextRail" aria-expanded="false">ⓘ</button>
    </div>
    <div class="thread" id="thread" role="log" aria-live="polite" aria-relevant="additions">
      <div class="empty" id="empty">
        <div class="empty-mark">L</div>
        <h1 id="emptyTitle">What should we move forward?</h1>
        <p id="emptyCopy">Ask a question or give Hermes a job. It can inspect the business and work across the Labs OS tools available to your account.</p>
        <div id="ownerDesk" hidden>
        <div class="cmo-desk">
          <div class="cmo-head"><div><b>CMO desk</b><span>Research, positioning, channels and measurable growth. External actions remain review-gated.</span></div><div class="cmo-badge">Active</div></div>
          <div class="cmo-actions">
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Read /root/ops-dashboard/docs/hermes-cmo-charter.md and /root/ops-dashboard/docs/competitive-intelligence-audit.md completely. Inspect current memory_facts categories state, business and preference; use labs sales and products for current commercial context; inspect the latest Instagram audit snapshot; and run fresh public research where facts may have changed. Produce a decision-ready weekly CMO brief: executive verdict, commercial and channel scorecard using only measured data, customer and market signals, meaningful competitor changes, website/offer/content gaps, the three highest-leverage priorities, tasks you can prepare now, owner decisions required, and KPI, guardrail and review date for each recommendation. Label every important claim as first-party measured, public verified, competitor self-claim, community anecdote or inference. Save durable decisions to memory_facts and concrete work to action_items. Do not publish, spend, message, contact competitors or change Shopify without explicit approval."><b>Run CMO brief</b><small>Full evidence-backed weekly review</small></button>
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Read the CMO charter and latest competitive intelligence audit, then run a fresh source-cited market watch across direct Indian mod sellers, adjacent Indian microbrands, official-brand substitutes, global custom-watch benchmarks, communities and discovery channels. Report only changes and opportunities affecting a TimeLabs decision. Compare positioning, price ladder, product proof, warranty, lead time, content formats, conversion path and channel use. Separate verified facts, self-claims, anecdotes and inference. End with at most five prioritized moves, each with expected effect, effort, dependency, KPI and stop/scale rule. No external writes or outreach."><b>Market watch</b><small>Competitors and category signals</small></button>
            <button type="button" class="cmo-action start" data-prompt="Act as my TimeLabs CMO. Audit our acquisition and retention path across Instagram, WhatsApp, Shopify, email, search, Reddit, YouTube and creator or collector partnerships using available first-party data and public evidence. Map discovery to proof, consultation, order, build, delivery and review/referral. Identify broken or unmeasured handoffs, privacy/access constraints, channels competitors use better, and the smallest 30-day plan to improve qualified demand. For every action include owner, prerequisite, KPI, guardrail and review date. Apply the Human Writing Standard to every draft. Prepare drafts and internal tasks only; do not publish, spend or message anyone without approval."><b>Channel plan</b><small>Build the 30-day growth system</small></button>
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
    </div>
    <div class="compose-wrap">
      <div class="creator-workbar" id="creatorWorkbar" hidden>
        <div class="creator-work-head"><div><span class="creator-kicker">Creator</span><b id="creatorStepLabel">Question 1 · Destination</b></div><button type="button" class="creator-new" id="creatorNewBtn">New content</button></div>
        <div class="creator-progress" aria-hidden="true"><i id="creatorProgress"></i></div>
        <div id="creatorStep" aria-live="polite"></div>
        <div class="creator-foot">
          <button type="button" class="creator-link" id="creatorDirectBtn">Ask Hermes directly</button>
          <button type="button" class="creator-link" id="creatorDraftBtn" hidden>Draft now with what I have</button>
        </div>
        <div class="creator-status" id="creatorStatus" role="status"></div>
      </div>
      <div class="attachments" id="attachments"></div>
      <div class="compose">
        <button type="button" class="attach-btn" id="attachBtn" aria-label="Attach photos" title="Attach photos">＋</button>
        <input id="fileInput" type="file" accept="image/jpeg,image/png,image/webp" aria-label="Attach photos" multiple hidden>
        <textarea id="input" rows="1" placeholder="Ask Hermes anything…" aria-label="Command"></textarea>
        <button type="button" class="send-btn" id="sendBtn" aria-label="Send">↑</button>
      </div>
      <div class="compose-note" id="composeNote">Ask Hermes for a dry run before any business change. Existing tool safeguards still apply.</div>
    </div>
  </section>
  <aside class="rail right" id="contextRail" aria-label="Business context">
    <div class="rail-head"><span class="rail-title" id="contextRailTitle">CMO intelligence</span><button type="button" class="icon-btn context-toggle" id="contextClose" aria-label="Close business context">×</button></div>
    <div class="context">
      <div id="creatorContext" hidden>
        <div class="context-block"><h3>Use this screen</h3><div class="cap-list">
          <div class="cap">Answer one question at a time <span>Brief</span></div>
          <div class="cap">Attach the real media or message <span>Source</span></div>
          <div class="cap">Let Hermes stop when it has enough <span>Draft</span></div>
          <div class="cap">Use Copy this text <span>Publish</span></div>
        </div></div>
        <div class="context-block"><h3>What Hermes needs</h3><div class="mode"><b>Real material, not polished instructions</b><span>Photos, the actual question or event, confirmed product facts, the audience and the desired next step are enough. If an important fact is missing, Hermes asks instead of guessing.</span></div></div>
        <div class="context-block"><h3>Before publishing</h3><div class="cap-list">
          <div class="cap">Media matches copy <span>Required</span></div>
          <div class="cap">Claims are confirmed <span>Required</span></div>
          <div class="cap">Price and link checked <span>Required</span></div>
          <div class="cap">Read aloud once <span>Recommended</span></div>
        </div></div>
        <div class="context-block"><h3>Your access</h3><div class="mode"><b>Private drafting only</b><span>You can research, attach media and prepare content. This account cannot send, post, publish or access customer and order data.</span></div></div>
      </div>
      <div id="ownerContext">
      <div class="context-block"><h3>Measured Instagram snapshot</h3><div class="analytics">
        <div class="metric"><b>__CMO_IG_FOLLOWERS__</b><span>followers</span></div><div class="metric"><b>__CMO_IG_POSTS__</b><span>posts / 90 days</span></div>
        <div class="metric"><b>__CMO_IG_TOP_VIEWS__</b><span>top content views</span></div><div class="metric"><b>__CMO_IG_TOP_INTERACTIONS__</b><span>top interactions</span></div>
      </div></div>
      <div class="context-block"><h3>Specialist agents</h3><div class="agent-feed">
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Instagram specialist under the Hermes CMO charter. Read all CMO context documents and the latest Instagram snapshot. Audit cadence, formats, creative themes, proof, captions, conversion handoff and measurement. Return the next five evidence-led content briefs with hook, shot list, proof, CTA, KPI and review rule. Prepare internal tasks only; do not publish."><span class="agent-icon">IG</span><span><b>Instagram Agent</b><small>Cadence, creative and conversion</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Content specialist under the Hermes CMO charter. Read Product Information, Marketing Strategy, Brand Voice, the Human Writing Standard and the current competitive audit. Build a four-week cross-channel content system from the three strongest evidence-backed themes. Include Reel, carousel, WhatsApp, email and search repurposing, production owner and acceptance criteria. Apply the Human Writing Standard to every draft. Draft only; do not publish."><span class="agent-icon">CO</span><span><b>Content Agent</b><small>Campaigns and repurposing</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs SEO and GEO specialist under the Hermes CMO charter. Audit the live storefront, product information and competitor sources for technical, on-page, search-intent and AI-answer visibility gaps. Prioritize queries that can lead to qualified custom-watch consultations. Return fixes and content briefs with evidence, effort, expected effect and measurement. Do not change Shopify without approval."><span class="agent-icon">SE</span><span><b>SEO &amp; GEO Agent</b><small>Search and AI visibility</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs Reddit and community specialist. Read the Human Writing Standard, then use the existing Reddit listener and public web research to identify genuine watch-community questions where TimeLabs expertise could help. Separate listening insights from reply opportunities, follow subreddit rules, avoid promotion and astroturfing, and apply the standard to drafts prepared for review only."><span class="agent-icon">RD</span><span><b>Reddit Agent</b><small>Listening and genuine replies</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs WhatsApp and Email lifecycle specialist. Read the Human Writing Standard and map consent-safe messages from consultation through order, build updates, delivery, care, review and referral. Use the CMO context and current operational reality. Identify missing data and CRM requirements, then prepare the smallest lifecycle plan and channel-native drafts. Do not send or broadcast anything."><span class="agent-icon">WA</span><span><b>WhatsApp &amp; Email</b><small>Consultation and lifecycle</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs UGC and owner-proof specialist. Design a permissioned system for build photos, wrist shots, reviews and referral proof from delivery through reuse. Include consent, asset fields, request timing, incentive guardrails and how proof maps back to products. Prepare the workflow and drafts only."><span class="agent-icon">UG</span><span><b>UGC Agent</b><small>Owner proof and referrals</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs influencer and collector-partnership specialist. Research a small, relevant India and diaspora watch-creator set using public evidence. Score fit, audience relevance, authenticity, likely format and risk. Propose a disclosed pilot with selection rules, deliverables, source tracking and stop/scale criteria. Do not contact anyone."><span class="agent-icon">IN</span><span><b>Influencer Agent</b><small>Collector and creator partnerships</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs competitor-intelligence specialist. Read the current audit, then verify only facts likely to have changed. Update the direct, adjacent, substitute and benchmark market map; identify meaningful price, offer, proof, content or channel changes; and state the TimeLabs decision each signal affects. Public research only, no block evasion or contact."><span class="agent-icon">CI</span><span><b>Competitor Agent</b><small>Market changes and threats</small></span><span class="agent-state">Ready</span></button>
        <button type="button" class="agent-card prompt-action" data-prompt="Act as the TimeLabs storefront conversion specialist. Audit the live site against Product Information, Marketing Strategy, Brand Voice, the Human Writing Standard and current customer trust gaps. Review navigation, disclosure, proof, product content, mobile friction and consultation handoff. Return a prioritized change plan and dry-run copy that follows the standard; do not modify Shopify."><span class="agent-icon">CV</span><span><b>Storefront Agent</b><small>Proof and conversion</small></span><span class="agent-state">Ready</span></button>
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
const sessionViewbar=document.getElementById('sessionViewbar'),activeChatsBtn=document.getElementById('activeChatsBtn'),archivedChatsBtn=document.getElementById('archivedChatsBtn');
const sessionMenu=document.getElementById('sessionMenu'),sessionArchiveAction=document.getElementById('sessionArchiveAction'),sessionDeleteAction=document.getElementById('sessionDeleteAction');
const sessionOpenAction=document.getElementById('sessionOpenAction'),sessionRenameAction=document.getElementById('sessionRenameAction'),sessionCopyLinkAction=document.getElementById('sessionCopyLinkAction');
const sessionMenuTitle=document.getElementById('sessionMenuTitle'),sessionMenuEngine=document.getElementById('sessionMenuEngine'),currentEngineBtn=document.getElementById('currentEngineBtn');
const attachEl=document.getElementById('attachments'), composeWrap=document.querySelector('.compose-wrap');
const cmoContext=document.getElementById('cmoContext'), chatsTab=document.getElementById('chatsTab'), companyTab=document.getElementById('companyTab');
const docModal=document.getElementById('docModal'), docTitle=document.getElementById('docTitle'), docBody=document.getElementById('docBody');
let busy=false, attachments=[], uploading=0, lastMessageId=0, historyGen=0, sessionsGen=0, runToken=0, accessInfo=null;
let allSessions=[],archivedView=false,sessionMenuTarget=null;
const CREATOR_CHANNELS={
 instagram:{label:'Instagram caption',route:'/caption'},story:{label:'Instagram Story',route:'/story'},
 reddit:{label:'Reddit',route:'/reddit'},whatsapp:{label:'WhatsApp',route:'/whatsapp'},
 sales:{label:'Sales reply',route:'/sales'},email:{label:'Email',route:'/email'},blog:{label:'Blog',route:'/blog'},
 youtube:{label:'YouTube',route:'/youtube'},meta_ad:{label:'Ad',route:'/ad'},product:{label:'Product copy',route:'/product'},
 founder:{label:'Founder post',route:'/founder'}
};
function freshCreatorState(){return{mode:'guided',phase:'channel',channel:null,answers:[],question:null,notice:''}}
let creatorState=freshCreatorState(),creatorPlanning=false,creatorRequest=0;
async function loadAccessMode(){
 try{
  let view='';try{view=new URLSearchParams(location.search).get('view_as')||''}catch(e){}
  let i=await api('/access/me'+(view?'?view_as='+encodeURIComponent(view):'')),creator=i.role==='creator';accessInfo=i;
  document.body.classList.toggle('creator-mode',creator);
  let ownerDesk=document.getElementById('ownerDesk');if(ownerDesk)ownerDesk.hidden=creator;
  document.getElementById('creatorWorkbar').hidden=!creator;
  document.getElementById('ownerContext').hidden=creator;
  document.getElementById('creatorContext').hidden=!creator;
  if(creator){
   restoreCreatorState();renderCreator();
   let emptyTitle=document.getElementById('emptyTitle'),emptyCopy=document.getElementById('emptyCopy');
   if(emptyTitle)emptyTitle.textContent='What are we creating?';
   if(emptyCopy)emptyCopy.textContent='Choose where it will go. Hermes asks one useful question at a time, then returns text you can review and copy.';
   document.getElementById('operatorMode').textContent=(i.preview?'Creator preview · ':'')+'Hermes content workspace';
   document.getElementById('leftRailTitle').textContent='Creator workspace';
   document.getElementById('contextRailTitle').textContent='Creator checklist';
   document.getElementById('composeNote').textContent='Use confirmed facts only. Hermes prepares drafts; you review, copy and publish manually.';
   input.placeholder='Ask Hermes directly, or return to the guided interview…';
   setSidebar('chats');
  }else composeWrap.classList.remove('creator-guided')
 }catch(e){}
}
function syncSendState(){sendBtn.disabled=busy||uploading>0}
function stored(k){try{return localStorage.getItem(k)}catch(e){return null}}
function remember(k,v){try{localStorage.setItem(k,v)}catch(e){}}
function normalizeSubreddit(value){let v=String(value||'').trim();if(!v||/^other subreddit$/i.test(v))return'';
 v=v.replace(/^https?:\/\/(?:www\.)?reddit\.com\/r\//i,'').replace(/^r\//i,'').replace(/^\/+|\/+$/g,'');
 return /^[A-Za-z0-9_]{2,21}$/.test(v)?'r/'+v:''}
function learnedSubreddits(){let data={};try{data=JSON.parse(stored('labs_creator_subreddits')||'{}')||{}}catch(e){}
 return Object.entries(data).filter(([name,row])=>normalizeSubreddit(name)&&row&&Number(row.count)>0)
  .sort((a,b)=>(Number(b[1].count)-Number(a[1].count))||(Number(b[1].last)-Number(a[1].last))).map(([name])=>normalizeSubreddit(name)).slice(0,8)}
function rememberSubreddit(value){let name=normalizeSubreddit(value);if(!name)return value;let data={};try{data=JSON.parse(stored('labs_creator_subreddits')||'{}')||{}}catch(e){}
 let prior=data[name]||{};data[name]={count:Math.max(0,Number(prior.count)||0)+1,last:Date.now()};remember('labs_creator_subreddits',JSON.stringify(data));return name}
function communityOptions(options){let out=[];learnedSubreddits().concat(options||[]).forEach(value=>{if(value&&!out.includes(value))out.push(value)});return out}
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
function getSessionFromUrl(){
  let raw=new URLSearchParams(location.search).get('session');
  if(!raw)return null;
  let v=parseInt(raw,10);
  return Number.isFinite(v)?v:null;
}
function syncSession(id, push){
  let next=Number.isFinite(id)?id:1,changed=next!==sid;
  if(document.body.classList.contains('creator-mode'))saveCreatorState();
  if(changed){creatorRequest++;creatorPlanning=false}
  sid=next;
  rememberSession();
  setSessionInUrl(sid,push);
  if(document.body.classList.contains('creator-mode')){restoreCreatorState();renderCreator()}
}
let selectedSessionButton=null;
function focusComposer(){if(matchMedia('(pointer:coarse)').matches||document.visibilityState!=='visible')return;
 if(document.body.classList.contains('creator-mode')&&creatorState.mode==='guided'){let a=document.getElementById('creatorAnswer');if(a)a.focus()}
 else input.focus()}
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
async function copyText(value){
 try{await navigator.clipboard.writeText(value);return true}catch(e){
  let t=document.createElement('textarea');t.value=value;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();
  let ok=false;try{ok=document.execCommand('copy')}catch(e2){}t.remove();return ok
 }
}
function copiedState(button,label){let old=button.textContent;button.textContent=label||'Copied';setTimeout(()=>button.textContent=old,1200)}
let cmoDocs=[];try{cmoDocs=JSON.parse(document.getElementById('cmoDocuments').textContent||'[]')}catch(e){}
function renderDocuments(){let el=document.getElementById('documentList');el.innerHTML='';cmoDocs.forEach(d=>{let b=document.createElement('button');b.type='button';b.className='doc-btn';b.innerHTML='<span class="doc-icon">D</span><span><b>'+esc(d.title)+'</b><small>'+esc(d.summary)+'</small></span><span class="doc-open">›</span>';b.onclick=()=>openDocument(d);el.appendChild(b)})}
let docTrigger=null;function openDocument(d){docTrigger=document.activeElement;docTitle.textContent=d.title;docBody.innerHTML=md(d.body);docModal.hidden=false;closeMenu(false);document.getElementById('docClose').focus()}
function closeDocument(){if(docModal.hidden)return;docModal.hidden=true;if(docTrigger&&docTrigger.focus)docTrigger.focus();docTrigger=null}
function setSidebar(view){let chats=view==='chats';sessionsEl.hidden=!chats;sessionViewbar.hidden=!chats;cmoContext.hidden=chats;chatsTab.classList.toggle('on',chats);companyTab.classList.toggle('on',!chats);chatsTab.setAttribute('aria-selected',String(chats));companyTab.setAttribute('aria-selected',String(!chats))}
function splitUserMedia(text,live){let media=(live||[]).map(a=>({name:String(a.name||'image'),url:String(a.url||'')})),clean=String(text||'');
 clean=clean.replace(/\s*\[attached photo:\s*([^\]]+)\]/gi,(all,name)=>{if(!media.some(a=>a.name===name.trim()))media.push({name:name.trim(),url:''});return ''}).trim();
 return{text:clean,media:media}}
function bubble(role,text,liveMedia){if(empty&&empty.parentNode)empty.remove();let m=document.createElement('div');m.className='msg '+role;
 m.innerHTML='<div class="msg-label">'+(role==='user'?'You':'Hermes')+'</div><div class="bubble"></div>';let b=m.querySelector('.bubble');
 if(role==='agent'){
  b.innerHTML='<div class="md">'+md(text)+'</div><div class="msg-tools"><button type="button" class="copy-all">Copy full response</button></div>';
  b.querySelector('.copy-all').onclick=async e=>{if(await copyText(text))copiedState(e.target)};
  b.querySelectorAll('.md pre').forEach(pre=>{let code=pre.querySelector('code'),button=document.createElement('button');button.type='button';button.className='block-copy';button.textContent='Copy this text';button.onclick=async()=>{if(await copyText(code.textContent))copiedState(button)};pre.appendChild(button)})
 }else{
  let parsed=splitUserMedia(text,liveMedia);
  if(parsed.text){let copy=document.createElement('div');copy.className='msg-text';copy.textContent=parsed.text;b.appendChild(copy)}
  if(parsed.media.length){let media=document.createElement('div');media.className='sent-media';parsed.media.forEach(a=>{let item=document.createElement('div');item.className='sent-media-item';item.title=a.name;
   if(a.url){let img=document.createElement('img');img.src=a.url;img.alt='Uploaded image: '+a.name;item.appendChild(img)}else{let icon=document.createElement('span');icon.className='sent-media-icon';icon.textContent='IMG';item.appendChild(icon)}
   let name=document.createElement('span');name.className='sent-media-name';name.textContent=a.name;item.appendChild(name);media.appendChild(item)});b.appendChild(media)}
 }
 threadEl.appendChild(m);threadEl.scrollTop=threadEl.scrollHeight;return m}
function thinking(){let creator=document.body.classList.contains('creator-mode'),engine=selectedEngine(allSessions.find(s=>s.id===sid)),d=document.createElement('div');d.className='thinking';d.innerHTML='<span class="dots"><i></i><i></i><i></i></span><span>'+(creator?engine+' is drafting with the TimeLabs writing standard…':engine+' is working with live business context…')+'</span>';threadEl.appendChild(d);threadEl.scrollTop=threadEl.scrollHeight;return d}
function notice(text,bad){let n=document.createElement('div');n.className='notice'+(bad?' bad':'');n.textContent=text;threadEl.appendChild(n);threadEl.scrollTop=threadEl.scrollHeight;return n}
async function api(path,opt){let r=await fetch(API+path,opt);let ct=r.headers.get('content-type')||'';
 if((r.status===401||r.status===403)&&ct.indexOf('application/json')<0){location.href='/oauth2/start?rd='+encodeURIComponent(location.pathname+location.search);throw new Error('Sign-in required')}
 let d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||('Request failed ('+r.status+')'));return d}
function engineName(model,short){if(!model||model==='hermes-default')return short?'Auto':'Hermes Auto';if(model==='claude-sonnet-4-6')return short?'Claude 4.6':'Claude Sonnet 4.6';if(model==='MiniMax-M3')return 'MiniMax M3';return String(model)}
function selectedEngine(s){return engineName(s&&s.preferred_model,false)}
function engineKey(s){if(!s||!s.preferred_model)return'auto';if(s.preferred_model==='claude-sonnet-4-6')return'claude';if(s.preferred_model==='MiniMax-M3')return'minimax';return''}
function syncEngineDisplay(s){let selected=selectedEngine(s),last=s&&s.last_model?engineName(s.last_model,false):'';currentEngineBtn.textContent=selected;currentEngineBtn.title='Selected: '+selected+(last?' · Last answer: '+last:'');currentEngineBtn.setAttribute('aria-label','Writing engine: '+selected+'. Change engine')}
function closeSessionMenu(){sessionMenu.hidden=true;sessionMenuTarget=null}
function openSessionMenu(s,trigger,x,y){if(!s)return;sessionMenuTarget=s;sessionMenuTitle.textContent=s.title;sessionMenuEngine.textContent='Selected: '+selectedEngine(s)+(s.last_model?' · Last answer: '+engineName(s.last_model,false):'');sessionArchiveAction.textContent=s.archived?'Restore chat':'Archive chat';sessionOpenAction.hidden=s.id===sid;
 document.querySelectorAll('.session-engine-action').forEach(b=>b.classList.toggle('on',b.dataset.engine===engineKey(s)));sessionMenu.hidden=false;
 let r=trigger&&trigger.getBoundingClientRect?trigger.getBoundingClientRect():null,left=Number.isFinite(x)?x:(r?r.right-sessionMenu.offsetWidth:12),top=Number.isFinite(y)?y:(r?r.bottom+4:12);
 let box=sessionMenu.getBoundingClientRect();sessionMenu.style.left=Math.max(8,Math.min(left,innerWidth-box.width-8))+'px';sessionMenu.style.top=Math.max(8,Math.min(top,innerHeight-box.height-8))+'px';(sessionOpenAction.hidden?sessionRenameAction:sessionOpenAction).focus()}
function renderSessionList(){sessionsEl.innerHTML='';let active=allSessions.filter(s=>!s.archived),archived=allSessions.filter(s=>s.archived),shown=archivedView?archived:active;
 activeChatsBtn.textContent='Active'+(active.length?' · '+active.length:'');archivedChatsBtn.textContent='Archived'+(archived.length?' · '+archived.length:'');activeChatsBtn.classList.toggle('on',!archivedView);archivedChatsBtn.classList.toggle('on',archivedView);
 if(!shown.length){sessionsEl.innerHTML='<div class="session-empty">'+(archivedView?'No archived chats.':'No active chats.')+'</div>';return}
 selectedSessionButton=null;shown.forEach(s=>{let row=document.createElement('div');row.className='session-row';let b=document.createElement('button');b.type='button';b.className='session'+(s.id===sid?' on':'');
  b.innerHTML='<b>'+esc(s.title)+'</b><span class="session-meta"><span>'+s.n+' messages · '+esc((s.updated_at||'').slice(0,16).replace('T',' '))+'</span><em class="session-engine">'+esc(engineName(s.preferred_model,true))+'</em></span>';b.onclick=()=>switchSession(s.id);
  let more=document.createElement('button');more.type='button';more.className='session-more';more.textContent='⋯';more.setAttribute('aria-label','Chat actions for '+s.title);more.onclick=e=>{e.stopPropagation();openSessionMenu(s,more)};
  row.oncontextmenu=e=>{e.preventDefault();openSessionMenu(s,row,e.clientX,e.clientY)};row.appendChild(b);row.appendChild(more);sessionsEl.appendChild(row);if(s.id===sid)selectedSessionButton=b});
 if(selectedSessionButton)selectedSessionButton.scrollIntoView({block:'nearest'})}
async function loadSessions(){
 let gen=++sessionsGen;
 try{
  let d=await api('/sessions');
  if(gen!==sessionsGen)return;
  if(!d.sessions.length){
    let n=await api('/session/new',{method:'POST'});
    if(gen!==sessionsGen)return;
    sid=n.id;
    d=await api('/sessions');
    if(gen!==sessionsGen)return;
  }
  allSessions=d.sessions||[];
  if(allSessions.length&&!allSessions.some(x=>x.id===sid))sid=(allSessions.find(x=>!x.archived)||allSessions[0]).id;
  if(gen!==sessionsGen)return;
  syncSession(sid);
  renderSessionList();
  let cur=allSessions.find(x=>x.id===sid);
  document.getElementById('threadTitle').textContent=cur?cur.title:'New command';syncEngineDisplay(cur);
 }catch(e){sessionsEl.innerHTML='<div class="notice bad">'+esc(e.message)+'</div>'}}
async function manageSession(action){let target=sessionMenuTarget;if(!target)return;if(busy&&target.id===sid){closeSessionMenu();notice('Wait for Hermes to finish before managing this chat.',false);return}
 if(action==='delete'&&!confirm('Permanently delete “'+target.title+'” and all of its messages? This cannot be undone.'))return;
 closeSessionMenu();try{if(action==='delete')await api('/session/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:target.id})});
  else await api('/session/archive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:target.id,archived:!target.archived})});
  let wasCurrent=target.id===sid;await loadSessions();if(wasCurrent&&(action==='delete'||!target.archived)){let next=allSessions.find(s=>!s.archived);if(next)await switchSession(next.id);else await newSession()}
 }catch(e){notice(e.message,true)}}
async function renameSession(){let target=sessionMenuTarget;if(!target)return;let title=prompt('Rename chat',target.title);if(title===null)return;title=title.trim();if(!title){notice('Chat name cannot be empty.',true);return}closeSessionMenu();try{await api('/session/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:target.id,title:title})});await loadSessions()}catch(e){notice(e.message,true)}}
async function copySessionLink(){let target=sessionMenuTarget;if(!target)return;let url=new URL(location.href);url.searchParams.set('session',target.id);closeSessionMenu();if(await copyText(url.toString()))notice('Chat link copied.',false);else notice('Could not copy the chat link.',true)}
async function setSessionEngine(engine){let target=sessionMenuTarget;if(!target)return;closeSessionMenu();try{await api('/session/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:target.id,engine:engine})});await loadSessions();notice('Writing engine set to '+selectedEngine(allSessions.find(s=>s.id===target.id))+'. It applies to the next message.',false)}catch(e){notice(e.message,true)}}
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
async function newSession(){runToken++;archivedView=false;setSidebar('chats');try{let d=await api('/session/new',{method:'POST'});await switchSession(d.id)}catch(e){notice(e.message,true)}}
async function send(){let text=input.value.trim();if(busy||uploading||(!text&&!attachments.length))return;let at=attachments.slice(),runSid=sid,baseline=lastMessageId,accepted=false;attachments=[];renderAttachments();input.value='';autosize();let optimistic=bubble('user',text,at);let token=++runToken;busy=true;syncSendState();statusEl.textContent='Working';let wait=thinking();
 try{let d=await api('/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:runSid,message:text,images:at.map(x=>({path:x.path,name:x.name})),preview_role:accessInfo&&accessInfo.preview?accessInfo.role:null})});accepted=true;
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
function renderAttachments(){attachEl.innerHTML='';attachments.forEach((a,i)=>{let c=document.createElement('div');c.className='attachment';c.innerHTML=(a.url?'<img src="'+a.url+'" alt="">':'')+'<span>'+esc(a.name)+'</span><button type="button" aria-label="Remove">×</button>';c.querySelector('button').onclick=()=>{attachments.splice(i,1);renderAttachments()};attachEl.appendChild(c)});if(document.body.classList.contains('creator-mode')){saveCreatorState();if(creatorState.mode==='guided'&&!creatorPlanning)renderCreator()}}
async function loadEvents(){try{let d=await api('/events'),el=document.getElementById('events');el.innerHTML='';(d.events||[]).slice(0,8).forEach(x=>{let v=document.createElement('div');v.className='event';v.innerHTML='<b>'+esc((x.app||'Labs')+' · '+(x.kind||'activity').replace(/_/g,' '))+'</b><p>'+esc(x.detail||'')+'</p><time>'+esc((x.created_at||'').slice(0,16).replace('T',' '))+'</time>';el.appendChild(v)});if(!el.children.length)el.innerHTML='<div class="event"><p>No recent activity.</p></div>'}catch(e){}}
function autosize(){input.style.height='auto';input.style.height=Math.min(input.scrollHeight,150)+'px'}
function creatorStorageKey(){return'labs_creator_interview_'+sid}
function saveCreatorState(){
 try{sessionStorage.setItem(creatorStorageKey(),JSON.stringify({state:creatorState,attachments:attachments.map(a=>({path:a.path,name:a.name}))}))}catch(e){}
}
function restoreCreatorState(){
 let packed=null;try{packed=JSON.parse(sessionStorage.getItem(creatorStorageKey())||'null')}catch(e){}
 let s=packed&&packed.state;if(!s||typeof s!=='object'){creatorState=freshCreatorState();attachments=[];renderAttachments();return}
 let channel=CREATOR_CHANNELS[s.channel]?s.channel:null,mode=s.mode==='direct'?'direct':'guided';
 let phase=['channel','source','question','ready'].includes(s.phase)?s.phase:'channel';if(!channel&&phase!=='channel')phase='channel';
 let answers=(Array.isArray(s.answers)?s.answers:[]).filter(a=>a&&/^[a-z][a-z0-9_]{0,39}$/.test(String(a.id||''))&&String(a.answer||'').trim()).slice(0,5).map(a=>({id:String(a.id),question:String(a.question||a.id).slice(0,220),answer:String(a.answer).slice(0,2400),help:String(a.help||'').slice(0,240),input:['choice','text','long_text'].includes(a.input)?a.input:'long_text',options:(Array.isArray(a.options)?a.options:[]).slice(0,5).map(String),required:a.required!==false}));
 let q=s.question&&typeof s.question==='object'?{status:'question',id:String(s.question.id||'').slice(0,40),question:String(s.question.question||'').slice(0,220),help:String(s.question.help||'').slice(0,240),input:['choice','text','long_text'].includes(s.question.input)?s.question.input:'text',options:(Array.isArray(s.question.options)?s.question.options:[]).slice(0,5).map(String),required:s.question.required!==false}:null;
 if(phase==='question'&&(!q||!/^[a-z][a-z0-9_]{0,39}$/.test(q.id)))phase='ready';
 creatorState={mode:mode,phase:phase,channel:channel,answers:answers,question:q,notice:String(s.notice||'').slice(0,180),draft:String(s.draft||'').slice(0,2400)};
 attachments=(packed&&Array.isArray(packed.attachments)?packed.attachments:[]).filter(a=>a&&a.path&&a.name).slice(0,8).map(a=>({path:String(a.path),name:String(a.name),url:''}));renderAttachments()
}
function creatorSourceQuestion(){return{id:'source',question:'What are we working from?',help:'Attach the real photos or video stills, or describe the message, product, event, or idea. Rough notes are better than polished instructions.',input:'long_text',options:[],required:true}}
function questionFromAnswer(a){return{status:'question',id:a.id,question:a.question,help:a.help||'Change this answer, then continue.',input:['choice','text','long_text'].includes(a.input)?a.input:'long_text',options:Array.isArray(a.options)?a.options:[],required:a.required!==false}}
function stripSourceMedia(value){return String(value||'').split('\n').filter(line=>!/^Attached media:/i.test(line.trim())).join('\n').trim()}
function creatorTrail(){let rows=[];if(creatorState.channel)rows.push('<div class="creator-trail-row"><span><b>Destination</b> · '+esc(CREATOR_CHANNELS[creatorState.channel].label)+'</span><button type="button" data-creator-destination>Edit</button></div>');creatorState.answers.forEach((a,i)=>rows.push('<div class="creator-trail-row"><span><b>'+esc(a.question)+'</b> · '+esc(a.answer)+'</span><button type="button" data-creator-edit="'+i+'">Edit</button></div>'));return rows.length?'<div class="creator-trail">'+rows.join('')+'</div>':''}
function bindCreatorTrail(){document.querySelectorAll('[data-creator-edit]').forEach(b=>b.onclick=()=>editCreatorAnswer(Number(b.dataset.creatorEdit)));let destination=document.querySelector('[data-creator-destination]');if(destination)destination.onclick=changeCreatorDestination}
function changeCreatorDestination(){if(creatorState.answers.length&&!confirm('Changing the destination will re-check the brief from the start. Continue?'))return;creatorRequest++;creatorPlanning=false;creatorState.phase='channel';creatorState.channel=null;creatorState.answers=[];creatorState.question=null;creatorState.draft='';creatorState.notice='';saveCreatorState();renderCreator();focusComposer()}
function editCreatorAnswer(index){if(!Number.isInteger(index)||index<0||index>=creatorState.answers.length)return;let later=creatorState.answers.length-index-1;if(later&&!confirm('Editing this answer will re-check the '+later+' answer'+(later===1?'':'s')+' after it. Continue?'))return;creatorRequest++;creatorPlanning=false;let answer=creatorState.answers[index];creatorState.answers=creatorState.answers.slice(0,index);creatorState.phase=answer.id==='source'?'source':'question';creatorState.question=answer.id==='source'?creatorSourceQuestion():questionFromAnswer(answer);creatorState.draft=answer.id==='source'?stripSourceMedia(answer.answer):answer.answer;creatorState.notice=later?'Later questions will be asked again so the brief stays consistent.':'';saveCreatorState();renderCreator();focusComposer()}
function backCreatorStep(){if(creatorState.answers.length){editCreatorAnswer(creatorState.answers.length-1);return}changeCreatorDestination()}
function setCreatorStatus(text,bad){let e=document.getElementById('creatorStatus');e.textContent=text||'';e.classList.toggle('bad',!!bad)}
function creatorProgress(){if(creatorState.mode==='direct'||creatorState.phase==='ready')return 100;if(creatorState.phase==='channel')return 14;if(creatorState.phase==='source')return 30;return Math.min(88,30+creatorState.answers.length*14)}
function renderCreator(){
 if(!document.body.classList.contains('creator-mode'))return;
 let step=document.getElementById('creatorStep'),label=document.getElementById('creatorStepLabel'),progress=document.getElementById('creatorProgress'),direct=document.getElementById('creatorDirectBtn'),draft=document.getElementById('creatorDraftBtn');
 progress.style.width=creatorProgress()+'%';composeWrap.classList.toggle('creator-guided',creatorState.mode==='guided');setCreatorStatus(creatorState.notice||'',false);
 if(creatorState.mode==='direct'){
  label.textContent='Direct workspace';direct.textContent='Guide me one question at a time';draft.hidden=true;
  step.innerHTML='<div class="creator-direct"><div><b>Ask Hermes in your own words</b><span>Human-writing rules and channel formatting still apply to every content draft.</span></div></div>';
  return
 }
 direct.textContent='Ask Hermes directly';draft.hidden=creatorState.phase==='channel'||creatorPlanning;
 if(creatorPlanning){label.textContent='Hermes is choosing the next question';step.innerHTML=creatorTrail()+'<p class="creator-question">One moment…</p><p class="creator-help">Hermes is checking what you already supplied so it only asks what still matters.</p><div class="creator-actions"><button type="button" class="creator-action" id="creatorBackBtn">Back and edit</button></div>';bindCreatorTrail();document.getElementById('creatorBackBtn').onclick=backCreatorStep;return}
 if(creatorState.phase==='channel'){
  label.textContent='Question 1 · Destination';step.innerHTML='<h3 class="creator-question">Where will this content go?</h3><p class="creator-help">Choose the final destination. The questions and paste-ready format will adapt to it.</p><div class="creator-choices" id="creatorChoices"></div>';
  let choices=document.getElementById('creatorChoices');Object.entries(CREATOR_CHANNELS).forEach(([key,c])=>{let b=document.createElement('button');b.type='button';b.className='creator-choice';b.textContent=c.label;b.onclick=()=>{creatorState.channel=key;creatorState.phase='source';creatorState.question=creatorSourceQuestion();creatorState.draft='';creatorState.notice='';saveCreatorState();renderCreator();focusComposer()};choices.appendChild(b)});return
 }
 if(creatorState.phase==='ready'){
  label.textContent='Brief ready · Review';step.innerHTML='<h3 class="creator-question">Hermes has enough for a first draft.</h3><p class="creator-help">Review or edit any answer. Changing an earlier answer safely re-checks everything after it.</p>'+creatorTrail()+'<div class="creator-actions" style="margin-top:9px"><button type="button" class="creator-action" id="creatorBackBtn">Back</button><button type="button" class="creator-action primary" id="creatorGenerateBtn">Generate '+esc(CREATOR_CHANNELS[creatorState.channel].label)+'</button></div>';
  bindCreatorTrail();document.getElementById('creatorGenerateBtn').onclick=generateCreatorDraft;document.getElementById('creatorBackBtn').onclick=backCreatorStep;return
 }
 let q=creatorState.phase==='source'?creatorSourceQuestion():creatorState.question;if(!q){creatorState.phase='ready';renderCreator();return}
 label.textContent='Question '+(creatorState.answers.length+2)+' · '+(creatorState.phase==='source'?'Source':'Brief');
 step.innerHTML=creatorTrail()+'<h3 class="creator-question">'+esc(q.question)+'</h3><p class="creator-help">'+esc(q.help||'Answer with confirmed information only.')+'</p>'+(q.input==='choice'?'<div class="creator-choices" id="creatorChoices"></div><div class="creator-actions"><button type="button" class="creator-action" id="creatorBackBtn">Back</button>'+(q.required===false?'<button type="button" class="creator-action" id="creatorSkipBtn">Skip</button>':'')+'</div>':'<div class="creator-answer"><textarea id="creatorAnswer" maxlength="2400" placeholder="Type a rough answer…">'+esc(creatorState.draft||'')+'</textarea><div class="creator-actions"><button type="button" class="creator-action" id="creatorBackBtn">Back</button>'+(q.id==='source'?'<button type="button" class="creator-action" id="creatorMediaBtn">＋ Add media</button>':'')+(q.required===false?'<button type="button" class="creator-action" id="creatorSkipBtn">Skip</button>':'')+'<button type="button" class="creator-action primary" id="creatorContinueBtn">Continue</button></div></div>');
 bindCreatorTrail();document.getElementById('creatorBackBtn').onclick=backCreatorStep;
 if(q.input==='choice'){let choices=document.getElementById('creatorChoices'),options=q.id==='community'?communityOptions(q.options):q.options||[];options.forEach(option=>{let b=document.createElement('button');b.type='button';b.className='creator-choice';b.textContent=option;b.onclick=()=>{if(q.id==='community'&&option==='Other subreddit'){creatorState.question={status:'question',id:'community',question:'Which subreddit exactly?',help:'Type the real community name, for example r/SeikoMods. This will be remembered as a future suggestion.',input:'text',options:[],required:true};creatorState.draft='';saveCreatorState();renderCreator();focusComposer();return}answerCreatorQuestion(option)};choices.appendChild(b)});let skip=document.getElementById('creatorSkipBtn');if(skip)skip.onclick=()=>answerCreatorQuestion('Not needed')}
 else{let a=document.getElementById('creatorAnswer'),go=document.getElementById('creatorContinueBtn');let sync=()=>{creatorState.draft=a.value;go.disabled=!a.value.trim()&&!(q.id==='source'&&attachments.length);saveCreatorState()};a.oninput=sync;sync();go.onclick=()=>answerCreatorQuestion(a.value);let media=document.getElementById('creatorMediaBtn');if(media)media.onclick=()=>document.getElementById('fileInput').click();let skip=document.getElementById('creatorSkipBtn');if(skip)skip.onclick=()=>answerCreatorQuestion('Not needed')}
 }
function answerCreatorQuestion(value){
 let q=creatorState.phase==='source'?creatorSourceQuestion():creatorState.question,answer=String(value||'').trim();
 if(q.id==='source'){answer=stripSourceMedia(answer);if(attachments.length){let media='Attached media: '+attachments.map(a=>a.name).join(', ');answer=answer?answer+'\n'+media:media}}
 if(q.id==='community'){answer=rememberSubreddit(answer);if(!normalizeSubreddit(answer)){setCreatorStatus('Enter a subreddit such as r/SeikoMods.',true);return}}
 if(!answer){setCreatorStatus('Add a rough answer'+(q.id==='source'?' or attach media':'')+' before continuing.',true);return}
 creatorState.answers=creatorState.answers.filter(a=>a.id!==q.id);creatorState.answers.push({id:q.id,question:q.question,answer:answer,help:q.help||'',input:q.input||'text',options:Array.isArray(q.options)?q.options:[],required:q.required!==false});creatorState.draft='';creatorState.notice='';saveCreatorState();requestCreatorQuestion()
}
function clientCreatorFallback(){
 if(creatorState.answers.some(a=>a.id==='outcome'))return{status:'ready',reason:'The brief has enough detail.'};
 return{status:'question',id:'outcome',question:'What should someone do after reading it?',help:'Choose the real next step. No call to action is also valid.',input:'choice',options:['Reply or comment','Send a DM','Visit a link','No action — just read'],required:true}
}
async function requestCreatorQuestion(){
 let token=++creatorRequest,target=sid;creatorPlanning=true;renderCreator();
 try{let d=await api('/creator/interview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:target,channel:creatorState.channel,answers:creatorState.answers,images:attachments.map(a=>({path:a.path,name:a.name}))})});if(token!==creatorRequest||target!==sid)return;
  creatorState.notice=d.notice||'';if(d.status==='ready'){creatorState.phase='ready';creatorState.question=null}else{creatorState.phase='question';creatorState.question=d}
 }catch(e){if(token!==creatorRequest||target!==sid)return;let d=clientCreatorFallback();creatorState.notice='Connection was interrupted, so the guided flow used a safe next question.';if(d.status==='ready'){creatorState.phase='ready';creatorState.question=null}else{creatorState.phase='question';creatorState.question=d}}
 finally{if(token===creatorRequest&&target===sid){creatorPlanning=false;saveCreatorState();renderCreator();focusComposer()}}
}
function draftCreatorNow(){
 if(creatorState.phase==='source'||creatorState.phase==='question'){
  let a=document.getElementById('creatorAnswer'),value=a?a.value:'';if(value.trim()||(creatorState.phase==='source'&&attachments.length)){answerCreatorQuestion(value);creatorRequest++;creatorPlanning=false}
 }
 if(!creatorState.answers.some(a=>a.id==='source')){setCreatorStatus('Add the source first so Hermes has something real to work from.',true);return}
 creatorState.phase='ready';creatorState.question=null;creatorState.notice='';saveCreatorState();renderCreator()
}
function generateCreatorDraft(){
 if(busy||uploading){setCreatorStatus('Wait for the current upload or Hermes task to finish.',true);return}
 let c=CREATOR_CHANNELS[creatorState.channel],lines=[c.route,'Use this completed Creator interview. Draft only from these answers; omit anything unknown.'];creatorState.answers.forEach(a=>{lines.push(a.question+' '+a.answer)});input.value=lines.join('\n');autosize();creatorState.mode='direct';creatorState.notice='';saveCreatorState();renderCreator();send()
}
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{closeMenu(false);input.value=b.dataset.prompt;send()});
document.getElementById('newBtn').onclick=newSession;
activeChatsBtn.onclick=()=>{archivedView=false;closeSessionMenu();renderSessionList()};archivedChatsBtn.onclick=()=>{archivedView=true;closeSessionMenu();renderSessionList()};
sessionArchiveAction.onclick=()=>manageSession('archive');sessionDeleteAction.onclick=()=>manageSession('delete');
sessionOpenAction.onclick=()=>{let target=sessionMenuTarget;closeSessionMenu();if(target)switchSession(target.id)};sessionRenameAction.onclick=renameSession;sessionCopyLinkAction.onclick=copySessionLink;
document.querySelectorAll('.session-engine-action').forEach(b=>b.onclick=()=>setSessionEngine(b.dataset.engine));currentEngineBtn.onclick=()=>openSessionMenu(allSessions.find(s=>s.id===sid),currentEngineBtn);
chatsTab.onclick=()=>setSidebar('chats');companyTab.onclick=()=>setSidebar('company');document.getElementById('docClose').onclick=closeDocument;
docModal.onclick=e=>{if(e.target===docModal)closeDocument()};renderDocuments();setSidebar('company');
document.getElementById('creatorNewBtn').onclick=async()=>{attachments=[];input.value='';autosize();await newSession();creatorState=freshCreatorState();saveCreatorState();renderAttachments();renderCreator()};
document.getElementById('creatorDirectBtn').onclick=()=>{creatorState.mode=creatorState.mode==='guided'?'direct':'guided';saveCreatorState();renderCreator();focusComposer()};
document.getElementById('creatorDraftBtn').onclick=draftCreatorNow;
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
 closeSessionMenu();
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
document.addEventListener('pointerdown',e=>{if(!sessionMenu.hidden&&!sessionMenu.contains(e.target))closeSessionMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!sessionMenu.hidden){closeSessionMenu();return}if(e.key==='Escape'&&!docModal.hidden){closeDocument();return}if(e.key==='Escape'&&(rail.classList.contains('open')||contextRail.classList.contains('open')))closeMenu()});
window.addEventListener('resize',closeSessionMenu);sessionsEl.addEventListener('scroll',closeSessionMenu,{passive:true});
for(let mq of [leftMq,contextMq]){if(mq.addEventListener)mq.addEventListener('change',()=>closeMenu(false));else mq.addListener(()=>closeMenu(false))}
syncDrawers();loadAccessMode();
loadSessions().then(loadHistory);loadEvents();focusComposer();
})();
""" + WHOAMI_JS + r"""
</script></body></html>"""
    doc = (doc.replace("__CMO_IG_FOLLOWERS__", f"{instagram['followers']:,}")
          .replace("__CMO_IG_POSTS__", f"{instagram['posts']:,}")
          .replace("__CMO_IG_TOP_VIEWS__", f"{instagram['top_views']:,}")
          .replace("__CMO_IG_TOP_INTERACTIONS__", f"{instagram['top_interactions']:,}"))
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, OUT)
    os.system(f"chown www-data:www-data {OUT}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
