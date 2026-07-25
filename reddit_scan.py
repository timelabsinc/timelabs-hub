#!/usr/bin/env python3
"""Keeps the Listener's pile of threads growing on its own.

Style learning is only as good as what it has read, and it was only reading
when someone remembered to press Sync. Anonymous RSS rate-limits hard, so
this takes ONE subreddit per run and rotates, rather than asking for all four
and getting three refusals.
"""
import sqlite3
import sys
import time

sys.path.insert(0, "/root/ops-dashboard")
import reddit_api

DB = "/root/ops-dashboard/data/hermes.db"


def main():
    subs = reddit_api.TARGET_SUBS + ["IndiaWatchMods"]
    # rotate by the hour so each sub comes round without keeping state
    sub = subs[int(time.time() // 3600) % len(subs)]
    try:
        threads = reddit_api.fetch_new_threads(subs=[sub], limit=25)
    except Exception as e:
        print(f"[reddit_scan] r/{sub}: {e}", flush=True)
        return 0        # a refused fetch is normal, not a failure worth alerting
    conn = sqlite3.connect(DB, timeout=30)
    added = 0
    for t in threads:
        cur = conn.execute("SELECT id FROM reddit_threads WHERE thread_id=?",
                           (t["thread_id"],)).fetchone()
        if cur:
            conn.execute("UPDATE reddit_threads SET score=COALESCE(?,score), "
                         "num_comments=COALESCE(?,num_comments), "
                         "opportunity_score=? WHERE thread_id=?",
                         (t["score"], t["num_comments"], t["opportunity_score"],
                          t["thread_id"]))
        else:
            conn.execute(
                "INSERT INTO reddit_threads (thread_id, subreddit, title, permalink, "
                "author, created_utc, score, num_comments, tag, opportunity_score) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t["thread_id"], t["subreddit"], t["title"], t["permalink"],
                 t["author"], t["created_utc"], t["score"], t["num_comments"],
                 t["tag"], t["opportunity_score"]))
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM reddit_threads").fetchone()[0]
    conn.close()
    print(f"[reddit_scan] r/{sub}: +{added} new, {total} known", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
