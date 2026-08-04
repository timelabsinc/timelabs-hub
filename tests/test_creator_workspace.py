import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CreatorWorkspaceTests(unittest.TestCase):
    def test_every_primary_channel_has_a_guided_brief(self):
        source = (ROOT / "command.py").read_text(encoding="utf-8")
        for route in (
                "/caption", "/story", "/reddit", "/whatsapp",
                "/sales", "/email", "/blog", "/youtube"):
            with self.subTest(route=route):
                self.assertIn(f'data-fill="{route}&#10;', source)
        self.assertIn("Creator guide", source)
        self.assertIn("creatorContext", source)

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

    def test_guide_covers_review_and_permission_boundary(self):
        guide = (ROOT / "docs/creator-guide.md").read_text(encoding="utf-8")
        self.assertIn("## The simple workflow", guide)
        self.assertIn("## Pick the right route", guide)
        self.assertIn("## Final check before publishing", guide)
        self.assertIn("does not post, send messages or publish", guide)


if __name__ == "__main__":
    unittest.main()
