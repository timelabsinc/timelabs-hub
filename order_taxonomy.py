#!/usr/bin/env python3
"""Product attribute vocabulary + extractor for the Order form.

Staff type an order as free text ("santos white arabic dial 40mm nh35"). To
learn what actually sells we need that split into columns, so this keyword-
matches the text against a vocabulary drawn from the real Shopify catalogue —
productType is effectively the case style there (Datejust, Royal Oak, Nautilus,
…), and tags carry dial colour/style and movement.

The same vocabulary is injected into the Order form page as JSON so the browser
can show the match live and let staff correct it before saving; this module
stays the single source of truth for the words themselves.

Matching is longest-phrase-first, so "rose gold" wins over "gold" and
"light blue" over "blue".
"""
import json
import os
import re
import time

CACHE = "/root/ops-dashboard/.taxonomy-cache.json"
CACHE_MAX_AGE = 7 * 86400   # refresh case styles from Shopify weekly

# Case style — seeded from the store's productType values. Aliases map the
# shorthand staff actually type onto the canonical name.
CASE_STYLES = [
    "Datejust", "Royal Oak", "Nautilus", "Submariner", "GMT", "Daytona",
    "Santos", "Aquanaut", "Seikojust", "Seikotona", "Tudor", "Hublot",
    "Panerai", "PRX", "Oyster", "Speedmaster", "Seamaster", "Explorer",
    "Milgauss", "Seikoak", "Yacht-Master", "Skeleton",
]
CASE_STYLE_ALIASES = {
    "sub": "Submariner", "subm": "Submariner", "ap": "Royal Oak",
    "ro": "Royal Oak", "dj": "Datejust", "naut": "Nautilus",
    "batman": "GMT", "pepsi": "GMT", "kermit": "Submariner",
    "hulk": "Submariner", "panda": "Daytona", "gmt master": "GMT",
    "gmt-master": "GMT", "royal-oak": "Royal Oak", "yacht master": "Yacht-Master",
}

DIAL_COLOURS = [
    "light blue", "sky blue", "royal blue", "ice blue", "light green",
    "rose gold", "champagne", "turquoise", "tiffany", "salmon",
    "burgundy", "charcoal", "black", "white", "cream", "green", "brown",
    "grey", "gray", "silver", "golden", "gold", "blue", "purple", "orange",
    "yellow", "pink", "red", "meteorite", "mother of pearl",
]
# "panda" is a dial layout (light dial, dark subdials) and "two tone" describes
# the case/bracelet — neither belongs in the colour list above.
DIAL_STYLES = [
    "arabic", "roman", "index", "baton", "skeleton", "sunburst", "textured",
    "fluted", "smooth", "diamond", "lume", "wave", "waffle", "linen",
    "tapisserie", "panda",
]
CASE_COLOURS = [
    "rose gold", "two tone", "two-tone", "stainless steel", "stainless",
    "yellow gold", "gunmetal", "bronze", "silver", "golden", "gold", "black",
    "pvd", "steel", "titanium",
]
MOVEMENTS = [
    "NH35", "NH36", "NH34", "NH38", "VK63", "VK64", "VH31", "VD78",
    "SKX", "Miyota", "Seiko automatic", "automatic", "quartz", "chronograph",
]

SIZE_RE = re.compile(r"\b(28|31|34|36|38|39|40|41|42|43|44|45)\s*mm\b", re.I)
# bare sizes are common too ("datejust arabic 36"), but only trust 2-digit
# numbers that aren't obviously a price or quantity
BARE_SIZE_RE = re.compile(r"\b(36|38|39|40|41|42|44)\b")

FIELDS = ["case_style", "dial_colour", "dial_style", "case_colour", "movement", "watch_size"]
FIELD_LABELS = {
    "case_style": "Case style", "dial_colour": "Dial colour",
    "dial_style": "Dial style", "case_colour": "Case colour",
    "movement": "Movement", "watch_size": "Size",
}


def _canon(word_list):
    """Longest first, so multi-word phrases beat their own substrings."""
    return sorted(word_list, key=lambda w: -len(w))


def case_styles():
    """Canonical case styles: cached Shopify productTypes merged with the seed."""
    extra = []
    try:
        if os.path.exists(CACHE):
            c = json.load(open(CACHE))
            extra = c.get("case_styles", [])
    except (OSError, json.JSONDecodeError):
        pass
    seen, out = set(), []
    for s in CASE_STYLES + extra:
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def refresh_from_shopify():
    """Pull live productTypes into the cache. Safe to fail — the seed stands."""
    try:
        import shopify_api
        if not shopify_api.configured():
            return False
        d = shopify_api.admin_graphql(
            "{ products(first:250) { edges { node { productType } } } }")
        types = {e["node"]["productType"].strip()
                 for e in d["products"]["edges"] if e["node"].get("productType")}
        # keep watch models, drop the part/service categories
        drop = {"dial", "movement", "case", "hands", "bezel insert", "crystal",
                "warranty", "strap", "bracelet", "slidecart - shipping protection"}
        keep = sorted(t for t in types if t.lower() not in drop)
        with open(CACHE, "w") as f:
            json.dump({"case_styles": keep, "fetched": time.time()}, f)
        return True
    except Exception as e:
        print(f"[taxonomy] refresh skipped: {e}")
        return False


def cache_is_stale():
    try:
        return time.time() - json.load(open(CACHE)).get("fetched", 0) > CACHE_MAX_AGE
    except (OSError, json.JSONDecodeError):
        return True


def vocab():
    """The word lists the browser matches against — same source as extract()."""
    return {
        "case_style": _canon(case_styles()),
        "case_style_aliases": CASE_STYLE_ALIASES,
        "dial_colour": _canon(DIAL_COLOURS),
        "dial_style": _canon(DIAL_STYLES),
        "case_colour": _canon(CASE_COLOURS),
        "movement": _canon(MOVEMENTS),
        "labels": FIELD_LABELS,
    }


def _find(text, words):
    """First vocabulary hit on a word boundary, longest phrase first."""
    for w in _canon(words):
        if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", text):
            return w
    return None


def _find_last(text, words):
    """The hit closest to the end of the span — i.e. nearest the anchor noun.
    Ranked by where the match ENDS, then by length, so "light blue" beats the
    "blue" sitting inside it."""
    best, best_key = None, (-1, -1)
    for w in _canon(words):
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", text):
            key = (m.end(), len(w))
            if key > best_key:
                best, best_key = w, key
    return best


def _near(text, anchors, words, window=32):
    """Colour sitting just before a noun: 'blue dial' beats 'rose gold' earlier
    in the same line. This is what stops the case colour hijacking the dial."""
    for anchor in anchors:
        for m in re.finditer(r"(?<![a-z0-9])" + anchor + r"(?![a-z0-9])", text):
            hit = _find_last(text[max(0, m.start() - window):m.start()], words)
            if hit:
                return hit
    return None


def extract(text):
    """Free-text order line → structured attributes. Server-side mirror of the
    matcher in the page, used as a backstop when the client sends nothing."""
    t = " " + (text or "").lower().replace("-", " ") + " "
    out = {}

    style = _find(t, [s.lower() for s in case_styles()])
    if style:
        out["case_style"] = next(s for s in case_styles() if s.lower() == style)
    else:
        for alias, canon in CASE_STYLE_ALIASES.items():
            if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", t):
                out["case_style"] = canon
                break

    # Anchored colours win: "blue dial" binds blue to the dial even when
    # "rose gold" came first, and "black bezel" keeps black off the dial.
    case_anchors = ["case", "bezel", "body", "bracelet", "watch"]
    cc = _near(t, case_anchors, CASE_COLOURS)

    dial = _near(t, ["dial", "face"], DIAL_COLOURS)
    if not dial:
        cand = _find(t, DIAL_COLOURS)
        dial = cand if cand != cc else None
    if dial:
        out["dial_colour"] = dial

    ds = _near(t, ["dial", "face"], DIAL_STYLES) or _find(t, DIAL_STYLES)
    if ds:
        out["dial_style"] = ds

    if not cc:
        cand = _find(t, CASE_COLOURS)
        cc = cand if cand != dial else None
    if cc and cc != dial:
        out["case_colour"] = cc

    mv = None
    for w in _canon(MOVEMENTS):
        if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", t):
            mv = w
            break
    if mv:
        out["movement"] = mv

    sz = SIZE_RE.search(t) or BARE_SIZE_RE.search(t)
    if sz:
        out["watch_size"] = sz.group(1) + "mm"
    return out


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        print("refreshed" if refresh_from_shopify() else "refresh failed")
    for probe in sys.argv[1:]:
        if not probe.startswith("--"):
            print(f"{probe!r} -> {extract(probe)}")
