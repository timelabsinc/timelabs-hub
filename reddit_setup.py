#!/usr/bin/env python3
"""Drafts the furniture a subreddit needs to look like somebody runs it.

A sub with no rules and an empty sidebar reads as abandoned to anyone who
lands on it, which is the opposite of what posting daily is meant to
achieve. Three pieces, each written once and then edited by hand:

  rules    - what belongs here and what does not, in plain language
  sidebar  - the description someone reads before deciding to subscribe
  flair    - post categories, which are also how a small sub looks organised

Written against what is actually in the sub already, not a template. It has
real members posting builds and asking where to buy parts in India, and the
rules should fit that rather than a generic watch community.

Runs on Hermes, scrubbed like everything else.
"""
import sqlite3
import subprocess
import sys

sys.path.insert(0, "/root/ops-dashboard")
import reddit_clean
import writing_quality

DB = "/root/ops-dashboard/data/hermes.db"
SUB = "IndiaWatchMods"
HERMES_TOOLSET = "web"


def existing():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT title, author FROM reddit_threads WHERE subreddit=? "
        "ORDER BY created_utc DESC LIMIT 15", (SUB,)).fetchall()
    conn.close()
    if not rows:
        return "Nothing collected from the sub yet."
    return "\n".join(f"- u/{r['author']}: {r['title'][:100]}" for r in rows)


CONTEXT = (
    f"r/{SUB} is a subreddit for Seiko watch modding in India, run openly by "
    f"Timelabs Co, a small brand that builds mods on NH35 and VK63 movements. "
    f"It is not a stealth marketing sub; it is the brand's community hub and "
    f"says so. It is small and real people already post there.\n\n"
    f"What is actually on the sub right now:\n{{posts}}\n\n"
    f"Indian context matters: people are sourcing parts through IndiaMART, "
    f"AliExpress and Instagram sellers, dealing with customs, and asking what "
    f"is worth importing. Write for them, not for an American modding "
    f"audience.\n"
)

PROMPTS = {
    "rules": (
        "Write the subreddit rules. Six at most, fewer is better. Each one a "
        "short title then one sentence of plain explanation. They should fit "
        "what people actually post here, allow the brand to post openly since "
        "it runs the place, and protect against the things that would actually "
        "ruin it: unmarked selling by others, fake or clone parts passed off as "
        "genuine, and low-effort posts with no detail. Do not write rules about "
        "problems this sub does not have.\n\n"
        "Plain text, numbered. No preamble, no closing note."),
    "sidebar": (
        "Write the sidebar description, the text someone reads before deciding "
        "whether to subscribe. Under 120 words. Say what the sub is for, who "
        "runs it and that they run it, and what a newcomer should do first. "
        "State plainly that Timelabs Co runs it, without turning it into an "
        "advert. No slogans.\n\n"
        "Plain text. No preamble."),
    "flair": (
        "Propose the post flairs. Between five and seven, each a short label "
        "and one line explaining when to use it. They must cover what people "
        "already post here (finished builds, help and sourcing questions, "
        "showing off a find) and what the brand will post. One flair should "
        "mark brand posts clearly so nobody has to guess.\n\n"
        # a colon, not a dash: the scrubber rewrites " - " as prose punctuation
        # and would eat the separator
        "Plain text, one flair per line as: Label: when to use it. No preamble."),
}


def main():
    kind = sys.argv[1]
    if kind not in PROMPTS:
        raise SystemExit(f"unknown piece: {kind}")
    prompt = CONTEXT.format(posts=existing()) + "\n" + PROMPTS[kind] + (
        "\n\nMandatory editorial standard:\n"
        + writing_quality.prompt_brief("reddit")
        + "\n\nNever use an em dash or an en dash. Straight quotes only.")
    base_cmd = ["hermes", "--ignore-rules", "-t", HERMES_TOOLSET, "-z"]
    r = subprocess.run(base_cmd + [prompt], capture_output=True,
                       text=True, timeout=600)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise SystemExit((r.stderr or "hermes gave nothing back")[:300])

    text, _ = reddit_clean.clean(out)
    # one more pass only if the scrub could not fix it alone
    if reddit_clean.verify(text):
        r2 = subprocess.run(
            base_cmd + [prompt + "\n\nYour previous attempt contained: "
             + "; ".join(reddit_clean.verify(text))
             + ". Rewrite without those."],
            capture_output=True, text=True, timeout=600)
        if r2.returncode == 0 and r2.stdout.strip():
            text, _ = reddit_clean.clean(r2.stdout.strip())

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("INSERT INTO reddit_setup (kind, text, updated_at) "
                 "VALUES (?,?,datetime('now')) ON CONFLICT(kind) DO UPDATE "
                 "SET text=excluded.text, updated_at=datetime('now')",
                 (kind, text))
    conn.commit()
    conn.close()
    print(f"[reddit_setup] {kind}: {len(text)} chars", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
