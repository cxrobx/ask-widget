from __future__ import annotations

import unittest

from ask_widget.prompts import append_system_for, build_user_prompt


class PromptTests(unittest.TestCase):
    def test_every_action_checks_outside_facts_instead_of_hedging(self) -> None:
        for web in (True, False):
            for action in ("eli5", "prove", "ask"):
                with self.subTest(action=action, web=web):
                    instruction = append_system_for(action, web=web)
                    self.assertIn("Never say you are answering from memory", instruction)
                    self.assertIn("never tell the reader to check something themselves", instruction)
                    self.assertIn("Don't announce the checks", instruction)

    def test_web_off_never_offers_a_web_tool(self) -> None:
        # A tool the model is told about but can't use costs a wasted turn: the
        # latency the setting exists to avoid.
        for action in ("eli5", "prove", "ask"):
            with self.subTest(action=action):
                on, off = append_system_for(action, web=True), append_system_for(action, web=False)
                self.assertIn("search the web", on)
                self.assertNotIn("search the web", off)
                self.assertNotIn("fetch a web page", off)
                self.assertIn("Web lookups are off", off)

    def test_prove_it_checks_the_folder_first(self) -> None:
        self.assertIn("in the files first, then check anything they cannot settle", build_user_prompt("prove", "a claim"))


if __name__ == "__main__":
    unittest.main()
