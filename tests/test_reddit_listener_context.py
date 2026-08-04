import sqlite3
import unittest

import reddit_api


class RedditListenerContextTests(unittest.TestCase):
    def test_only_http_links_can_reach_the_listener_ui(self):
        self.assertEqual(reddit_api._http_url("javascript:alert(1)"), "")
        self.assertEqual(
            reddit_api._http_url("https://www.reddit.com/r/SeikoMods"),
            "https://www.reddit.com/r/SeikoMods")

    def test_rss_context_keeps_self_text_and_media_link(self):
        raw = (
            '<div class="md"><p>First NH35 build.</p><p>Will this dial fit?</p></div>'
            ' submitted by <a href="https://reddit.com/u/example">u/example</a>'
            '<a href="https://i.redd.it/watch.jpg">[link]</a>'
        )
        body, link, thumbnail, post_type = reddit_api._rss_context(
            raw, "https://reddit.com/r/SeikoMods/comments/abc/example")
        self.assertIn("First NH35 build.", body)
        self.assertIn("Will this dial fit?", body)
        self.assertEqual(link, "https://i.redd.it/watch.jpg")
        self.assertEqual(thumbnail, "")
        self.assertEqual(post_type, "text+image")

    def test_classification_uses_body_not_only_headline(self):
        tag, reason = reddit_api._classify(
            "Hello everyone",
            "I need help fitting an NH35 movement and cannot find a compatible dial.")
        self.assertEqual(tag, "build_help")
        self.assertIn("fitment", reason)

    def test_buying_request_beats_generic_question(self):
        tag, _ = reddit_api._classify(
            "Need advice", "Where can I buy an NH35 movement in India?")
        self.assertEqual(tag, "buying_intent")
        tag, _ = reddit_api._classify(
            "Custom logo", "Wanted to know if there is a custom logo maker with no MOQ.")
        self.assertEqual(tag, "buying_intent")
        tag, _ = reddit_api._classify(
            "[Recommendation] Help choosing an anniversary watch", "Two-watch shortlist")
        self.assertEqual(tag, "buying_intent")

    def test_title_only_showcase_is_not_mistaken_for_build_help(self):
        tag, _ = reddit_api._classify(
            "Weekend Build 3: Namoki x Tron with a retro vibe?", "")
        self.assertEqual(tag, "showcase")

    def test_wear_update_is_recognized_from_post_body(self):
        tag, _ = reddit_api._classify(
            "Blue Oak Skeleton Mod",
            "A month of daily wear later and the coating is holding up well.")
        self.assertEqual(tag, "experience")

    def test_scan_cursor_is_durable_and_rotates(self):
        conn = sqlite3.connect(":memory:")
        first = reddit_api.claim_next_scan_sub(conn)
        conn.commit()
        second = reddit_api.claim_next_scan_sub(conn)
        self.assertEqual(first, "IndiaWatchMods")
        self.assertEqual(second, "SeikoMods")

    def test_upsert_adds_and_refreshes_complete_context(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE reddit_threads (
            id INTEGER PRIMARY KEY, thread_id TEXT UNIQUE, subreddit TEXT,
            title TEXT, permalink TEXT, author TEXT, created_utc INTEGER,
            score INTEGER, num_comments INTEGER, tag TEXT,
            opportunity_score REAL, status TEXT, fetched_at TEXT)""")
        item = {
            "thread_id": "t3_abc", "subreddit": "SeikoMods", "title": "Help",
            "permalink": "https://reddit.com/thread", "author": "builder",
            "created_utc": 2_000_000_000, "score": None, "num_comments": None,
            "tag": "build_help", "opportunity_score": 0.86,
            "body": "The complete question", "content_url": "", "thumbnail_url": "",
            "flair": "Question", "post_type": "text",
            "match_reason": "A build problem", "source": "rss",
        }
        self.assertEqual(reddit_api.upsert_threads(conn, [item]), 1)
        item["body"] = "The corrected complete question"
        self.assertEqual(reddit_api.upsert_threads(conn, [item]), 0)
        row = conn.execute(
            "SELECT body, tag, source FROM reddit_threads WHERE thread_id='t3_abc'"
        ).fetchone()
        self.assertEqual(row, ("The corrected complete question", "build_help", "rss"))

        conn.execute(
            "UPDATE reddit_threads SET title=?, body='' WHERE thread_id='t3_abc'",
            ("Weekend Build 3: Namoki x Tron with a retro vibe?",))
        self.assertEqual(reddit_api.reclassify_recent_threads(conn, days=30), 1)
        self.assertEqual(conn.execute(
            "SELECT tag FROM reddit_threads WHERE thread_id='t3_abc'"
        ).fetchone()[0], "showcase")


if __name__ == "__main__":
    unittest.main()
