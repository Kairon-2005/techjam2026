"""Phase 6C1: the profile decision wired in, and proved inert.

Shadow means shadow. The gates this file holds are the ones that make that a
structural fact rather than a convention: the snapshot is taken between
_candidates() and _rerank(), correctness does not depend on `trace`, no session
state moves, no index is built, and off/shadow agree bit-exact.
"""
from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import starter.agent as A
import starter.context as C
from tests.test_indexes import _catalog_file

OPENING = "I'm looking for Clothing Women Dresses. A key requirement is: silk."
FOLLOW = "Hmm, hard to say really."


class ProfileIntegrationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def agent(self, **cfg) -> A.Agent:
        return A.Agent(self.path, config=cfg)

    def turns(self, ag, profile=None):
        ag.reset("s", profile if profile is not None else
                 {"preference_tags": ["silk", "cotton", "fit"]})
        return [ag.respond("s", OPENING, 1, 5), ag.respond("s", FOLLOW, 2, 5)]


class ModeTest(ProfileIntegrationBase):
    def test_the_default_is_off(self) -> None:
        self.assertEqual(A.DEFAULTS["profile_context_mode"], "off")

    def test_only_off_and_shadow_exist(self) -> None:
        # There is deliberately no "control" in 6C1: adding one would let it be
        # switched on before the evidence exists.
        self.assertEqual(C.PROFILE_MODES, ("off", "shadow"))

    def test_control_is_refused_and_degrades_to_off(self) -> None:
        with redirect_stderr(io.StringIO()) as buf:
            ag = self.agent(profile_context_mode="control")
            self.assertEqual(ag._profile_mode(ag.cfg), "off")
        self.assertIn("unknown profile_context_mode", buf.getvalue())

    def test_an_unknown_mode_never_becomes_shadow(self) -> None:
        with redirect_stderr(io.StringIO()):
            for bad in ("shadow ", "SHADOW", "on", "true", "", "control"):
                ag = self.agent(profile_context_mode=bad)
                self.assertEqual(ag._profile_mode(ag.cfg), "off", bad)

    def test_off_computes_no_decision(self) -> None:
        ag = self.agent(profile_context_mode="off", trace=True)
        self.turns(ag)
        trace = ag._sessions["s"]["trace_log"][-1]
        self.assertNotIn("profile_session_verdict", trace)

    def test_the_profile_weights_stay_zero(self) -> None:
        self.assertEqual(A.DEFAULTS["w_profile"], 0.0)
        self.assertEqual(A.DEFAULTS["w_profile_adaptive"], 0.0)


class CallSiteTest(ProfileIntegrationBase):
    """Pinned between _candidates() and _rerank(). Anything else is circular."""

    def order(self, **cfg) -> list[str]:
        ag = self.agent(profile_context_mode="shadow", **cfg)
        seen: list[str] = []
        cands, rerank, prof = ag._candidates, ag._rerank, ag._profile_decision
        ag._candidates = lambda *a, _o=cands, **k: (seen.append("candidates"), _o(*a, **k))[1]
        ag._rerank = lambda *a, _o=rerank, **k: (seen.append("rerank"), _o(*a, **k))[1]
        ag._profile_decision = lambda *a, _o=prof, **k: (seen.append("profile"), _o(*a, **k))[1]
        ag.reset("s", {"preference_tags": ["silk"]})
        ag.respond("s", OPENING, 1, 5)
        return seen

    def test_the_profile_snapshot_is_taken_after_candidates_before_rerank(self) -> None:
        seen = self.order()
        self.assertIn("profile", seen)
        self.assertIn("rerank", seen)
        self.assertLess(seen.index("candidates"), seen.index("profile"))
        self.assertLess(seen.index("profile"), seen.index("rerank"),
                        "a post-rerank snapshot measures a window the profile "
                        "would have helped select")

    def test_the_order_holds_without_trace(self) -> None:
        seen = self.order(trace=False)
        self.assertLess(seen.index("candidates"), seen.index("profile"))
        self.assertLess(seen.index("profile"), seen.index("rerank"))

    def test_exactly_one_decision_per_turn(self) -> None:
        self.assertEqual(self.order().count("profile"), 1)

    def test_the_window_is_pre_rerank_candidates(self) -> None:
        # Recorded so the lab can join it; it must be what _candidates gave,
        # not what _rerank returned.
        ag = self.agent(profile_context_mode="shadow", trace=True)
        captured = {}
        original = ag._profile_decision

        def spy(state, window_asins, cfg, *a, **k):
            captured["window"] = list(window_asins)
            return original(state, window_asins, cfg, *a, **k)

        ag._profile_decision = spy
        cands_seen = {}
        orig_cands = ag._candidates

        def cands_spy(*a, **k):
            out = orig_cands(*a, **k)
            cands_seen["asins"] = [asin for asin, _ in out[0]]
            return out

        ag._candidates = cands_spy
        ag.reset("s", {"preference_tags": ["silk"]})
        ag.respond("s", OPENING, 1, 5)
        depth = ag.cfg["pool_depth"]
        self.assertEqual(captured["window"], cands_seen["asins"][:depth])


class InertnessTest(ProfileIntegrationBase):
    """Shadow must not move anything a customer or the scorer can see."""

    def outputs(self, mode: str, **cfg):
        A.clear_catalog_cache()
        ag = self.agent(profile_context_mode=mode, **cfg)
        out = self.turns(ag)
        return [(r["message"], r["ask_attribute"],
                 tuple(d["parent_asin"] for d in r["recommendations"])) for r in out]

    def test_off_and_shadow_are_bit_exact_with_trace(self) -> None:
        self.assertEqual(self.outputs("off", trace=True),
                         self.outputs("shadow", trace=True))

    def test_off_and_shadow_are_bit_exact_without_trace(self) -> None:
        # Correctness must not depend on trace; only telemetry may.
        self.assertEqual(self.outputs("off", trace=False),
                         self.outputs("shadow", trace=False))

    def test_shadow_does_not_mutate_session_state(self) -> None:
        seen = []
        for mode in ("off", "shadow"):
            A.clear_catalog_cache()
            ag = self.agent(profile_context_mode=mode, trace=True)
            self.turns(ag)
            state = ag._sessions["s"]
            seen.append({k: copy.deepcopy(v) for k, v in state.items()
                         if k != "trace_log"})
        self.assertEqual(seen[0], seen[1], "shadow wrote to session state")

    def test_shadow_builds_no_index_that_off_did_not(self) -> None:
        built = {}
        for mode in ("off", "shadow"):
            A.clear_catalog_cache()
            ag = self.agent(profile_context_mode=mode, trace=True)
            self.turns(ag)
            built[mode] = {name: getattr(ag.cat, name) is not None
                           for name in ("_cat_index", "_facet_index", "_dense_index")}
        # The gate is EQUALITY, not "all None": retrieval builds what it
        # builds in both modes, and asserting None would fail for reasons that
        # have nothing to do with the profile path.
        self.assertEqual(built["off"], built["shadow"],
                         "the profile path built an index the legacy path did not")

    def test_a_hostile_profile_changes_nothing(self) -> None:
        # Regex metacharacters, over-long tags, duplicates, non-strings.
        #
        # Non-strings were once excluded here, and not because 6C1 mishandled
        # them -- normalize_profile_tags drops None and coerces 42, and
        # test_profile_primitives covers both. They crashed the agent in the
        # RERANKER, at `_norm(t)`, on a path that predates this phase and runs
        # regardless of profile_context_mode or w_profile. That path now routes
        # through the same normalizer, so this fixture carries the non-strings
        # it always should have; the crash itself is pinned by
        # test_a_non_string_tag_does_not_crash_the_agent.
        hostile = {"preference_tags": [".*", "(", "a" * 500, "silk", "silk",
                                       "", "x" * 41, None, 42] + ["t"] * 20}
        A.clear_catalog_cache()
        base = self.outputs("off", trace=True)
        A.clear_catalog_cache()
        ag = self.agent(profile_context_mode="shadow", trace=True)
        out = self.turns(ag, profile=hostile)
        got = [(r["message"], r["ask_attribute"],
                tuple(d["parent_asin"] for d in r["recommendations"])) for r in out]
        self.assertEqual(base, got)

    def test_a_non_string_tag_does_not_crash_the_agent(self) -> None:
        # The reranker normalizes preference_tags on EVERY turn, BEFORE the
        # w_profile check, so a non-string reached `_norm` -- and crashed --
        # in both modes and at the shipped w_profile of 0.0. Unreachable on
        # the official data, whose tags are all strings, and reachable from
        # any other profile source.
        hostile = {"preference_tags": ["silk", None, 42, ""]}
        for mode in ("off", "shadow"):
            with self.subTest(mode=mode):
                A.clear_catalog_cache()
                ag = self.agent(profile_context_mode=mode, trace=True)
                out = self.turns(ag, profile=hostile)
                self.assertEqual(len(out), 2)
                for reply in out:
                    self.assertIn("recommendations", reply)

    def test_coercing_a_non_string_tag_changes_no_output(self) -> None:
        # The guard is a crash guard and nothing more: at w_profile 0.0 the
        # normalized tags are computed and discarded, so a profile carrying
        # None, an int and "" must land on exactly the baseline output.
        A.clear_catalog_cache()
        base = self.outputs("off", trace=True)
        A.clear_catalog_cache()
        ag = self.agent(profile_context_mode="off", trace=True)
        out = self.turns(ag, profile={"preference_tags": ["silk", None, 42, ""]})
        got = [(r["message"], r["ask_attribute"],
                tuple(d["parent_asin"] for d in r["recommendations"])) for r in out]
        self.assertEqual(base, got)


class RerankerTagNormalizationTest(ProfileIntegrationBase):
    """The reranker's profile-tag handling: total, shared, and not eager."""

    def normalize_calls(self, profile, **cfg) -> int:
        import starter.retrieval as R
        calls = []
        original = R.normalize_profile_tags
        R.normalize_profile_tags = lambda *a, _o=original, **k: (
            calls.append(1), _o(*a, **k))[1]
        try:
            A.clear_catalog_cache()
            ag = self.agent(**cfg)
            self.turns(ag, profile=profile)
        finally:
            R.normalize_profile_tags = original
        return len(calls)

    def test_zero_weights_do_not_normalize_at_all(self) -> None:
        # The requirement: at the shipped w_profile / w_profile_adaptive of
        # 0.0, no branch reads these tags, so the work must be SKIPPED rather
        # than done and discarded. Doing it eagerly is how a non-string
        # crashed the agent while profile weighting was switched off.
        self.assertEqual(
            self.normalize_calls({"preference_tags": ["silk", None, 42]}), 0)

    def test_a_non_zero_weight_does_normalize(self) -> None:
        # And the guard must not have turned the feature off: with a weight
        # set, the shared normalizer runs.
        self.assertGreater(
            self.normalize_calls({"preference_tags": ["silk"]}, w_profile=0.5), 0)
        self.assertGreater(
            self.normalize_calls({"preference_tags": ["silk"]},
                                 w_profile_adaptive=0.5), 0)

    def test_the_shipped_weights_are_still_zero(self) -> None:
        self.assertEqual(A.DEFAULTS["w_profile"], 0.0)
        self.assertEqual(A.DEFAULTS["w_profile_adaptive"], 0.0)

    def test_the_reranker_uses_the_shared_normalizer(self) -> None:
        # One definition of what a profile tag is. A second normalization here
        # would let the reranker and the profile classifier disagree about the
        # same tag, which is invisible until it changes a score.
        import starter.context as C
        import starter.retrieval as R
        self.assertIs(R.normalize_profile_tags, C.normalize_profile_tags)

    def test_hostile_tags_survive_a_non_zero_weight(self) -> None:
        # With the weight ON, the path that used to crash is now exercised for
        # real: None, an int, an empty string, duplicates and an over-long tag.
        A.clear_catalog_cache()
        ag = self.agent(w_profile=0.5)
        out = self.turns(ag, profile={"preference_tags": [
            "silk", None, 42, "", "silk", "z" * 500, ".*", "("]})
        self.assertEqual(len(out), 2)
        for reply in out:
            self.assertIn("recommendations", reply)


class TelemetryTest(ProfileIntegrationBase):
    def trace(self, **cfg) -> dict:
        ag = self.agent(profile_context_mode="shadow", trace=True, **cfg)
        self.turns(ag)
        return ag._sessions["s"]["trace_log"][-1]

    def test_the_trace_carries_the_decision(self) -> None:
        trace = self.trace()
        for key in ("profile_session_verdict", "profile_credible_tags",
                    "profile_tag_count", "profile_window_size",
                    "profile_first_recommendation_turn"):
            self.assertIn(key, trace)

    def test_the_trace_carries_raw_category_counts(self) -> None:
        trace = self.trace()
        for category in C.PROFILE_CATEGORY_PRECEDENCE:
            self.assertIn(f"profile_cat_{category.value}", trace)

    def test_the_trace_carries_per_tag_support(self) -> None:
        # match_count as well as coverage: the lab needs both, and a coverage
        # threshold cannot distinguish "matches nothing" from "matches one".
        rows = self.trace()["profile_tags"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), {"tag", "category", "match_count", "coverage"})

    def test_shadow_without_trace_records_nothing_and_still_runs(self) -> None:
        ag = self.agent(profile_context_mode="shadow", trace=False)
        self.turns(ag)
        self.assertEqual(ag._sessions["s"].get("trace_log", []), [])


if __name__ == "__main__":
    unittest.main()
