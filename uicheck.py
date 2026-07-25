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


def unstyled(page):
    """Classes the page emits that no rule defines.

    This is the check that would have caught a whole tab shipping with its
    CSS deleted, and it did catch Drop's header losing .top-actions. It was
    nearly useless on Drop though: scraping class="..." picks up JS template
    strings too, so the one real finding arrived alongside thirteen fragments
    like "(f.dir" and "===" and got skimmed past. A class name is letters,
    digits, dashes and underscores; anything else came out of a template.
    """
    html = open(page, encoding="utf-8", errors="replace").read()
    styles = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    defined = set(re.findall(r"\.([A-Za-z][\w-]*)", styles))
    used = set()
    for chunk in re.findall(r'class="([^"]*)"', html):
        for tok in chunk.split():
            if CLASS_RE.match(tok):
                used.add(tok)
    return sorted(c for c in used - defined - STATE_CLASSES if len(c) > 2)


def main():
    pages = sorted(glob.glob("/var/www/ops/*.html")) + ["/var/www/drop/index.html"]

    print("═══ EVERY CLASS HAS A RULE ═══")
    missing = 0
    for page in pages:
        try:
            orph = unstyled(page)
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
    return 1 if (total or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
