"""Phase 6C1: profile tag classification, and its precedence.

The precedence is the contract:

    conflicting -> duplicated_session_evidence -> unsupported -> generic
                -> specific_informative

Revision 1 of the design had no `unsupported`, so a tag matching NOTHING fell
through `generic` (coverage 0.0 is not > 0.5) and was classified
`specific_informative` -- the worst available error, because a tag with no
candidate support would then have been counted as the evidence that
personalization is viable. Several cases here exist only to keep that fixed.
"""
from __future__ import annotations

import dataclasses
import unittest

import starter.context as C

CEILING = 0.5


def window(hits: int, size: int = 30, tag: str = "cotton") -> tuple[str, ...]:
    """A window where exactly `hits` of `size` candidates match `tag`."""
    return tuple([f"a {tag} shirt"] * hits + ["a plain coat"] * (size - hits))


def policy(**kw) -> C.ProfilePolicy:
    return C.ProfilePolicy(**{"profile_max_coverage": CEILING, **kw})


def snapshot(tags=("cotton",), stated=(), negated=(), blocked=(), turn=1):
    return C.ProfileSnapshot(
        tags=C.normalize_profile_tags(tags),
        stated_values=tuple(stated), negated_values=tuple(negated),
        blocked_values=tuple(blocked), turn=turn)


def classify(snap, texts, pol=None) -> C.ProfileDecision:
    return C.classify_profile(snap, pol or policy(), texts)


def category_of(decision, tag) -> str:
    return {c.tag: c.category for c in decision.tags}[tag]


class PrecedenceTest(unittest.TestCase):
    """First match wins, in exactly the registered order."""

    def test_supported_and_uncommon_is_specific_informative(self) -> None:
        got = classify(snapshot(), window(3))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.SPECIFIC_INFORMATIVE)

    def test_zero_support_is_unsupported_not_specific_informative(self) -> None:
        # The revision-1 defect, pinned: coverage 0.0 is not > 0.5, so without
        # an `unsupported` category this fell through to credible.
        got = classify(snapshot(), window(0))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.UNSUPPORTED)
        self.assertEqual(got.credible_tags, ())

    def test_over_the_ceiling_is_generic(self) -> None:
        got = classify(snapshot(), window(16))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.GENERIC)

    def test_stated_beats_support(self) -> None:
        got = classify(snapshot(stated=("cotton",)), window(3))
        self.assertEqual(category_of(got, "cotton"),
                         C.ProfileTagCategory.DUPLICATED_SESSION_EVIDENCE)

    def test_negated_beats_stated(self) -> None:
        got = classify(snapshot(stated=("cotton",), negated=("cotton",)), window(3))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.CONFLICTING)

    def test_blocked_by_override_is_conflicting(self) -> None:
        got = classify(snapshot(blocked=("cotton",)), window(3))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.CONFLICTING)

    def test_conflicting_beats_zero_support(self) -> None:
        # The precedence fixture from notes/37: a higher rule wins even when a
        # lower one also applies.
        got = classify(snapshot(negated=("cotton",)), window(0))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.CONFLICTING)

    def test_duplicated_beats_zero_support(self) -> None:
        got = classify(snapshot(stated=("cotton",)), window(0))
        self.assertEqual(category_of(got, "cotton"),
                         C.ProfileTagCategory.DUPLICATED_SESSION_EVIDENCE)

    def test_unsupported_beats_generic_is_unreachable_but_ordered(self) -> None:
        # match_count 0 and coverage > ceiling cannot co-occur; the ordering is
        # asserted anyway so a later refactor cannot reorder them unnoticed.
        order = list(C.PROFILE_CATEGORY_PRECEDENCE)
        self.assertEqual(order, [
            C.ProfileTagCategory.CONFLICTING,
            C.ProfileTagCategory.DUPLICATED_SESSION_EVIDENCE,
            C.ProfileTagCategory.UNSUPPORTED,
            C.ProfileTagCategory.GENERIC,
            C.ProfileTagCategory.SPECIFIC_INFORMATIVE,
        ])


class CeilingBoundaryTest(unittest.TestCase):
    """`>` against `>=`: the likeliest silent inversion."""

    def test_exactly_at_the_ceiling_is_still_credible(self) -> None:
        got = classify(snapshot(), window(15))
        self.assertEqual(category_of(got, "cotton"),
                         C.ProfileTagCategory.SPECIFIC_INFORMATIVE)
        self.assertEqual({c.tag: c.coverage for c in got.tags}["cotton"], 0.5)

    def test_one_candidate_over_the_ceiling_is_generic(self) -> None:
        got = classify(snapshot(), window(16))
        self.assertEqual(category_of(got, "cotton"), C.ProfileTagCategory.GENERIC)

    def test_the_singleton_hit_is_credible(self) -> None:
        # D3 lost its lower coverage bound for this case: one match in Top-30
        # identifies a candidate uniquely and is the MOST informative signal
        # available, not the least.
        got = classify(snapshot(), window(1))
        self.assertEqual(category_of(got, "cotton"),
                         C.ProfileTagCategory.SPECIFIC_INFORMATIVE)
        self.assertEqual({c.tag: c.match_count for c in got.tags}["cotton"], 1)


class SubstringTrapTest(unittest.TestCase):
    def test_a_substring_only_window_is_unsupported(self) -> None:
        # Under substring matching this window would read as full coverage and
        # classify `generic`; under the word-boundary kernel it is unsupported.
        # The two matchers MUST disagree here.
        texts = tuple(["a great outfit"] * 30)
        got = classify(snapshot(tags=("fit",)), texts)
        self.assertEqual(category_of(got, "fit"), C.ProfileTagCategory.UNSUPPORTED)
        self.assertTrue(all("fit" in t for t in texts), "the trap must contain the substring")


class SessionVerdictTest(unittest.TestCase):
    def test_no_tags_is_no_signal(self) -> None:
        for empty in ((), ("",), (None,)):
            got = classify(snapshot(tags=empty), window(3))
            self.assertEqual(got.session_verdict, C.ProfileSessionVerdict.NO_SIGNAL)
            self.assertEqual(got.tags, ())

    def test_all_tags_rejected_is_not_no_signal(self) -> None:
        # "The user told us nothing" and "what they told us was useless" call
        # for different conclusions and must stay distinguishable.
        got = classify(snapshot(), window(0))
        self.assertEqual(got.session_verdict, C.ProfileSessionVerdict.NO_CREDIBLE_TAG)
        self.assertNotEqual(got.session_verdict, C.ProfileSessionVerdict.NO_SIGNAL)

    def test_one_credible_tag_is_credible(self) -> None:
        got = classify(snapshot(), window(3))
        self.assertEqual(got.session_verdict, C.ProfileSessionVerdict.HAS_CREDIBLE_TAG)
        self.assertEqual(got.credible_tags, ("cotton",))


class DeterminismAndBoundsTest(unittest.TestCase):
    def test_identical_inputs_give_identical_decisions(self) -> None:
        a = classify(snapshot(tags=("cotton", "wool")), window(3))
        b = classify(snapshot(tags=("cotton", "wool")), window(3))
        self.assertEqual(a, b)

    def test_credible_tags_preserve_input_order(self) -> None:
        texts = tuple(["a cotton wool blend"] * 3 + ["plain"] * 27)
        got = classify(snapshot(tags=("wool", "cotton")), texts)
        self.assertEqual(got.credible_tags, ("wool", "cotton"))

    def test_the_decision_is_immutable(self) -> None:
        got = classify(snapshot(), window(3))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            got.session_verdict = C.ProfileSessionVerdict.NO_SIGNAL   # type: ignore[misc]

    def test_at_most_eight_tags_are_classified(self) -> None:
        got = classify(snapshot(tags=[f"tag{i}" for i in range(40)]), window(0))
        self.assertLessEqual(len(got.tags), C.MAX_PROFILE_TAGS)

    def test_the_snapshot_is_not_mutated(self) -> None:
        snap = snapshot(tags=("cotton", "wool"))
        before = dataclasses.asdict(snap)
        classify(snap, window(3))
        self.assertEqual(dataclasses.asdict(snap), before)

    def test_an_empty_window_classifies_every_tag_unsupported(self) -> None:
        got = classify(snapshot(tags=("cotton", "wool")), ())
        self.assertEqual({c.category for c in got.tags},
                         {C.ProfileTagCategory.UNSUPPORTED})


class PurityTest(unittest.TestCase):
    def test_classify_takes_no_agent_catalog_or_ground_truth(self) -> None:
        import inspect
        names = set(inspect.signature(C.classify_profile).parameters)
        for banned in ("agent", "catalog", "cat", "state", "index", "callback",
                       "target", "ground_truth", "parent_asin", "sample"):
            self.assertNotIn(banned, names, banned)

    def test_no_snapshot_field_can_carry_the_target(self) -> None:
        # D5 joins the target in the LAB, after the decision. If it could reach
        # the decision the whole phase would be measuring a leak.
        fields = {f.name for f in dataclasses.fields(C.ProfileSnapshot)}
        for banned in ("target", "ground_truth", "parent_asin", "asin", "answer"):
            self.assertNotIn(banned, fields, banned)

    def test_the_decision_carries_no_candidate_identity(self) -> None:
        fields = {f.name for f in dataclasses.fields(C.ProfileDecision)}
        for banned in ("asins", "candidates", "window", "ranked", "target"):
            self.assertNotIn(banned, fields, banned)


if __name__ == "__main__":
    unittest.main()
