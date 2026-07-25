#!/usr/bin/env python3
"""Strips the fingerprints that make a post read as machine-written.

Asking a model not to use em dashes fails often enough to be useless: our own
stored Hermes output already contains them. So this is deterministic. The
model writes, then this runs over the text and the result is checked; if a
tell survives, the draft does not go out.

Two separate problems:

1. Characters a person typing on a phone would never produce. Zero-width
   joiners, byte-order marks and non-breaking spaces are invisible on screen
   but survive copy-paste, and several are used as watermarks. Em dashes are
   the visible giveaway; almost nobody types one on a phone keyboard.

2. Phrasing. "Delve", "testament to", "not just X but Y", tidy three-item
   rhythms. These cannot be swapped out safely by regex without mangling the
   sentence, so they are reported rather than replaced, and a rewrite pass
   deals with them.
"""
import re
import unicodedata

# Invisible or non-typeable characters. Anything here is deleted outright.
INVISIBLE = {
    "​": "",   # zero-width space
    "‌": "",   # zero-width non-joiner
    "‍": "",   # zero-width joiner
    "⁠": "",   # word joiner
    "﻿": "",   # byte-order mark
    "­": "",   # soft hyphen
    "᠎": "",   # mongolian vowel separator
}

# Characters a phone keyboard does not produce, mapped to what it does.
SUBSTITUTE = {
    " ": " ",   # non-breaking space
    " ": " ",   # narrow no-break space
    " ": " ",   # thin space
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
    "′": "'", "″": '"',
    "−": "-",   # minus sign
}

# Phrasing that reads as generated. Reported, never auto-replaced.
TELLS = [
    r"\bdelve\b", r"\bdelving\b", r"\btapestry\b", r"\btestament to\b",
    r"\belevat(e|es|ing|ed)\b", r"\bseamless(ly)?\b", r"\brobust\b",
    r"\bleverage\b", r"\bunlock(ing)?\b", r"\bempower(s|ing)?\b",
    r"\bgame[- ]chang(er|ing)\b", r"\bnavigat(e|ing) the\b",
    r"\bin today'?s (world|market|landscape)\b", r"\bit'?s worth noting\b",
    r"\bthat said,", r"\bmoreover,", r"\bfurthermore,", r"\bin conclusion\b",
    r"\bat the end of the day\b", r"\bwhen it comes to\b",
    r"\bnot just .{1,40} but\b", r"\bisn'?t just .{1,40} it'?s\b",
    r"\bwhether you'?re\b", r"\bdive into\b", r"\bcrafted\b",
    r"\bmeticulous(ly)?\b", r"\bstands? as\b", r"\bboasts?\b",
]


def strip_dashes(text):
    """Em and en dashes out, sensible punctuation in.

    An em dash is nearly always doing one of two jobs: a parenthetical aside
    (a comma does it) or a break before a clause that could stand alone (a
    full stop does it). Guessing between them is what makes a naive
    replacement read wrong, so this uses the following clause: if what comes
    after could open a sentence, end the one before it.
    """
    def repl(m):
        after = m.group("after")
        # a following clause starting with a subject-ish word reads better as
        # its own sentence than as an aside
        if re.match(r"\s*(I|we|it|that|this|they|you|he|she|there|but|and)\b",
                    after, re.I):
            return ". " + after.lstrip()[:1].upper() + after.lstrip()[1:]
        return ", " + after.lstrip()

    # A dash between two numbers is a range, not a stylistic tell. "5,500-7,000"
    # and "2-4 weeks" are what a person types; turning them into "5,500, 7,000"
    # makes a price list read as nonsense, which is how this was found.
    text = re.sub(r"(?<=\d)\s*[—–]\s*(?=\d)", "-", text)

    # A matched pair with a short span between them is an aside, and both
    # ends want commas. Handled first, because treating each dash on its own
    # turns "And - honestly - I'd" into "And, honestly. I'd".
    text = re.sub(r"\s*[—–]\s*(?P<mid>[^—–\n]{1,60}?)\s*[—–]\s*",
                  lambda m: ", " + m.group("mid").strip() + ", ", text)
    text = re.sub(r"(?<!\d)\s*[—–]\s*(?P<after>.)", repl, text)
    # a hyphen doing an em dash's job, " - ", is the same tell
    text = re.sub(r"(?<!\d)\s+-\s+(?P<after>.)", repl, text)
    return text


def clean(text):
    """Returns (cleaned_text, [what was changed])."""
    if not text:
        return text, []
    notes = []
    original = text

    for ch, sub in INVISIBLE.items():
        if ch in text:
            notes.append(f"removed {unicodedata.name(ch, hex(ord(ch)))} "
                         f"x{text.count(ch)}")
            text = text.replace(ch, sub)
    for ch, sub in SUBSTITUTE.items():
        if ch in text:
            notes.append(f"replaced {unicodedata.name(ch, hex(ord(ch)))} "
                         f"x{text.count(ch)}")
            text = text.replace(ch, sub)

    dashes = len(re.findall(r"[—–]", text)) + len(re.findall(r"\s-\s", text))
    if dashes:
        notes.append(f"rewrote {dashes} dash break(s)")
        text = strip_dashes(text)

    # tidy up what the substitutions left behind
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"(?<!\.)\.\s*\.(?!\.)", ".", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()

    if text != original and not notes:
        notes.append("whitespace tidied")
    return text, notes


def tells(text):
    """Phrasing a rewrite pass should deal with."""
    found = []
    for pat in TELLS:
        for m in re.finditer(pat, text, re.I):
            found.append(m.group(0).strip())
    return sorted(set(found))


def verify(text):
    """Anything left that would give it away. Empty list means it passes."""
    problems = []
    for ch in INVISIBLE:
        if ch in text:
            problems.append(f"invisible character {unicodedata.name(ch, hex(ord(ch)))}")
    for ch in ("—", "–"):
        if ch in text:
            problems.append(f"{unicodedata.name(ch)} still present")
    for ch in text:
        if ord(ch) > 127 and ch not in "₹°":
            cat = unicodedata.category(ch)
            if cat.startswith("C") or cat == "Zs":
                problems.append(f"odd character U+{ord(ch):04X}")
    t = tells(text)
    if t:
        problems.append("phrasing: " + ", ".join(t[:6]))
    return problems


if __name__ == "__main__":
    import sys
    src = sys.stdin.read()
    out, notes = clean(src)
    sys.stderr.write("; ".join(notes or ["nothing to change"]) + "\n")
    left = verify(out)
    if left:
        sys.stderr.write("STILL WRONG: " + "; ".join(left) + "\n")
    sys.stdout.write(out)
