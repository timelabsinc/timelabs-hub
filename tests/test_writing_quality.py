import unittest

import writing_quality


class WritingQualityTests(unittest.TestCase):
    def test_prompt_is_channel_specific(self):
        reddit = writing_quality.prompt_brief("reddit")
        instagram = writing_quality.prompt_brief("instagram")
        self.assertIn("useful participant", reddit)
        self.assertIn("image cannot show", instagram)
        self.assertNotEqual(reddit, instagram)

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


if __name__ == "__main__":
    unittest.main()
