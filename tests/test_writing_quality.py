import pathlib
import unittest

import writing_quality


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WritingQualityTests(unittest.TestCase):
    def test_prompt_is_channel_specific(self):
        reddit = writing_quality.prompt_brief("reddit")
        instagram = writing_quality.prompt_brief("instagram")
        self.assertIn("useful participant", reddit)
        self.assertIn("image cannot show", instagram)
        self.assertNotEqual(reddit, instagram)

    def test_every_creator_channel_has_native_guidance(self):
        channels = (
            "reddit", "instagram", "linkedin", "meta_ad", "whatsapp",
            "sales", "email", "blog", "youtube", "product", "founder",
        )
        for channel in channels:
            with self.subTest(channel=channel):
                prompt = writing_quality.prompt_brief(channel)
                self.assertIn("Channel rule:", prompt)
                self.assertNotIn("shortest native shape", prompt)

    def test_latest_feedback_becomes_a_hard_revision_constraint(self):
        prompt = writing_quality.prompt_brief("whatsapp")
        self.assertIn("latest explicit user correction overrides", prompt)
        self.assertIn("criticized phrase, premise, structure or angle as rejected", prompt)
        self.assertIn("Do not mistake a complaint about wording", prompt)
        self.assertIn("remove every contradiction", prompt)
        standard = (ROOT / "docs/human-writing-standard.md").read_text(encoding="utf-8")
        self.assertIn("five-part revision ledger", standard)
        self.assertIn("Treat criticism as criticism", standard)
        self.assertIn("Does any rejected phrase, premise or structure remain?", standard)

    def test_whatsapp_introduces_links_without_inventing_the_entry_source(self):
        prompt = writing_quality.prompt_brief("whatsapp")
        self.assertIn("identify what it is and why it is relevant", prompt)
        self.assertIn("Do not assume where the person came from", prompt)

    def test_residue_and_vague_sources_block(self):
        text = "Here is a polished version. Studies show this is better."
        codes = {flag["code"] for flag in writing_quality.lint(text)}
        self.assertIn("residue.polished_version", codes)
        self.assertIn("claim.vague_source", codes)
        self.assertEqual(len(writing_quality.blocking_problems(text)), 2)

    def test_phrase_is_review_by_default_and_strict_for_reddit(self):
        text = "This is not just a watch but a testament to craft."
        self.assertEqual(writing_quality.blocking_problems(text), [])
        strict = writing_quality.blocking_problems(text, "reddit", include_phrases=True)
        self.assertTrue(any(item.startswith("structure.parallelism") for item in strict))
        self.assertTrue(any(item.startswith("phrase.inflated.testament") for item in strict))

    def test_single_question_is_not_a_rule_of_three(self):
        flags = writing_quality.lint("Would you keep the silver hands?")
        self.assertFalse(any(flag["code"].startswith("structure.rule_of_three") for flag in flags))

    def test_free_form_content_requests_are_routed(self):
        cases = {
            "Write an Instagram caption for these photos": "instagram",
            "Draft a WhatsApp sales message for this buyer": "sales",
            "Create a Reddit showcase post": "reddit",
            "Rewrite this customer email": "email",
            "Make this shorter without changing the facts": "general",
            "Adapt the approved copy for another channel": "general",
            "What were sales yesterday?": None,
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(writing_quality.infer_channel(request), expected)


if __name__ == "__main__":
    unittest.main()
