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
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
import reddit_clean

DB = "/root/ops-dashboard/data/hermes.db"
REDDIT_MEDIA = "/root/ops-dashboard/data/reddit-media"
SUB = "IndiaWatchMods"
HERMES_TIMEOUT = 420
HERMES_TOOLSET = "web"
PHOTO_EXTENSIONS = frozenset((".jpg", ".jpeg", ".png", ".webp"))


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def hermes(prompt, toolset=None):
    """One Hermes call, scrubbed on the way out.

    Cleaning here rather than only at the end means no later pass ever sees a
    dash or a watermark character and copies the habit forward."""
    # Briefs and collected thread titles are untrusted text. Never let a
    # drafting prompt inherit the owner's global rules/memory or root-capable
    # default toolset, even when this worker was launched by a content user.
    cmd = ["hermes", "--ignore-rules", "-t", toolset or HERMES_TOOLSET]
    cmd += ["-z", prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=HERMES_TIMEOUT)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError((r.stderr or out or "hermes gave nothing back")[:300])
    cleaned, _ = reddit_clean.clean(out)
    return cleaned


def set_stage(post_id, stage, **cols):
    conn = db()
    sets = "stage=?" + "".join(f", {k}=?" for k in cols)
    conn.execute(f"UPDATE reddit_posts SET {sets} WHERE id=?",
                 [stage] + list(cols.values()) + [post_id])
    conn.commit()
    conn.close()


def our_sub():
    """What is already on our own subreddit.

    It is not an empty room: other people post builds there. That matters
    twice over. A post has to sit sensibly next to what neighbours have
    written, and posting daily over the top of the handful of people who
    showed up on their own is the fastest way to make a small sub feel like
    one person talking to themselves."""
    conn = db()
    rows = conn.execute(
        "SELECT title, author, created_utc FROM reddit_threads "
        "WHERE subreddit=? ORDER BY created_utc DESC LIMIT 15", (SUB,)).fetchall()
    conn.close()
    if not rows:
        return "Nothing collected from our own sub yet."
    mine = sum(1 for r in rows if (r["author"] or "").lower() == "brief_client_2900")
    lines = [f"- u/{r['author']}: {r['title'][:100]}" for r in rows]
    return (f"Recent posts on r/{SUB} ({len(rows)} seen, {mine} of them ours):\n"
            + "\n".join(lines))


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


PASSES = ("research", "draft", "audit", "humanise", "geo", "polish", "verify")

# How many times the pipeline may loop back and rewrite before it settles.
# Finesse comes from re-reading, not from one careful attempt.
MAX_ROUNDS = 3


def ask_owner(post_id, question):
    """Stop and put a question to the owner rather than guessing.

    A pass that needs a fact we don't have (which movement, what the price
    is, whether a claim is true) should say so. Inventing it is how a post
    ends up with something in it we can't stand behind."""
    conn = db()
    conn.execute("UPDATE reddit_posts SET status='needs_input', stage='question', "
                 "question=? WHERE id=?", (question.strip()[:600], post_id))
    conn.commit()
    conn.close()


def answered(post_id):
    conn = db()
    row = conn.execute("SELECT answer FROM reddit_posts WHERE id=?",
                       (post_id,)).fetchone()
    conn.close()
    return (row["answer"] or "").strip() if row else ""


def validated_photos(post_id, raw):
    """Resolve only this post's durable regular image files.

    A retry can happen days after upload. Refuse to draft if a referenced file
    has disappeared instead of claiming the model saw a build that was no
    longer present.
    """
    if not isinstance(raw, list):
        raise RuntimeError("draft photos are not a valid list")
    root = os.path.join(REDDIT_MEDIA, str(int(post_id)))
    photos = []
    for value in raw:
        if not isinstance(value, str):
            raise RuntimeError("draft contains a non-text photo path")
        filename = os.path.basename(value)
        stem, ext = os.path.splitext(filename)
        expected = os.path.join(root, filename)
        if (value != expected or not stem.isascii() or not stem.isdigit()
                or str(int(stem)) != stem or int(stem) < 1
                or ext.lower() not in PHOTO_EXTENSIONS
                or os.path.islink(value) or not os.path.isfile(value)):
            raise RuntimeError(
                "a persisted draft photo is missing or outside its private post folder")
        photos.append(value)
    return photos


def inspect_photos(photos):
    """Ground the writing context in what is actually visible.

    The long writing passes can stay on the inexpensive web model. This one
    bounded pass gets only the vision tool and is explicitly forbidden from
    guessing hidden parts or commercial claims.
    """
    listing = "\n".join(f"{index}. {path}" for index, path in enumerate(photos, 1))
    return hermes(
        "Use the vision tool to open every numbered local image below before "
        "answering. These are photos of one watch build. Return concise factual "
        "observations only: visible dial colour/design, case finish/shape, bezel, "
        "hands, bracelet or strap, complications, and text only when genuinely "
        "readable. Distinguish observations by image where useful. Do not infer "
        "the movement, materials, water resistance, price, origin, durability, "
        "or any hidden specification. Say uncertain when the photo does not prove "
        "something.\n\nImages:\n" + listing,
        toolset="vision")


def run(post_id):
    conn = db()
    post = conn.execute("SELECT * FROM reddit_posts WHERE id=?", (post_id,)).fetchone()
    conn.close()
    if not post:
        raise SystemExit(f"no post {post_id}")

    try:
        raw_photos = json.loads(post["photos"] or "[]")
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("draft photos contain invalid JSON")
    photos = validated_photos(post_id, raw_photos)
    brief = (post["brief"] or "").strip()
    kind = post["kind"] or "showcase"
    out = {}
    if photos:
        set_stage(post_id, "inspect photos", status="running")
        out["image_observations"] = inspect_photos(photos)
        # Persist the grounding pass immediately. If a later writing call
        # fails, the owner can still inspect what the model actually saw.
        set_stage(
            post_id, "inspect photos", status="running",
            passes=json.dumps({"image_observations": out["image_observations"]}))
        shots = (
            f"{len(photos)} photo(s) will be attached when posting.\n"
            "Grounded observations from opening those exact files:\n"
            f"{out['image_observations']}")
    else:
        shots = "No photos attached."

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

    # 1 — research
    set_stage(post_id, "research", status="running")
    out["research"] = hermes(
        context +
        "\nWhat our Listener has collected from the bigger watch subreddits, "
        "ordered by discussion:\n\n" + evidence() +
        "\n\nAnd our own sub, which is small and has real people posting in "
        "it already:\n\n" + our_sub() +
        "\n\nIn under 200 words: what would make this post worth commenting on "
        "rather than just upvoting, given who is already posting there? A small "
        "sub needs posts that give the handful of regulars something to answer, "
        "not broadcasts. Name one thing in the existing posts worth building on. "
        "If the data is thin, say so plainly rather than inventing a trend.")

    # 2 — draft
    set_stage(post_id, "draft")
    out["draft"] = hermes(
        context + "\nResearch notes:\n" + out["research"] +
        "\n\nWrite the Reddit post. Return exactly:\nTITLE: <one line>\nBODY:\n<the post>\n\n"
        "Rules: no hashtags, no emoji, no marketing voice, no call to action, "
        "no links. Reddit body text, a few short paragraphs at most. Write as "
        "the person who built the watch. End on something that invites a reply "
        "— a real question, not 'let me know what you think'.")

    # 3 - audit
    set_stage(post_id, "audit")
    out["audit"] = hermes(
        context + "\nDraft:\n" + out["draft"] +
        "\n\nAudit this draft. List only actual problems, each on one line:\n"
        "- claims we cannot stand behind (specs, durability, comparisons)\n"
        "- anything that reads like an advertisement\n"
        "- anything that would embarrass a small brand if a watch enthusiast "
        "picked it apart\n"
        "- title that oversells what the photos show\n"
        "If a fact is missing that only the builder could know, write a line "
        "starting exactly ASK: followed by the single question worth asking.\n"
        "If there is nothing wrong, reply exactly: CLEAN")

    # A pass may stop and ask rather than invent. The run resumes from here
    # once the owner answers, so the question has to be worth the interruption.
    # Capped at MAX_ROUNDS: `rounds` was already tracked on every answer but
    # never checked, so a post whose regenerated draft kept raising a *new*
    # question each round had no floor — every answer just triggered another
    # full re-run that could ask something else. Past the cap, proceed with
    # whatever's known rather than stopping again.
    for line in out["audit"].splitlines():
        if line.strip().upper().startswith("ASK:"):
            q = line.split(":", 1)[1].strip()
            prior = answered(post_id)
            if not prior:
                if (post["rounds"] or 0) >= MAX_ROUNDS:
                    context += (f"\nThe pipeline wanted to ask: {q}\nBut the owner has "
                                f"already been asked {MAX_ROUNDS} times for this post — "
                                f"proceed without this fact rather than asking again, and "
                                f"say plainly in the post where you're unsure.\n")
                    break
                ask_owner(post_id, q)
                return None
            context += f"\nThe owner was asked: {q}\nHe answered: {prior}\n"
            break

    # 4 - humanise
    set_stage(post_id, "humanise")
    out["humanise"] = hermes(
        context + "\nDraft:\n" + out["draft"] + "\n\nAudit findings:\n" + out["audit"] +
        "\n\nRewrite the post fixing every audit finding, and make it read like "
        "a person typed it on a phone. Remove: bulleted feature lists, the word "
        "'elevate', three-item rhythms, sentences that restate the previous "
        "sentence, and any tidy summary at the end. Vary sentence length hard, "
        "some very short. Keep it modest, this is someone showing a watch they "
        "made.\n\nNever use an em dash or an en dash. Use a comma or a full stop. "
        "Use straight quotes only.\n\n"
        "Return exactly:\nTITLE: <one line>\nBODY:\n<the post>")

    # 5 - geo
    set_stage(post_id, "geo")
    out["geo"] = hermes(
        context + "\nPost:\n" + out["humanise"] +
        "\n\nThe long game is for this subreddit to be what both people and AI "
        "answer engines cite when someone asks about Seiko modding in India. "
        "Engines quote passages that answer a specific question with specific "
        "facts, attached to a named source.\n\n"
        "Without making it read like SEO, suggest in under 120 words: what "
        "concrete detail is missing that would make this passage worth quoting "
        "(a real number, a part name, a price, a failure and what fixed it), "
        "and what question this post would be the answer to. Do not rewrite it.")

    # 6 - polish, looping until the text survives its own inspection
    best = out["humanise"]
    for rnd in range(MAX_ROUNDS):
        set_stage(post_id, f"polish {rnd + 1}")
        problems = reddit_clean.verify(best)
        instruction = (
            context + "\nPost:\n" + best +
            "\n\nEditor notes to work in:\n" + out["geo"] +
            ("\n\nThese give it away as machine-written and must go:\n- "
             + "\n- ".join(problems) if problems else "") +
            "\n\nProduce the final version. Rules, all of them:\n"
            "no em dash or en dash anywhere, no bullet lists, no rhetorical "
            "questions stacked in threes, no closing summary, no marketing "
            "adjectives, straight quotes only. It is fine for the post to be "
            "short. It is not fine for it to sound smooth and empty.\n\n"
            "Return exactly:\nTITLE: <one line>\nBODY:\n<the post>")
        candidate = hermes(instruction)
        t2, b2 = parse(candidate)
        if not t2:
            break
        best = candidate
        if not reddit_clean.verify(b2):
            break
    out["polish"] = best

    # 7 - verify, read cold
    set_stage(post_id, "verify")
    out["verify"] = hermes(
        context + "\nFinal post:\n" + best +
        "\n\nRead this cold, as a watch enthusiast scrolling the sub who has "
        "never heard of us. Would you reply to it? Does any sentence sound "
        "like it came from a language model? Answer in under 80 words, then a "
        "last line of exactly GO or NO-GO.")

    title, body = parse(out["polish"])
    if not title:
        title, body = parse(out["humanise"])
    if not title:
        title, body = parse(out["draft"])

    # Final gate. Everything above is advisory; this is not. A draft that
    # still carries a tell is held back rather than shown as ready, because
    # the whole point is that nobody on Reddit can tell.
    title, _ = reddit_clean.clean(title)
    body, cleaned_notes = reddit_clean.clean(body)
    out["scrubber"] = "; ".join(cleaned_notes) or "nothing needed removing"
    left = reddit_clean.verify(title + "\n" + body)
    status = "ready" if not left else "needs_input"
    question = ("" if not left else
                "The final check still found: " + "; ".join(left) +
                ". Edit it here, or discard and run it again.")

    conn = db()
    conn.execute(
        "UPDATE reddit_posts SET status=?, stage='done', title=?, body=?, "
        "passes=?, question=?, finished_at=datetime('now') WHERE id=?",
        (status, title, body, json.dumps(out), question, post_id))
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
