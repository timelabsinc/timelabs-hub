import pathlib
import unittest
from unittest import mock

import access_store


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CreatorWorkspaceTests(unittest.TestCase):
    def test_every_primary_channel_has_a_guided_brief(self):
        source = (ROOT / "command.py").read_text(encoding="utf-8")
        for route in (
                "/caption", "/story", "/reddit", "/whatsapp",
                "/sales", "/email", "/blog", "/youtube",
                "/ad", "/product", "/founder"):
            with self.subTest(route=route):
                self.assertIn(f'data-fill="{route}&#10;', source)
        self.assertIn("creatorContext", source)

    def test_creator_controls_remain_available_inside_an_existing_chat(self):
        source = (ROOT / "command.py").read_text(encoding="utf-8")
        compose = source.index('<div class="compose-wrap">')
        workbar = source.index('<div class="creator-workbar" id="creatorWorkbar"')
        self.assertGreater(workbar, compose)
        self.assertIn("Start new content", source)
        self.assertNotIn("Open Creator guide", source)
        self.assertFalse((ROOT / "docs/creator-guide.md").exists())

    def test_publish_blocks_have_a_dedicated_copy_action(self):
        source = (ROOT / "command.py").read_text(encoding="utf-8")
        self.assertIn("Copy this text", source)
        self.assertIn("Copy full response", source)
        self.assertIn("querySelectorAll('.md pre')", source)

    def test_agent_handoff_separates_copy_from_review_notes(self):
        source = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        self.assertIn("CONTENT_HANDOFF", source)
        self.assertIn("inside one or more fenced code blocks", source)
        self.assertIn("Never put explanations, citations, internal notes", source)
        self.assertIn("ask one short question instead of", source)

    def test_admin_can_preview_creator_presentation_without_changing_permissions(self):
        payload = access_store.presentation_for(access_store.ADMINS[0], "creator")
        self.assertEqual(payload["role"], "creator")
        self.assertEqual(payload["tools"], ["chat", "reddit", "drop"])
        self.assertTrue(payload["preview"])
        self.assertTrue(payload["actual_admin"])
        self.assertFalse(payload["admin"])

        with mock.patch.object(access_store, "get_role", return_value="creator"):
            ignored = access_store.presentation_for("creator@example.test", "admin")
        self.assertEqual(ignored["role"], "creator")
        self.assertFalse(ignored["preview"])
        self.assertFalse(ignored["actual_admin"])

    def test_preview_is_visible_and_creator_prompt_is_restricted(self):
        shell = (ROOT / "hub_shell.py").read_text(encoding="utf-8")
        server = (ROOT / "agent_chat_server.py").read_text(encoding="utf-8")
        drop = (ROOT / "www/drop-index.html").read_text(encoding="utf-8")
        self.assertIn("Preview Labs OS as a role", shell)
        self.assertIn("Visual preview only", shell)
        self.assertIn('params.get("view_as")', server)
        self.assertIn('preview_role in ("full", "creator")', server)
        self.assertIn("window.LabsAccessPromise", drop)
        self.assertIn("i.role === 'creator'", drop)
        self.assertIn("preview_role:accessInfo&&accessInfo.preview", (ROOT / "command.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
