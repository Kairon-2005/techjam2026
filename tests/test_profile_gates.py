"""Phase 6C1: the B1 fixtures, the D5 controls, and the pooling guard.

B1 is the instrument check that does not depend on real data: every fixture's
expected classification is determined by CONSTRUCTION, so a failure here is a
classifier defect and nothing else. The D5 controls exist because a statistical
gate that cannot fail is not a gate -- and the positive control exists because
three tests proving a gate can fail would be satisfied by a gate that always
fails.
"""
from __future__ import annotations

import unittest

import starter.context as C
from lab import profilegates as G

SIZE = 30
TAG = "cotton"


def window(hits: int, size: int = SIZE, tag: str = TAG) -> tuple[str, ...]:
    return tuple([f"a {tag} shirt"] * hits + ["a plain coat"] * (size - hits))


def classify(tags=(TAG,), texts=window(3), **snap) -> C.ProfileDecision:
    return C.classify_profile(
        C.ProfileSnapshot(tags=C.normalize_profile_tags(tags), **snap),
        C.ProfilePolicy(profile_max_coverage=0.5), texts)


def only(decision) -> C.ProfileTagVerdict:
    verdict, = decision.tags
    return verdict


class B1FixtureTest(unittest.TestCase):
    """Exact known match counts. Every fixture must pass; not a majority."""

    def test_exact_singleton(self) -> None:
        got = only(classify(texts=window(1)))
        self.assertEqual(got.match_count, 1)
        self.assertEqual(got.category, C.ProfileTagCategory.SPECIFIC_INFORMATIVE)

    def test_mid_support(self) -> None:
        got = only(classify(texts=window(10)))
        self.assertEqual((got.match_count, got.coverage), (10, 0.3333))
        self.assertEqual(got.category, C.ProfileTagCategory.SPECIFIC_INFORMATIVE)

    def test_at_the_ceiling_is_credible(self) -> None:
        got = only(classify(texts=window(15)))
        self.assertEqual(got.coverage, 0.5)
        self.assertEqual(got.category, C.ProfileTagCategory.SPECIFIC_INFORMATIVE)

    def test_one_over_the_ceiling_is_generic(self) -> None:
        got = only(classify(texts=window(16)))
        self.assertGreater(got.coverage, 0.5)
        self.assertEqual(got.category, C.ProfileTagCategory.GENERIC)

    def test_zero_support(self) -> None:
        got = only(classify(texts=window(0)))
        self.assertEqual(got.match_count, 0)
        self.assertEqual(got.category, C.ProfileTagCategory.UNSUPPORTED)

    def test_substring_trap(self) -> None:
        # The two matchers MUST disagree here: substring reads full coverage
        # and says generic, word-boundary says unsupported.
        texts = tuple(["a great outfit"] * SIZE)
        got = only(classify(tags=("fit",), texts=texts))
        self.assertEqual(got.category, C.ProfileTagCategory.UNSUPPORTED)
        self.assertTrue(all("fit" in t for t in texts),
                        "the trap stopped containing the substring")

    def test_stated(self) -> None:
        got = only(classify(texts=window(3), stated_values=(TAG,)))
        self.assertEqual(got.category,
                         C.ProfileTagCategory.DUPLICATED_SESSION_EVIDENCE)

    def test_negated(self) -> None:
        got = only(classify(texts=window(3), negated_values=(TAG,)))
        self.assertEqual(got.category, C.ProfileTagCategory.CONFLICTING)

    def test_precedence_negated_and_unsupported(self) -> None:
        got = only(classify(texts=window(0), negated_values=(TAG,)))
        self.assertEqual(got.category, C.ProfileTagCategory.CONFLICTING)

    def test_every_fixture_is_required(self) -> None:
        # D4 needs "B1 all fixtures pass", so the count is part of the contract.
        names = [n for n in dir(self) if n.startswith("test_")]
        self.assertGreaterEqual(len(names), 9)


class D5ControlTest(unittest.TestCase):
    """Three ways D5 could silently become unfailable, plus one that it works."""

    def test_all_ties_fail(self) -> None:
        # Pins the tie convention against a later "ties are half a win" drift,
        # which would turn a null into a pass.
        got = G.d5_target_alignment([0.0] * 200)
        self.assertEqual(got.verdict, G.FAIL)
        self.assertEqual(got.detail["wins"], 0)

    def test_insufficient_significance_fails(self) -> None:
        # 20 of 30 wins: genuinely above 0.5, p ~ 0.049, above alpha = 0.01.
        margins = [0.5] * 20 + [-0.5] * 10
        got = G.d5_target_alignment(margins)
        self.assertEqual(got.verdict, G.FAIL)
        self.assertFalse(got.detail["significant"])
        self.assertGreater(got.detail["p_value"], G.D5_ALPHA)

    def test_insufficient_median_margin_fails(self) -> None:
        # Overwhelming and significant -- 190 of 200 -- but median margin 0.02.
        # Significance without effect size is what large n manufactures.
        margins = [0.02] * 190 + [-0.02] * 10
        got = G.d5_target_alignment(margins)
        self.assertEqual(got.verdict, G.FAIL)
        self.assertTrue(got.detail["significant"])
        self.assertFalse(got.detail["margin_sufficient"])

    def test_a_clearly_aligned_sequence_passes(self) -> None:
        # The positive control. Without it, a gate that always failed would
        # satisfy all three tests above.
        margins = [0.4] * 180 + [-0.1] * 20
        got = G.d5_target_alignment(margins)
        self.assertEqual(got.verdict, G.PASS)
        self.assertTrue(got.detail["significant"])
        self.assertTrue(got.detail["margin_sufficient"])

    def test_below_the_minimum_is_insufficient_not_failed(self) -> None:
        got = G.d5_target_alignment([0.9] * 29)
        self.assertEqual(got.verdict, G.INSUFFICIENT)
        self.assertNotEqual(got.verdict, G.FAIL)
        self.assertNotEqual(got.verdict, G.PASS)

    def test_the_binomial_is_exact(self) -> None:
        # 20/30 one-sided exact is ~0.0494; a normal approximation gives ~0.034
        # and would pass borderline cases the exact test rejects.
        self.assertAlmostEqual(G.binomial_p_value(20, 30), 0.049369, places=5)
        self.assertAlmostEqual(G.binomial_p_value(30, 30), 2 ** -30, places=12)
        self.assertAlmostEqual(G.binomial_p_value(0, 30), 1.0, places=9)

    def test_ties_are_not_wins(self) -> None:
        strict = G.d5_target_alignment([0.0] * 100 + [0.5] * 100)
        self.assertEqual(strict.detail["wins"], 100)


class AlignmentTest(unittest.TestCase):
    def test_alignment_uses_the_shared_kernel(self) -> None:
        # Word-boundary, so "outfit" does not count as a "fit" match.
        self.assertEqual(G.alignment(("fit",), "a great outfit"), 0.0)
        self.assertEqual(G.alignment(("fit",), "a relaxed fit"), 1.0)

    def test_alignment_is_the_share_of_credible_tags_matching(self) -> None:
        self.assertEqual(G.alignment(("cotton", "wool"), "a cotton shirt"), 0.5)

    def test_no_credible_tags_is_zero_not_an_error(self) -> None:
        self.assertEqual(G.alignment((), "anything"), 0.0)

    def test_margin_is_target_minus_median_non_target(self) -> None:
        margin = G.session_margin(("cotton",), "a cotton shirt",
                                  ["a wool coat"] * 5)
        self.assertEqual(margin, 1.0)

    def test_margin_is_none_when_not_computable(self) -> None:
        self.assertIsNone(G.session_margin((), "t", ["a"]))
        self.assertIsNone(G.session_margin(("cotton",), "t", []))


class PseudoReplicationTest(unittest.TestCase):
    """Pooling is a stop condition, not a preference."""

    def obs(self, sample_id, scenario=G.PRIMARY_SCENARIO) -> G.SessionObservation:
        return G.SessionObservation(sample_id=sample_id, scenario=scenario)

    def test_a_duplicated_sample_id_raises(self) -> None:
        with self.assertRaises(ValueError) as caught:
            G.primary([self.obs("a"), self.obs("a")])
        self.assertIn("pseudo-replication", str(caught.exception))

    def test_other_scenarios_are_excluded_not_pooled(self) -> None:
        rows = [self.obs("a"), self.obs("a", "uncooperative"),
                self.obs("b", "contradiction")]
        kept = G.primary(rows)
        self.assertEqual([o.sample_id for o in kept], ["a"])

    def test_the_primary_scenario_is_clean(self) -> None:
        self.assertEqual(G.PRIMARY_SCENARIO, "clean")


class FirstTurnTest(unittest.TestCase):
    def test_only_first_recommendation_turns_are_taken(self) -> None:
        rows = [{"sample_id": "a", "scenario": "clean",
                 "profile_first_recommendation_turn": True,
                 "profile_credible_tags": ["cotton"], "profile_tags": [],
                 "profile_session_verdict": "has_credible_tag",
                 "profile_window_size": 30},
                {"sample_id": "a", "scenario": "clean",
                 "profile_first_recommendation_turn": False,
                 "profile_credible_tags": [], "profile_tags": [],
                 "profile_session_verdict": "no_credible_tag",
                 "profile_window_size": 30}]
        got = G.first_turn_observations(rows)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].credible, ("cotton",))


class GateVerdictTest(unittest.TestCase):
    def gate(self, name, verdict, n=200) -> G.GateResult:
        return G.GateResult(name, verdict, n)

    def test_insufficient_anywhere_blocks_and_is_not_a_failure(self) -> None:
        out = G.phase_verdict(self.gate("D1", G.INSUFFICIENT),
                              self.gate("D2", G.PASS), self.gate("D3", G.PASS),
                              self.gate("D5", G.PASS), self.gate("D4", G.PASS))
        self.assertFalse(out["design_6c2"])
        self.assertEqual(out["verdict"], G.INSUFFICIENT)
        self.assertIn("neither passed nor failed", out["reason"])

    def test_a_d5_failure_is_worded_as_not_demonstrated(self) -> None:
        out = G.phase_verdict(self.gate("D1", G.PASS), self.gate("D2", G.PASS),
                              self.gate("D3", G.PASS), self.gate("D5", G.FAIL),
                              self.gate("D4", G.PASS))
        self.assertEqual(out["reason"],
                         "target alignment was not demonstrated on the public "
                         "clean set")
        self.assertNotIn("does not exist", out["reason"])

    def test_a_broken_instrument_admits_no_conclusion(self) -> None:
        out = G.phase_verdict(self.gate("D1", G.FAIL), self.gate("D2", G.FAIL),
                              self.gate("D3", G.FAIL), self.gate("D5", G.FAIL),
                              self.gate("D4", G.FAIL))
        self.assertIn("no conclusion about Arm A", out["reason"])

    def test_6c2_is_designed_only_when_everything_passes(self) -> None:
        out = G.phase_verdict(*[self.gate(n, G.PASS)
                                for n in ("D1", "D2", "D3", "D5", "D4")])
        self.assertTrue(out["design_6c2"])

    def test_d4_requires_b1_and_b2_including_d5(self) -> None:
        b2 = [self.gate(n, G.PASS) for n in ("D1", "D2", "D3")]
        b2.append(self.gate("D5", G.FAIL))
        got = G.d4_instrument(True, b2, 100)
        self.assertEqual(got.verdict, G.FAIL)
        self.assertEqual(got.detail["b2_failed_gates"], ["D5"])

    def test_d4_fails_when_a_b1_fixture_fails(self) -> None:
        b2 = [self.gate(n, G.PASS) for n in ("D1", "D2", "D3", "D5")]
        self.assertEqual(G.d4_instrument(False, b2, 100).verdict, G.FAIL)

    def test_d4_is_insufficient_below_thirty_eligible(self) -> None:
        b2 = [self.gate(n, G.PASS) for n in ("D1", "D2", "D3", "D5")]
        self.assertEqual(G.d4_instrument(True, b2, 29).verdict, G.INSUFFICIENT)


if __name__ == "__main__":
    unittest.main()
