#!/usr/bin/env python3
"""Shared Reddit access layer — the foundation for the Listener (reddit.py)
and, later, draft-assist posting.

Reads REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USERNAME /
REDDIT_PASSWORD from /root/ops-dashboard/.env. Until they're added it reports
not-configured cleanly, so the Listener shows a setup state instead of
breaking the build.

Why these particular credentials: Reddit closed self-service OAuth app
creation in Nov 2025 — a "script" app now requires manual approval via
a Reddit Developer Support ticket (support.reddithelp.com, ~7 day review
once submitted — not yet submitted as of 2026-07-24). A script app
authenticates as the account itself (password grant), which is the
simplest flow for a single-account bot like this one. Datacenter IPs are
hard-blocked from most of Reddit's public surface — old.reddit's JSON
endpoints and www's HTML both refuse this VPS (verified directly) — so
oauth.reddit.com is the only reachable path for full data (score, comment
counts, search).

Until credentials exist, fetch_new_threads() falls back to Reddit's public
Atom RSS feeds (www.reddit.com/r/<sub>/new/.rss), which are NOT IP-blocked
(also verified directly) and need no key at all — a sanctioned, long-standing
feature for feed readers, not scraping. The tradeoff: RSS gives title, body,
author and link but not score or comment count, so those fields come back
None until OAuth is live. mode() reports which source is currently active.

Create the app: reddit.com/prefs/apps (once the support ticket is approved) →
"script" type → note the client_id (under the app name) and client_secret →
add to .env as:
  REDDIT_CLIENT_ID=xxxxx
  REDDIT_CLIENT_SECRET=xxxxx
  REDDIT_USERNAME=Brief_Client_2900
  REDDIT_PASSWORD=xxxxx
"""
import base64
import datetime
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

ENV_PATH = "/root/ops-dashboard/.env"
USER_AGENT = "timelabs-labsos/1.0 by /u/Brief_Client_2900"

# Home turf first, broadest reach last. India-specific subs get added here
# once a named community is picked — see reference_reddit_timelabs memory.
TARGET_SUBS = ["SeikoMods", "Watches", "watchmodding", "Seiko"]

# Cheap keyword heuristics for a first pass at tagging — good enough to sort
# the inbox by likely value without calling a model per thread. A human
# reviews every thread before anything is drafted, so a wrong tag costs
# nothing but ordering.
_TAG_RULES = [
    ("buying_intent", ("recommend", "under $", "budget", "which one should",
                        "first automatic", "looking for a", "worth buying")),
    ("answerable_question", ("is it worth", "worth it?", "how do i", "question about",
                              "homage vs", "should i")),
    ("competitor_complaint", ("scam", "ripped off", "bad experience", "disappointed",
                               "never again", "avoid")),
    ("style_trend", ("dial", "case style", "bezel", "strap", "trend")),
]


def _env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def credentials():
    env = _env()
    return (env.get("REDDIT_CLIENT_ID", ""), env.get("REDDIT_CLIENT_SECRET", ""),
            env.get("REDDIT_USERNAME", ""), env.get("REDDIT_PASSWORD", ""))


def configured():
    cid, secret, user, pw = credentials()
    return bool(cid and secret and user and pw)


class RedditError(Exception):
    pass


_token_cache = {"value": None, "expires": 0}


def _get_token():
    if _token_cache["value"] and time.time() < _token_cache["expires"] - 30:
        return _token_cache["value"]
    cid, secret, user, pw = credentials()
    if not configured():
        raise RedditError("Reddit isn't connected yet — add API credentials to .env.")
    body = urllib.parse.urlencode({
        "grant_type": "password", "username": user, "password": pw,
    }).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=body,
        headers={"User-Agent": USER_AGENT})
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RedditError(f"Reddit auth failed ({e.code}): {detail}")
    except urllib.error.URLError as e:
        raise RedditError(f"Couldn't reach Reddit: {e.reason}")
    if "access_token" not in data:
        raise RedditError(f"Reddit auth response missing a token: {data}")
    _token_cache["value"] = data["access_token"]
    _token_cache["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["value"]


def _api_get(path, params=None):
    token = _get_token()
    url = "https://oauth.reddit.com" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"bearer {token}", "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RedditError(f"Reddit API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RedditError(f"Couldn't reach Reddit: {e.reason}")


def _tag(title, body=""):
    text = f"{title} {body}".lower()
    for tag, keywords in _TAG_RULES:
        if any(k in text for k in keywords):
            return tag
    return "other"


def _opportunity_score(tag, num_comments):
    base = {"buying_intent": 0.9, "answerable_question": 0.7,
            "competitor_complaint": 0.6, "style_trend": 0.4, "other": 0.2}[tag]
    # A thread with a few replies already is easier to add real value to
    # than a brand-new post or one that's already ballooned into a mega-thread.
    if 1 <= num_comments <= 15:
        base += 0.1
    return round(min(base, 1.0), 2)


def _fetch_new_threads_oauth(subs, limit):
    out = []
    for sub in subs:
        data = _api_get(f"/r/{sub}/new", {"limit": limit})
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            tag = _tag(p.get("title", ""), p.get("selftext", ""))
            out.append({
                "thread_id": p.get("name", ""),
                "subreddit": sub,
                "title": p.get("title", ""),
                "permalink": "https://reddit.com" + p.get("permalink", ""),
                "author": p.get("author", ""),
                "created_utc": int(p.get("created_utc", 0)),
                "score": int(p.get("score", 0)),
                "num_comments": int(p.get("num_comments", 0)),
                "tag": tag,
                "opportunity_score": _opportunity_score(tag, int(p.get("num_comments", 0))),
            })
    return out


_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _fetch_new_threads_rss(subs, limit):
    """No auth, no rate-limit budget to spend — just Reddit's own public
    feed. Missing score/comment count (RSS doesn't carry them) means the
    opportunity score is tag-only here, a little less precise than the
    OAuth version, but real threads beat none while the ticket is pending.

    One subreddit's transient 429/error doesn't kill the whole sync — each
    is fetched independently, so a hiccup on r/Watches still leaves whatever
    r/SeikoMods and the rest returned. Only raises if every subreddit failed,
    since a sync that silently returns nothing looks identical to "nothing
    new" otherwise."""
    out = []
    errors = []
    for i, sub in enumerate(subs):
        if i:
            time.sleep(4)   # a beat between requests, unauthenticated and unhurried —
                             # anonymous RSS's real per-IP budget turned out tighter than
                             # expected in testing, so err conservative
        url = f"https://www.reddit.com/r/{sub}/new/.rss?limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            errors.append(f"r/{sub}: {e.code}")
            continue
        except urllib.error.URLError as e:
            errors.append(f"r/{sub}: {e.reason}")
            continue
        root = ET.fromstring(body)
        for entry in root.findall("atom:entry", _ATOM_NS)[:limit]:
            title = (entry.findtext("atom:title", "", _ATOM_NS) or "").strip()
            content = (entry.findtext("atom:content", "", _ATOM_NS) or "")
            raw_author = entry.findtext("atom:author/atom:name", "", _ATOM_NS) or ""
            author = raw_author[3:] if raw_author.startswith("/u/") else raw_author
            link_el = entry.find("atom:link", _ATOM_NS)
            permalink = link_el.get("href") if link_el is not None else ""
            thread_id = (entry.findtext("atom:id", "", _ATOM_NS) or "").strip()
            updated = entry.findtext("atom:updated", "", _ATOM_NS) or ""
            try:
                created_utc = int(datetime.datetime.fromisoformat(updated).timestamp())
            except ValueError:
                created_utc = 0
            tag = _tag(title, content)
            out.append({
                "thread_id": thread_id, "subreddit": sub, "title": title,
                "permalink": permalink, "author": author, "created_utc": created_utc,
                "score": None, "num_comments": None, "tag": tag,
                "opportunity_score": _opportunity_score(tag, 0),
            })
    if errors and not out:
        raise RedditError("Couldn't reach any subreddit's RSS feed: " + "; ".join(errors))
    if errors:
        print(f"[reddit_rss] partial fetch, some subs failed: {'; '.join(errors)}", flush=True)
    return out


def mode():
    """Which source fetch_new_threads() is currently using — the UI shows a
    lighter banner (not a hard "disconnected" one) when this is 'rss', since
    the Listener still works, just with less data per thread."""
    return "oauth" if configured() else "rss"


def fetch_new_threads(subs=None, limit=25):
    """New posts from each target sub, tagged and scored. Read-only — makes
    no writes to Reddit either way. Uses the OAuth API when credentials
    exist, otherwise falls back to the public RSS feed (see module
    docstring) so the Listener has real data before the app is approved."""
    subs = subs or TARGET_SUBS
    if configured():
        return _fetch_new_threads_oauth(subs, limit)
    return _fetch_new_threads_rss(subs, limit)
