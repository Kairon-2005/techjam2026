"""The documents a judge reads must agree with the code they describe.

Every number in the README is a claim, and a stale claim in a README is worse
than no README: it is the one thing a reader will check. These tests keep the
prose tied to the locks.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import starter.agent as A
from tests.test_config_lock import (COMPAT_ANCHOR, PUBLIC_HR10, PUBLIC_MRR,
                                    PUBLIC_MTTC, PUBLIC_SCORE)

README = Path("README.md")


def flatten(text: str) -> str:
    """Prose with blockquote markers and line wrapping removed."""
    return " ".join(line.lstrip("> ").strip() for line in text.splitlines()).replace(
        "  ", " ")



VERIFICATION = Path("docs/FINAL_VERIFICATION.md")
VERIFICATION_JSON = Path("docs/FINAL_VERIFICATION.md.json")


class ReadmeNumbersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = README.read_text(encoding="utf-8")

    def test_the_headline_numbers_are_the_locked_ones(self) -> None:
        for value in (PUBLIC_SCORE, PUBLIC_HR10, PUBLIC_MRR, PUBLIC_MTTC):
            self.assertIn(str(value), self.text, f"{value} is not in the README")

    def test_no_stale_score_is_quoted_as_the_result(self) -> None:
        # 0.9280 and 0.928708 are real historical numbers; neither is the
        # submitted score, and neither may appear as one.
        for stale in ("0.9280", "0.928508"):
            for line in self.text.splitlines():
                if stale in line:
                    self.assertNotIn("TechnicalScore", line, line)

    def test_the_weak_baseline_matches_the_shipped_reference(self) -> None:
        baseline = json.loads(
            Path("docs/baseline_results.json").read_text(encoding="utf-8"))
        self.assertIn(str(baseline["technical_score"]), self.text)

    def test_the_cost_claims_come_from_the_verification_run(self) -> None:
        report = json.loads(VERIFICATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report["result"]["score"], PUBLIC_SCORE)
        self.assertTrue(report["ok"], "the committed verification run did not pass")
        self.assertEqual(report["third_party_loaded"], [])
        self.assertIn(report["python"], VERIFICATION.read_text(encoding="utf-8"))

    def test_zero_cost_is_claimed_because_it_was_measured(self) -> None:
        report = json.loads(VERIFICATION_JSON.read_text(encoding="utf-8"))
        usage = report.get("token_usage") or {}
        self.assertEqual(usage.get("prompt_tokens"), 0)
        self.assertEqual(usage.get("completion_tokens"), 0)


class ReadmeHonestyTest(unittest.TestCase):
    """The claims the write-up is required to qualify, and the ones it must not make."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = README.read_text(encoding="utf-8")
        # Prose is line-wrapped and quoted, so a required phrase is matched
        # against the text with blockquote markers stripped and whitespace
        # collapsed. Otherwise re-flowing a paragraph, or indenting it as a
        # quote, would break a test that has nothing to do with either.
        cls.flat = flatten(cls.text)

    def requires(self, phrase: str) -> None:
        # No `msg` carrying the text: a failed assertIn already prints both
        # operands, and printing a 12 KB README makes the real failure
        # unreadable.
        self.assertTrue(" ".join(phrase.split()) in self.flat,
                        f"the README does not say: {phrase!r}")

    def test_the_showcases_are_never_credited_with_the_public_score(self) -> None:
        for phrase in ("No public number", "architecture demonstration"):
            self.assertTrue(phrase.lower() in self.flat.lower(), phrase)

    def test_a1_is_stated_with_its_transfer_failure(self) -> None:
        for phrase in ("within-generator generalization",
                       "failed cross-distribution transfer",
                       "not claimed as the sole factor"):
            self.requires(phrase)

    def test_the_supplementary_corpus_is_labelled_synthetic(self) -> None:
        self.requires("synthetic")
        self.requires("not a real-user effect")

    def test_cross_session_memory_is_denied(self) -> None:
        self.requires("No cross-session memory")
        self.requires("no stable user identity")

    def test_the_popularity_prior_is_written_as_a_limitation(self) -> None:
        limitations = flatten(self.text.split("## Limitations", 1)[1])
        self.assertIn("artefact of this evaluation", limitations)
        self.assertIn("99.5th popularity percentile", limitations)

    def test_the_readme_does_not_claim_an_llm(self) -> None:
        lowered = self.flat.lower()
        self.assertIn("no llm", lowered)
        for banned in ("gpt-", "we prompt", "our llm"):
            self.assertNotIn(banned, lowered, banned)

    def test_every_referenced_document_exists(self) -> None:
        for path in re.findall(r"\]\((?!http)([^)#]+)\)", self.text):
            if path.startswith("<"):
                continue
            with self.subTest(path=path):
                self.assertTrue(Path(path).exists(), f"{path} is referenced and missing")

    def test_the_documented_profiles_are_the_real_ones(self) -> None:
        for name in A.PROFILES:
            self.assertIn(name, self.text, f"{name} is undocumented")

    def test_the_test_count_claim_is_not_wildly_stale(self) -> None:
        claimed = int(re.search(r"([\d,]+) tests", self.text).group(1).replace(",", ""))
        actual = sum(1 for path in Path("tests").glob("test_*.py")
                     for line in path.read_text(encoding="utf-8").splitlines()
                     if line.strip().startswith("def test_"))
        # Subtests and skips make an exact match brittle; a 10% drift is stale.
        self.assertLess(abs(claimed - actual) / max(actual, 1), 0.10,
                        f"README claims {claimed} tests, source defines {actual}")


class CommandTest(unittest.TestCase):
    """Every command in the README has to be one a judge can actually run."""

    def test_the_reproduction_command_is_real(self) -> None:
        text = README.read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "local_evaluator" in l)
        self.assertIn("--catalog data/catalog.jsonl", line)
        self.assertIn("--dataset data/public_set.jsonl", line)
        self.assertTrue(Path("data/public_set.jsonl").exists())

    def test_the_demo_command_is_real(self) -> None:
        self.assertIn("python3 -m demo", README.read_text(encoding="utf-8"))
        self.assertTrue(Path("demo/__main__.py").exists())

    def test_both_requirements_files_are_referenced_and_present(self) -> None:
        text = README.read_text(encoding="utf-8")
        for name in ("requirements.txt", "requirements-semantic.txt"):
            self.assertIn(name, text)
            self.assertTrue(Path(name).exists())


if __name__ == "__main__":
    unittest.main()
