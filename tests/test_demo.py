"""The demo runs, and it does not leak the target to the agent.

A demo is a claim about the system, so the ways it could quietly become a lie
are worth testing: showing the agent the answer, drifting from the scenario
definitions the measurements used, or silently stopping to exercise the path its
own caption promises.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

import demo.__main__ as D

SOURCE = Path("demo/__main__.py").read_text(encoding="utf-8")


class TargetIsolationTest(unittest.TestCase):
    """The agent is never told the answer."""

    def test_the_target_never_reaches_respond(self) -> None:
        tree = ast.parse(SOURCE)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "respond"]
        self.assertTrue(calls, "the demo never calls respond()")
        for call in calls:
            passed = ast.unparse(call)
            self.assertNotIn("target", passed,
                             f"a target reaches the agent: {passed}")

    def test_ground_truth_is_read_only_by_the_harness(self) -> None:
        # `ground_truth` may be read to REPORT the outcome. It must never be
        # placed into the profile or a message the agent sees.
        for line in SOURCE.splitlines():
            if "ground_truth" not in line:
                continue
            self.assertNotIn("respond(", line, line)
            self.assertNotIn("user_profile", line.split("ground_truth")[0], line)

    def test_the_result_banner_says_the_agent_cannot_see_it(self) -> None:
        self.assertIn("harness only, never visible to the agent", SOURCE)


class ScenarioReuseTest(unittest.TestCase):
    """Customer behaviour has ONE definition, and the demo does not fork it."""

    def test_the_demo_drives_the_evaluators_own_loop(self) -> None:
        for needed in ("E.materialize_hidden_fields", "E.initial_message",
                       "E.customer_reply", "E.normalize_recommendations"):
            self.assertIn(needed, SOURCE, f"{needed} is not used")

    def test_the_uncooperative_demo_uses_the_measured_scenario(self) -> None:
        from lab import scenarios as SC
        self.assertIn("uncooperative", SC.BY_NAME)
        self.assertIn('SC.BY_NAME["uncooperative"]', SOURCE)


class HonestyTest(unittest.TestCase):
    """The captions have to match what score_default does."""

    def test_the_override_demo_does_not_claim_erasure_is_the_default(self) -> None:
        import starter.agent as A
        self.assertEqual(A.DEFAULTS["on_override"], "keep")
        self.assertIn("on_override='keep'", SOURCE)
        self.assertIn("it is not the default", SOURCE)

    def test_the_showcases_are_labelled_feature_off(self) -> None:
        for marker in ("(feature-off)", "NO public result",
                       "Architecture demonstration"):
            self.assertIn(marker, SOURCE)

    def test_the_profile_demo_denies_cross_session_memory(self) -> None:
        self.assertIn("no cross-session memory", SOURCE)
        self.assertIn("stable user identity", SOURCE)


class CommandTest(unittest.TestCase):
    def test_every_named_demo_is_callable(self) -> None:
        for name, fn in {**D.DEFAULT, **D.EXTRA}.items():
            self.assertTrue(callable(fn), name)

    def test_list_runs_without_a_catalog(self) -> None:
        self.assertEqual(D.main(["--list"]), 0)

    def test_the_four_defaults_are_the_documented_ones(self) -> None:
        self.assertEqual(sorted(D.DEFAULT),
                         ["browsing", "buying", "override", "uncooperative"])
        self.assertEqual(sorted(D.EXTRA), ["dense", "profile", "semantic"])

    def test_the_demo_script_documents_every_scenario(self) -> None:
        script = Path("docs/DEMO_SCRIPT.md").read_text(encoding="utf-8")
        for name in {**D.DEFAULT, **D.EXTRA}:
            self.assertIn(name, script, f"{name} is undocumented")


if __name__ == "__main__":
    unittest.main()
