#!/usr/bin/env python3
"""Does the money still fit on a phone?

The class-coverage audit catches "this class has no rule" — the failure that
shipped an unstyled Batches tab. It cannot catch "five cells don't fit in
390px", which is how the money strip shipped with amounts spilling out.

Deliberately narrow. A first version flagged every tabular-nums rule lacking
an overflow property: 69 findings across 15 pages, nearly all shared shell
classes in containers that were fine. A checker nobody trusts is worse than
none, so this reports only what it can measure precisely: a grid strip
carrying rupee amounts whose columns are narrower than the amount needs.

Known limit, stated plainly: it does NOT catch an un-wrapping flex row that
divides its width by however many cells it gets at runtime — which is the
shape the money strip originally broke in. A heuristic for that was tried
and produced identical output on the broken and the fixed page, so it was
removed rather than shipped. The protection there is that money strips are
now grids, which this can measure.
"""
import glob, os, re, sys

PHONE, PAGE_PAD, CELL_PAD = 390, 24, 22
WIDEST = "₹1,34,500.00"     # a lakh-plus batch total, not a token 0.00


def text_px(s, size):
    return len(s) * size * 0.60   # slight over-estimate: borderline reports


def decomment(css):
    """Drop /* ... */ before any selector parsing.

    Nothing here parses CSS properly; it splits on braces. That means the run
    of text before a "{" is "everything since the last }", which includes the
    comment sitting above the rule. This file's own house style puts a
    paragraph of comment above most rules, so `strips()` was handing back
    selectors like "/* Money, always visible ... */\\n  .moneybar", and
    `base = sel.split()[0]` then evaluated to "/*". Every commented strip
    failed the region test and was skipped, which is how "every money strip
    fits" was printed while the supplier strip was slicing ₹1,44,300.00
    through its last digit on a phone. Strip comments first and the selector
    is a selector again.
    """
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def strips(css):
    """(selector, min-column-px, font-size-of-its-value) for grid strips."""
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', decomment(css)):
        sel, body = m.group(1).strip(), m.group(2).replace(" ", "")
        c = re.search(r'grid-template-columns:repeat\(auto-fit,minmax\((\d+)px', body)
        if c:
            out.append((sel, int(c.group(1))))
    return out


def applies_at(cond, width):
    """Does this @media condition hold at `width`? Unknown conditions -> yes.

    Only the width bounds are read, because they are the only part of a media
    query that changes whether a rupee amount fits in a column.
    """
    for lo in re.findall(r"min-width:\s*(\d+)px", cond):
        if width < int(lo):
            return False
    for hi in re.findall(r"max-width:\s*(\d+)px", cond):
        if width > int(hi):
            return False
    return True


def declarations(css, width):
    """(selector, body) in cascade order for the rules live at `width`."""
    css = decomment(css)
    out, i, n = [], 0, len(css)
    while i < n:
        at = css.find("@media", i)
        if at == -1:
            out += [(m.group(1).strip(), m.group(2))
                    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css[i:])]
            break
        out += [(m.group(1).strip(), m.group(2))
                for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css[i:at])]
        ob = css.find("{", at)
        cond, depth, j = css[at + 6:ob], 1, ob + 1
        while j < n and depth:
            depth += (css[j] == "{") - (css[j] == "}")
            j += 1
        if applies_at(cond, width):
            out += [(m.group(1).strip(), m.group(2))
                    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css[ob + 1:j - 1])]
        i = j
    return out


def _last_px(css, width, want, prop):
    """Last value of `prop` at `width` among selectors matching `want`."""
    found = None
    for sel, body in declarations(css, width):
        if not want(sel):
            continue
        m = None
        for m in re.finditer(rf"(?:^|;)\s*{prop}:\s*([^;]+)", body):
            pass
        if m:
            found = m.group(1).strip()
    return found


# Which element carries the amount inside each strip, which carries the
# padding, how much of the 390px the strip's ancestors have already eaten, and
# the strip's own gap. Stated rather than guessed: a "looks like a value"
# heuristic put .verdict .v-icon's 26px against the supplier strip and
# reported a strip that fits as 80px short, and assuming a bare .wrap put
# .okpis at three columns when the panel it sits in gives it two. Four numbers
# per strip is not a burden; a wrong number is. A strip that is not listed
# here is reported as unchecked rather than silently passing.
#   inset  = every horizontal padding between the viewport and the strip
#   widest = the longest string THIS strip can actually render. The two strips
#            do not format money the same way, and checking a strip against a
#            format it never shows is how a checker earns a reputation for
#            crying wolf: the supplier strip prints inr() with paise, the
#            order strip prints whole rupees and tops out below a crore.
STRIP_PARTS = {
    # .swrap padding 16*2
    "moneybar": (".mcell", ".mcell .mv", 32, 0, "₹1,34,500.00"),
    # .wrap padding 20*2 + .panel padding 18*2
    "okpis": (".okpi", ".okpi b", 76, 12, "₹99,99,999"),
}


def value_size(css, cell, value_sel, width=PHONE):
    """font-size of the amount inside this strip, at `width`.

    Reads the size that actually applies rather than assuming a default: the
    supplier strip declares 14px, steps down to 13px below 560px and up to
    15px above it, and checking a phone against the desktop size reports a
    strip that fits as broken.
    """
    got = _last_px(css, width, lambda s: s == value_sel, "font-size")
    return float(re.match(r"[\d.]+", got).group(0)) if got else 15.0


def cell_pad(css, cell, width=PHONE):
    """Horizontal padding of the strip's cell at `width`, both sides."""
    got = _last_px(css, width, lambda s: s == cell, "padding")
    if not got:
        return 0.0        # the cell selector is explicit now; no rule means none
    parts = re.findall(r"([\d.]+)px", got)
    if len(parts) >= 2:
        return float(parts[1]) * 2
    return float(parts[0]) * 2 if parts else 0.0


def check(page):
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    out = []
    for sel, colmin in strips(css):
        base = sel.lstrip('.').split()[0]
        # only strips this page actually renders money into. The markup is
        # often an empty div that JS fills, so look at the whole page for the
        # formatter as well as at the element itself.
        if not re.search(rf'{re.escape(base)}["\']', html):
            continue
        if "inr(" not in html and "₹" not in html:
            continue
        if base not in STRIP_PARTS:
            out.append((sel, "carries money but is not in STRIP_PARTS — add its "
                             "cell and value selectors so it can be measured"))
            continue
        cell, value_sel, inset, gap, widest = STRIP_PARTS[base]
        avail = PHONE - inset
        cols = max(1, int((avail + gap) // (colmin + gap)))
        inner = (avail - gap * (cols - 1)) / cols - cell_pad(css, cell)
        size = value_size(css, cell, value_sel)
        need = text_px(widest, size)
        if inner < need:
            out.append((sel, f"{cols} cols at {PHONE}px leaves ~{inner:.0f}px; "
                             f"{widest} at {size:.0f}px needs ~{need:.0f}px"))
    return out


CLASS_RE = re.compile(r"^[A-Za-z][\w-]*$")
# Toggled by JS rather than written in the stylesheet; not orphans.
STATE_CLASSES = {"on", "open", "active", "show", "hide", "sel", "up", "err",
                 "ok", "over", "picked", "dropped", "dragover", "running",
                 "ready", "failed", "posted", "draft", "settled", "flash"}
# Selector hooks: a class that exists only so querySelector can find the node,
# with the visible styling carried by a sibling class on the same element
# (`class="pinput f-title"`). The f- prefix is the convention that says so, and
# it is checked below rather than trusted — a hook nobody queries is a typo.
HOOK_RE = re.compile(r"^f-[\w-]+$")


def unstyled(page, sources=()):
    """Classes the page emits that no rule defines.

    This is the check that would have caught a whole tab shipping with its
    CSS deleted, and it did catch Drop's header losing .top-actions. Two ways
    it has been wrong since, both fixed here:

    Scraping class="..." picks up JS template strings, so `'<span class="tst '
    + cls + '">'` reported a variable named `cls` as a missing class. A quoted
    class attribute that contains a quote-and-concatenate is a fragment, not a
    class list, so those spans are skipped.

    It also filtered out anything three characters or shorter to quieten that
    same noise, which hid a genuine orphan: Drop's search results emitted
    `.ph` with no rule behind it. Length is not evidence, so no length filter.
    """
    html = open(page, encoding="utf-8", errors="replace").read()
    styles = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    defined = set(re.findall(r"\.([A-Za-z][\w-]*)", styles))
    used = set()
    for chunk in re.findall(r'class="([^"]*)"', html):
        # In `class="ost s-' + cls(o.status) + '"` only the run before the
        # first quote or plus is real; everything after belongs to JS. Taking
        # the prefix beats trying to strip the expression out, which produced
        # its own debris (`VIP` out of `t==='VIP'?'vip':''`, `s-` out of a
        # concatenated prefix).
        cut = min((i for i in (chunk.find("'"), chunk.find("+")) if i >= 0),
                  default=len(chunk))
        for tok in chunk[:cut].split():
            # A trailing dash means the name was being built, not written
            if CLASS_RE.match(tok) and not tok.endswith(("-", "_")):
                used.add(tok)
    orphans = used - defined - STATE_CLASSES
    hooks = {c for c in orphans if HOOK_RE.match(c)}
    # A hook earns its exemption by being queried somewhere. src is the
    # generator's own text, where `box.querySelector('.f-title')` lives.
    if hooks and sources:
        src = " ".join(open(s, encoding="utf-8", errors="replace").read()
                       for s in sources if os.path.exists(s))
        orphans -= {c for c in hooks if f"'.{c}'" in src or f'".{c}"' in src}
    return sorted(orphans)


ZERO = ("0", "0px", "0rem", "0em")


def _rules(css):
    """(selector, body, inside_a_media_query) in source order."""
    out, depth, i, n, buf, media = [], 0, 0, len(css), "", None
    while i < n:
        ch = css[i]
        if ch == "{":
            if buf.strip().startswith("@"):
                depth += 1
                media = depth if media is None else media
                buf, i = "", i + 1
                continue
            j = css.find("}", i)
            if j == -1:
                break
            out.append((buf.strip(), css[i + 1:j], media is not None))
            buf, i = "", j + 1
            continue
        if ch == "}":
            depth -= 1
            if media is not None and depth < media:
                media = None
            buf, i = "", i + 1
            continue
        buf, i = buf + ch, i + 1
    return out


def _vmargin(body):
    last = None
    for m in re.finditer(r"(margin(?:-top|-bottom)?)\s*:\s*([^;]+)", body):
        last = (m.group(1), m.group(2).strip())
    return last


def _is_zero(prop, val):
    val = val.replace("!important", "").strip()
    return (val in ZERO) if prop != "margin" else (val.split() or [""])[0] in ZERO


def cancelled(page):
    """Vertical margins a media query zeroes that nothing else replaces.

    This is the shape that took the gap out from under the supplier tabs: a
    base rule gave .stagebar margin-top:16px, the desktop block set it to 0 so
    the chips would sit inline, and that margin was the only thing separating
    the whole control row from the tabs. Valid CSS, parses clean, renders
    wrong, and no other check here can see it — 0px is not an error, it is
    just the wrong number.

    Reported, not failed: zeroing a child's margin is legitimate when the
    parent supplies gap. The point is that it should be a decision on the
    screen rather than a side effect nobody looked at.
    """
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    base, hits = {}, []
    for sel, body, in_media in _rules(css):
        vm = _vmargin(body)
        if not vm:
            continue
        for s in (x.strip() for x in sel.split(",")):
            if not s:
                continue
            if not in_media and not _is_zero(*vm):
                base[s] = vm
            elif in_media and _is_zero(*vm) and s in base:
                hits.append((s, base[s]))
    return hits


def command_containment(page="/var/www/ops/command.html"):
    """Keep long conversations inside the viewport instead of moving compose.

    A flex column used as a CSS-grid child defaults to min-height:auto. Once an
    old conversation was taller than the viewport, that intrinsic minimum made
    .workspace grow to the full transcript height and pushed the composer off
    screen. JavaScript parsing and class-coverage checks both pass in that
    broken state, so guard the two containment rules the layout depends on.
    """
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    required = {
        ".workspace": {"min-height": "0", "overflow": "hidden"},
        ".thread": {"min-height": "0", "overflow": "auto"},
    }
    issues = []
    for selector, props in required.items():
        for prop, want in props.items():
            got = _last_px(css, PHONE, lambda s: s == selector, prop)
            if got not in ({"0", "0px"} if want == "0" else {want}):
                issues.append(f"{selector} needs {prop}:{want} (found {got or 'nothing'})")
    return issues


def drop_chrome_contract(page="/var/www/drop/index.html"):
    """Drop's SPA stylesheet must still honor the shared Labs chrome.

    The header markup is regenerated from hub_shell, but Drop intentionally
    keeps a separate application stylesheet. Checking only class coverage let
    old widths, naked icon buttons, a duplicate desktop upload FAB and ragged
    content-sized folder cards survive behind perfectly valid selectors.
    """
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    checks = (
        (PHONE, ".wrap", "max-width", {"1020px"}),
        (PHONE, ".topbar", "justify-content", {"space-between"}),
        (PHONE, ".topbar .iconbtn", "width", {"36px"}),
        (PHONE, ".folders", "display", {"grid"}),
        (1024, ".fab", "display", {"none"}),
    )
    issues = []
    for width, selector, prop, want in checks:
        got = _last_px(css, width, lambda s, selector=selector: s == selector, prop)
        if got not in want:
            issues.append(
                f"{selector} needs {prop}:{'/'.join(sorted(want))} at {width}px "
                f"(found {got or 'nothing'})")
    if "/ops/agent/api/access/me" not in html:
        issues.append("identity and role-filtered navigation are not wired to access/me")
    return issues


def role_navigation_contract(page):
    """Role-filtered links must be hidden visually, not just semantically."""
    html = open(page, encoding="utf-8", errors="replace").read()
    if 'class="appitem' not in html:
        return []
    issues = []
    if "/ops/agent/api/access/me" not in html:
        issues.append("app navigation is not wired to access/me")
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    got = _last_px(css, PHONE, lambda s: s == "[hidden]", "display")
    if got != "none !important":
        issues.append(f"[hidden] needs display:none !important (found {got or 'nothing'})")
    return issues


def touch_target_contract():
    """Guard the compact controls that are actually used on touch screens."""
    checks = (
        ("/var/www/drop/index.html", ".fdots", "width", "36px"),
        ("/var/www/drop/index.html", "#gdReconnect", "min-height", "36px"),
        ("/var/www/ops/command.html", ".msg-tools button", "min-height", "32px"),
        ("/var/www/ops/product-builder.html", ".rte-bar button", "height", "36px"),
    )
    issues = []
    for page, selector, prop, want in checks:
        html = open(page, encoding="utf-8", errors="replace").read()
        css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
        got = _last_px(css, PHONE, lambda s, selector=selector: s == selector, prop)
        if got != want:
            issues.append((os.path.basename(page),
                           f"{selector} needs {prop}:{want} on touch (found {got or 'nothing'})"))
    return issues


def home_action_contract(page="/var/www/ops/index.html"):
    """Home must remain an action surface, not regress to passive metrics."""
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    issues = []
    required = ('id="action-center"', 'class="action-grid"',
                'href="/ops/order-form.html"', 'href="/drop/"',
                'href="/ops/command.html"')
    for marker in required:
        if marker not in html:
            issues.append(f"missing {marker}")
    got = _last_px(css, PHONE, lambda s: s == ".action-grid", "display")
    if got != "grid":
        issues.append(f".action-grid needs display:grid (found {got or 'nothing'})")
    return issues


def drop_share_contract(page="/var/www/drop/index.html"):
    """Externally reachable Drop links must stay visible and revocable."""
    html = open(page, encoding="utf-8", errors="replace").read()
    issues = []
    required = ('id="sharesBtn"', 'id="sharesheet"', "api('/shares')",
                'data-revoke', 'function sharesClose()')
    for marker in required:
        if marker not in html:
            issues.append(f"missing {marker}")
    return issues


def main():
    pages = (sorted(glob.glob("/var/www/ops/*.html"))
             + ["/var/www/drop/index.html", "/var/www/intake/index.html"])
    srcs = glob.glob("/root/ops-dashboard/*.py")

    print("═══ EVERY CLASS HAS A RULE ═══")
    missing = 0
    for page in pages:
        try:
            orph = unstyled(page, srcs)
        except Exception as e:
            print(f"  ? {os.path.basename(page)}: {e}")
            continue
        if orph:
            missing += len(orph)
            print(f"  ✗ {os.path.basename(page):22} {', '.join(orph)}")
    print("  ✓ nothing unstyled" if not missing else f"  {missing} unstyled")

    print(f"\n═══ MONEY FITS AT {PHONE}px ═══")
    total = 0
    for page in pages:
        for sel, why in check(page):
            total += 1
            print(f"  ✗ {os.path.basename(page)}  {sel[:28]}  {why}")
    print("  ✓ every money strip fits" if not total else f"  {total} too tight")

    print("\n═══ NO SPACING CANCELLED IN A MEDIA QUERY ═══")
    warn = 0
    for page in pages:
        for sel, was in cancelled(page):
            warn += 1
            print(f"  ! {os.path.basename(page):22} {sel} loses "
                  f"{was[0]}:{was[1]} — is the parent supplying that gap?")
    print("  ✓ nothing cancelled" if not warn
          else f"  {warn} to eyeball (warning, not a failure)")

    print("\n═══ COMMAND STAYS INSIDE THE VIEWPORT ═══")
    containment = command_containment()
    for issue in containment:
        print(f"  ✗ command.html  {issue}")
    print("  ✓ long conversations keep the composer visible" if not containment
          else f"  {len(containment)} broken containment rule(s)")

    print("\n═══ DROP USES THE CURRENT LABS CHROME ═══")
    drop_chrome = drop_chrome_contract()
    for issue in drop_chrome:
        print(f"  ✗ drop/index.html  {issue}")
    print("  ✓ header, folders and upload actions stay aligned" if not drop_chrome
          else f"  {len(drop_chrome)} broken Drop chrome rule(s)")

    print("\n═══ ROLE-FILTERED NAVIGATION IS REALLY HIDDEN ═══")
    role_nav = 0
    for page in pages:
        for issue in role_navigation_contract(page):
            role_nav += 1
            print(f"  ✗ {os.path.basename(page):22} {issue}")
    print("  ✓ hidden tools do not remain visible" if not role_nav
          else f"  {role_nav} broken role-navigation rule(s)")

    print("\n═══ HIGH-FREQUENCY TOUCH TARGETS ARE USABLE ═══")
    touch_targets = touch_target_contract()
    for page, issue in touch_targets:
        print(f"  ✗ {page:22} {issue}")
    print("  ✓ compact actions have a touch-safe hit area" if not touch_targets
          else f"  {len(touch_targets)} undersized touch target(s)")

    print("\n═══ HOME STARTS WITH ACTIONS ═══")
    home_actions = home_action_contract()
    for issue in home_actions:
        print(f"  ✗ index.html             {issue}")
    print("  ✓ operational exceptions lead to the owning tool" if not home_actions
          else f"  {len(home_actions)} broken Home action rule(s)")

    print("\n═══ DROP SHARES STAY CONTROLLABLE ═══")
    drop_shares = drop_share_contract()
    for issue in drop_shares:
        print(f"  ✗ drop/index.html        {issue}")
    print("  ✓ active links can be reviewed and revoked" if not drop_shares
          else f"  {len(drop_shares)} broken Drop sharing rule(s)")

    return 1 if (total or missing or containment or drop_chrome or role_nav or touch_targets
                 or home_actions or drop_shares) else 0


if __name__ == "__main__":
    sys.exit(main())
