#!/usr/bin/env python3
"""Shared Reddit access layer — the foundation for the Listener (reddit.py)
and, later, draft-assist posting.

Reads REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USERNAME /
REDDIT_PASSWORD from /root/ops-dashboard/.env. Until they're added it reports
not-configured cleanly, so the Listener shows a setup state instead of
breaking the build.

Why these particular credentials: Reddit closed self-service OAuth app
creation in Nov 2025 — a "script" app now requires manual approval via
Reddit's Developer Support (ticket filed 2026-07-24, ~7 day review). A script
app authenticates as the account itself (password grant), which is the
simplest flow for a single-account bot like this one. Datacenter IPs are
hard-blocked from Reddit's public pages (verified directly from this VPS),
so oauth.reddit.com is the only reachable path — there is no scraping
fallback if these credentials are ever missing.

Create the app: reddit.com/prefs/apps (once support approves the request) →
"script" type → note the client_id (under the app name) and client_secret →
add to .env as:
  REDDIT_CLIENT_ID=xxxxx
  REDDIT_CLIENT_SECRET=xxxxx
  REDDIT_USERNAME=Brief_Client_2900
  REDDIT_PASSWORD=xxxxx
"""
import base64
import json
import time
import urllib.request
import urllib.error
import urllib.parse

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


def fetch_new_threads(subs=None, limit=25):
    """New posts from each target sub, tagged and scored. Read-only — makes
    no writes to Reddit. Raises RedditError if not configured or unreachable."""
    out = []
    for sub in (subs or TARGET_SUBS):
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
