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
