#!/usr/bin/env python3
"""Pure helpers for Board (saved design/creative references).

Kept separate from agent_chat_server.py — which runs live schema migrations
against the production DB on import — so this stays safely importable from
tests, the same reason order_numbers.py and order_taxonomy.py are separate.

Stdlib only: the agent server runs on system python and imports this directly.
"""
import html
import ipaddress
import socket
import urllib.parse

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


def public_http_url(url):
    """Admit only a plain http(s) URL that resolves to a public address.

    The image URLs a Board link fetch hands back come from a model that just
    read an untrusted third-party page, so they are attacker-influenced by
    construction. Fetching one blindly from this box would be a server-side
    request forgery primitive against everything on loopback and the private
    network — oauth2-proxy, both Python services, the cloud metadata
    endpoint. Callers must re-check every redirect hop too, because a public
    host is free to redirect to 127.0.0.1.

    Returns the normalized URL, or None if it must not be fetched.

    Entities are unescaped first. A URL lifted out of page source arrives
    with `&amp;` between its query parameters, and a signed CDN URL whose
    parameters are mangled that way is rejected by the CDN with a 403 that
    looks exactly like a blocked scrape — this cost a real debugging pass
    on Meta's ad images.
    """
    try:
        parts = urllib.parse.urlsplit(html.unescape(str(url or "")).strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:                      # malformed port
        return None
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    if not infos:
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return None
    return parts.geturl()
