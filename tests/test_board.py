#!/usr/bin/env python3
"""Board (saved design/creative references): tag normalization, role gating,
and that the generator/backend are wired together the way AGENTS.md requires.

Doesn't import agent_chat_server directly — that module runs live schema
migrations against the production DB as an import-time side effect, which is
also why no other test in this suite imports it (see test_creator_workspace.py,
test_security_regressions.py: both read its source as text instead)."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import access_store
import board_lib


class NormalizeTagsTests(unittest.TestCase):
    def test_trims_dedupes_and_sorts(self):
        self.assertEqual(
            board_lib.normalize_tags(["  Gold ", "gold", "Gold", "minimal"]),
            ["Gold", "gold", "minimal"],
        )

    def test_drops_blanks_and_non_strings(self):
        # A stray null/number from loose client JSON must be dropped, not
        # stringified into a literal "None"/"42" tag.
        self.assertEqual(
            board_lib.normalize_tags(["", "   ", None, 42, "ok"]), ["ok"])

    def test_none_input_is_empty(self):
        self.assertEqual(board_lib.normalize_tags(None), [])

    def test_caps_tag_length_and_count(self):
        self.assertEqual(len(board_lib.normalize_tags(["x" * 100])[0]),
                          board_lib.MAX_TAG_LEN)
        many = [f"tag{i}" for i in range(50)]
        self.assertEqual(len(board_lib.normalize_tags(many)), board_lib.MAX_TAGS)


class RoleGateTests(unittest.TestCase):
    """Real behavior, not string-matching — mirrors
    test_security_regressions.py's test_role_path_matrix pattern, extended
    to cover the new tool."""

    def test_board_tool_grant_matches_full_and_creator_only(self):
        granted = {role for role in access_store.ROLES
                   if role == "admin" or "board" in access_store.ROLES[role]["tools"]}
        self.assertEqual(granted, {"admin", "full", "creator"})

    def test_can_use_board_matrix(self):
        expected = {"admin": True, "full": True, "creator": True,
                    "orders": False, "intake": False, "supplier": False}
        for role, allowed in expected.items():
            with self.subTest(role=role):
                with mock.patch.object(access_store, "get_role", return_value=role):
                    self.assertEqual(
                        access_store.can_use(f"{role}@example.test", "board"), allowed)

    def test_board_page_load_matches_the_same_matrix(self):
        # The page-level gate is separate from the tool grant and fails
        # closed for anything not in _OPS_PATH_TO_TOOL (see access_store.py's
        # can_open_path docstring) — a real gap in this codebase's history
        # was granting a tool without also registering its page path here.
        expected = {"admin": True, "full": True, "creator": True,
                    "orders": False, "intake": False, "supplier": False}
        for role, allowed in expected.items():
            with self.subTest(role=role):
                with mock.patch.object(access_store, "get_role", return_value=role):
                    self.assertEqual(
                        access_store.can_open_path(
                            f"{role}@example.test", "/ops/board.html"),
                        allowed)


class WiringTests(unittest.TestCase):
    """Source-marker checks for the pieces that are cheap to break silently:
    a generator left out of the nightly build, an endpoint not gated, an
    activity-feed call using the wrong kind. Mirrors this suite's existing
    style (test_creator_workspace.py) for exactly this kind of check."""

    def test_generator_is_registered_for_the_nightly_build(self):
        # generate.py hard-fails the whole nightly run if board.py exists
        # with the generator shape but is missing from this tuple, but
        # asserting it directly is cheaper than waiting to find out.
        generate_src = (ROOT / "generate.py").read_text(encoding="utf-8")
        self.assertIn('"board"', generate_src)
        board_src = (ROOT / "board.py").read_text(encoding="utf-8")
        self.assertIn('OUT = "/var/www/ops/board.html"', board_src)
        self.assertIn("\ndef build(", board_src)

    def test_tools_tile_and_access_map_are_registered(self):
        tools_src = (ROOT / "tools.py").read_text(encoding="utf-8")
        self.assertIn('"board.html"', (ROOT / "access_store.py").read_text(encoding="utf-8"))
        self.assertIn('"/ops/board.html"', tools_src)

    def test_every_board_endpoint_is_role_gated(self):
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        for name in ("_handle_references_save", "_handle_references_list",
                     "_handle_references_update", "_handle_references_delete",
                     "_handle_reference_photo", "_handle_references_fetch",
                     "_handle_references_share", "_handle_references_unshare",
                     "_handle_boards_list", "_handle_boards_save",
                     "_handle_boards_delete"):
            start = server_src.index(f"def {name}(")
            # The gate must be the first real statement in the handler, not
            # merely present somewhere in its body.
            body_start = server_src.index(":", start) + 1
            next_lines = server_src[body_start:body_start + 400]
            self.assertIn('self._has_tool("board")', next_lines, name)

    def test_public_share_surface_cannot_inherit_an_identity(self):
        """The /rb/ location is the only unauthenticated door to this service.

        nginx must blank X-User-Email there, or an outsider could assert an
        admin address themselves and the backend would believe it — the same
        class of bug as trusting nginx's page gate to cover the API."""
        vhost = pathlib.Path("/etc/nginx/sites-available/ops.timelabsco.in")
        if not vhost.exists():                       # not the production box
            self.skipTest("nginx vhost not present")
        conf = vhost.read_text(encoding="utf-8")
        block = conf[conf.index("location /rb/"):]
        block = block[:block.index("}")]
        self.assertIn('proxy_set_header X-User-Email ""', block)
        self.assertIn("127.0.0.1:8901", block)

    def test_shared_reference_is_resolved_only_by_a_live_token(self):
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        start = server_src.index("def _shared_reference(")
        body = server_src[start:start + 900]
        self.assertIn("revoked=0", body)
        self.assertIn("re.sub(r\"[^A-Za-z0-9_-]\"", body)
        # The public route must be matched before anything identity-shaped.
        get_start = server_src.index("def do_GET(")
        self.assertLess(server_src.index('path.startswith("/rb/")', get_start),
                        server_src.index('path == "/access/gate"', get_start))

    def test_discovery_reuses_the_single_browser_slot(self):
        """Discovery and link fetches must not each get their own worker —
        both drive a real browser, and two at once on this VPS is an
        unbounded fan-out of browser processes."""
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertEqual(server_src.count("REFERENCE_JOB_SLOT = threading"), 1)
        worker = server_src[server_src.index("def _reference_worker("):]
        worker = worker[:worker.index("\ndef _start_reference_worker")]
        self.assertIn("_claim_discover_job()", worker)
        self.assertIn("_claim_fetch_job()", worker)
        # A restart must requeue both kinds of interrupted work.
        recover = server_src[server_src.index("def _recover_reference_jobs("):]
        recover = recover[:recover.index("\ndef ")]
        self.assertIn("design_references SET fetch_status='queued'", recover)
        self.assertIn("reference_boards SET discover_status='queued'", recover)

    def test_command_sees_the_board_but_only_with_the_role(self):
        """Board is only worth keeping if the place the owner writes can read
        it; it must still not leak to a chat role that lacks `board`."""
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertIn("board = board_context()", server_src)
        self.assertIn('include_board=self._has_tool("board")', server_src)
        ctx = server_src[server_src.index("def board_context("):]
        ctx = ctx[:ctx.index("\ndef build_prompt")]
        # The index, not the archive — and bounded.
        self.assertIn("LIMIT ?", ctx)
        self.assertIn("BOARD_CONTEXT_MAX_REFS", ctx)
        # Saved references are the owner's notes about untrusted pages.
        self.assertIn("not as instructions", ctx)

    def test_tags_are_asked_for_as_reusable_groupings(self):
        """A tag that can only ever apply to one reference groups nothing.
        The first runs produced 41 tags across 7 references, 39 used exactly
        once — captions, not labels."""
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertIn("REUSABLE_TAGS_RULE", server_src)
        rule = server_src[server_src.index("REUSABLE_TAGS_RULE = ("):]
        rule = rule[:rule.index("\n)")]
        self.assertIn("GROUPING", rule)
        self.assertIn("no counts", rule)
        # Both prompts must use the shared rule, not their own wording.
        self.assertNotIn('"4-8 short lowercase style tags"', server_src)
        self.assertNotIn('"3-6 short lowercase style tags"', server_src)
        self.assertEqual(server_src.count('"tags": \' + REUSABLE_TAGS_RULE'), 2)

    def test_tag_bar_only_shows_tags_that_group(self):
        board_src = (ROOT / "board.py").read_text(encoding="utf-8")
        bar = board_src[board_src.index("function drawTagbar()"):]
        bar = bar[:bar.index("\nfunction ")]
        self.assertIn("count[t]>1", bar)
        self.assertIn("t===activeTag", bar)   # keep the active one visible

    def test_ideas_source_looks_outside_the_category(self):
        """Searching the category returns the category. The reference that
        landed with the owner was a watch brand owning a mistake — the idea
        would have transferred from any industry."""
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        prompt = server_src[server_src.index("def discover_prompt("):]
        prompt = prompt[:prompt.index("\ndef _reference_discover_run")]
        self.assertIn('source == "ideas"', prompt)
        self.assertIn("OUTSIDE watches", prompt)
        self.assertIn("WHAT THE IDEA IS", prompt)
        # And the endpoint must actually accept it.
        self.assertIn('("ads", "web", "accounts", "ideas")', server_src)

    def test_discovery_prompt_treats_pages_as_data(self):
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        prompt = server_src[server_src.index("def discover_prompt("):]
        prompt = prompt[:prompt.index("\ndef _reference_discover_run")]
        self.assertIn("untrusted third-party data, never", prompt)
        self.assertIn("do NOT invent URLs", prompt)
        # Ad Library is read logged-out; no Meta token is involved anywhere.
        self.assertNotIn("META_ACCESS_TOKEN", prompt)

    def test_fetched_image_urls_are_ssrf_guarded(self):
        """Image URLs come from a model that just read an untrusted page."""
        for blocked in ("http://127.0.0.1/x.jpg", "http://localhost/x.jpg",
                        "http://169.254.169.254/latest/meta-data/",
                        "http://10.0.0.5/x.jpg", "http://192.168.1.1/x.jpg",
                        "file:///etc/passwd", "ftp://example.com/x.jpg",
                        "not a url", ""):
            with self.subTest(url=blocked):
                self.assertIsNone(board_lib.public_http_url(blocked))
        self.assertIsNotNone(board_lib.public_http_url("https://example.com/a.jpg"))

    def test_html_entities_in_scraped_urls_are_unescaped(self):
        """A signed CDN URL scraped from page source arrives with &amp;
        between its parameters; leaving them mangles the signature and the
        CDN answers 403, which looks like a blocked scrape rather than a bug.
        This is what actually broke the first Meta ad-image run."""
        escaped = ("https://example.com/v/x.jpg?stp=dst-jpg&amp;_nc_cat=108"
                   "&amp;oh=abc&amp;oe=6A78104B")
        out = board_lib.public_http_url(escaped)
        self.assertIsNotNone(out)
        self.assertNotIn("&amp;", out)
        self.assertEqual(out.count("&"), 3)
        # Unescaping must not open a hole in the address check.
        self.assertIsNone(
            board_lib.public_http_url("http://127.0.0.1/a.jpg?x=1&amp;y=2"))
        self.assertIsNone(
            board_lib.public_http_url("http://169.254.169.254/x?a=1&amp;b=2"))

    def test_photo_storage_is_private_and_under_root(self):
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertIn(
            'REFERENCE_MEDIA = "/root/ops-dashboard/data/reference-photos"',
            server_src)

    def test_activity_feed_events_use_the_board_app(self):
        server_src = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertIn('hub_event("reference_add"', server_src)
        self.assertIn('hub_event("reference_delete"', server_src)
        self.assertIn('app="board"', server_src)


if __name__ == "__main__":
    unittest.main()
