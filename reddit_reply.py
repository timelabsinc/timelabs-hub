#!/usr/bin/env python3
"""Drafts a reply to somebody else's thread.

Replies matter more than posts on a small subreddit. A sub with three people
broadcasting and nobody answering reads as dead; a sub where questions get
real answers is the one people come back to. There is an unanswered question
on r/IndiaWatchMods right now about where to buy NH35 and 8215 movements in
India, which the owner can answer better than almost anyone posting there.

Fewer passes than a post, deliberately. A reply is short, so the risk is not
that it reads as generated, it is that it says nothing useful or that it
turns an answer into an advertisement. The passes reflect that:

  1. answer    — actually answer the question, from what we know
  2. audit     — is this useful to the person who asked, or is it about us?
  3. humanise  — the way somebody types a reply on a phone, not an essay
  4. verify    — would this be worth the reader's time

Runs on Hermes, and every output goes through the same scrubber the posts do.
"""
import json
import sqlite3
import subprocess
import sys

sys.path.insert(0, "/root/ops-dashboard")
import reddit_clean
import writing_quality

DB = "/root/ops-dashboard/data/hermes.db"
HERMES_TIMEOUT = 420
HERMES_TOOLSET = "web"


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def hermes(prompt):
    # Thread text and operator notes are untrusted prompt input. A reply draft
    # needs public-web research at most, never owner memory or terminal access.
    r = subprocess.run(
        ["hermes", "--ignore-rules", "-t", HERMES_TOOLSET, "-z", prompt],
        capture_output=True, text=True, timeout=HERMES_TIMEOUT)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError((r.stderr or out or "hermes gave nothing back")[:300])
    cleaned, _ = reddit_clean.clean(out)
    return cleaned


def set_stage(draft_id, stage, **cols):
    conn = db()
    sets = "stage=?" + "".join(f", {k}=?" for k in cols)
    conn.execute(f"UPDATE reddit_drafts SET {sets} WHERE id=?",
                 [stage] + list(cols.values()) + [draft_id])
    conn.commit()
    conn.close()


BUSINESS = (
    "You are replying as the person behind Timelabs Co, a small Indian brand "
    "that builds Seiko mods on NH35 and VK63 movements, roughly Rs 11,000 to "
    "Rs 25,000 a build. You source parts yourself and have done for a while, "
    "so you genuinely know where things come from, what arrives faulty, and "
    "what a realistic timeline looks like.\n\n"
    "You are not here to sell. Mentioning the brand at all is optional and "
    "usually wrong. If the honest answer is that somebody else's part is "
    "better, say so. A reply that helps and never mentions us is worth more "
    "to this subreddit than one that does.\n\n"
    "Apply this standard before drafting:\n" +
    writing_quality.prompt_brief("reddit") + "\n")


def only_reply(text):
    """Keep the reply, drop the thinking that came with it.

    Asked for "only the reply text", a model will still sometimes narrate its
    reasoning first and then announce the answer. The first real draft opened
    with three paragraphs about which specs it could not verify before getting
    to the reply. Cutting at the last separator or announcement is cruder than
    asking again, and it is reliable."""
    for marker in ("\n---\n", "\nHere's the reply:", "\nHere is the reply:",
                   "\nReply:\n", "\nFinal reply:"):
        idx = text.rfind(marker)
        if idx != -1:
            text = text[idx + len(marker):]
    return text.strip().strip("-").strip()


def run(draft_id):
    conn = db()
    d = conn.execute(
        "SELECT d.*, t.title, t.subreddit, t.author, t.permalink "
        "FROM reddit_drafts d JOIN reddit_threads t ON t.id = d.thread_id "
        "WHERE d.id=?", (draft_id,)).fetchone()
    conn.close()
    if not d:
        raise SystemExit(f"no draft {draft_id}")

    note = (d["answer"] or "").strip()
    thread = (f"Subreddit: r/{d['subreddit']}\n"
              f"Posted by u/{d['author']}\n"
              f"Title: {d['title']}\n")
    context = BUSINESS + "\nThe thread you are replying to:\n" + thread
    if note:
        context += f"\nWhat the owner wants said: {note}\n"

    out = {}

    set_stage(draft_id, "answer", status="running")
    out["answer"] = hermes(
        context +
        "\nWrite a reply that actually answers what they asked. Be specific: "
        "name real parts, real places, real prices in rupees where you know "
        "them, and say plainly where you are not sure. If the title is a "
        "showcase rather than a question, respond to the watch itself and ask "
        "the one thing you would genuinely want to know.\n\n"
        "If you need a fact only the owner has, write a single line starting "
        "exactly ASK: followed by the question.")

    for line in out["answer"].splitlines():
        if line.strip().upper().startswith("ASK:"):
            if not note:
                conn = db()
                conn.execute("UPDATE reddit_drafts SET status='needs_input', "
                             "stage='question', question=? WHERE id=?",
                             (line.split(":", 1)[1].strip()[:600], draft_id))
                conn.commit()
                conn.close()
                return None
            break

    set_stage(draft_id, "audit")
    out["audit"] = hermes(
        context + "\nDraft reply:\n" + out["answer"] +
        "\n\nAudit it. One line each, only real problems:\n"
        "- does it answer the question, or talk around it\n"
        "- anything that reads as promoting ourselves\n"
        "- any claim about a part, price or supplier we cannot stand behind\n"
        "- anything condescending to someone who is clearly a beginner\n"
        "If it is fine, reply exactly: CLEAN")

    set_stage(draft_id, "humanise")
    out["humanise"] = hermes(
        context + "\nDraft:\n" + out["answer"] + "\n\nAudit:\n" + out["audit"] +
        "\n\nRewrite it fixing every finding. Make it read like a reply typed "
        "on a phone by somebody who knows the answer and is not trying to "
        "impress: short, direct, no preamble, no sign-off, no bullet lists "
        "unless a list is genuinely the answer. Two or three short paragraphs "
        "at most, often less.\n\n"
        "Never use an em dash or an en dash. Straight quotes only. Do not "
        "begin with 'Great question'. Return only the reply text.")

    best = out["humanise"]
    for _ in range(2):
        problems = reddit_clean.verify(best)
        if not problems:
            break
        set_stage(draft_id, "polish")
        best = hermes(
            context + "\nReply:\n" + best +
            "\n\nThese fail the TimeLabs editorial check and must go:\n- "
            + "\n- ".join(problems) +
            "\n\nReturn only the corrected reply, same meaning, no dashes.")

    set_stage(draft_id, "verify")
    out["verify"] = hermes(
        context + "\nReply:\n" + best +
        "\n\nIf you were the person who posted that thread, would this reply "
        "be worth reading? Under 60 words, then a last line of exactly GO or "
        "NO-GO.")
    out["polish"] = best

    text, notes = reddit_clean.clean(only_reply(best))
    out["scrubber"] = "; ".join(notes) or "nothing needed removing"
    left = reddit_clean.verify(text)

    conn = db()
    conn.execute(
        "UPDATE reddit_drafts SET status=?, stage='done', draft_text=?, "
        "passes=?, question=?, finished_at=datetime('now') WHERE id=?",
        ("ready" if not left else "needs_input", text, json.dumps(out),
         "" if not left else "Still found: " + "; ".join(left), draft_id))
    conn.commit()
    conn.close()
    return text


def main():
    did = int(sys.argv[1])
    try:
        t = run(did)
        print(f"[reddit_reply] draft {did} " +
              ("ready" if t else "waiting on the owner"), flush=True)
    except Exception as e:
        conn = db()
        conn.execute("UPDATE reddit_drafts SET status='failed', error=?, "
                     "finished_at=datetime('now') WHERE id=?",
                     (str(e)[:400], did))
        conn.commit()
        conn.close()
        print(f"[reddit_reply] draft {did} failed: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
