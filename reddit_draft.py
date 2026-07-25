#!/usr/bin/env python3
"""Builds a post for r/IndiaWatchMods from photos of a build, in passes.

One generation gives you something that reads like a generation. This runs
five, each with a job the others don't do, and keeps every pass so the draft
can be inspected rather than trusted:

  1. research   — what actually gets engagement in watch-mod subs right now,
                  read from the Listener's own collected threads, not guessed
  2. draft      — write the post from the build, aimed at that evidence
  3. audit      — check it against the sub's purpose and the claims we can
                  actually stand behind
  4. humanise   — strip the tells: listicles, "elevate", tricolon openers,
                  the em-dash-and-summary rhythm models fall into
  5. verify     — read it cold as a subscriber would and say go or no-go

Runs on Hermes (MiniMax), not Claude — this is long, repetitive, well-specified
work, exactly what should not consume the Claude budget. Each pass is its own
`hermes -z` call so a failure is isolated to a stage and the rest survive.

The subreddit is Schezan's own and openly branded as the Timelabs hub, so the
usual self-promotion caution doesn't apply the way it does in r/SeikoMods —
but "it's our sub" is not a licence to post advertising, and the audit pass
is what enforces that.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

DB = "/root/ops-dashboard/data/hermes.db"
SUB = "IndiaWatchMods"
HERMES_TIMEOUT = 420


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def hermes(prompt, toolset=None):
    """One Hermes call. Returns text, or raises with whatever it said."""
    cmd = ["hermes"]
    if toolset:
        cmd += ["-t", toolset]
    cmd += ["-z", prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=HERMES_TIMEOUT)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError((r.stderr or out or "hermes gave nothing back")[:300])
    return out


def set_stage(post_id, stage, **cols):
    conn = db()
    sets = "stage=?" + "".join(f", {k}=?" for k in cols)
    conn.execute(f"UPDATE reddit_posts SET {sets} WHERE id=?",
                 [stage] + list(cols.values()) + [post_id])
    conn.commit()
    conn.close()


def evidence():
    """What the Listener has actually seen do well, rather than a guess about
    Reddit in general. Falls back to a plain statement of ignorance when the
    Listener hasn't collected anything yet — an honest 'no data' beats an
    invented trend, which is the sort of thing that produces confident,
    wrong advice."""
    conn = db()
    rows = conn.execute(
        "SELECT subreddit, title, tag, num_comments, score FROM reddit_threads "
        "ORDER BY COALESCE(num_comments,0) DESC, opportunity_score DESC "
        "LIMIT 40").fetchall()
    conn.close()
    if not rows:
        return ("No collected threads yet — the Listener has not gathered "
                "engagement data. Do not invent trends; rely on general "
                "watch-community judgement and say so.")
    lines = []
    for r in rows[:25]:
        c = r["num_comments"]
        lines.append(f"- r/{r['subreddit']} [{r['tag']}] "
                     f"{'(' + str(c) + ' comments) ' if c else ''}{r['title'][:110]}")
    return "\n".join(lines)


PASSES = ("research", "draft", "audit", "humanise", "verify")


def run(post_id):
    conn = db()
    post = conn.execute("SELECT * FROM reddit_posts WHERE id=?", (post_id,)).fetchone()
    conn.close()
    if not post:
        raise SystemExit(f"no post {post_id}")

    photos = json.loads(post["photos"] or "[]")
    brief = (post["brief"] or "").strip()
    kind = post["kind"] or "showcase"
    shots = (f"{len(photos)} photo(s) of the build will be attached by hand "
             f"when posting." if photos else "No photos attached.")

    context = (
        f"You are writing for r/{SUB}, a brand-new subreddit run by Timelabs Co, "
        f"a small Indian brand that builds Seiko mods (NH35/VK63 movements, "
        f"₹11,000–₹25,000 builds). The sub is openly the brand's community hub — "
        f"it is our own subreddit, so we are not sneaking promotion into someone "
        f"else's space. That is not a licence to post advertising: the sub only "
        f"grows if the posts are worth reading on their own.\n\n"
        f"Post type: {kind}.\n"
        f"What the owner said about this build: {brief or '(nothing beyond the photos)'}\n"
        f"{shots}\n")

    out = {}

    # 1 — research
    set_stage(post_id, "research", status="running")
    out["research"] = hermes(
        context +
        "\nHere is what our Listener has actually collected from watch subreddits, "
        "ordered by how much discussion each thread got:\n\n" + evidence() +
        "\n\nIn under 200 words: what shapes of post are earning replies here, "
        "and what would make a build showcase worth commenting on rather than "
        "just upvoting? Be concrete. If the data is thin, say so plainly rather "
        "than inventing a trend.")

    # 2 — draft
    set_stage(post_id, "draft")
    out["draft"] = hermes(
        context + "\nResearch notes:\n" + out["research"] +
        "\n\nWrite the Reddit post. Return exactly:\nTITLE: <one line>\nBODY:\n<the post>\n\n"
        "Rules: no hashtags, no emoji, no marketing voice, no call to action, "
        "no links. Reddit body text, a few short paragraphs at most. Write as "
        "the person who built the watch. End on something that invites a reply "
        "— a real question, not 'let me know what you think'.")

    # 3 — audit
    set_stage(post_id, "audit")
    out["audit"] = hermes(
        context + "\nDraft:\n" + out["draft"] +
        "\n\nAudit this draft. List only actual problems, each on one line:\n"
        "- claims we cannot stand behind (specs, durability, comparisons)\n"
        "- anything that reads like an advertisement\n"
        "- anything that would embarrass a small brand if a watch enthusiast "
        "picked it apart\n"
        "- title that oversells what the photos show\n"
        "If there is nothing wrong, reply exactly: CLEAN")

    # 4 — humanise
    set_stage(post_id, "humanise")
    out["humanise"] = hermes(
        context + "\nDraft:\n" + out["draft"] + "\n\nAudit findings:\n" + out["audit"] +
        "\n\nRewrite the post fixing every audit finding, and make it read like "
        "a person typed it. Specifically remove: bulleted feature lists, the "
        "word 'elevate', three-item rhythms, sentences that restate the previous "
        "sentence, and any tidy summary at the end. Vary sentence length. Keep "
        "it modest — this is someone showing a watch they made, not a launch.\n\n"
        "Return exactly:\nTITLE: <one line>\nBODY:\n<the post>")

    # 5 — verify
    set_stage(post_id, "verify")
    out["verify"] = hermes(
        context + "\nFinal post:\n" + out["humanise"] +
        "\n\nRead this cold, as a watch enthusiast scrolling the sub who has "
        "never heard of us. Would you reply to it? Answer in under 80 words, "
        "then a last line of exactly GO or NO-GO.")

    title, body = parse(out["humanise"])
    if not title:
        title, body = parse(out["draft"])

    conn = db()
    conn.execute(
        "UPDATE reddit_posts SET status='ready', stage='done', title=?, body=?, "
        "passes=?, finished_at=datetime('now') WHERE id=?",
        (title, body, json.dumps(out), post_id))
    conn.commit()
    conn.close()
    return title


def parse(text):
    """Pull TITLE/BODY out of a pass. Models drift on format, so this is
    forgiving rather than strict — a draft that arrived is worth recovering."""
    title, body = "", text.strip()
    for line in text.splitlines():
        if line.strip().upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip().strip('"')
            break
    if "BODY:" in text:
        body = text.split("BODY:", 1)[1].strip()
    elif title:
        body = "\n".join(l for l in text.splitlines()
                         if not l.strip().upper().startswith("TITLE:")).strip()
    return title[:300], body


def main():
    post_id = int(sys.argv[1])
    try:
        title = run(post_id)
        print(f"[reddit_draft] post {post_id} ready: {title}", flush=True)
    except Exception as e:
        conn = db()
        conn.execute("UPDATE reddit_posts SET status='failed', error=?, "
                     "finished_at=datetime('now') WHERE id=?", (str(e)[:400], post_id))
        conn.commit()
        conn.close()
        print(f"[reddit_draft] post {post_id} failed: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
