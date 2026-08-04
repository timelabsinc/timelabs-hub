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
import html
import json
import re
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ENV_PATH = "/root/ops-dashboard/.env"
USER_AGENT = "timelabs-labsos/1.0 by /u/Brief_Client_2900"

# Home turf first, India-specific communities next, broadest reach last.
# r/Watches and r/Seiko sit at the end deliberately, not omitted: both are
# huge/strict on vendor self-promo (see reference_reddit_timelabs memory), so
# a keyword hit there is far more often noise than a real opportunity for an
# India-focused Seiko-mod brand. watchesindia/WatchEnthusiastIndia/
# WatchCollectorsIndia/IndiaWatchExchange are the actually-relevant India
# watch communities found via r/all search 2026-07-25 — they were missing
# entirely before, which is why "other subreddit" recommendations read as
# irrelevant: the Listener was mostly watching threads with no India angle.
TARGET_SUBS = ["SeikoMods", "watchmodding", "watchesindia", "WatchEnthusiastIndia",
               "WatchCollectorsIndia", "IndiaWatchExchange", "Watches", "Seiko"]

# One request per scan respects anonymous RSS limits. Repetition gives our own
# community and SeikoMods more attention without starving the smaller India
# communities; the persisted cursor prevents timer drift from skipping slots.
SCAN_SUBS = [
    "IndiaWatchMods", "SeikoMods", "watchesindia", "IndiaWatchMods",
    "watchmodding", "WatchEnthusiastIndia", "SeikoMods",
    "WatchCollectorsIndia", "IndiaWatchMods", "IndiaWatchExchange",
    "Watches", "Seiko",
]

# Deterministic classification keeps the Listener fast and inspectable. The
# full post body is part of the decision: classifying a build question from
# its headline alone was the main reason the old inbox felt random.
_QUESTION = re.compile(
    r"(?:\?|\b(?:help|advice|question|troubleshoot|problem|want(?:ed)? to know)\b|"
    r"(?:^|[.!]\s*)(?:how|what|where|why|which|can|could|should|would|is|are|does|do)\b)",
    re.I)
_BUYING = re.compile(
    r"\b(?:where (?:can|do) i (?:buy|get|find|source)|where to (?:buy|get|find|source)|"
    r"looking to buy|looking for (?:a )?(?:watch|seller|vendor|supplier|source|recommendation)|"
    r"want to buy|worth buying|buying advice|recommendation|recommend(?: me)? (?:a|an|some)|"
    r"which one should i (?:buy|choose|get)|does anyone have|"
    r"seller|vendor|supplier|custom logo maker|no moq|minimum order|store|budget|price|"
    r"available in india|under (?:rs\.?|inr|\$|₹))\b",
    re.I)
_BUILD = re.compile(
    r"\b(?:nh3[456]|vk63|movement|dial|case|hands?|crown|stem|bezel|crystal|chapter ring|"
    r"gasket|bracelet|strap|lug|rotor|compatib|fit(?:ting)?|align|mod(?:ding)?|build)\b",
    re.I)
_IDENTIFY = re.compile(
    r"\b(?:identify|identification|reference|ref\.? no|model number|legit check|authentic|"
    r"genuine|fake|real or fake|what model|which model)\b",
    re.I)
_COMPLAINT = re.compile(
    r"\b(?:scam|scammed|ripped off|bad experience|disappointed|never again|avoid|"
    r"refund|chargeback|seller issue|vendor issue|arrived broken|arrived damaged)\b",
    re.I)
_STYLE = re.compile(
    r"\b(?:which (?:dial|bezel|strap|bracelet|colour|color)|dial choice|colourway|colorway|"
    r"style trend|design choice|this or that|versus|\bvs\.?)\b",
    re.I)
_EXPERIENCE = re.compile(
    r"\b(?:review|wear update|daily wear|ownership experience|long[- ]term|holding up|"
    r"worn (?:it )?for|after (?:a |one |two |three |\d+ )?(?:day|week|month|year)s?)\b",
    re.I)
_SHOWCASE = re.compile(
    r"\b(?:my (?:first|new|latest)|(?:first|1st)(?: ever)? (?:watch )?build|new build|"
    r"latest build|weekend build(?: \d+)?|finished|completed|wrist check|sotc|"
    r"joined the .*fam|build of the day|skill building day)\b",
    re.I)

TAG_REASONS = {
    "buying_intent": "A buyer is asking where, what or whether to buy",
    "build_help": "A specific build, fitment or parts problem needs an answer",
    "answerable_question": "A concrete watch question can be answered usefully",
    "identification": "The author needs a model or authenticity check",
    "competitor_complaint": "A product or seller problem is being discussed",
    "style_trend": "The author is comparing a visible design choice",
    "experience": "An owner is sharing a real wear or ownership update",
    "showcase": "A community member is sharing a build or watch",
    "other": "Recent watch-community post; no strong reply signal detected",
}


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


def _http_url(value):
    value = html.unescape(str(value or "")).strip()
    return value if urllib.parse.urlparse(value).scheme in ("http", "https") else ""


def _classify(title, body=""):
    """Return a transparent tag and reason from the complete available post."""
    text = " ".join(f"{title or ''} {body or ''}".split())
    question = bool(_QUESTION.search(text))
    if _COMPLAINT.search(text):
        tag = "competitor_complaint"
    elif _BUYING.search(text):
        tag = "buying_intent"
    elif (not (body or "").strip() and _SHOWCASE.search(text)
          and not re.search(r"\b(?:help|advice|question|problem|issue)\b", text, re.I)):
        tag = "showcase"
    elif question and _BUILD.search(text):
        tag = "build_help"
    elif _IDENTIFY.search(text):
        tag = "identification"
    elif question:
        tag = "answerable_question"
    elif _EXPERIENCE.search(text):
        tag = "experience"
    elif _STYLE.search(text):
        tag = "style_trend"
    elif _SHOWCASE.search(text):
        tag = "showcase"
    else:
        tag = "other"
    return tag, TAG_REASONS[tag]


def _tag(title, body=""):
    """Compatibility wrapper used by older callers and tests."""
    return _classify(title, body)[0]


def _opportunity_score(tag, num_comments=None, subreddit="", body=""):
    base = {"buying_intent": 0.9, "build_help": 0.84,
            "answerable_question": 0.72, "identification": 0.66,
            "competitor_complaint": 0.55, "style_trend": 0.42,
            "experience": 0.48, "showcase": 0.3, "other": 0.15}[tag]
    # A thread with a few replies already is easier to add real value to
    # than a brand-new post or one that's already ballooned into a mega-thread.
    if num_comments is not None and 1 <= num_comments <= 15:
        base += 0.06
    community = (subreddit or "").lower()
    if community == "indiawatchmods":
        base += 0.15
    elif community in {"watchesindia", "watchenthusiastindia",
                        "watchcollectorsindia", "indiawatchexchange"}:
        base += 0.07
    if len((body or "").strip()) >= 80:
        base += 0.02
    return round(min(base, 1.0), 2)


class _FeedContentParser(HTMLParser):
    """Extract Reddit's self-text and outbound media link from Atom HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.md_depth = 0
        self.body_parts = []
        self.links = []
        self.images = []
        self._href = ""
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "md" in attrs.get("class", "").split():
            self.md_depth = 1
        elif self.md_depth and tag == "div":
            self.md_depth += 1
        if self.md_depth and tag in ("p", "br", "li"):
            self.body_parts.append("\n")
        if tag == "a":
            self._href = html.unescape(attrs.get("href", "")).strip()
            self._anchor_text = []
        if tag == "img" and attrs.get("src"):
            self.images.append(html.unescape(attrs["src"]).strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append(("".join(self._anchor_text).strip(), self._href))
            self._href = ""
            self._anchor_text = []
        if tag == "div" and self.md_depth:
            self.md_depth -= 1
        if self.md_depth and tag in ("p", "li"):
            self.body_parts.append("\n")

    def handle_data(self, data):
        if self.md_depth:
            self.body_parts.append(data)
        if self._href:
            self._anchor_text.append(data)


def _rss_context(content, permalink=""):
    parser = _FeedContentParser()
    try:
        parser.feed(content or "")
    except Exception:
        pass
    body = re.sub(r"[ \t]+", " ", "".join(parser.body_parts))
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()[:12000]
    content_url = ""
    for label, href in parser.links:
        if label.strip().lower() == "[link]" and href and href != permalink:
            content_url = _http_url(href)
            break
    thumbnail = _http_url(parser.images[0]) if parser.images else ""
    media = bool(thumbnail or re.search(
        r"(?:i\.redd\.it|reddit\.com/gallery|\.(?:jpe?g|png|webp|gif)(?:\?|$))",
        content_url, re.I))
    post_type = ("text+image" if media else "text") if body else (
        "image" if media else "link")
    return body, content_url, thumbnail, post_type


THREAD_CONTEXT_COLUMNS = {
    "body": "TEXT",
    "content_url": "TEXT",
    "thumbnail_url": "TEXT",
    "flair": "TEXT",
    "post_type": "TEXT",
    "match_reason": "TEXT",
    "source": "TEXT",
}


def ensure_thread_columns(conn):
    """Keep timer and web sync writers compatible during rolling deploys."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(reddit_threads)")}
    for name, decl in THREAD_CONTEXT_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE reddit_threads ADD COLUMN {name} {decl}")


def claim_next_scan_sub(conn):
    """Return and advance the durable one-subreddit scan cursor."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("""CREATE TABLE IF NOT EXISTS reddit_scan_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        next_index INTEGER NOT NULL DEFAULT 0)""")
    row = conn.execute(
        "SELECT next_index FROM reddit_scan_state WHERE singleton=1").fetchone()
    index = (row[0] if row else 0) % len(SCAN_SUBS)
    conn.execute(
        "INSERT INTO reddit_scan_state(singleton,next_index) VALUES(1,?) "
        "ON CONFLICT(singleton) DO UPDATE SET next_index=excluded.next_index",
        ((index + 1) % len(SCAN_SUBS),))
    return SCAN_SUBS[index]


def upsert_threads(conn, threads):
    """Persist every field used to classify, display and draft from a post."""
    ensure_thread_columns(conn)
    added = 0
    for item in threads:
        exists = conn.execute(
            "SELECT 1 FROM reddit_threads WHERE thread_id=?", (item["thread_id"],)
        ).fetchone()
        values = (
            item["subreddit"], item["title"], item["permalink"], item["author"],
            item["created_utc"], item["score"], item["num_comments"], item["tag"],
            item["opportunity_score"], item.get("body", ""),
            item.get("content_url", ""), item.get("thumbnail_url", ""),
            item.get("flair", ""), item.get("post_type", ""),
            item.get("match_reason", ""), item.get("source", ""),
            item["thread_id"],
        )
        if exists:
            conn.execute(
                "UPDATE reddit_threads SET subreddit=?, title=?, permalink=?, author=?, "
                "created_utc=?, score=COALESCE(?,score), "
                "num_comments=COALESCE(?,num_comments), tag=?, opportunity_score=?, "
                "body=?, content_url=?, thumbnail_url=?, flair=?, post_type=?, "
                "match_reason=?, source=? WHERE thread_id=?", values)
        else:
            conn.execute(
                "INSERT INTO reddit_threads (subreddit,title,permalink,author,created_utc,"
                "score,num_comments,tag,opportunity_score,body,content_url,thumbnail_url,"
                "flair,post_type,match_reason,source,thread_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            added += 1
    return added


def reclassify_recent_threads(conn, days=30):
    """Apply current transparent rules to already-stored recent records."""
    ensure_thread_columns(conn)
    rows = conn.execute(
        "SELECT id, subreddit, title, body, num_comments FROM reddit_threads "
        "WHERE created_utc >= strftime('%s','now',?)", (f"-{int(days)} days",)
    ).fetchall()
    for row in rows:
        tag, reason = _classify(row[2] or "", row[3] or "")
        score = _opportunity_score(tag, row[4], row[1] or "", row[3] or "")
        conn.execute(
            "UPDATE reddit_threads SET tag=?, opportunity_score=?, match_reason=? "
            "WHERE id=?", (tag, score, reason, row[0]))
    return len(rows)


def _fetch_new_threads_oauth(subs, limit):
    out = []
    for sub in subs:
        data = _api_get(f"/r/{sub}/new", {"limit": limit})
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            title = p.get("title", "")
            body = p.get("selftext", "")
            tag, reason = _classify(title, body)
            permalink = "https://reddit.com" + p.get("permalink", "")
            content_url = _http_url(
                p.get("url_overridden_by_dest") or p.get("url", ""))
            if content_url == permalink or p.get("is_self"):
                content_url = ""
            thumbnail = _http_url(p.get("thumbnail", ""))
            media = bool(p.get("post_hint") == "image" or thumbnail or re.search(
                r"(?:i\.redd\.it|reddit\.com/gallery|\.(?:jpe?g|png|webp|gif)(?:\?|$))",
                content_url, re.I))
            post_type = ("text+image" if media else "text") if body else (
                "image" if media else "link")
            comments = int(p.get("num_comments", 0))
            out.append({
                "thread_id": p.get("name", ""),
                "subreddit": sub,
                "title": title,
                "permalink": permalink,
                "author": p.get("author", ""),
                "created_utc": int(p.get("created_utc", 0)),
                "score": int(p.get("score", 0)),
                "num_comments": comments,
                "tag": tag,
                "opportunity_score": _opportunity_score(
                    tag, comments, sub, body),
                "body": body[:12000], "content_url": content_url,
                "thumbnail_url": thumbnail, "flair": p.get("link_flair_text") or "",
                "post_type": post_type, "match_reason": reason, "source": "oauth",
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
            permalink = _http_url(link_el.get("href")) if link_el is not None else ""
            thread_id = (entry.findtext("atom:id", "", _ATOM_NS) or "").strip()
            updated = entry.findtext("atom:updated", "", _ATOM_NS) or ""
            try:
                created_utc = int(datetime.datetime.fromisoformat(updated).timestamp())
            except ValueError:
                created_utc = 0
            post_body, content_url, thumbnail, post_type = _rss_context(
                content, permalink)
            media_thumb = entry.find(
                "{http://search.yahoo.com/mrss/}thumbnail")
            if media_thumb is not None and media_thumb.get("url"):
                thumbnail = _http_url(media_thumb.get("url", ""))
                if not post_body:
                    post_type = "image"
            tag, reason = _classify(title, post_body)
            out.append({
                "thread_id": thread_id, "subreddit": sub, "title": title,
                "permalink": permalink, "author": author, "created_utc": created_utc,
                "score": None, "num_comments": None, "tag": tag,
                "opportunity_score": _opportunity_score(
                    tag, None, sub, post_body),
                "body": post_body, "content_url": content_url,
                "thumbnail_url": thumbnail, "flair": "", "post_type": post_type,
                "match_reason": reason, "source": "rss",
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
