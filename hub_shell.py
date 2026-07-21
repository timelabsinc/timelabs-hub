"""Timelabs Hub — "Caliber" shell: instrument-panel design system.

This module owns the chrome AND the chart engine of Hub: brand bar, the
mobile-first app switcher (Face / Drop / Ledger / Chat / Key), the Face
sub-tab bar, the Key member panel, server-side SVG charts (sparklines, area
chart, dial gauges), and all CSS/JS. generate.py builds data fragments and
calls page(...).

Design language: a watch movement seen through a caseback. Deep ground,
luminous gold accents, tabular serif numerals for display figures, dial-style
gauges with tick marks. Lively but legible: numbers count up, tabs spring,
pull-to-refresh on mobile.

Never hand-edit /var/www/ops/index.html — it is generated from here.
"""
import datetime
import html
import math

# Mirrors agent_chat_server.ADMIN_EMAILS — keep in sync.
ADMIN_EMAILS = ("timelabs.inc@gmail.com", "schezan.m@gmail.com")


# --------------------------------------------------------------------- helpers
def _refund_pct(s):
    """Refunds as a share of gross — a one-glance leak indicator."""
    gross = s.get("gross_sales") or s.get("total_sales") or 0
    refunds = s.get("refund_total") or 0
    if gross <= 0:
        return ""
    return f"{100 * refunds / gross:.0f}% of gross"


def fmt_compact_inr(n):
    """₹3.4L / ₹46k style for chart labels."""
    n = float(n)
    a = abs(n)
    if a >= 100000:
        return f"₹{n/100000:.1f}L"
    if a >= 1000:
        return f"₹{n/1000:.0f}k"
    return f"₹{n:.0f}"


def source_chip(src, name):
    """Honest per-source freshness chip driven by each source's own as_of."""
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


# ----------------------------------------------------------------- SVG charts
def svg_spark(values, w=110, h=34, cls="spark"):
    """Tiny inline sparkline polyline. Returns '' when there's nothing to plot."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(vals):
        x = 2 + i * (w - 4) / (len(vals) - 1)
        y = h - 4 - (v - lo) / rng * (h - 8)
        pts.append(f"{x:.1f},{y:.1f}")
    last = pts[-1].split(",")
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/>'
            f'<circle cx="{last[0]}" cy="{last[1]}" r="2.4" fill="currentColor"/></svg>')


def _zero_fill(sparse, span):
    """Expand [(date, val), ...] sparse rows over [start, end] with 0 gaps."""
    by_day = {d: float(v) for d, v, *_ in sparse}
    start = datetime.date.fromisoformat(span[0])
    end = datetime.date.fromisoformat(span[1])
    out = []
    d = start
    while d <= end:
        iso = d.isoformat()
        out.append((iso, by_day.get(iso, 0.0)))
        d += datetime.timedelta(days=1)
    return out


def svg_revenue_chart(sales_daily, span, w=680, h=190):
    """90-day daily revenue: gold bars above the baseline, refund days in red
    below it. Bars, not an area path — sparse spiky data reads honestly as bars."""
    if not sales_daily or not span:
        return ""
    series = _zero_fill(sales_daily, span)
    vals = [v for _, v in series]
    hi = max(max(vals), 1.0)
    lo = min(min(vals), 0.0)
    rng = hi - lo or 1.0
    pad_t, pad_b, pad_x = 16, 20, 6
    plot_h = h - pad_t - pad_b
    n = len(series)
    step = (w - 2 * pad_x) / n
    bw = max(2.0, step * 0.55)
    base_y = pad_t + (hi / rng) * plot_h  # y of ₹0
    bars, labels = [], []
    for i, (day, v) in enumerate(series):
        if v == 0:
            continue
        x = pad_x + i * step + (step - bw) / 2
        bh = abs(v) / rng * plot_h
        if v > 0:
            y = base_y - bh
            cls = "rev-bar"
        else:
            y = base_y
            cls = "rev-bar neg"
        bars.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(bh,1.5):.1f}" rx="1.2">'
            f'<title>{day} · {fmt_compact_inr(v)}</title></rect>'
        )
    # month labels along the axis
    seen_months = set()
    for i, (day, _) in enumerate(series):
        m = day[:7]
        if m not in seen_months and day.endswith(("-01", span[0][-2:])) or (i == 0):
            if m in seen_months:
                continue
            seen_months.add(m)
            x = pad_x + i * step
            mon = datetime.date.fromisoformat(day).strftime("%b")
            labels.append(f'<text class="ax" x="{x:.1f}" y="{h-5}">{mon}</text>')
    peak = f'<text class="ax hi" x="{w-pad_x}" y="{pad_t-4}" text-anchor="end">{fmt_compact_inr(hi)}</text>'
    return (f'<svg class="revchart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Daily sales, last 90 days">'
            f'<line class="baseline" x1="{pad_x}" y1="{base_y:.1f}" x2="{w-pad_x}" y2="{base_y:.1f}"/>'
            f'{"".join(bars)}{"".join(labels)}{peak}</svg>')


def svg_gauge(pct, label, sublabel="", tone="accent", vid=""):
    """Watch-subdial gauge: 0..100% sweep over a 240° arc with tick marks and a
    needle. tone: accent | good | crit."""
    pct = max(0.0, min(100.0, float(pct)))
    cx = cy = 60
    r = 46
    a0, a1 = 210, -30  # degrees, sweeping clockwise 240°
    def pt(angle_deg, radius):
        a = math.radians(angle_deg)
        return cx + radius * math.cos(a), cy - radius * math.sin(a)
    # ticks every 10%
    ticks = []
    for i in range(11):
        a = a0 + (a1 - a0) * i / 10
        x1, y1 = pt(a, r - 1)
        x2, y2 = pt(a, r - 6 if i % 5 else r - 9)
        ticks.append(f'<line class="tick{" maj" if i % 5 == 0 else ""}" '
                     f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    # arc track + value arc
    def arc(a_from, a_to, radius):
        x1, y1 = pt(a_from, radius)
        x2, y2 = pt(a_to, radius)
        large = 1 if abs(a_from - a_to) > 180 else 0
        return f"M {x1:.1f} {y1:.1f} A {radius} {radius} 0 {large} 1 {x2:.1f} {y2:.1f}"
    a_val = a0 + (a1 - a0) * pct / 100
    needle_x, needle_y = pt(a_val, r - 14)
    return (
        f'<figure class="dial {tone}">'
        f'<svg viewBox="0 0 120 104" role="img" aria-label="{html.escape(label)}: {pct:.0f}%">'
        f'<path class="track" d="{arc(a0, a1, r-3)}"/>'
        f'<path class="value" d="{arc(a0, a_val, r-3)}"/>'
        f'{"".join(ticks)}'
        f'<line class="needle" x1="{cx}" y1="{cy}" x2="{needle_x:.1f}" y2="{needle_y:.1f}"/>'
        f'<circle class="pivot" cx="{cx}" cy="{cy}" r="3.2"/>'
        f'<text class="dval num" x="{cx}" y="{cy+26}" text-anchor="middle"'
        f'{f" data-countup=\"{pct:.1f}\" data-suffix=\"%\"" if vid else ""}>{pct:.0f}%</text>'
        f'</svg>'
        f'<figcaption><b>{html.escape(label)}</b>'
        f'{f"<span>{html.escape(sublabel)}</span>" if sublabel else ""}</figcaption>'
        f'</figure>'
    )


def kpi_card(label, value, ctx, spark_vals=None, raw=None, prefix="", spark_tone=""):
    """KPI card with optional count-up + sparkline."""
    countup = f' data-countup="{raw}" data-prefix="{html.escape(prefix)}"' if raw is not None else ""
    spark = svg_spark(spark_vals) if spark_vals else ""
    return (f'<div class="kpi"><div class="label">{html.escape(label)}</div>'
            f'<div class="val num"{countup}>{html.escape(value)}</div>'
            f'<div class="kpi-foot"><span class="ctx">{html.escape(ctx)}</span>'
            f'<span class="sparkwrap {spark_tone}">{spark}</span></div></div>')


# ------------------------------------------------------------------ CSS  (plain string — no brace escaping)
HUB_STYLE = r"""
  :root{
    --ground:#0a0f0c; --surface:#131a15; --surface-2:#1a231d; --raised:#202b24;
    --ink:#f0f4ef; --muted:#94a89b; --line:#25332b; --hairline:#2d3d33;
    --accent:#d4af6a; --accent-2:#e8cf9a; --accent-soft:rgba(212,175,106,.13);
    --lume:#7ee0a3; --lume-soft:rgba(126,224,163,.13);
    --warn:#d9b45b; --warn-soft:rgba(217,180,91,.14);
    --crit:#e06a45; --crit-soft:rgba(224,106,69,.15);
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:"SF Mono","Cascadia Code","Consolas",ui-monospace,monospace;
    --appnav-h:64px; --radius:14px;
    --spring:cubic-bezier(.34,1.4,.64,1);
    --shadow-card:0 1px 0 rgba(255,255,255,.03) inset, 0 8px 26px rgba(0,0,0,.28);
  }
  @media (prefers-color-scheme: light){
    :root{ --ground:#edefe9; --surface:#ffffff; --surface-2:#f4f6f1; --raised:#eef1ea;
      --ink:#141d17; --muted:#54685c; --line:#dbe2da; --hairline:#cfd8ce;
      --accent:#8a6a2c; --accent-2:#6e541f; --accent-soft:rgba(138,106,44,.11);
      --lume:#2f8f5b; --lume-soft:rgba(47,143,91,.11);
      --warn:#9a7b2b; --warn-soft:rgba(154,123,43,.12);
      --crit:#c1502e; --crit-soft:rgba(193,80,46,.11);
      --shadow-card:0 1px 2px rgba(20,29,23,.06), 0 6px 18px rgba(20,29,23,.07); }
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.5;
    -webkit-font-smoothing:antialiased;overscroll-behavior-y:contain;
    background-image:radial-gradient(1100px 500px at 50% -140px, rgba(212,175,106,.05), transparent 60%);}
  .num{font-variant-numeric:tabular-nums lining-nums}
  a{color:var(--accent);}
  button{font:inherit;color:inherit;}
  .hub{max-width:980px;margin:0 auto;padding:0 1rem calc(var(--appnav-h) + env(safe-area-inset-bottom,0) + 1.4rem);}

  /* ---- pull-to-refresh indicator ---- */
  #ptr{position:fixed;top:-52px;left:50%;transform:translateX(-50%);z-index:90;width:40px;height:40px;
    border-radius:50%;background:var(--surface-2);border:1px solid var(--accent);display:flex;
    align-items:center;justify-content:center;color:var(--accent);transition:top .18s;
    box-shadow:0 6px 22px rgba(0,0,0,.35);}
  #ptr svg{width:20px;height:20px;transition:transform .1s;}
  #ptr.spin svg{animation:ptrspin .8s linear infinite;}
  @keyframes ptrspin{to{transform:rotate(360deg)}}

  /* ---- brand bar ---- */
  .hub-top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;padding:.95rem .1rem .75rem;}
  .brand{display:flex;align-items:baseline;gap:.5rem;text-decoration:none;}
  .brand-mark{font-family:var(--mono);font-weight:700;letter-spacing:.22em;font-size:.8rem;color:var(--ink);}
  .brand-sub{font-family:var(--serif);font-style:italic;font-size:1.06rem;color:var(--accent);
    text-shadow:0 0 18px var(--accent-soft);}
  .hub-actions{display:flex;align-items:center;gap:.5rem;}
  .who{font-family:var(--mono);font-size:.64rem;color:var(--muted);max-width:8.5rem;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .iconbtn{font-family:var(--mono);font-size:.95rem;line-height:1;cursor:pointer;border:1px solid var(--line);
    background:var(--surface);color:var(--ink);border-radius:10px;min-width:42px;min-height:42px;
    display:inline-flex;align-items:center;justify-content:center;
    transition:border-color .15s, transform .15s var(--spring);}
  .iconbtn:hover{border-color:var(--accent);}
  .iconbtn:active{transform:scale(.9);}
  .iconbtn:disabled{opacity:.5;cursor:wait;}

  /* ---- app switcher ---- */
  .appnav{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;background:var(--surface);
    border-top:1px solid var(--hairline);padding-bottom:env(safe-area-inset-bottom,0);
    box-shadow:0 -8px 28px rgba(0,0,0,.30);backdrop-filter:blur(10px);}
  .appitem{position:relative;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:.16rem;padding:.5rem .2rem;min-height:var(--appnav-h);text-decoration:none;color:var(--muted);
    font-family:var(--mono);font-size:.58rem;letter-spacing:.04em;text-transform:uppercase;background:none;
    border:none;cursor:pointer;transition:color .15s;}
  .appitem .ic{font-size:1.3rem;line-height:1;transition:transform .25s var(--spring);}
  .appitem.active{color:var(--accent);}
  .appitem.active .ic{transform:translateY(-2px);text-shadow:0 0 14px var(--accent-soft);}
  .appitem:active .ic{transform:scale(.82);}
  .admin-only{display:none;}
  body.is-admin .admin-only{display:flex;}

  /* ---- Face header ---- */
  .face-head{padding:.3rem .1rem .95rem;}
  .face-title{font-family:var(--serif);font-weight:600;font-size:clamp(1.65rem,6vw,2.15rem);margin:0;}
  .face-sub{color:var(--muted);font-size:.88rem;margin:.2rem 0 .85rem;}
  .face-controls{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;}
  .rangebar{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--surface);}
  .rangebar button{font-family:var(--mono);font-size:.74rem;border:none;background:transparent;
    color:var(--muted);padding:.55rem .9rem;cursor:pointer;min-height:40px;transition:background .15s;}
  .rangebar button.active{background:var(--accent-soft);color:var(--accent);font-weight:700;}
  .rangebar button:hover{color:var(--ink);}
  .refreshed{font-family:var(--mono);font-size:.68rem;color:var(--muted);}
  .cta-row{display:flex;gap:.5rem;flex-wrap:wrap;margin:.15rem 0 1.1rem;}
  .cta{font-family:var(--mono);font-size:.74rem;text-decoration:none;border:1px solid var(--line);
    background:var(--surface);color:var(--ink);border-radius:10px;padding:.55rem .9rem;min-height:40px;
    display:inline-flex;align-items:center;gap:.4rem;transition:transform .15s var(--spring), border-color .15s;}
  .cta:hover{border-color:var(--accent);}
  .cta:active{transform:scale(.95);}
  .cta.primary{background:var(--accent);border-color:var(--accent);color:#151208;font-weight:700;
    box-shadow:0 4px 18px rgba(212,175,106,.25);}

  /* ---- Face sub-tabs ---- */
  .face-nav{position:sticky;top:0;z-index:30;background:linear-gradient(var(--ground) 82%, transparent);
    padding:.65rem 0 .6rem;margin:0 -1rem .95rem;}
  .face-tabs{display:flex;gap:.4rem;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:0 1rem;}
  .face-tabs::-webkit-scrollbar{display:none;}
  .tabbtn{position:relative;flex:0 0 auto;font-family:var(--mono);font-size:.74rem;border:1px solid var(--line);
    background:var(--surface);color:var(--muted);border-radius:999px;padding:.5rem .95rem;cursor:pointer;
    white-space:nowrap;min-height:38px;transition:all .2s var(--spring);}
  .tabbtn:hover{color:var(--ink);}
  .tabbtn.active{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:700;
    transform:scale(1.04);}
  .tabbtn:active{transform:scale(.94);}
  .tabbadge{margin-left:.35rem;font-size:.62rem;color:var(--accent);}
  .searchrow{padding:0 1rem;margin-top:.55rem;}
  #q{font-family:var(--sans);font-size:.92rem;border:1px solid var(--line);border-radius:11px;
    background:var(--surface);color:var(--ink);padding:.6rem .85rem;width:100%;min-height:42px;}
  #q:focus{outline:2px solid var(--accent);border-color:var(--accent);}

  .tabpanel{display:none;}
  .tabpanel.active{display:block;animation:rise .32s var(--spring);}
  @keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion: reduce){
    .tabpanel.active{animation:none;}
    .appitem .ic,.tabbtn,.cta,.iconbtn{transition:none;}
  }
  .srch-hidden{display:none!important;}

  h2{font-family:var(--serif);font-size:1.04rem;font-weight:600;margin:0 0 .8rem;}
  section{margin-bottom:1.5rem;}
  .panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
    padding:1.05rem 1.1rem;box-shadow:var(--shadow-card);}

  /* ---- sources ---- */
  .sources{display:flex;gap:.5rem;flex-wrap:wrap;}
  .src{display:flex;align-items:center;gap:.4rem;background:var(--surface);border:1px solid var(--line);
    border-radius:999px;padding:.38rem .72rem;font-size:.75rem;}
  .src-name{font-weight:600;}
  .src-dot{width:.5rem;height:.5rem;border-radius:50%;background:var(--muted);}
  .src-dot.fresh{background:var(--lume);box-shadow:0 0 9px var(--lume);}
  .src-dot.stale{background:var(--crit);}
  .src-when{font-family:var(--mono);font-size:.66rem;color:var(--muted);}
  .src-when.fresh{color:var(--lume);}
  .src-when.stale{color:var(--crit);}
  .healthrow{display:flex;gap:.45rem;flex-wrap:wrap;margin:.7rem 0 0;}
  .chiplet{font-family:var(--mono);font-size:.64rem;padding:.28rem .6rem;border-radius:999px;
    border:1px solid var(--line);color:var(--muted);}
  .chiplet.ok{border-color:var(--lume);color:var(--lume);}
  .chiplet.bad{border-color:var(--crit);color:var(--crit);font-weight:700;}

  /* ---- KPI cards ---- */
  .kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:.7rem;}
  .kpi{background:linear-gradient(160deg, var(--surface) 60%, var(--surface-2));border:1px solid var(--line);
    border-radius:var(--radius);padding:.9rem 1rem .75rem;box-shadow:var(--shadow-card);
    transition:transform .2s var(--spring), border-color .2s;}
  .kpi:active{transform:scale(.97);}
  .kpi .label{font-family:var(--mono);font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.09em;}
  .kpi .val{font-family:var(--serif);font-size:1.62rem;margin:.3rem 0 .15rem;letter-spacing:.01em;}
  .kpi-foot{display:flex;align-items:flex-end;justify-content:space-between;gap:.5rem;}
  .kpi .ctx{font-size:.72rem;color:var(--muted);}
  .sparkwrap{color:var(--accent);flex:0 0 auto;display:flex;}
  .sparkwrap.lume{color:var(--lume);}
  .sparkwrap.crit{color:var(--crit);}
  .spark{width:96px;height:30px;}

  /* ---- verdict ---- */
  .verdict{margin:0 0 1.3rem;display:flex;gap:.9rem;background:var(--surface);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:var(--radius);padding:1rem 1.1rem;box-shadow:var(--shadow-card);}
  .verdict b{font-family:var(--serif);font-weight:600;display:block;margin-bottom:.3rem;}
  .verdict p{margin:0;color:var(--muted);font-size:.87rem;line-height:1.55;}
  .verdict .stale{font-family:var(--mono);font-size:.66rem;color:var(--muted);display:block;margin-top:.5rem;}

  /* ---- dial gauges ---- */
  .dials{display:flex;gap:.7rem;flex-wrap:wrap;}
  .dial{flex:1 1 130px;max-width:200px;margin:0;background:radial-gradient(circle at 50% 30%, var(--surface-2), var(--surface) 75%);
    border:1px solid var(--hairline);border-radius:var(--radius);padding:.8rem .6rem .7rem;text-align:center;
    box-shadow:var(--shadow-card);}
  .dial svg{width:100%;max-width:132px;height:auto;}
  .dial .track{fill:none;stroke:var(--surface-2);stroke-width:5;stroke-linecap:round;}
  .dial .value{fill:none;stroke:var(--accent);stroke-width:5;stroke-linecap:round;
    filter:drop-shadow(0 0 5px var(--accent-soft));}
  .dial.crit .value{stroke:var(--crit);filter:drop-shadow(0 0 5px var(--crit-soft));}
  .dial.good .value{stroke:var(--lume);filter:drop-shadow(0 0 5px var(--lume-soft));}
  .dial .tick{stroke:var(--line);stroke-width:1;}
  .dial .tick.maj{stroke:var(--muted);stroke-width:1.4;}
  .dial .needle{stroke:var(--ink);stroke-width:1.6;stroke-linecap:round;
    transform-origin:60px 60px;}
  .dial .pivot{fill:var(--accent);}
  .dial .dval{font-family:var(--serif);font-size:1.15rem;fill:var(--ink);}
  .dial figcaption b{display:block;font-family:var(--mono);font-size:.6rem;text-transform:uppercase;
    letter-spacing:.09em;color:var(--muted);font-weight:600;margin-top:.2rem;}
  .dial figcaption span{font-size:.68rem;color:var(--muted);}

  /* ---- revenue chart ---- */
  .revchart{width:100%;height:auto;display:block;}
  .revchart .rev-bar{fill:var(--accent);opacity:.92;}
  .revchart .rev-bar:hover{fill:var(--accent-2);}
  .revchart .rev-bar.neg{fill:var(--crit);}
  .revchart .baseline{stroke:var(--line);stroke-width:1;}
  .revchart .ax{font-family:var(--mono);font-size:9px;fill:var(--muted);}
  .revchart .ax.hi{fill:var(--accent);}
  .chart-legend{display:flex;gap:1rem;font-size:.7rem;color:var(--muted);font-family:var(--mono);margin-top:.5rem;}
  .chart-legend i{display:inline-block;width:.65rem;height:.65rem;border-radius:2px;margin-right:.3rem;vertical-align:-1px;}
  .chart-legend .lg-sale i{background:var(--accent);}
  .chart-legend .lg-refund i{background:var(--crit);}

  /* ---- tables, funnel, channels ---- */
  table{width:100%;border-collapse:collapse;font-size:.84rem;}
  th,td{text-align:left;padding:.55rem .6rem;border-bottom:1px solid var(--line);}
  th{font-size:.66rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-family:var(--mono);}
  td.n,th.n{text-align:right;}
  .tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .empty{color:var(--muted);font-size:.9rem;}
  .f-note{color:var(--muted);font-size:.74rem;margin:.8rem 0 0;}
  .srcrow{display:grid;grid-template-columns:minmax(88px,128px) 1fr 58px;align-items:center;gap:.6rem;margin:.5rem 0;}
  .s-name{font-size:.82rem;}
  .s-track{background:var(--surface-2);border-radius:6px;height:13px;overflow:hidden;border:1px solid var(--line);}
  .s-fill{height:100%;background:linear-gradient(90deg, var(--accent), var(--accent-2));border-radius:5px;
    transform-origin:left;animation:grow .7s var(--spring) both;}
  @keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
  .s-val{font-size:.8rem;color:var(--muted);text-align:right;}
  .fstep{display:grid;grid-template-columns:minmax(88px,128px) 1fr 58px;align-items:center;gap:.6rem;margin:.55rem 0;}
  .fname{font-size:.83rem;}
  .ftrack{background:var(--surface-2);border-radius:7px;height:24px;overflow:hidden;border:1px solid var(--line);}
  .ffill{height:100%;border-radius:6px;background:linear-gradient(90deg,#3a6b4b,var(--accent));
    display:flex;align-items:center;justify-content:flex-end;min-width:30px;
    transform-origin:left;animation:grow .7s var(--spring) both;}
  .ffill span{font-size:.72rem;color:#10150f;font-weight:700;padding-right:.5rem;}
  .fpct{font-size:.75rem;color:var(--muted);text-align:right;}
  .oo-row{display:flex;align-items:center;gap:.8rem;padding:.55rem 0;border-bottom:1px solid var(--line);
    font-size:.88rem;flex-wrap:wrap;}
  .oo-row:last-of-type{border-bottom:none;}
  .oo-row b{flex:1;min-width:8rem;font-weight:600;}

  .pill{font-size:.62rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;padding:.22rem .5rem;
    border-radius:999px;border:1px solid var(--line);color:var(--muted);}
  .pill.good{color:var(--lume);background:var(--lume-soft);border-color:var(--lume);}
  .pill.warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn);}
  .pill.crit{color:var(--crit);background:var(--crit-soft);border-color:var(--crit);}

  /* ---- plan ---- */
  .a-project{font-family:var(--mono);font-size:.68rem;text-transform:uppercase;letter-spacing:.11em;
    color:var(--accent);margin:1rem 0 .2rem;}
  .a-item{padding:.65rem 0;border-bottom:1px solid var(--line);}
  .a-item:last-of-type{border-bottom:none;}
  .a-head{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;font-size:.9rem;}
  .a-head b{flex:1;min-width:10rem;}
  .a-why{margin:.3rem 0 0;font-size:.8rem;color:var(--muted);line-height:1.5;}
  .a-blocked{margin:.3rem 0 0;font-size:.78rem;color:var(--crit);line-height:1.5;}
  .donebtn{font-family:var(--mono);font-size:.62rem;color:var(--muted);background:transparent;
    border:1px solid var(--line);border-radius:7px;padding:.35rem .6rem;cursor:pointer;min-height:34px;
    transition:all .15s var(--spring);}
  .donebtn:hover{border-color:var(--lume);color:var(--lume);}
  .donebtn:active{transform:scale(.9);}

  /* ---- research / markdown ---- */
  .research-bar{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:.5rem;}
  #researchBtn{font-family:var(--mono);font-size:.72rem;border:1px solid var(--accent);color:var(--accent);
    background:transparent;border-radius:8px;padding:.5rem .85rem;cursor:pointer;min-height:38px;}
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
  .md a{color:var(--accent);}
  .md code{font-family:var(--mono);font-size:.82em;background:var(--surface-2);padding:.06em .3em;border-radius:3px;}
  .finding{padding:.75rem 0;border-bottom:1px solid var(--line);}
  .finding:last-of-type{border-bottom:none;}
  .finding p{margin:.25rem 0 0;font-size:.88rem;line-height:1.55;}
  .f-meta{font-family:var(--mono);font-size:.66rem;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;}

  /* ---- Key panel (bottom sheet on mobile, card on desktop) ---- */
  #keyveil{position:fixed;inset:0;z-index:70;background:rgba(6,10,8,.55);backdrop-filter:blur(3px);
    opacity:0;pointer-events:none;transition:opacity .2s;}
  #keyveil.open{opacity:1;pointer-events:auto;}
  #keypanel{position:fixed;left:0;right:0;bottom:0;z-index:75;background:var(--surface);
    border:1px solid var(--hairline);border-bottom:none;border-radius:18px 18px 0 0;
    padding:1.1rem 1.2rem calc(1.3rem + env(safe-area-inset-bottom,0));
    transform:translateY(105%);transition:transform .32s var(--spring);max-height:80vh;overflow-y:auto;
    box-shadow:0 -12px 40px rgba(0,0,0,.4);}
  #keypanel.open{transform:none;}
  .key-grip{width:38px;height:4px;border-radius:2px;background:var(--line);margin:0 auto .8rem;}
  .key-title{font-family:var(--serif);font-size:1.2rem;font-weight:600;margin:0 0 .15rem;}
  .key-sub{color:var(--muted);font-size:.8rem;margin:0 0 .9rem;}
  .key-add{display:flex;gap:.5rem;margin-bottom:.9rem;}
  #keyEmail{flex:1;font-size:.9rem;border:1px solid var(--line);border-radius:10px;background:var(--surface-2);
    color:var(--ink);padding:.6rem .8rem;min-height:44px;}
  #keyEmail:focus{outline:2px solid var(--accent);border-color:var(--accent);}
  #keyAddBtn{font-family:var(--mono);font-size:.78rem;font-weight:700;border:none;border-radius:10px;
    background:var(--accent);color:#151208;padding:0 1.1rem;min-height:44px;cursor:pointer;
    transition:transform .15s var(--spring);}
  #keyAddBtn:active{transform:scale(.93);}
  #keyAddBtn:disabled{opacity:.5;}
  .key-member{display:flex;align-items:center;gap:.6rem;padding:.6rem 0;border-bottom:1px solid var(--line);}
  .key-member:last-of-type{border-bottom:none;}
  .key-member .em{flex:1;font-size:.88rem;overflow:hidden;text-overflow:ellipsis;}
  .key-member .em small{display:block;font-family:var(--mono);font-size:.6rem;color:var(--accent);
    text-transform:uppercase;letter-spacing:.08em;}
  .key-rm{font-family:var(--mono);font-size:.66rem;border:1px solid var(--line);background:transparent;
    color:var(--muted);border-radius:7px;padding:.35rem .6rem;cursor:pointer;min-height:34px;}
  .key-rm:hover{border-color:var(--crit);color:var(--crit);}
  .key-rm:disabled{opacity:.35;cursor:not-allowed;}

  /* ---- toast ---- */
  .toast{position:fixed;left:50%;bottom:calc(var(--appnav-h) + env(safe-area-inset-bottom,0) + .9rem);
    transform:translateX(-50%) translateY(1rem);background:var(--surface-2);color:var(--ink);
    border:1px solid var(--accent);border-radius:11px;padding:.7rem 1rem;font-size:.82rem;z-index:95;
    opacity:0;pointer-events:none;transition:opacity .2s, transform .25s var(--spring);
    box-shadow:0 10px 34px rgba(0,0,0,.4);max-width:calc(100% - 2rem);text-align:center;}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
  footer{margin-top:2rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);
    font-family:var(--mono);font-size:.7rem;display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem;}

  /* ---- desktop ---- */
  @media (min-width:760px){
    :root{--appnav-h:0px;}
    .hub{padding-bottom:3rem;}
    .appnav{position:sticky;top:0;bottom:auto;border-top:none;border-bottom:1px solid var(--hairline);
      box-shadow:none;border-radius:0 0 var(--radius) var(--radius);backdrop-filter:none;}
    .appitem{flex:0 0 auto;flex-direction:row;gap:.45rem;min-height:auto;padding:.85rem 1.15rem;font-size:.72rem;}
    .appitem .ic{font-size:1rem;}
    .kpis{grid-template-columns:repeat(4,1fr);}
    .face-nav{margin-left:0;margin-right:0;}
    .face-tabs,.searchrow{padding-left:0;padding-right:0;}
    .toast{bottom:1.5rem;}
    #keypanel{left:50%;right:auto;bottom:50%;transform:translate(-50%,60%) scale(.96);opacity:0;
      width:min(460px,92vw);border-radius:18px;border-bottom:1px solid var(--hairline);
      transition:transform .3s var(--spring), opacity .2s;pointer-events:none;}
    #keypanel.open{transform:translate(-50%,50%) scale(1);opacity:1;pointer-events:auto;}
    .key-grip{display:none;}
  }
"""

# ------------------------------------------------------------------ JS  (plain string)
HUB_SCRIPT = r"""
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

  /* ---- count-up numbers ---- */
  var REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  function countUp(el){
    var target = parseFloat(el.dataset.countup);
    if(isNaN(target) || REDUCED) return;
    var prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    var dec = (el.dataset.countup.indexOf('.') !== -1 && target < 1000) ? 1 : 0;
    var t0 = null, dur = 900;
    function frame(ts){
      if(!t0) t0 = ts;
      var p = Math.min((ts - t0) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var v = target * eased;
      el.textContent = prefix + v.toLocaleString('en-IN', {maximumFractionDigits: dec, minimumFractionDigits: 0}) + suffix;
      if(p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  /* ---- pull-to-refresh (mobile) ---- */
  (function(){
    var startY = null, pulling = false, THRESH = 78;
    document.addEventListener('touchstart', function(e){
      if(window.scrollY <= 0 && !document.getElementById('keypanel').classList.contains('open')){
        startY = e.touches[0].clientY;
      } else startY = null;
    }, {passive: true});
    document.addEventListener('touchmove', function(e){
      if(startY === null) return;
      var dy = e.touches[0].clientY - startY;
      var ptr = document.getElementById('ptr');
      if(dy > 24 && window.scrollY <= 0){
        pulling = dy > THRESH;
        ptr.style.top = Math.min(dy * 0.42 - 46, 26) + 'px';
        ptr.querySelector('svg').style.transform = 'rotate(' + Math.min(dy * 2.2, 360) + 'deg)';
      }
    }, {passive: true});
    document.addEventListener('touchend', function(){
      var ptr = document.getElementById('ptr');
      if(pulling){
        ptr.style.top = '14px';
        ptr.classList.add('spin');
        doRefresh();
      } else {
        ptr.style.top = '-52px';
      }
      startY = null; pulling = false;
    });
  })();

  async function markDone(id, btn){
    btn.disabled = true; btn.textContent = '…';
    try {
      var res = await fetch('/ops/agent/api/plan/toggle', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id})
      });
      if (res.ok) { toast('Marked done ✓'); setTimeout(function(){ location.reload(); }, 700); return; }
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
    if(btn){ btn.disabled = true; }
    var url = days ? ('/ops/refresh?days=' + days) : '/ops/refresh';
    try {
      var res = await fetch(url, {method:'POST'});
      if (res.ok) { location.reload(); return; }
      var body = await res.json().catch(function(){ return {}; });
      toast(body.error === 'refresh already running' ? 'Already refreshing…' : 'Refresh failed — retry');
    } catch (e) { toast('Refresh failed — retry'); }
    var ptr = document.getElementById('ptr');
    ptr.classList.remove('spin'); ptr.style.top = '-52px';
    if(btn){ setTimeout(function(){ btn.disabled = false; }, 2500); }
  }

  /* ---- account / admin ---- */
  async function initAccount(){
    try {
      var res = await fetch('/ops/agent/api/whoami');
      if(!res.ok) return;
      var info = await res.json();
      if(info.email){
        var who = document.getElementById('who');
        if(who) who.textContent = info.email;
      }
      if(info.admin) document.body.classList.add('is-admin');
    } catch (e) {}
  }

  /* ---- Key panel ---- */
  function keyOpen(){
    document.getElementById('keyveil').classList.add('open');
    document.getElementById('keypanel').classList.add('open');
    keyLoad();
  }
  function keyClose(){
    document.getElementById('keyveil').classList.remove('open');
    document.getElementById('keypanel').classList.remove('open');
  }
  function renderMembers(members){
    var box = document.getElementById('keyMembers');
    box.innerHTML = '';
    members.forEach(function(m){
      var row = document.createElement('div');
      row.className = 'key-member';
      var em = document.createElement('div');
      em.className = 'em';
      em.textContent = m.email;
      if(m.admin){
        var s = document.createElement('small');
        s.textContent = m.you ? 'admin · you' : 'admin';
        em.appendChild(s);
      }
      var rm = document.createElement('button');
      rm.className = 'key-rm';
      rm.textContent = 'remove';
      rm.disabled = !!m.you;
      rm.onclick = function(){ keyRemove(m.email, rm); };
      row.appendChild(em); row.appendChild(rm);
      box.appendChild(row);
    });
  }
  async function keyLoad(){
    try {
      var res = await fetch('/ops/agent/api/allowlist');
      var data = await res.json();
      if(!res.ok) throw new Error(data.error || 'failed');
      renderMembers(data.members);
    } catch(e) {
      document.getElementById('keyMembers').innerHTML = '<p class="empty">' + (e.message || 'Could not load members') + '</p>';
    }
  }
  async function keyAdd(){
    var input = document.getElementById('keyEmail');
    var btn = document.getElementById('keyAddBtn');
    var email = input.value.trim();
    if(!email) return;
    btn.disabled = true;
    try {
      var res = await fetch('/ops/agent/api/allowlist/add', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({email: email})
      });
      var data = await res.json();
      if(!res.ok) throw new Error(data.error || 'failed');
      input.value = '';
      toast(data.note === 'already a member' ? email + ' is already in' : email + ' can now sign in ✓');
      keyLoad();
    } catch(e) { toast(e.message || 'Could not add'); }
    btn.disabled = false;
  }
  async function keyRemove(email, btn){
    if(!confirm('Remove ' + email + '? They will lose access on their next page load.')) return;
    btn.disabled = true;
    try {
      var res = await fetch('/ops/agent/api/allowlist/remove', {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({email: email})
      });
      var data = await res.json();
      if(!res.ok) throw new Error(data.error || 'failed');
      toast(email + ' removed');
      keyLoad();
    } catch(e) { toast(e.message || 'Could not remove'); btn.disabled = false; }
  }

  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.tabbtn').forEach(function(b){ b.addEventListener('click', function(){ showTab(b.dataset.tab); }); });
    var q = document.getElementById('q');
    if(q) q.addEventListener('input', applySearch);
    document.querySelectorAll('.appitem[data-soon]').forEach(function(a){
      a.addEventListener('click', function(e){ e.preventDefault(); toast(a.dataset.soon + ' is coming soon — building it next.'); });
    });
    var keyBtn = document.getElementById('keyNav');
    if(keyBtn) keyBtn.addEventListener('click', function(e){ e.preventDefault(); keyOpen(); });
    document.getElementById('keyveil').addEventListener('click', keyClose);
    document.getElementById('keyAddBtn').addEventListener('click', keyAdd);
    document.getElementById('keyEmail').addEventListener('keydown', function(e){ if(e.key === 'Enter') keyAdd(); });
    document.addEventListener('keydown', function(e){
      if(e.key === 'Escape') keyClose();
      if(e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA'){
        e.preventDefault(); if(q) q.focus();
      }
    });
    showTab((location.hash || '#overview').slice(1));
    document.querySelectorAll('[data-countup]').forEach(countUp);
    initAccount();
  });
"""


def _appnav(active="face", drop_ready=False):
    """OS app switcher. Unbuilt items toast instead of dead-linking."""
    drop_attr = 'href="/drop/"' if drop_ready else 'href="#" data-soon="Drop"'
    items = [
        ('face', 'href="/ops/#overview"', '◷', 'Face', ""),
        ('drop', drop_attr, '⬆', 'Drop', ""),
        ('ledger', 'href="#" data-soon="Ledger"', '₹', 'Ledger', ""),
        ('chat', 'href="/ops/agent/"', '✦', 'Chat', ""),
        ('key', 'href="#" id="keyNav"', '⚷', 'Key', " admin-only"),
    ]
    out = []
    for key, attr, icon, label, extra in items:
        cls = "appitem" + (" active" if key == active else "") + extra
        out.append(f'<a class="{cls}" {attr}><span class="ic">{icon}</span><span>{label}</span></a>')
    return '<nav class="appnav" aria-label="Apps">' + "".join(out) + '</nav>'


KEY_PANEL = """
<div id="keyveil"></div>
<aside id="keypanel" role="dialog" aria-label="Key — manage access">
  <div class="key-grip"></div>
  <h3 class="key-title">⚷ Key</h3>
  <p class="key-sub">Who can sign in to Hub with their Google account. Changes apply instantly.</p>
  <div class="key-add">
    <input id="keyEmail" type="email" inputmode="email" autocomplete="off"
           placeholder="teammate@gmail.com">
    <button id="keyAddBtn">Add</button>
  </div>
  <div id="keyMembers"><p class="empty">Loading…</p></div>
</aside>
"""

PTR = """
<div id="ptr" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
  stroke-linecap="round"><path d="M20 11A8 8 0 1 0 18.6 15.5"/><path d="M20 4v7h-7"/></svg></div>
"""


def page(*, generated_at, lookback_days, source_status_html, kpi_html, verdict_html,
         funnel_html, channel_html, campaign_html, overview_orders_html, orders_html,
         plan_html, research_html, content_html, findings_html, health_html,
         dials_html="", revenue_chart_html="", drop_ready=False):
    """Assemble the full Hub/Face HTML from data fragments built by generate.py."""
    def rb(days, label):
        active = ' class="active"' if lookback_days == days else ""
        return f'<button{active} onclick="doRefresh({days})">{label}</button>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0f0c">
<meta name="color-scheme" content="dark light">
<title>Timelabs Hub — Face</title>
<style>{HUB_STYLE}</style>
<script>{HUB_SCRIPT}</script>
</head>
<body>
{PTR}
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
      <p class="face-sub">The state of the business, at a glance.</p>
      <div class="face-controls">
        <div class="rangebar">{rb(7, "7d")}{rb(30, "30d")}{rb(90, "90d")}</div>
        <span class="refreshed num">refreshed {generated_at}</span>
      </div>
    </div>

    <div class="cta-row">
      <a class="cta primary" href="/ops/agent/">✦ Ask the team</a>
      <a class="cta" href="/ops/playbooks/community-playbook.pdf">Playbook</a>
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
        <h2>Key numbers</h2>
        <div class="kpis">{kpi_html}</div>
      </section>
      {f'<section><h2>Instruments</h2><div class="dials">{dials_html}</div></section>' if dials_html else ''}
      {revenue_chart_html}
      {overview_orders_html}
      {funnel_html}
      {channel_html}
      {campaign_html}
      <section>
        <h2>Source status</h2>
        <div class="sources">{source_status_html}</div>
        <div class="healthrow">{health_html}</div>
      </section>
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
{KEY_PANEL}
<div id="toast" class="toast" role="status" aria-live="polite"></div>
</body>
</html>
"""
