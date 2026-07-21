"""Timelabs Hub — "Meridian" shell: clean modern design system.

Owns the chrome and chart engine of Hub: header, mobile bottom-bar / desktop
top-nav, Face underline tabs, the Key member panel, server-side SVG charts,
and all CSS/JS. generate.py builds data fragments and calls page(...).

Design language (v3): Linear/Notion-class. Light-first with a first-class dark
mode, system sans stack, generous whitespace, hairline borders, one restrained
gold accent, subtle 150ms motion. No gauges, no serif numerals, no retro.

Never hand-edit /var/www/ops/index.html — it is generated from here.
"""
import datetime
import html

# Mirrors agent_chat_server.ADMIN_EMAILS — keep in sync.
ADMIN_EMAILS = ("timelabs.inc@gmail.com", "schezan.m@gmail.com")


# --------------------------------------------------------------------- helpers
def _refund_pct(s):
    gross = s.get("gross_sales") or s.get("total_sales") or 0
    refunds = s.get("refund_total") or 0
    if gross <= 0:
        return ""
    return f"{100 * refunds / gross:.0f}% of gross"


def fmt_compact_inr(n):
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
        return (f'<div class="src"><span class="src-dot off"></span>'
                f'<span class="src-name">{html.escape(name)}</span>'
                f'<span class="src-when">not connected</span></div>')
    date = src.get("as_of") or src.get("via_bridge") or ""
    cls, when = "fresh", "live"
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(date)).days
        if age <= 0:
            when = "today"
        elif age == 1:
            when = "yesterday"
        else:
            when = date
        if age > 3:
            cls = "stale"
    except (ValueError, TypeError):
        pass
    return (f'<div class="src"><span class="src-dot {cls}"></span>'
            f'<span class="src-name">{html.escape(name)}</span>'
            f'<span class="src-when {cls}">{html.escape(when)}</span></div>')


def stat_pill(label, value, tone=""):
    """Compact inline stat for the conversion strip (replaces the old gauges)."""
    return (f'<div class="stat {tone}"><span class="stat-val num">{html.escape(value)}</span>'
            f'<span class="stat-label">{html.escape(label)}</span></div>')


# ----------------------------------------------------------------- SVG charts
def svg_spark(values, w=104, h=30, cls="spark"):
    """Tiny inline sparkline. Returns '' when there's nothing to plot."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(vals):
        x = 2 + i * (w - 4) / (len(vals) - 1)
        y = h - 3 - (v - lo) / rng * (h - 6)
        pts.append(f"{x:.1f},{y:.1f}")
    last = pts[-1].split(",")
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="{last[0]}" cy="{last[1]}" r="2.2" fill="currentColor"/></svg>')


def _zero_fill(sparse, span):
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


def svg_revenue_chart(sales_daily, span, w=680, h=180):
    """Daily revenue bars; refund days plotted below the baseline in red."""
    if not sales_daily or not span:
        return ""
    series = _zero_fill(sales_daily, span)
    vals = [v for _, v in series]
    hi = max(max(vals), 1.0)
    lo = min(min(vals), 0.0)
    rng = hi - lo or 1.0
    pad_t, pad_b, pad_x = 14, 18, 4
    plot_h = h - pad_t - pad_b
    n = len(series)
    step = (w - 2 * pad_x) / n
    bw = max(2.5, step * 0.6)
    base_y = pad_t + (hi / rng) * plot_h
    bars, labels = [], []
    for i, (day, v) in enumerate(series):
        if v == 0:
            continue
        x = pad_x + i * step + (step - bw) / 2
        bh = abs(v) / rng * plot_h
        y = base_y - bh if v > 0 else base_y
        cls = "rev-bar" if v > 0 else "rev-bar neg"
        bars.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(bh,1.5):.1f}" rx="1.5">'
            f'<title>{day} · {fmt_compact_inr(v)}</title></rect>'
        )
    seen = set()
    for i, (day, _) in enumerate(series):
        m = day[:7]
        if m in seen:
            continue
        seen.add(m)
        mon = datetime.date.fromisoformat(day).strftime("%b")
        labels.append(f'<text class="ax" x="{pad_x + i * step:.1f}" y="{h-4}">{mon}</text>')
    peak = f'<text class="ax hi" x="{w-pad_x}" y="{pad_t-3}" text-anchor="end">{fmt_compact_inr(hi)}</text>'
    return (f'<svg class="revchart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Daily sales, last 90 days">'
            f'<line class="baseline" x1="{pad_x}" y1="{base_y:.1f}" x2="{w-pad_x}" y2="{base_y:.1f}"/>'
            f'{"".join(bars)}{"".join(labels)}{peak}</svg>')


def kpi_card(label, value, ctx, spark_vals=None, raw=None, prefix="", spark_tone=""):
    countup = f' data-countup="{raw}" data-prefix="{html.escape(prefix)}"' if raw is not None else ""
    spark = svg_spark(spark_vals) if spark_vals else ""
    return (f'<div class="kpi"><div class="kpi-top"><span class="label">{html.escape(label)}</span>'
            f'<span class="sparkwrap {spark_tone}">{spark}</span></div>'
            f'<div class="val num"{countup}>{html.escape(value)}</div>'
            f'<div class="ctx">{html.escape(ctx)}</div></div>')


# ------------------------------------------------------------------ CSS
HUB_STYLE = r"""
  :root{
    --bg:#f8f8f7; --card:#ffffff; --card-2:#f4f4f2; --border:#e8e7e3; --border-2:#dedcd6;
    --ink:#191918; --body:#3f3f3c; --muted:#84837d;
    --accent:#996c1f; --accent-ink:#7a5416; --accent-bg:rgba(153,108,31,.08);
    --good:#188351; --good-bg:rgba(24,131,81,.09);
    --bad:#cc3f2f; --bad-bg:rgba(204,63,47,.08);
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Inter","Helvetica Neue",Arial,sans-serif;
    --mono:ui-monospace,"SF Mono","Cascadia Code",Consolas,monospace;
    --nav-h:60px; --r:12px; --r-s:8px;
    --shadow:0 1px 2px rgba(25,25,24,.04);
    --shadow-lg:0 4px 24px rgba(25,25,24,.08);
    --ease:cubic-bezier(.25,.6,.3,1);
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#111113; --card:#1a1a1d; --card-2:#212124; --border:#2a2a2e; --border-2:#37373c;
      --ink:#f2f2f0; --body:#c9c9c4; --muted:#8b8b86;
      --accent:#d8a94c; --accent-ink:#e5be6e; --accent-bg:rgba(216,169,76,.10);
      --good:#3fc380; --good-bg:rgba(63,195,128,.10);
      --bad:#ef6b5b; --bad-bg:rgba(239,107,91,.10);
      --shadow:0 1px 2px rgba(0,0,0,.2);
      --shadow-lg:0 8px 32px rgba(0,0,0,.35);
    }
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--bg);color:var(--body);font-family:var(--sans);line-height:1.55;
    font-size:15px;-webkit-font-smoothing:antialiased;overscroll-behavior-y:contain;}
  .num{font-variant-numeric:tabular-nums lining-nums}
  a{color:var(--accent);text-decoration:none;}
  a:hover{text-decoration:underline;}
  button{font:inherit;color:inherit;}
  h1,h2,h3{color:var(--ink);}
  .wrap{max-width:1020px;margin:0 auto;padding:0 20px calc(var(--nav-h) + env(safe-area-inset-bottom,0) + 28px);}

  /* ---- pull-to-refresh ---- */
  #ptr{position:fixed;top:-52px;left:50%;transform:translateX(-50%);z-index:90;width:38px;height:38px;
    border-radius:50%;background:var(--card);border:1px solid var(--border-2);display:flex;
    align-items:center;justify-content:center;color:var(--accent);transition:top .18s;box-shadow:var(--shadow-lg);}
  #ptr svg{width:18px;height:18px;transition:transform .1s;}
  #ptr.spin svg{animation:ptrspin .8s linear infinite;}
  @keyframes ptrspin{to{transform:rotate(360deg)}}

  /* ---- header ---- */
  .topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 0 14px;}
  .brand{display:flex;align-items:center;gap:10px;text-decoration:none;}
  .brand:hover{text-decoration:none;}
  .brand-dot{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent-ink));
    display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:13px;}
  .brand-name{font-weight:700;font-size:15px;color:var(--ink);letter-spacing:-.01em;}
  .brand-name span{color:var(--muted);font-weight:500;}
  .top-actions{display:flex;align-items:center;gap:8px;}
  .who{font-size:12px;color:var(--muted);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .iconbtn{cursor:pointer;border:1px solid var(--border);background:var(--card);color:var(--body);
    border-radius:var(--r-s);width:36px;height:36px;display:inline-flex;align-items:center;justify-content:center;
    font-size:15px;transition:all .15s var(--ease);box-shadow:var(--shadow);}
  .iconbtn:hover{border-color:var(--border-2);color:var(--ink);}
  .iconbtn:active{transform:scale(.94);}
  .iconbtn:disabled{opacity:.5;cursor:wait;}

  /* ---- app nav: bottom bar (mobile) / inline pills (desktop) ---- */
  .appnav{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;background:var(--card);
    border-top:1px solid var(--border);padding-bottom:env(safe-area-inset-bottom,0);
    box-shadow:0 -4px 20px rgba(0,0,0,.06);}
  .appitem{position:relative;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:3px;padding:8px 4px;min-height:var(--nav-h);text-decoration:none;color:var(--muted);
    font-size:10.5px;font-weight:600;background:none;border:none;cursor:pointer;transition:color .15s;}
  .appitem:hover{text-decoration:none;}
  .appitem .ic{width:21px;height:21px;}
  .appitem .ic svg{width:100%;height:100%;stroke:currentColor;fill:none;stroke-width:1.8;
    stroke-linecap:round;stroke-linejoin:round;}
  .appitem.active{color:var(--accent);}
  .appitem:active .ic{transform:scale(.88);}
  .admin-only{display:none;}
  body.is-admin .admin-only{display:flex;}

  /* ---- page head ---- */
  .page-head{padding:6px 0 18px;}
  .page-title{font-size:26px;font-weight:750;letter-spacing:-.02em;margin:0 0 2px;}
  .page-sub{color:var(--muted);font-size:14px;margin:0 0 14px;}
  .head-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
  .seg{display:inline-flex;background:var(--card-2);border:1px solid var(--border);border-radius:9px;padding:2px;}
  .seg button{border:none;background:transparent;color:var(--muted);font-size:12.5px;font-weight:600;
    padding:6px 12px;border-radius:7px;cursor:pointer;transition:all .15s var(--ease);}
  .seg button.active{background:var(--card);color:var(--ink);box-shadow:var(--shadow);}
  .refreshed{font-size:12px;color:var(--muted);}
  .btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);background:var(--card);
    color:var(--ink);font-size:13px;font-weight:600;border-radius:var(--r-s);padding:8px 14px;cursor:pointer;
    text-decoration:none;transition:all .15s var(--ease);box-shadow:var(--shadow);}
  .btn:hover{border-color:var(--border-2);text-decoration:none;}
  .btn:active{transform:scale(.97);}
  .btn.primary{background:var(--ink);border-color:var(--ink);color:var(--bg);}
  .btn.primary:hover{opacity:.9;}

  /* ---- Face tabs: underline style ---- */
  .face-nav{position:sticky;top:0;z-index:30;background:var(--bg);margin:0 -20px 20px;padding:6px 0 0;
    border-bottom:1px solid var(--border);}
  .face-tabs{display:flex;gap:2px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:0 20px;}
  .face-tabs::-webkit-scrollbar{display:none;}
  .tabbtn{position:relative;flex:0 0 auto;border:none;background:none;color:var(--muted);font-size:13.5px;
    font-weight:600;padding:10px 12px;cursor:pointer;white-space:nowrap;transition:color .15s;}
  .tabbtn:hover{color:var(--ink);}
  .tabbtn.active{color:var(--ink);}
  .tabbtn.active::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;
    border-radius:2px;background:var(--accent);}
  .tabbadge{margin-left:5px;font-size:11px;color:var(--accent);}
  .searchrow{padding:0 20px 10px;margin-top:2px;}
  #q{font-family:var(--sans);font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--card);color:var(--ink);padding:9px 12px;width:100%;transition:border-color .15s;}
  #q::placeholder{color:var(--muted);}
  #q:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}

  .tabpanel{display:none;}
  .tabpanel.active{display:block;animation:fadeup .22s var(--ease);}
  @keyframes fadeup{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion: reduce){.tabpanel.active{animation:none;}}
  .srch-hidden{display:none!important;}

  h2{font-size:14px;font-weight:650;margin:0 0 10px;letter-spacing:-.01em;}
  section{margin-bottom:22px;}
  .panel{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px;
    box-shadow:var(--shadow);}

  /* ---- insight banner ---- */
  .verdict{margin:0 0 20px;background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    box-shadow:var(--shadow);overflow:hidden;}
  .verdict summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;padding:14px 18px;}
  .verdict summary::-webkit-details-marker{display:none;}
  .verdict .v-icon{flex:0 0 auto;width:28px;height:28px;border-radius:8px;background:var(--accent-bg);
    color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:14px;}
  .verdict .v-teaser{flex:1;font-size:13.5px;color:var(--body);overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;min-width:0;}
  .verdict .v-chev{color:var(--muted);transition:transform .2s;}
  .verdict[open] .v-chev{transform:rotate(180deg);}
  .verdict[open] .v-teaser{white-space:normal;display:none;}
  .verdict .v-body{padding:0 18px 16px 56px;font-size:13.5px;line-height:1.6;color:var(--body);}
  .verdict .v-model{display:block;margin-top:8px;font-size:11px;color:var(--muted);}

  /* ---- KPI cards ---- */
  .kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;}
  .kpi{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;
    box-shadow:var(--shadow);transition:border-color .15s;}
  .kpi:hover{border-color:var(--border-2);}
  .kpi-top{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:22px;}
  .kpi .label{font-size:12px;font-weight:600;color:var(--muted);}
  .kpi .val{font-size:24px;font-weight:700;letter-spacing:-.02em;color:var(--ink);margin:2px 0 1px;}
  .kpi .ctx{font-size:12px;color:var(--muted);}
  .sparkwrap{color:var(--accent);display:flex;opacity:.9;}
  .sparkwrap.lume{color:var(--good);}
  .sparkwrap.crit{color:var(--bad);}
  .spark{width:84px;height:26px;}

  /* ---- conversion stat strip ---- */
  .stats{display:flex;gap:10px;flex-wrap:wrap;}
  .stat{flex:1 1 120px;background:var(--card);border:1px solid var(--border);border-radius:var(--r);
    padding:12px 16px;box-shadow:var(--shadow);}
  .stat-val{display:block;font-size:20px;font-weight:700;letter-spacing:-.02em;color:var(--ink);}
  .stat.crit .stat-val{color:var(--bad);}
  .stat.good .stat-val{color:var(--good);}
  .stat-label{font-size:12px;color:var(--muted);}

  /* ---- charts ---- */
  .revchart{width:100%;height:auto;display:block;}
  .revchart .rev-bar{fill:var(--accent);opacity:.85;}
  .revchart .rev-bar:hover{opacity:1;}
  .revchart .rev-bar.neg{fill:var(--bad);}
  .revchart .baseline{stroke:var(--border-2);stroke-width:1;}
  .revchart .ax{font-family:var(--sans);font-size:9.5px;fill:var(--muted);}
  .revchart .ax.hi{fill:var(--accent);}
  .chart-legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-top:8px;}
  .chart-legend i{display:inline-block;width:9px;height:9px;border-radius:2.5px;margin-right:5px;}
  .chart-legend .lg-sale i{background:var(--accent);}
  .chart-legend .lg-refund i{background:var(--bad);}

  /* ---- sources ---- */
  .sources{display:flex;gap:8px;flex-wrap:wrap;}
  .src{display:flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--border);
    border-radius:999px;padding:6px 12px;font-size:12.5px;}
  .src-name{font-weight:600;color:var(--ink);}
  .src-dot{width:7px;height:7px;border-radius:50%;background:var(--muted);}
  .src-dot.fresh{background:var(--good);}
  .src-dot.stale{background:var(--bad);}
  .src-when{font-size:11.5px;color:var(--muted);}
  .src-when.stale{color:var(--bad);}
  .healthrow{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;}
  .chiplet{font-size:11px;font-weight:600;padding:4px 10px;border-radius:999px;background:var(--card-2);
    border:1px solid var(--border);color:var(--muted);}
  .chiplet.ok{color:var(--good);background:var(--good-bg);border-color:transparent;}
  .chiplet.bad{color:var(--bad);background:var(--bad-bg);border-color:transparent;}

  /* ---- tables / funnel / channels ---- */
  table{width:100%;border-collapse:collapse;font-size:13.5px;}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--border);}
  tr:last-child td{border-bottom:none;}
  th{font-size:11.5px;font-weight:600;color:var(--muted);}
  td{color:var(--body);}
  td.n,th.n{text-align:right;}
  .tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .empty{color:var(--muted);font-size:13.5px;}
  .f-note{color:var(--muted);font-size:12px;margin:12px 0 0;}
  .srcrow{display:grid;grid-template-columns:minmax(84px,124px) 1fr 54px;align-items:center;gap:10px;margin:8px 0;}
  .s-name{font-size:13px;color:var(--body);}
  .s-track{background:var(--card-2);border-radius:5px;height:10px;overflow:hidden;}
  .s-fill{height:100%;background:var(--accent);border-radius:5px;transform-origin:left;
    animation:grow .5s var(--ease) both;}
  @keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
  .s-val{font-size:12.5px;color:var(--muted);text-align:right;}
  .fstep{display:grid;grid-template-columns:minmax(84px,124px) 1fr 54px;align-items:center;gap:10px;margin:9px 0;}
  .fname{font-size:13px;color:var(--body);}
  .ftrack{background:var(--card-2);border-radius:6px;height:20px;overflow:hidden;}
  .ffill{height:100%;border-radius:6px;background:var(--accent);display:flex;align-items:center;
    justify-content:flex-end;min-width:28px;transform-origin:left;animation:grow .5s var(--ease) both;}
  .ffill span{font-size:11px;color:#fff;font-weight:700;padding-right:7px;}
  .fpct{font-size:12px;color:var(--muted);text-align:right;}
  .oo-row{display:flex;align-items:center;gap:12px;padding:9px 0;border-bottom:1px solid var(--border);
    font-size:13.5px;flex-wrap:wrap;}
  .oo-row:last-of-type{border-bottom:none;}
  .oo-row b{flex:1;min-width:8rem;font-weight:600;color:var(--ink);}

  .pill{font-size:10.5px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;
    border-radius:999px;background:var(--card-2);border:1px solid var(--border);color:var(--muted);}
  .pill.good{color:var(--good);background:var(--good-bg);border-color:transparent;}
  .pill.warn{color:var(--accent);background:var(--accent-bg);border-color:transparent;}
  .pill.crit{color:var(--bad);background:var(--bad-bg);border-color:transparent;}

  /* ---- plan ---- */
  .a-project{font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
    color:var(--accent);margin:16px 0 4px;}
  .a-item{padding:10px 0;border-bottom:1px solid var(--border);}
  .a-item:last-of-type{border-bottom:none;}
  .a-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13.5px;}
  .a-head b{flex:1;min-width:10rem;color:var(--ink);font-weight:600;}
  .a-why{margin:4px 0 0;font-size:12.5px;color:var(--muted);line-height:1.5;}
  .a-blocked{margin:4px 0 0;font-size:12.5px;color:var(--bad);line-height:1.5;}
  .donebtn{font-size:11.5px;font-weight:600;color:var(--muted);background:var(--card);
    border:1px solid var(--border);border-radius:7px;padding:5px 10px;cursor:pointer;
    transition:all .15s var(--ease);}
  .donebtn:hover{border-color:var(--good);color:var(--good);}
  .donebtn:active{transform:scale(.94);}

  /* ---- research / markdown ---- */
  .research-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:8px;}
  #researchBtn{font-size:12.5px;font-weight:600;border:1px solid var(--border);color:var(--accent);
    background:var(--card);border-radius:var(--r-s);padding:7px 13px;cursor:pointer;transition:all .15s;}
  #researchBtn:hover{border-color:var(--accent);background:var(--accent-bg);}
  #researchBtn:disabled{opacity:.55;cursor:wait;}
  .research-body summary{cursor:pointer;font-size:12px;color:var(--muted);margin-bottom:6px;}
  .md h1{font-size:17px;font-weight:700;margin:14px 0 6px;letter-spacing:-.01em;}
  .md h2{font-size:14.5px;font-weight:650;margin:14px 0 5px;}
  .md p,.md li{font-size:13.5px;line-height:1.6;color:var(--body);}
  .md ul,.md ol{padding-left:20px;}
  .md table{border-collapse:collapse;font-size:12.5px;display:block;overflow-x:auto;margin:8px 0;}
  .md th,.md td{border:1px solid var(--border);padding:5px 9px;text-align:left;}
  .md th{background:var(--card-2);}
  .md code{font-family:var(--mono);font-size:.85em;background:var(--card-2);padding:1px 5px;border-radius:4px;}
  .finding{padding:11px 0;border-bottom:1px solid var(--border);}
  .finding:last-of-type{border-bottom:none;}
  .finding p{margin:3px 0 0;font-size:13.5px;line-height:1.55;color:var(--body);}
  .f-meta{font-size:11px;font-weight:650;color:var(--accent);text-transform:uppercase;letter-spacing:.04em;}

  /* ---- Key panel ---- */
  #keyveil{position:fixed;inset:0;z-index:70;background:rgba(15,15,14,.4);backdrop-filter:blur(2px);
    opacity:0;pointer-events:none;transition:opacity .18s;}
  #keyveil.open{opacity:1;pointer-events:auto;}
  #keypanel{position:fixed;left:0;right:0;bottom:0;z-index:75;background:var(--card);
    border:1px solid var(--border);border-bottom:none;border-radius:16px 16px 0 0;
    padding:16px 20px calc(22px + env(safe-area-inset-bottom,0));
    transform:translateY(105%);transition:transform .28s var(--ease);max-height:80vh;overflow-y:auto;
    box-shadow:var(--shadow-lg);}
  #keypanel.open{transform:none;}
  .key-grip{width:36px;height:4px;border-radius:2px;background:var(--border-2);margin:0 auto 14px;}
  .key-title{font-size:17px;font-weight:700;margin:0 0 2px;letter-spacing:-.01em;}
  .key-sub{color:var(--muted);font-size:13px;margin:0 0 14px;}
  .key-add{display:flex;gap:8px;margin-bottom:14px;}
  #keyEmail{flex:1;font-size:14px;border:1px solid var(--border);border-radius:var(--r-s);
    background:var(--bg);color:var(--ink);padding:9px 12px;min-height:42px;}
  #keyEmail:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg);}
  #keyAddBtn{font-size:13px;font-weight:650;border:none;border-radius:var(--r-s);background:var(--ink);
    color:var(--bg);padding:0 18px;min-height:42px;cursor:pointer;transition:all .15s var(--ease);}
  #keyAddBtn:hover{opacity:.9;}
  #keyAddBtn:active{transform:scale(.95);}
  #keyAddBtn:disabled{opacity:.5;}
  .key-member{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--border);}
  .key-member:last-of-type{border-bottom:none;}
  .key-member .em{flex:1;font-size:13.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;}
  .key-member .em small{display:block;font-size:10.5px;font-weight:650;color:var(--accent);
    text-transform:uppercase;letter-spacing:.05em;}
  .key-rm{font-size:11.5px;font-weight:600;border:1px solid var(--border);background:transparent;
    color:var(--muted);border-radius:7px;padding:5px 10px;cursor:pointer;transition:all .15s;}
  .key-rm:hover{border-color:var(--bad);color:var(--bad);}
  .key-rm:disabled{opacity:.35;cursor:not-allowed;}

  /* ---- toast ---- */
  .toast{position:fixed;left:50%;bottom:calc(var(--nav-h) + env(safe-area-inset-bottom,0) + 14px);
    transform:translateX(-50%) translateY(10px);background:var(--ink);color:var(--bg);
    border-radius:10px;padding:10px 16px;font-size:13px;font-weight:550;z-index:95;
    opacity:0;pointer-events:none;transition:all .22s var(--ease);box-shadow:var(--shadow-lg);
    max-width:calc(100% - 32px);text-align:center;}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
  footer{margin-top:28px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);
    font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;}

  /* ---- desktop ---- */
  @media (min-width:760px){
    :root{--nav-h:0px;}
    body{font-size:14.5px;}
    .wrap{padding-bottom:44px;}
    .appnav{position:static;display:inline-flex;background:var(--card-2);border:1px solid var(--border);
      border-radius:10px;padding:3px;box-shadow:none;gap:2px;}
    .appitem{flex:0 0 auto;flex-direction:row;gap:7px;min-height:auto;padding:7px 14px;font-size:12.5px;
      border-radius:8px;}
    .appitem .ic{width:15px;height:15px;}
    .appitem.active{background:var(--card);color:var(--ink);box-shadow:var(--shadow);}
    .topbar{padding:20px 0 18px;}
    .kpis{grid-template-columns:repeat(4,1fr);}
    .face-nav{margin:0 0 22px;}
    .face-tabs,.searchrow{padding-left:0;padding-right:0;}
    .toast{bottom:24px;}
    #keypanel{left:50%;right:auto;bottom:50%;transform:translate(-50%,60%) scale(.97);opacity:0;
      width:min(440px,92vw);border-radius:14px;border-bottom:1px solid var(--border);
      transition:transform .25s var(--ease), opacity .18s;pointer-events:none;}
    #keypanel.open{transform:translate(-50%,50%) scale(1);opacity:1;pointer-events:auto;}
    .key-grip{display:none;}
  }
"""

# ------------------------------------------------------------------ JS
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

  var REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  function countUp(el){
    var target = parseFloat(el.dataset.countup);
    if(isNaN(target) || REDUCED) return;
    var prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    var t0 = null, dur = 700;
    function frame(ts){
      if(!t0) t0 = ts;
      var p = Math.min((ts - t0) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var v = target * eased;
      el.textContent = prefix + Math.round(v).toLocaleString('en-IN') + suffix;
      if(p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  (function(){
    var startY = null, pulling = false, THRESH = 78;
    document.addEventListener('touchstart', function(e){
      var kp = document.getElementById('keypanel');
      if(window.scrollY <= 0 && !(kp && kp.classList.contains('open'))){
        startY = e.touches[0].clientY;
      } else startY = null;
    }, {passive: true});
    document.addEventListener('touchmove', function(e){
      if(startY === null) return;
      var dy = e.touches[0].clientY - startY;
      var ptr = document.getElementById('ptr');
      if(dy > 24 && window.scrollY <= 0){
        pulling = dy > THRESH;
        ptr.style.top = Math.min(dy * 0.42 - 46, 24) + 'px';
        ptr.querySelector('svg').style.transform = 'rotate(' + Math.min(dy * 2.2, 360) + 'deg)';
      }
    }, {passive: true});
    document.addEventListener('touchend', function(){
      var ptr = document.getElementById('ptr');
      if(pulling){
        ptr.style.top = '12px';
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
      if (res.ok) { toast('Marked done'); setTimeout(function(){ location.reload(); }, 650); return; }
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
      rm.textContent = 'Remove';
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
      toast(data.note === 'already a member' ? email + ' is already in' : email + ' can now sign in');
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
      a.addEventListener('click', function(e){ e.preventDefault(); toast(a.dataset.soon + ' is coming soon'); });
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

# Feather-style inline icons (stroke, inherit currentColor)
_ICONS = {
    "face": '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 14l3-3 2 2 3-4"/></svg>',
    "drop": '<svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>',
    "ledger": '<svg viewBox="0 0 24 24"><path d="M5 3h14v18H5z"/><path d="M9 7h6M9 11h6M9 15h4"/></svg>',
    "chat": '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5z"/></svg>',
    "key": '<svg viewBox="0 0 24 24"><circle cx="8" cy="14" r="4"/><path d="M11 11l8-8M17 4l3 3M14 7l2 2"/></svg>',
}


def _appnav(active="face", drop_ready=False):
    drop_attr = 'href="/drop/"' if drop_ready else 'href="#" data-soon="Drop"'
    items = [
        ("face", 'href="/ops/#overview"', "Face", ""),
        ("drop", drop_attr, "Drop", ""),
        ("ledger", 'href="/ops/ledger.html"', "Ledger", ""),
        ("chat", 'href="/ops/agent/"', "Chat", ""),
        ("key", 'href="#" id="keyNav"', "Key", " admin-only"),
    ]
    out = []
    for key, attr, label, extra in items:
        cls = "appitem" + (" active" if key == active else "") + extra
        out.append(f'<a class="{cls}" {attr}><span class="ic">{_ICONS[key]}</span><span>{label}</span></a>')
    return '<nav class="appnav" aria-label="Apps">' + "".join(out) + "</nav>"


KEY_PANEL = """
<div id="keyveil"></div>
<aside id="keypanel" role="dialog" aria-label="Key — manage access">
  <div class="key-grip"></div>
  <h3 class="key-title">Access</h3>
  <p class="key-sub">Who can sign in to Hub with their Google account. Changes apply instantly.</p>
  <div class="key-add">
    <input id="keyEmail" type="email" inputmode="email" autocomplete="off"
           placeholder="teammate@gmail.com">
    <button id="keyAddBtn">Invite</button>
  </div>
  <div id="keyMembers"><p class="empty">Loading…</p></div>
</aside>
"""

PTR = """
<div id="ptr" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
  stroke-linecap="round"><path d="M20 11A8 8 0 1 0 18.6 15.5"/><path d="M20 4v7h-7"/></svg></div>
"""


def verdict_banner(analysis):
    """Collapsible insight banner: one-line teaser, expands to the full reading."""
    if not analysis:
        return ""
    text = analysis["text"]
    teaser = text[:180]
    return (
        '<details class="verdict"><summary>'
        '<span class="v-icon">✦</span>'
        f'<span class="v-teaser">{html.escape(teaser)}…</span>'
        '<span class="v-chev">▾</span></summary>'
        f'<div class="v-body">{html.escape(text)}'
        f'<span class="v-model">via {html.escape(analysis["model"])}</span></div>'
        '</details>'
    )


def page(*, generated_at, lookback_days, source_status_html, kpi_html, verdict_html,
         funnel_html, channel_html, campaign_html, overview_orders_html, orders_html,
         plan_html, research_html, content_html, findings_html, health_html,
         stats_html="", revenue_chart_html="", drop_ready=False):
    """Assemble the full Hub/Face HTML from data fragments built by generate.py."""
    def rb(days, label):
        active = ' class="active"' if lookback_days == days else ""
        return f'<button{active} onclick="doRefresh({days})">{label}</button>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f8f8f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111113" media="(prefers-color-scheme: dark)">
<meta name="color-scheme" content="light dark">
<title>Timelabs Hub</title>
<style>{HUB_STYLE}</style>
<script>{HUB_SCRIPT}</script>
</head>
<body>
{PTR}
<div class="wrap">
  <header class="topbar">
    <a class="brand" href="/ops/#overview">
      <span class="brand-dot">T</span>
      <span class="brand-name">Timelabs <span>Hub</span></span>
    </a>
    <div class="top-actions">
      <span id="who" class="who"></span>
      <button id="refreshBtn" class="iconbtn" title="Refresh now" aria-label="Refresh now" onclick="doRefresh()">↻</button>
    </div>
  </header>

  {_appnav(active="face", drop_ready=drop_ready)}

  <main>
    <div class="page-head">
      <h1 class="page-title">Face</h1>
      <p class="page-sub">The state of the business, at a glance.</p>
      <div class="head-controls">
        <div class="seg">{rb(7, "7d")}{rb(30, "30d")}{rb(90, "90d")}</div>
        <span class="refreshed num">refreshed {generated_at}</span>
        <a class="btn primary" href="/ops/agent/">Ask the team</a>
      </div>
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
        <div class="kpis">{kpi_html}</div>
      </section>
      {f'<section><h2>Conversion</h2><div class="stats">{stats_html}</div></section>' if stats_html else ''}
      {revenue_chart_html}
      {overview_orders_html}
      {funnel_html}
      {channel_html}
      {campaign_html}
      <section>
        <h2>Sources</h2>
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
      <span>Timelabs Hub</span>
      <span>daily refresh + on-demand</span>
    </footer>
  </main>
</div>
{KEY_PANEL}
<div id="toast" class="toast" role="status" aria-live="polite"></div>
</body>
</html>
"""
