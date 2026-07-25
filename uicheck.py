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


def strips(css):
    """(selector, min-column-px, font-size-of-its-value) for grid strips."""
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sel, body = m.group(1).strip(), m.group(2).replace(" ", "")
        c = re.search(r'grid-template-columns:repeat\(auto-fit,minmax\((\d+)px', body)
        if c:
            out.append((sel, int(c.group(1))))
    return out


def value_size(css, strip_sel):
    """font-size of the value element inside this strip, if it declares one."""
    base = strip_sel.lstrip('.').split()[0]
    best = 15.0
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sel, body = m.group(1), m.group(2)
        if base in sel and ('.mv' in sel or ' b' in sel or '.v' in sel):
            f = re.search(r'font-size:\s*([\d.]+)px', body)
            if f:
                best = float(f.group(1))
    return best


def check(page):
    html = open(page, encoding="utf-8", errors="replace").read()
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    out = []
    for sel, colmin in strips(css):
        base = sel.lstrip('.').split()[0]
        # only strips this page actually renders money into
        region = re.search(rf'{re.escape(base)}["\'][\s\S]{{0,900}}', html)
        if not region or ('inr(' not in region.group(0)
                          and '₹' not in region.group(0)):
            continue
        cols = max(1, (PHONE - PAGE_PAD) // colmin)
        inner = (PHONE - PAGE_PAD) / cols - CELL_PAD
        size = value_size(css, sel)
        need = text_px(WIDEST, size)
        if inner < need:
            out.append((sel, f"{cols} cols at {PHONE}px leaves ~{inner:.0f}px; "
                             f"{WIDEST} at {size:.0f}px needs ~{need:.0f}px"))
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


def main():
    pages = sorted(glob.glob("/var/www/ops/*.html")) + ["/var/www/drop/index.html"]
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

    return 1 if (total or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
