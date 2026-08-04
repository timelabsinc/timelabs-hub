#!/usr/bin/env python3
"""Keeps the Listener's pile of threads growing on its own.

Style learning is only as good as what it has read, and it was only reading
when someone remembered to press Sync. Anonymous RSS rate-limits hard, so
this takes ONE subreddit per run and rotates, rather than asking for all four
and getting three refusals.
"""
import sqlite3
import sys

sys.path.insert(0, "/root/ops-dashboard")
import reddit_api

DB = "/root/ops-dashboard/data/hermes.db"


def main():
    state = sqlite3.connect(DB, timeout=30)
    sub = reddit_api.claim_next_scan_sub(state)
    state.commit()
    state.close()
    try:
        threads = reddit_api.fetch_new_threads(subs=[sub], limit=25)
    except Exception as e:
        print(f"[reddit_scan] r/{sub}: {e}", flush=True)
        return 0        # a refused fetch is normal, not a failure worth alerting
    conn = sqlite3.connect(DB, timeout=30)
    added = reddit_api.upsert_threads(conn, threads)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM reddit_threads").fetchone()[0]
    conn.close()
    print(f"[reddit_scan] r/{sub}: +{added} new, {total} known", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
