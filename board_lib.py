#!/usr/bin/env python3
"""Pure helpers for Board (saved design/creative references).

Kept separate from agent_chat_server.py — which runs live schema migrations
against the production DB on import — so this stays safely importable from
tests, the same reason order_numbers.py and order_taxonomy.py are separate.
"""

MAX_TAGS = 20
MAX_TAG_LEN = 40


def normalize_tags(raw):
    """Trim, drop blanks, dedupe, cap length and count. Shared by save + update
    so the two can't quietly drift into different rules for the same field.

    Non-string entries (a stray null/number from loose client JSON) are
    dropped rather than stringified — str(None) is the truthy, non-blank
    string "None", which would otherwise silently become a real tag."""
    tags = set()
    for t in (raw or []):
        if not isinstance(t, str):
            continue
        t = t.strip()[:MAX_TAG_LEN]
        if t:
            tags.add(t)
    return sorted(tags)[:MAX_TAGS]
