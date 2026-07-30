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

  /* Header — sticky, and it has to stay compact to earn that: it is pinned
     for the entire session, so every row added here is a row permanently
     taken off the queue. Controls only. Anything that is read rather than
     used belongs in #queue-extra, below, which scrolls. */
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
  .tabs{display:flex;gap:4px;margin-top:11px;
    border-bottom:1px solid var(--border);}
  .tabs button{flex:none;border:none;background:none;color:var(--muted);font:inherit;
    font-size:14px;font-weight:650;padding:8px 2px;margin-right:16px;cursor:pointer;
    border-bottom:2px solid transparent;min-height:38px;}
  .tabs button.on{color:var(--ink);border-bottom-color:var(--accent);}
  .pane{display:none;} .pane.on{display:block;}

  /* Stage filter stays upfront — it's the question the supplier asks every
     time they open this ("what needs me?"), so it shouldn't cost a tap to
     see. Icons carry it so four stages fit a phone width without wrapping;
     the label is hidden on narrow screens but kept for screen readers. */
  #queue-controls{margin-top:16px;}
  /* Section tabs: one per stage, scrolling sideways when they don't fit
     rather than shrinking labels away — the stage names are the whole point. */
  .stagebar{display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch;
    scrollbar-width:none;padding-bottom:2px;}
  .stagebar::-webkit-scrollbar{display:none;}
  .stg{flex:none;display:inline-flex;align-items:center;gap:7px;white-space:nowrap;
    border:1px solid var(--border);background:var(--card);color:var(--muted);
    border-radius:999px;padding:8px 14px;font:inherit;font-size:13px;font-weight:650;
    cursor:pointer;transition:background .12s,color .12s,border-color .12s;}
  .stg b{font-size:12px;font-weight:800;line-height:1;background:var(--card-2);
    color:var(--muted);border-radius:999px;padding:1px 7px;min-width:8px;text-align:center;}
  .stg .lbl{font-size:13px;}
  .stg.on{background:var(--ink);color:var(--bg);border-color:var(--ink);}
  .stg.on b{background:rgba(255,255,255,.22);color:#fff;}

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
  .syncline{margin-top:8px;}

  /* Money, always visible — the owner's standing ask that the queue show
     totals rather than making him open something else to find them. Costs
     stay in the supplier's own currency; only "you owe" is converted, since
     that's the figure that means something on our side of the table. */
  /* A wrapping grid, not a five-across row. As a flex row each cell got
     about 49px of content on a 390px phone while "₹34,500.00" needs ~85px,
     so the amounts spilled out of their cells. auto-fit gives three columns
     on a phone and all five on a desktop without a second breakpoint.
     Dividers are drawn by each cell rather than by a border-coloured
     background behind a 1px gap. Five cells never divide evenly into three
     columns, and with the old technique that leftover slot showed the bare
     background as a grey tile sitting in the strip, which read as something
     having failed to load. Cells paint their own right/bottom rule, so an
     absent cell is simply absent; the container's overflow:hidden trims the
     rules that fall on the outer edge. */
  .moneybar{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));
    gap:0;margin-top:10px;background:var(--card);
    border:1px solid var(--border);border-radius:var(--r-s);overflow:hidden;}
  .moneybar:empty{display:none;}
  .mcell{min-width:0;padding:8px 11px;background:var(--card);
    box-shadow:1px 0 0 var(--border),0 1px 0 var(--border);}
  /* The last cell must not rule off its right edge: when the row is short
     that rule has nothing on the other side of it and draws one wall of a
     box around the empty slot. The row rule above the gap is fine — it just
     reads as the row divider running the full width. */
  .mcell:last-child{box-shadow:0 1px 0 var(--border);}
  .mcell .ml{font-size:10.5px;color:var(--muted);white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;}
  /* Never ellipsise money — "₹34,50…" is worse than a smaller number, so
     the value shrinks to fit rather than being cut off. That was the stated
     intent from the start but nothing implemented it: nowrap + overflow
     hidden is precisely "cut off", and a phone showed ₹1,44,300.00 sliced
     through the last digit. Three columns on a 390px phone leave 105px of
     cell once the padding below is applied, and 13px tabular digits put a
     13-character amount (₹12,44,300.00) inside that. */
  .mcell .mv{font-size:14px;font-weight:750;color:var(--ink);
    font-variant-numeric:tabular-nums;line-height:1.25;
    white-space:nowrap;overflow:hidden;}
  @media(max-width:559px){ .mcell{padding:8px 8px;} .mcell .mv{font-size:13px;} }
  @media(min-width:560px){ .mcell .mv{font-size:15px;} }
  .mcell.owe .mv{color:var(--accent);}

  /* Phone: the search box is the loser in a three-up row. Two flex:none
     buttons leave it about 120px — six characters of a placeholder that is
     six words long — so below the phone breakpoint the search takes its own
     line and the two buttons split the one under it. */
  @media(max-width:520px){
    .fbar{flex-wrap:wrap;}
    .ssearch{flex:1 1 100%;}
    .fbar .fbtn{flex:1 1 0;justify-content:center;}
  }

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
  /* "nothing to filter by yet" inside a chip row. Without this the text
     rendered at full body size and weight, louder than the chips it stands
     in for. */
  .fnone{font-size:12.5px;color:var(--muted);padding:7px 2px;}

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
  .ocost{display:flex;align-items:center;gap:7px;margin-top:8px;flex-wrap:wrap;}
  .ocost .cl{font-size:11px;color:var(--muted);}
  .ocost b{font-size:14px;font-weight:750;color:var(--ink);font-variant-numeric:tabular-nums;}
  .ocost button{border:none;background:none;color:var(--accent);font:inherit;font-size:12px;
    font-weight:650;cursor:pointer;padding:4px 2px;min-height:30px;}
  .ocost .addcost{border:1px dashed var(--border-2);border-radius:var(--r-s);
    padding:5px 10px;color:var(--muted);}
  .ocost .addcost:hover{border-color:var(--accent);color:var(--accent);}
  .ocost .defc{font-size:11px;color:var(--muted);}
  .ocost.locked b{color:var(--muted);}
  /* Batches. These rules were lost once already: they sat inside a block of
     dead Shipments CSS that got deleted wholesale, and the tab rendered as
     bare bullets and stacked buttons until it was noticed on screen. Kept
     here, after .owedcard, well away from anything retired. */
  .bcard{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:15px 16px;margin-bottom:11px;box-shadow:var(--shadow);}
  .bcard.ack{border-color:var(--good);}
  .bcard.flash{animation:bflash 2.2s ease;}
  @keyframes bflash{0%,100%{box-shadow:var(--shadow);}
    15%,60%{box-shadow:0 0 0 3px var(--accent-bg),var(--shadow);}}
  @media (prefers-reduced-motion:reduce){ .bcard.flash{animation:none;} }
  .btop{display:flex;align-items:flex-start;gap:12px;}
  .bno{font-size:14.5px;font-weight:750;color:var(--ink);letter-spacing:-.01em;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}
  .bmeta{font-size:11.5px;color:var(--muted);margin-top:3px;}
  .bright{margin-left:auto;text-align:right;flex:none;}
  .btot{font-size:19px;font-weight:800;color:var(--ink);letter-spacing:-.02em;
    font-variant-numeric:tabular-nums;line-height:1.15;}
  .bstat{display:inline-block;font-size:10px;font-weight:750;text-transform:uppercase;
    letter-spacing:.05em;padding:3px 8px;border-radius:999px;margin-top:5px;}
  .bstat.draft{color:var(--muted);background:var(--card-2);}
  .bstat.ok{color:var(--good);background:var(--good-bg);}
  .blines{list-style:none;margin:13px 0 0;padding:12px 0 0;
    border-top:1px solid var(--border);}
  .blines li{display:flex;align-items:baseline;gap:12px;padding:7px 0;font-size:13px;}
  .blines li + li{border-top:1px solid var(--border);}
  .blines li span{flex:1;min-width:0;color:var(--muted);overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .blines li b{flex:none;color:var(--ink);font-weight:650;
    font-variant-numeric:tabular-nums;}
  .blines li.ship span,.blines li.ship b{color:var(--ink);}
  .blines li.paid span,.blines li.paid b{color:var(--good);}
  .bnote{font-size:12.5px;color:var(--muted);margin-top:11px;padding-left:11px;
    border-left:2px solid var(--border-2);}
  .backby{font-size:11.5px;color:var(--good);margin-top:9px;}
  .bact{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap;
    padding-top:13px;border-top:1px solid var(--border);}
  .bact button{flex:none;min-height:38px;padding:0 14px;border-radius:var(--r-s);
    border:1px solid var(--border-2);background:var(--card);color:var(--ink);
    font:inherit;font-size:13px;font-weight:650;cursor:pointer;white-space:nowrap;
    display:inline-flex;align-items:center;justify-content:center;}
  .bact button:hover{border-color:var(--accent);color:var(--accent);}
  .bact button.go{background:var(--ink);color:var(--bg);border-color:var(--ink);}
  .bact button.go:hover{opacity:.9;color:var(--bg);}
  .bact button.danger{color:var(--bad);border-color:var(--border);margin-left:auto;}
  .bact button.danger:hover{border-color:var(--bad);color:var(--bad);}
  .btrk{display:flex;align-items:center;gap:7px;margin-top:9px;flex-wrap:wrap;}
  .btrk .chip{font-size:11.5px;font-weight:650;color:var(--ink);background:var(--card-2);
    border-radius:6px;padding:3px 8px;word-break:break-all;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}
  .btrk button{border:none;background:none;color:var(--accent);font:inherit;font-size:12px;
    font-weight:650;cursor:pointer;padding:4px 2px;min-height:30px;}

  /* How the numbers are worked out. Collapsed by default — it's reference,
     not something to read every visit — but present, because the owner
     asked for the calculation to be explainable rather than trusted. */
  .helpbox{margin-top:10px;}
  .helpbox summary{cursor:pointer;font-size:12px;color:var(--muted);
    padding:6px 0;list-style:none;}
  .helpbox summary::-webkit-details-marker{display:none;}
  .helpbox summary::before{content:'?';display:inline-flex;align-items:center;
    justify-content:center;width:15px;height:15px;border-radius:50%;
    border:1px solid var(--border-2);font-size:10px;font-weight:700;margin-right:6px;
    vertical-align:-2px;}
  .helpbox[open] summary{color:var(--ink);}
  .helpbox .hb{background:var(--card);border:1px solid var(--border);
    border-radius:var(--r-s);padding:12px 14px;font-size:12.5px;line-height:1.65;
    color:var(--muted);}
  .helpbox .hb b{color:var(--ink);font-weight:650;}
  .helpbox .hb ul{margin:6px 0 0;padding-left:18px;}
  .helpbox .hb li{margin:3px 0;}
  .ocost .lockic svg{width:13px;height:13px;stroke:var(--muted);fill:none;stroke-width:1.9;
    stroke-linecap:round;stroke-linejoin:round;display:block;}
  /* .trkform is shared: it is what an inline cost edit turns into. Per-build
     tracking is gone, so its .otrk chip rules went with it. */
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
  .fbtn.newbatch{border-color:var(--accent);color:var(--accent);background:var(--accent-bg);}
  .fbtn.newbatch.on{background:var(--accent);color:#fff;border-color:var(--accent);}
  /* Says what to do while picking. Selection used to live behind the Filters
     panel, where nobody would look for it — this replaces that with a mode
     that announces itself. */
  .pickhint{display:none;align-items:center;gap:8px;margin-top:10px;padding:9px 12px;
    background:var(--accent-bg);border:1px solid var(--accent);border-radius:var(--r-s);
    font-size:12.5px;color:var(--accent);font-weight:650;}
  .pickhint.on{display:flex;}
  .pickhint .sp{flex:1;}
  .pickhint span:first-child{min-width:0;}
  .pickhint button{flex:none;white-space:nowrap;
    border:none;background:none;color:var(--accent);font:inherit;
    font-size:12.5px;font-weight:750;cursor:pointer;text-decoration:underline;
    padding:4px 2px;min-height:30px;}
  .bulkbar b{font-size:13.5px;}
  .bulkbar .sp{flex:1;}
  .bulkbar button{min-height:38px;border:1px solid rgba(255,255,255,.28);background:transparent;
    color:var(--bg);border-radius:var(--r-s);padding:0 12px;font:inherit;font-size:13px;
    font-weight:650;cursor:pointer;}
  .bulkbar button.primary{background:var(--bg);color:var(--ink);border-color:var(--bg);}

  /* the "you owe" summary at the top of the Batches tab */
  .owedcard{background:var(--card);border:1px solid var(--accent);border-radius:var(--r);
    box-shadow:var(--shadow);padding:16px 18px;margin-bottom:16px;}
  .owedtop{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;}
  .owedtop b{font-size:26px;font-weight:800;color:var(--ink);letter-spacing:-.02em;
    font-variant-numeric:tabular-nums;}
  .owedtop span{font-size:11.5px;color:var(--muted);text-transform:uppercase;
    letter-spacing:.05em;font-weight:650;}
  .owedbar{display:flex;gap:16px;margin-top:10px;flex-wrap:wrap;}
  .owedbar .b{font-size:12.5px;color:var(--muted);}
  .owedbar .b b{color:var(--ink);font-weight:650;}
  .sempty{text-align:center;color:var(--muted);font-size:14px;line-height:1.6;padding:44px 20px;}
  .sempty.small{padding:20px;font-size:13px;}
  .sempty svg{width:40px;height:40px;stroke:var(--border-2);fill:none;stroke-width:1.4;margin-bottom:12px;}
  .sfoot{text-align:center;color:var(--muted);font-size:11.5px;padding:26px 0 34px;}

  /* ------------------------------------------------------------------
     Desktop density. Keep this block LAST: a media query carries no extra
     specificity, so anything it overrides must be declared above it or the
     plain rule simply wins. That is how the old copy of this block ended up
     unable to zero .fpanel's margin-top.

     On a phone the stage cards are correctly-sized tap targets; stretched
     across a laptop they became four ~450px boxes that ate the whole first
     screen before a single build appeared. Above this breakpoint they
     collapse to one segmented row sharing a line with search.

     The breakpoint is 1000px and not 760px because the row genuinely does
     not fit at 760: the four segments need ~450px and the search plus its
     two buttons need ~460px, against ~728px of content width. At 760 the
     result was a segmented bar alone on one line and the search hanging off
     the right edge of the next — the single-line layout this block exists
     to produce only actually began above ~950px.
     ------------------------------------------------------------------ */
  @media(min-width:1000px){
    #queue-controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px;
      margin-top:16px;}
    /* The five section names plus a running count don't fit on one line beside
       the search, so the tabs take their own full-width row (still pills) and
       the search sits below. */
    .stagebar{flex:1 1 100%;}
    .fbar{margin-top:0;flex:1 1 100%;min-width:0;}
    .ssearch{flex:1 1 220px;width:auto;min-width:150px;max-width:220px;
      font-size:13.5px;padding:8px 12px;}
    .fbtn{min-height:36px;font-size:12.5px;}
    /* full-width rows under the control line, not content-width flex items.
       Every direct child of #queue-controls that isn't part of the control
       line itself belongs here — miss one and it squeezes in beside the
       search box instead of taking its own row. */
    .fpanel,.pickhint,.moneybar,.syncline,.helpbox{flex-basis:100%;margin-top:0;}
    /* The money strip is its own band, not another control, so it gets the
       same 16px the controls get from the tabs. The flex row-gap supplies
       10px of that; 6 more makes the rhythm one number instead of three. */
    .moneybar{margin-top:6px;}
    /* "Updated 04:55" reads as a footnote to the whole header, so it sits
       last regardless of where it falls in the markup */
    .syncline{order:99;}
  }
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

var STATUSES=[], PIPELINE=[], LABELS={}, ORDERS=[], filter='pending', q='', openDetail={}, sort='newest', onlyPhotos=false, IS_ADMIN=false;
var caseF='', moveF='', selectMode=false, SEL={};

/* The five sections, in order. Each stage IS its own section now — a build
   sits in exactly one, and the button on its card walks it to the next. */
function stLabel(k){return LABELS[k]||k;}
function nextOf(st){
  var i=PIPELINE.indexOf(st);
  if(i<0||i>=PIPELINE.length-1)return null;   // last stage or off-pipeline
  return PIPELINE[i+1];
}
/* The section a build belongs to is simply its stage. */
function bucket(o){return o.status;}
function matches(o){
  if(onlyPhotos&&!o.photos)return false;
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
  if(sort!=='newest')n++;
  if(q)n++;
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
  if(!vals.length){ el.innerHTML='<span class="fnone">None recorded yet</span>'; return; }
  el.innerHTML=vals.map(function(v){
    return '<button class="schip'+(v===cur?' on':'')+'" data-v="'+esc(v)+'">'+esc(v)+'</button>';
  }).join('');
  el.querySelectorAll('.schip').forEach(function(b){
    b.onclick=function(){ setter(b.dataset.v===cur?'':b.dataset.v); render(); };
  });
}
function sortOrders(list){
  var c=list.slice();
  if(sort==='newest')c.sort(function(a,b){return b.id-a.id;});
  else if(sort==='oldest')c.sort(function(a,b){return a.id-b.id;});
  else if(sort==='stage')c.sort(function(a,b){
    return STATUSES.indexOf(a.status)-STATUSES.indexOf(b.status)||a.id-b.id;});
  /* Sorting by price uses the default rate for anything unpriced, because
     that IS what it will cost — sorting an unpriced build to the bottom as
     a zero would misrepresent it. */
  else if(sort==='pricehigh'||sort==='pricelow'){
    var dir = sort==='pricehigh' ? -1 : 1;
    c.sort(function(a,b){
      var pa=a.supplier_cost!=null?toINR(a.supplier_cost,a.supplier_cost_ccy):(a.default_cost||0);
      var pb=b.supplier_cost!=null?toINR(b.supplier_cost,b.supplier_cost_ccy):(b.default_cost||0);
      return (pa-pb)*dir || a.id-b.id;
    });
  }
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
var photoOrderId=0;
async function uploadSupplierPhoto(id,file){
  var fd=new FormData();fd.append('image',file,file.name||('photo-'+Date.now()+'.jpg'));
  var r=await fetch(API+'/upload',{method:'POST',body:fd}), d=await r.json();
  if(!r.ok)throw new Error(d.error||'Upload failed');
  await jpost('/supplier/photos/update',{id:id,photo_paths:[d.path]});
}
async function removeSupplierPhoto(o){
  if(!o.photos){toast('This order has no images');return;}
  var raw=prompt('Image number to remove (1–'+o.photos+'):',String(o.photos));
  if(raw===null)return;
  var n=parseInt(raw,10);
  if(!(n>=1&&n<=o.photos)){toast('Enter a number from 1 to '+o.photos);return;}
  await jpost('/supplier/photos/update',{id:o.id,remove:[n-1]});
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

/* Cost sits on the card itself rather than behind a menu — it's the one
   field the supplier fills in for every single build, so it shouldn't cost
   a tap to reach. Once the bill carrying it is acknowledged it goes
   read-only with a lock, matching what the server will enforce anyway. */
function costEl(o){
  if(o.locked){
    return '<div class="ocost locked"><span class="cl">Cost</span>'+
      '<b>'+rs(o.supplier_cost,o.supplier_cost_ccy)+'</b>'+
      '<span class="lockic" title="On an acknowledged bill">'+
      '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/>'+
      '<path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg></span></div>';
  }
  if(o.supplier_cost!=null){
    return '<div class="ocost"><span class="cl">Cost</span>'+
      '<b>'+rs(o.supplier_cost,o.supplier_cost_ccy)+'</b>'+
      '<button data-cost="'+o.id+'">Change</button></div>';
  }
  /* No price set: show the rate that will be applied if he never sets one,
     so the number on the batch is never a surprise. */
  return '<div class="ocost"><button class="addcost" data-cost="'+o.id+'">+ Add cost</button>'+
    '<span class="defc">or '+inr(o.default_cost)+' '+
    (o.default_label==='unidentified'?'default':esc(o.default_label))+'</span></div>';
}
function cancelledCostEl(o){
  var amount=o.supplier_cost!=null
    ?rs(o.supplier_cost,o.supplier_cost_ccy)
    :inr(o.default_cost)+' '+(o.default_label==='unidentified'?'default':esc(o.default_label));
  return '<div class="ocost locked"><span class="cl">Cost</span><b>'+amount+'</b></div>';
}

function cardEl(o){
  var nxt=nextOf(o.status), le=lastEvent(o), problem=o.status==='cancelled';
  /* Only the first section (Pending) is a to-do; give those cards the accent
     edge. Once moving, the section itself says where it is. */
  var attn=(o.status===PIPELINE[0]);
  var onum=o.order_no||o.id;
  return '<article class="ocard'+(attn?' attn':'')+(selectMode&&!problem?' selectable':'')+
      (SEL[o.id]&&!problem?' sel':'')+'" data-id="'+o.id+'">'+
    (selectMode&&!problem?'<button class="osel'+(SEL[o.id]?' on':'')+'" data-sel="'+o.id+
      '" aria-label="Select order '+onum+'"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></button>':'')+
    '<div class="otop">'+photoEl(o)+
      '<div class="omid">'+
        '<div class="onum">'+(o.ref_code?'ORDER '+esc(o.ref_code):'ORDER #'+onum)+'</div>'+
        '<h3 class="oprod">'+esc(o.product||'—')+
          (o.quantity>1?'<span class="oqty">x'+o.quantity+'</span>':'')+'</h3>'+
        specEl(o)+
        (o.notes?'<div class="onote">'+esc(o.notes)+'</div>':'')+
        (problem?cancelledCostEl(o):costEl(o))+
      '</div>'+
    '</div>'+
    '<div class="obar">'+
      '<span class="ost s-'+cls(o.status)+'"><i></i>'+esc(stLabel(o.status))+
        (le?' <span class="when">· '+esc(ago(le.at))+'</span>':'')+'</span>'+
    '</div>'+
    '<div class="oact">'+
      (problem?'<span class="obtn" aria-label="Needs admin review">Needs admin review</span>'
       :nxt?'<button class="obtn" data-adv="'+o.id+'" data-to="'+esc(nxt)+'">'+
        '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>'+esc(stLabel(nxt))+'</button>'
       :'<span class="obtn" aria-label="Build complete">Complete</span>')+
      '<button class="obtn ghost" data-share="'+o.id+'" aria-label="Share status">'+
        '<svg viewBox="0 0 24 24"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/><path d="M12 15V3M8 7l4-4 4 4"/></svg></button>'+
      '<button class="obtn ghost" data-det="'+o.id+'" aria-label="Details">&#9432;</button>'+
    '</div>'+
    '<div class="odet'+(openDetail[o.id]?' on':'')+'" id="det-'+o.id+'">'+
      timelineEl(o)+
      '<form class="noteform" data-note="'+o.id+'">'+
        '<input type="text" placeholder="Add a note — e.g. dial out of stock" aria-label="Add a note">'+
        '<button type="submit">Send</button>'+
      '</form><div class="bact">'+
        '<button type="button" data-photoadd="'+o.id+'">+ Add image</button>'+
        (o.photos?'<button type="button" data-photorm="'+o.id+'">Remove image</button>':'')+
      '</div>'+
    '</div>'+
  '</article>';
}

/* Redraw a single card. render() rebuilds every card's innerHTML, photos
   included, which is the right thing when the filter or the data changes and
   badly wrong for a status tap — the whole list re-decodes its images and the
   tap feels laggy on a phone. Interactions that only change one build use
   this instead. */
function refreshCard(id){
  var el=$('list').querySelector('.ocard[data-id="'+id+'"]');
  var o=byId(id);
  if(!el||!o){ render(); return; }
  /* a build that no longer belongs in the current view needs the full pass */
  if(!matches(o)||(filter!=='all'&&bucket(o)!==filter)){ render(); return; }
  var tmp=document.createElement('div');
  tmp.innerHTML=cardEl(o);
  el.replaceWith(tmp.firstChild);
  wire();
  drawBulk();
}

/* The section tabs are built from the pipeline the server sends, so adding or
   renaming a stage is a one-file change in order_stages.py — nothing here
   hardcodes the five. */
function drawStageBar(counts){
  var bar=$('stagebar');
  if(!bar)return;
  var html=PIPELINE.map(function(k){
    return '<button class="stg'+(k===filter?' on':'')+'" data-f="'+esc(k)+'">'+
      '<b>'+(counts[k]||0)+'</b><span class="lbl">'+esc(stLabel(k))+'</span></button>';
  });
  if(counts.cancelled){
    html.push('<button class="stg'+('cancelled'===filter?' on':'')+
      '" data-f="cancelled"><b>'+counts.cancelled+'</b><span class="lbl">Problems</span></button>');
  }
  html.push('<button class="stg'+('all'===filter?' on':'')+'" data-f="all">'+
    '<b>'+(counts.all||0)+'</b><span class="lbl">All</span></button>');
  bar.innerHTML=html.join('');
  bar.querySelectorAll('.stg').forEach(function(c){
    c.onclick=function(){ filter=c.dataset.f; render(); window.scrollTo({top:0,behavior:'smooth'}); };
  });
}

function render(){
  var vis=ORDERS.filter(matches);
  /* One bucket per stage, in pipeline order. */
  var groups={};
  PIPELINE.forEach(function(k){groups[k]=[];});
  vis.forEach(function(o){ (groups[o.status]||(groups[o.status]=[])).push(o); });
  var show = filter==='all' ? PIPELINE.concat(['cancelled']) : [filter];
  var html='';
  show.forEach(function(k){
    var list=sortOrders(groups[k]||[]);
    /* In a single-section view, show the empty state rather than nothing; in
       the All view, skip empty stages so it stays scannable. */
    if(!list.length && filter==='all')return;
    html+='<div class="shead"><h2>'+esc(stLabel(k))+'</h2><span class="n">'+list.length+'</span></div>'+
      (list.length?'<div class="ogrid">'+list.map(cardEl).join('')+'</div>'
        :'<div class="sempty small"><div>Nothing in '+esc(stLabel(k))+' right now.</div></div>');
  });
  if(!html){
    html='<div class="sempty"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg><div>'+
      (q||onlyPhotos||caseF||moveF?'Nothing matches the current filters.'
        :'Nothing in the queue yet. New orders land in Pending the moment they&rsquo;re logged.')+
      '</div></div>';
  }
  $('list').innerHTML=html;
  var counts={all:vis.length};
  PIPELINE.forEach(function(k){counts[k]=(groups[k]||[]).length;});
  counts.cancelled=(groups.cancelled||[]).length;
  drawStageBar(counts);
  document.querySelectorAll('.schip[data-sort]').forEach(function(c){
    c.classList.toggle('on',c.dataset.sort===sort);
  });
  var pc=$('f-photos'); if(pc)pc.classList.toggle('on',onlyPhotos);
  $('newbatch').classList.toggle('on',selectMode);
  $('newbatch').querySelector('span').textContent=selectMode?'Cancel':'New batch';
  $('pickhint').classList.toggle('on',selectMode);
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
function selIds(){
  return Object.keys(SEL).filter(function(k){return SEL[k];}).map(Number).filter(function(id){
    var o=byId(id);
    return o&&o.status!=='cancelled';
  });
}
function drawBulk(){
  var ids=selIds();
  /* Visible for the whole of select mode, not only once something is ticked —
     otherwise the bar (and the way out of the mode) vanishes the moment you
     deselect the last build. */
  $('bulkbar').classList.toggle('on',selectMode);
  var tot=ids.reduce(function(a,i){
    var o=byId(i); if(!o)return a;
    var unit=o.supplier_cost!=null?toINR(o.supplier_cost,o.supplier_cost_ccy):(o.default_cost||0);
    return a+unit*Math.max(1,Number(o.quantity)||1);
  },0);
  $('bulkn').textContent=ids.length?ids.length+' selected · '+inr(tot):'Nothing selected';
  $('bulk-bill').disabled=!ids.length;
  $('bulk-bill').style.opacity=ids.length?'':'.5';
}
function unbilledIds(){
  return ORDERS.filter(function(o){
    return !o.bill_id&&o.status!=='cancelled'&&matches(o)&&
      (filter==='all'||bucket(o)===filter);
  }).map(function(o){return o.id;});
}
function enterSelect(preselect){
  selectMode=true;
  SEL={};
  if(preselect)unbilledIds().forEach(function(i){SEL[i]=true;});
  render();
}
function exitSelect(){ selectMode=false; SEL={}; render(); }
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
  L.querySelectorAll('[data-photoadd]').forEach(function(b){
    b.onclick=function(){photoOrderId=+b.dataset.photoadd;$('supplier-photo-input').click();};
  });
  L.querySelectorAll('[data-photorm]').forEach(function(b){
    b.onclick=function(){
      var o=byId(+b.dataset.photorm);
      removeSupplierPhoto(o).then(function(){toast('Image removed');load(true);})
        .catch(function(e){toast(e.message);});
    };
  });
  /* Inline edit, same pattern as tracking — typing a number in place beats
     a modal for a field entered dozens of times a week. */
  L.querySelectorAll('[data-cost]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.cost, o=byId(id);
      var host=b.closest('.ocost');
      host.outerHTML='<form class="trkform" data-costf="'+id+'">'+
        '<input type="number" step="0.01" min="0" inputmode="decimal" value="'+
        (o.supplier_cost!=null?o.supplier_cost:'')+
        '" placeholder="Cost in ₹" aria-label="Cost in rupees">'+
        '<button type="submit">Save</button></form>';
      var f=L.querySelector('[data-costf="'+id+'"]');
      var inp=f.querySelector('input'); inp.focus(); inp.select();
      f.onsubmit=function(e){
        e.preventDefault();
        var v=inp.value.trim();
        if(v===''){ render(); return; }
        inp.disabled=true;
        jpost('/supplier/cost',{order_id:id,cost:v,currency:'INR'}).then(function(){
          o.supplier_cost=Number(v); o.supplier_cost_ccy='INR';
          toast('Cost saved');
          refreshCard(id); drawMoney();
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
  refreshCard(id);
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
    /* Returns as soon as it's queued; delivery happens server-side. The
       timeline on the order is what confirms it actually landed, so refresh
       shortly after rather than making the supplier wait on the send. */
    try{
      await jpost('/supplier/whatsapp',{id:id,with_pdf:how==='postpdf'});
      toast('Sending to WhatsApp…');
      setTimeout(function(){ load(true); }, 6000);
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
  var allowed=[o.status];var nxt=nextOf(o.status);if(nxt)allowed.push(nxt);
  $('sheet-opts').innerHTML=allowed.map(function(s){
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
    $('pane-bills').classList.toggle('on',k==='bills');
    ['queue-controls','queue-extra'].forEach(function(id){
      $(id).style.display = k==='queue' ? '' : 'none';
    });
    if(k==='bills')loadBills();
  };
});

/* ---- bills ---- */
var BILLS=[], CAN_ACK=false;
function billCard(b){
  var ack=b.status==='acknowledged';
  var paid=0, arr=(ARREARS&&ARREARS.bills||[]).filter(function(x){return x.id===b.id;})[0];
  if(arr)paid=arr.paid_inr||0;
  var totINR=b.total_inr!=null?b.total_inr:toINR(b.total,b.currency);
  var lines=(b.items||[]).map(function(it){
    var qty=Math.max(1,Number(it.quantity||1));
    return '<li><span>'+esc(it.ref_code||('#'+(it.order_no||it.order_id)))+' · '+
      esc(it.description||'')+(qty>1?' · '+qty+' watches':'')+'</span><b>'+
      rs(Number(it.cost||0)*qty,b.currency)+'</b></li>';
  }).join('');
  var units=(b.items||[]).reduce(function(n,it){
    return n+Math.max(1,Number(it.quantity||1));},0);
  return '<article class="bcard'+(ack?' ack':'')+'" data-bill="'+b.id+'">'+
    '<div class="btop">'+
      '<div><div class="bno">'+esc(b.bill_no)+'</div>'+
        '<div class="bmeta">'+units+' watch'+(units===1?'':'es')+
        ' across '+(b.items||[]).length+' order'+
        ((b.items||[]).length===1?'':'s')+' · '+
        esc((b.created_at||'').slice(0,10))+'</div></div>'+
      '<div class="bright"><div class="btot">'+inr(totINR)+'</div>'+
        '<span class="bstat '+(ack?'ok':'draft')+'">'+(ack?'Acknowledged':'Not yet agreed')+'</span></div>'+
    '</div>'+
    '<ul class="blines">'+lines+
      (b.shipping_cost?'<li class="ship"><span>Shipping</span><b>'+
        rs(b.shipping_cost,b.currency)+'</b></li>':'')+
      (paid>0.5?'<li class="paid"><span>Paid so far</span><b>-'+inr(paid)+'</b></li>':'')+
    '</ul>'+
    (b.notes?'<div class="bnote">'+esc(b.notes)+'</div>':'')+
    (ack&&b.acknowledged_by?'<div class="backby">Acknowledged by '+
      esc(String(b.acknowledged_by).split('@')[0])+'</div>':'')+
    '<div class="btrk">'+(b.tracking_code
      ? '<span class="chip">'+esc(b.tracking_code)+'</span><button data-btrk="'+b.id+'">Change</button>'
      : '<button data-btrk="'+b.id+'">+ Add courier / tracking</button>')+'</div>'+
    '<div class="bact">'+
      '<button data-billpdf="'+b.id+'">Bill PDF</button>'+
      '<button data-billwa="'+b.id+'">Send to WhatsApp</button>'+
      '<button data-billlink="'+b.id+'">Copy link</button>'+
      (!ack?'<button data-billedit="'+b.id+'">Edit bill</button>':'')+
      (CAN_ACK&&!ack?'<button class="go" data-billack="'+b.id+'">Agree this batch</button>':'')+
      (CAN_ACK&&ack?'<button data-billpay="'+b.id+'">Record payment</button>':'')+
      (CAN_ACK?'<button class="danger" data-billdel="'+b.id+'">Delete</button>':'')+
    '</div>'+
  '</article>';
}
function drawBills(){
  var L=$('billlist');
  if(!BILLS.length){
    L.innerHTML='<div class="sempty">No batches yet. Select builds in the queue, '+
      'then use <b>Create batch</b>. Anything without a price gets its default rate.</div>';
  } else {
    L.innerHTML=BILLS.map(billCard).join('');
  }
  if(ARREARS){
    var owed=ARREARS.owed_inr||0;
    $('bills-owed').innerHTML='<div class="owedcard">'+
      '<div class="owedtop"><b>'+inr(Math.abs(owed))+'</b><span>'+
        (owed>0.5?'currently owed':owed<-0.5?'in credit':'settled')+'</span></div>'+
      '<div class="owedbar">'+
        '<span class="b">Billed <b>'+inr(ARREARS.total_cost_inr)+'</b></span>'+
        '<span class="b">Paid <b>'+inr(ARREARS.total_paid_inr)+'</b></span>'+
        '<span class="b">Not yet billed <b>'+inr(ARREARS.unbilled_cost_inr||0)+'</b></span>'+
      '</div></div>';
  }
  L.querySelectorAll('[data-billpdf]').forEach(function(b){
    b.onclick=function(){
      var name=localStorage.getItem('labs_bill_from')||'';
      if(!name){
        name=(prompt('Business name for the bill header:','')||'').trim();
        if(name)localStorage.setItem('labs_bill_from',name);
      }
      window.open(API+'/supplier/bill?id='+b.dataset.billpdf+
        '&from='+encodeURIComponent(name||'Supplier'),'_blank');
    };
  });
  L.querySelectorAll('[data-billack]').forEach(function(b){
    b.onclick=function(){
      if(!confirm('Agree this batch? Prices lock and it goes to your Ledger.'))return;
      jpost('/supplier/bill/acknowledge',{id:+b.dataset.billack}).then(function(){
        toast('Agreed — sent to your Ledger');
        loadBills(); load(true);
      }).catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-billedit]').forEach(function(btn){
    btn.onclick=async function(){
      var b=BILLS.filter(function(x){return x.id===+btn.dataset.billedit;})[0];
      if(!b)return;
      var costs={};
      for(var i=0;i<(b.items||[]).length;i++){
        var it=b.items[i], raw=prompt('Cost per watch for '+
          (it.ref_code||('#'+(it.order_no||it.order_id)))+':',it.cost);
        if(raw===null)return;
        costs[String(it.id)]=raw;
      }
      var shipping=prompt('Shipping cost:',b.shipping_cost||0);if(shipping===null)return;
      var notes=prompt('Bill notes:',b.notes||'');if(notes===null)return;
      try{
        await jpost('/supplier/bill/update',{id:b.id,costs:costs,
          shipping_cost:shipping,notes:notes});
        toast('Bill updated');await loadBills();await loadOrders();
      }catch(e){toast(e.message);}
    };
  });
  L.querySelectorAll('[data-billdel]').forEach(function(b){
    b.onclick=function(){
      if(!confirm('Delete this batch? Any Ledger entry is reversed and the '+
                  'builds become editable again.'))return;
      jpost('/supplier/bill/delete',{id:+b.dataset.billdel}).then(function(){
        toast('Batch deleted and reversed');
        loadBills(); load(true);
      }).catch(function(e){ toast(e.message); });
    };
  });
  /* The batch reaches the other side through the group everyone already
     watches, PDF attached. Queued server-side, so this returns at once. */
  L.querySelectorAll('[data-billwa]').forEach(function(b){
    b.onclick=function(){
      var name=localStorage.getItem('labs_bill_from')||'';
      if(!name){
        name=(prompt('Business name for the bill header:','')||'').trim();
        if(name)localStorage.setItem('labs_bill_from',name);
      }
      b.disabled=true;
      jpost('/supplier/bill/whatsapp',{id:+b.dataset.billwa,from:name||'Supplier'})
        .then(function(){ toast('Sending to WhatsApp…'); })
        .catch(function(e){ toast(e.message); })
        .then(function(){ b.disabled=false; });
    };
  });
  /* A deep link rather than a public one: agreeing a batch moves money into
     the Ledger, so it stays behind the sign-in. Hannan sends this, the owner
     opens it already signed in and lands on the batch ready to agree. */
  L.querySelectorAll('[data-billlink]').forEach(function(b){
    b.onclick=function(){
      var url=location.origin+location.pathname+'#batch-'+b.dataset.billlink;
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(url)
          .then(function(){ toast('Link copied'); })
          .catch(function(){ prompt('Copy this link:',url); });
      } else { prompt('Copy this link:',url); }
    };
  });
  L.querySelectorAll('[data-btrk]').forEach(function(b){
    b.onclick=function(){
      var id=+b.dataset.btrk;
      var cur=(BILLS.filter(function(x){return x.id===id;})[0]||{}).tracking_code||'';
      var v=prompt('Courier / tracking number for this batch:',cur);
      if(v===null)return;
      jpost('/supplier/bill/tracking',{id:id,tracking_code:v.trim()}).then(function(){
        toast(v.trim()?'Tracking saved':'Tracking cleared'); loadBills();
      }).catch(function(e){ toast(e.message); });
    };
  });
  L.querySelectorAll('[data-billpay]').forEach(function(b){
    b.onclick=function(){
      var amount=prompt('Amount paid, in ₹:');
      if(!amount)return;
      var note=prompt('Note (optional):')||'';
      jpost('/supplier/payment/record',{amount:amount,currency:'INR',
        bill_id:+b.dataset.billpay,note:note}).then(function(){
        toast('Payment recorded'); loadBills();
      }).catch(function(e){ toast(e.message); });
    };
  });
}
/* Someone arriving on #batch-7 should land on it, not on the queue. */
function batchFromHash(){
  var m=/^#batch-(\d+)$/.exec(location.hash||'');
  return m?+m[1]:0;
}
function focusHashBatch(){
  var id=batchFromHash();
  if(!id)return;
  var el=$('billlist').querySelector('.bcard[data-bill="'+id+'"]');
  if(!el)return;
  el.scrollIntoView({behavior:'smooth',block:'center'});
  el.classList.add('flash');
  setTimeout(function(){ el.classList.remove('flash'); },2200);
}
async function loadBills(){
  try{
    var d=await api('/supplier/bills');
    BILLS=d.bills||[]; CAN_ACK=!!d.can_acknowledge;
    try{ ARREARS=await api('/supplier/arrears'); }catch(e){}
    drawBills();
    focusHashBatch();
  }catch(e){ $('billlist').innerHTML='<div class="sempty">'+esc(e.message)+'</div>'; }
}

/* ---- shipments ---- */
function inr(n){
  return '₹'+Number(n||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
}
/* Everything on screen is rupees, per the owner. Anything quoted in dollars
   is converted at the same flat 100 the Ledger and Home already use, so one
   figure never disagrees with the same figure elsewhere in the OS. */
var USD_INR=100;
function toINR(n,cur){
  if(n===null||n===undefined||n==='')return 0;
  return Number(n)*(String(cur||'INR').toUpperCase()==='USD'?USD_INR:1);
}
function rs(n,cur){
  if(n===null||n===undefined||n==='')return '—';
  return inr(toINR(n,cur));
}
var ARREARS=null;

/* The strip under the filters — the owner's standing ask that totals be
   visible without opening anything else. Every figure is rupees. */
function drawMoney(){
  var box=$('moneybar'); if(!box)return;
  if(!ARREARS){ box.innerHTML=''; return; }
  var costed=ORDERS.filter(function(o){return o.supplier_cost!=null;});
  var total=costed.reduce(function(a,o){
    return a+toINR(o.supplier_cost,o.supplier_cost_ccy)*
      Math.max(1,Number(o.quantity||1));},0);
  var owed=ARREARS.owed_inr||0;
  var cells=[
    ['Builds', String(ORDERS.length)],
    ['Costed', costed.length+' of '+ORDERS.length],
    ['Priced up', inr(total)],
    ['Not yet billed', inr(ARREARS.unbilled_cost_inr||0)]
  ];
  var html=cells.map(function(c){
    return '<div class="mcell"><div class="ml">'+esc(c[0])+'</div>'+
           '<div class="mv">'+esc(c[1])+'</div></div>';
  }).join('');
  html+='<div class="mcell owe"><div class="ml">'+
        (owed<-0.5?'In credit':'You owe')+'</div><div class="mv">'+
        inr(Math.abs(owed))+'</div></div>';
  box.innerHTML=html;
}

/* ---- bulk buttons ---- */
$('bulk-clear').onclick=exitSelect;
$('bulk-cost').onclick=function(){
  var ids=selIds();
  if(!ids.length){toast('Nothing selected');return;}
  var v=prompt('Cost per watch, in ₹ (applies to all '+ids.length+' orders):');
  if(v===null)return;
  v=v.trim(); if(v==='')return;
  jpost('/supplier/cost/bulk',{ids:ids,cost:v,currency:'INR'}).then(function(d){
    toast(d.updated+' updated'+(d.skipped&&d.skipped.length?', '+d.skipped.length+' locked':''));
    clearSel(); load(true);
  }).catch(function(e){ toast(e.message); });
};
$('bulk-bill').onclick=function(){
  var ids=selIds();
  if(!ids.length){toast('Nothing selected');return;}
  /* Unpriced builds no longer block the batch — they take their default
     rate — but say so up front rather than surprising him with the total. */
  var miss=ids.filter(function(i){var o=byId(i);return !o||o.supplier_cost==null;});
  if(miss.length&&!confirm(miss.length+' of '+ids.length+
      ' have no price set. They will use their default rate. Continue?'))return;
  var ship=prompt('Shipping / freight for the whole batch, in ₹ (0 if none):','0');
  if(ship===null)return;
  var note=prompt('Note on the batch (optional):')||'';
  jpost('/supplier/bill/create',{ids:ids,shipping_cost:ship||0,currency:'INR',notes:note})
    .then(function(d){
      toast('Batch '+d.bill_no+' created — '+inr(d.total)+
        (d.defaulted?' ('+d.defaulted+' at default rate)':''));
      exitSelect(); load(true); loadBills();
      document.querySelector('.tabs button[data-tab="bills"]').click();
    }).catch(function(e){ toast(e.message); });
};
/* One obvious way in. Everything not yet billed starts ticked, because a
   batch is normally "the outstanding watches" — untick the few that aren't
   ready rather than hunting for the many that are. Two batches from one
   queue is then just: pick some, create, pick the rest, create. */
$('newbatch').onclick=function(){
  if(selectMode){ exitSelect(); return; }
  var n=unbilledIds().length;
  if(!n){ toast('Every build is already in a batch'); return; }
  enterSelect(true);
  window.scrollTo({top:0,behavior:'smooth'});
};
$('pick-all').onclick=function(){ enterSelect(true); };
$('pick-none').onclick=function(){ clearSel(); };
$('bulk-status').onclick=function(){
  var chosen=selIds().map(byId).filter(Boolean);
  var stages={};chosen.forEach(function(o){stages[o.status]=1;});
  var current=Object.keys(stages);
  if(current.length!==1){toast('Select builds at the same stage to move them together');return;}
  var nxt=nextOf(current[0]);
  if(!nxt){toast('Those builds have no next stage');return;}
  $('sheet-title').textContent=chosen.length+' orders — next stage';
  $('sheet-opts').innerHTML='<button class="opt" data-s="'+esc(nxt)+
    '"><i></i>'+esc(stLabel(nxt))+'</button>';
  $('sheet-opts').querySelectorAll('.opt').forEach(function(b){
    b.onclick=function(){ closeSheet(); bulkDo({status:b.dataset.s},'Status set'); };
  });
  $('sheet').classList.add('on');
};
$('bulk-pdf').onclick=function(){
  var ids=selIds();
  if(!ids.length)return;
  window.open(API+'/supplier/ledger?ids='+ids.join(','),'_blank');
};

/* filters + search — the stage tabs wire themselves in drawStageBar, since
   they're rebuilt from the pipeline on every render */
document.querySelectorAll('.schip[data-sort]').forEach(function(c){
  c.onclick=function(){ sort=c.dataset.sort; render(); };
});
$('f-photos').onclick=function(){ onlyPhotos=!onlyPhotos; render(); };
$('supplier-photo-input').onchange=function(){
  var f=(this.files||[])[0];this.value='';
  if(!f||!photoOrderId)return;
  uploadSupplierPhoto(photoOrderId,f).then(function(){toast('Image added');load(true);})
    .catch(function(e){toast(e.message);});
};
$('fbtn').onclick=function(){
  var open=$('fpanel').classList.toggle('on');
  $('fbtn').classList.toggle('open',open);
  $('fbtn').setAttribute('aria-expanded',open?'true':'false');
};
$('fclear').onclick=function(){
  q='';onlyPhotos=false;sort='newest';caseF='';moveF='';
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
    PIPELINE=d.pipeline||STATUSES.filter(function(s){return s!=='cancelled';});
    LABELS=d.labels||{};
    ORDERS=d.orders||[];
    $('sync').textContent='Updated '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    $('sync').classList.remove('err');
    render();
    /* Arrears rides along with the queue load so the money strip is right
       on first paint, not a beat later. Failure is silent and non-fatal —
       a build queue that works without totals beats one that won't load. */
    try{ ARREARS=await api('/supplier/arrears'); drawMoney(); }catch(e){}
  }catch(e){
    $('sync').textContent=e.message;
    $('sync').classList.add('err');
    if(!ORDERS.length)$('list').innerHTML='<div class="sempty">'+esc(e.message)+'</div>';
  }
  loading=false;
}
/* The background refresh must never interrupt work in progress: rebuilding
   the list would drop a half-typed note or cost, and re-deriving selection
   mid-pick is worse. Skip the tick and catch the next one. */
function busyEditing(){
  var a=document.activeElement;
  if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'))return true;
  return selectMode;
}
document.addEventListener('visibilitychange',function(){
  if(!document.hidden&&!busyEditing())load(true); });
setInterval(function(){ if(!document.hidden&&!busyEditing())load(true); },90000);
load();
/* Arriving on a #batch-N link should open Batches, not the queue. */
if(/^#batch-\d+$/.test(location.hash||'')){
  var _bt=document.querySelector('.tabs button[data-tab="bills"]');
  if(_bt)_bt.click();
}

fetch(API+'/whoami').then(function(r){return r.ok?r.json():null;}).then(function(i){
  if(i&&i.email){var w=$('who');if(w)w.textContent=i.email;}
  if(i&&i.admin){IS_ADMIN=true; if(BILLS.length)drawBills();}
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
      <button data-tab="bills">Batches</button>
    </div>

    <div id="queue-controls">
      <!-- One tab per pipeline stage, built in drawStageBar() from what the
           server sends. Starts empty so it's never briefly wrong. -->
      <div class="stagebar" id="stagebar"></div>
      <div class="fbar">
        <input id="q" class="ssearch" type="search" placeholder="Search number, product, spec, tracking"
               aria-label="Search the queue" autocomplete="off">
        <button class="fbtn" id="fbtn" aria-expanded="false" aria-controls="fpanel">
          <svg viewBox="0 0 24 24"><path d="M4 6h16M7 12h10M10 18h4"/></svg>
          Filters<span class="cnt" id="fcount" style="display:none"></span>
          <svg class="chev" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <button class="fbtn newbatch" id="newbatch">
          <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
          <span>New batch</span>
        </button>
      </div>
      <div class="fpanel" id="fpanel">
        <p class="flab">Case style</p>
        <div class="frow" id="f-case"></div>
        <p class="flab">Movement</p>
        <div class="frow" id="f-move"></div>
        <p class="flab">Order by</p>
        <div class="frow">
          <button class="schip on" data-sort="oldest">Oldest first</button>
          <button class="schip" data-sort="newest">Newest first</button>
          <button class="schip" data-sort="stage">Stage</button>
          <button class="schip" data-sort="pricehigh">Price high&ndash;low</button>
          <button class="schip" data-sort="pricelow">Price low&ndash;high</button>
        </div>
        <div class="frow">
          <button class="schip" id="f-photos">With a photo</button>
          <button class="fclear" id="fclear">Reset filters</button>
        </div>
      </div>
      <div class="pickhint" id="pickhint">
        <span id="pickmsg">Tap the builds for this batch</span>
        <span class="sp"></span>
        <button id="pick-all">All unbilled</button>
        <button id="pick-none">None</button>
      </div>
    </div>
  </div>

  <!-- Reference, not controls, so it scrolls away. Kept inside .stop these
       three added ~120px to a header that is pinned for the whole session:
       on a 390x844 phone the queue itself was left about half a card. -->
  <div id="queue-extra">
      <div class="moneybar" id="moneybar"></div>
      <div class="syncline"><span class="sync" id="sync">Loading&hellip;</span></div>
      <details class="helpbox">
        <summary>How these numbers are worked out</summary>
        <div class="hb">
          <p><b>Each build has a price.</b> Type one in, or leave it and the
          default for its movement applies when the batch is made:</p>
          <ul>
            <li>VK63 &mdash; &#8377;6,500</li>
            <li>NH35 (including anything marked automatic) &mdash; &#8377;9,000</li>
            <li>Movement not identified &mdash; &#8377;5,000</li>
          </ul>
          <p style="margin-top:8px"><b>A batch</b> is a set of builds sent
          together and billed together. Its total is every build's price added
          up, plus one shipping charge for the whole batch. Prices are copied
          onto the batch when it's made, so editing a build later never changes
          a batch that already exists.</p>
          <p style="margin-top:8px"><b>You owe</b> counts agreed batches only,
          minus anything already paid. A batch that hasn't been agreed yet
          isn't owed. <b>Not yet billed</b> is work that's been priced but
          isn't on any batch.</p>
        </div>
      </details>
  </div>

  <section class="pane on" id="pane-queue">
    <main id="list"><div class="sempty">Loading&hellip;</div></main>
    <div class="bulkbar" id="bulkbar">
      <b id="bulkn">0 selected</b>
      <span class="sp"></span>
      <button id="bulk-cost">Set cost</button>
      <button id="bulk-status">Set status</button>
      <button id="bulk-pdf">Build request PDF</button>
      <button class="primary" id="bulk-bill">Create batch</button>
      <button id="bulk-clear">Cancel</button>
    </div>
  </section>

  <section class="pane" id="pane-bills">
    <div id="bills-owed"></div>
    <div id="billlist"><div class="sempty">Loading&hellip;</div></div>
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

<input id="supplier-photo-input" type="file" accept="image/jpeg,image/png,image/webp" hidden>
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
