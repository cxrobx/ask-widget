from __future__ import annotations

import unittest

from ask_widget.prompts import append_system_for, build_user_prompt


class PromptTests(unittest.TestCase):
    def test_every_action_checks_outside_facts_instead_of_hedging(self) -> None:
        for action in ("eli5", "prove", "ask"):
            with self.subTest(action=action):
                instruction = append_system_for(action)
                self.assertIn("search the web", instruction)
                self.assertIn("Never say you are answering from memory", instruction)
                self.assertIn("never tell the reader to check something themselves", instruction)
                self.assertIn("Don't announce the checks", instruction)

    def test_prove_it_reaches_the_web_only_after_the_folder(self) -> None:
        instruction = append_system_for("prove")
        self.assertLess(instruction.index("Grep/Glob/Read"), instruction.index("check it on the web"))
        self.assertIn("or the URL the evidence came from", instruction)
        self.assertIn("on the web for anything the files cannot settle", build_user_prompt("prove", "a claim"))


if __name__ == "__main__":
    unittest.main()
