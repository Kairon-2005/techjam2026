"""Phase 6B1: the pre-retrieval decision, and the grid that proves it is the
same rule that already exists.

6B1 relocates a decision rather than proposing one, so the governing test is
not "does the new rule look right" but "does it agree with the old one on
every cell of the boundary grid".
"""
from __future__ import annotations

import dataclasses
import itertools
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
import starter.context as C
from tests.test_indexes import PRODUCTS, _catalog_file


class ContractTest(unittest.TestCase):
    ALLOWED = {"route", "query_term_count", "active_slot_count", "dry_streak",
               "rotate_pending"}

    def test_the_snapshot_holds_nothing_from_after_retrieval(self) -> None:
        fields = {f.name for f in dataclasses.fields(C.PreRetrievalSnapshot)}
        self.assertEqual(fields, self.ALLOWED)
        for banned in ("pool_size", "category_count", "category_entropy",
                       "overgeneral", "profile_tags", "turn", "outcome", "rerank"):
            self.assertNotIn(banned, fields)

    def test_policy_carries_the_thresholds_not_the_snapshot(self) -> None:
        self.assertEqual({f.name for f in dataclasses.fields(C.RetrievalPolicy)},
                         {"candidates", "starved_candidates", "starved_after",
                          "starved_max_terms", "starved_max_slots"})

    def test_both_are_immutable(self) -> None:
        snap = C.PreRetrievalSnapshot("buying", 5, 1, 0, False)
        policy = C.policy_from(A.DEFAULTS)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.route = "browsing"        # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.candidates = 1          # type: ignore[misc]

    def test_action_codes_are_disjoint_from_observation_codes(self) -> None:
        self.assertEqual({c.value for c in C.RetrievalReason}
                         & {c.value for c in C.ReasonCode}, set())


class DepthSemanticsTest(unittest.TestCase):
    """max(candidates, starved_candidates), not an assignment."""

    def _depth(self, candidates: int, starved_candidates: int) -> int:
        policy = dataclasses.replace(C.policy_from(A.DEFAULTS),
                                     candidates=candidates,
                                     starved_candidates=starved_candidates,
                                     starved_after=1, starved_max_terms=99,
                                     starved_max_slots=99)
        snap = C.PreRetrievalSnapshot("buying", 1, 0, 5, False)
        decision = C.decide_retrieval(snap, policy)
        self.assertTrue(decision.starved)
        return decision.candidate_depth

    def test_starved_candidates_below_base(self) -> None:
        # The only case where max() is observable: assigning starved_candidates
        # would NARROW the pool while claiming to widen it.
        self.assertEqual(self._depth(500, 100), 500)

    def test_starved_candidates_equal_to_base(self) -> None:
        self.assertEqual(self._depth(500, 500), 500)

    def test_starved_candidates_above_base(self) -> None:
        self.assertEqual(self._depth(100, 1000), 1000)

    def test_unstarved_depth_is_the_base(self) -> None:
        policy = C.policy_from(A.DEFAULTS)
        snap = C.PreRetrievalSnapshot("buying", 99, 99, 0, False)
        self.assertEqual(C.decide_retrieval(snap, policy).candidate_depth,
                         policy.candidates)


class AttributionTest(unittest.TestCase):
    def test_request_more_wins_attribution_without_changing_the_verdict(self) -> None:
        policy = dataclasses.replace(C.policy_from(A.DEFAULTS), starved_after=1)
        both = C.PreRetrievalSnapshot("buying", 1, 0, 5, True)
        stall_only = C.PreRetrievalSnapshot("buying", 1, 0, 5, False)
        a, b = C.decide_retrieval(both, policy), C.decide_retrieval(stall_only, policy)
        self.assertEqual(a.reasons, (C.RetrievalReason.WIDEN_REQUEST_MORE,))
        self.assertEqual(b.reasons, (C.RetrievalReason.WIDEN_THIN_EVIDENCE,))
        self.assertEqual((a.starved, a.candidate_depth),
                         (b.starved, b.candidate_depth),
                         "attribution changed the behaviour")

    def test_widening_disabled_is_reported_as_such(self) -> None:
        policy = dataclasses.replace(C.policy_from(A.DEFAULTS), starved_candidates=0)
        snap = C.PreRetrievalSnapshot("buying", 1, 0, 9, True)
        decision = C.decide_retrieval(snap, policy)
        self.assertFalse(decision.starved)
        self.assertEqual(decision.reasons, (C.RetrievalReason.WIDEN_DISABLED,))


class BoundaryGridTest(unittest.TestCase):
    """Every cell compares legacy _starved() with decide_retrieval().

    The at-threshold cells carry the weight: `>=` against `>` in a relocated
    comparison is the likeliest way this fails silently.
    """

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.agent = A.Agent(_catalog_file(Path(cls._tmp.name)))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def test_every_cell_agrees(self) -> None:
        after, max_terms, max_slots, base = 2, 8, 1, 100
        checked = 0
        for dry, terms, slots, rotate, starved_cands in itertools.product(
                (after - 1, after),                        # below / at
                (max_terms - 1, max_terms, max_terms + 1),  # below / at / above
                (max_slots - 1, max_slots, max_slots + 1),
                (False, True),
                (0, base - 50, base, base + 900)):
            cfg = {**self.agent.cfg, "starved_after": after,
                   "starved_max_terms": max_terms, "starved_max_slots": max_slots,
                   "candidates": base, "starved_candidates": starved_cands}
            state = {"dry_streak": dry, "rotate_pending": rotate,
                     "terms": ["t"] * terms,
                     "slots": [A.SlotValue(attribute="material", value=f"v{i}")
                               for i in range(slots)]}
            legacy = self.agent._starved(state, cfg)
            snap = C.PreRetrievalSnapshot(
                route="buying", query_term_count=terms, active_slot_count=slots,
                dry_streak=dry, rotate_pending=rotate)
            decision = C.decide_retrieval(snap, C.policy_from(cfg))
            with self.subTest(dry=dry, terms=terms, slots=slots,
                              rotate=rotate, starved_candidates=starved_cands):
                self.assertEqual(decision.starved, legacy)
                expected = max(base, starved_cands) if legacy else base
                self.assertEqual(decision.candidate_depth, expected)
            checked += 1
        self.assertEqual(checked, 2 * 3 * 3 * 2 * 4)


if __name__ == "__main__":
    unittest.main()


class ModeTest(unittest.TestCase):
    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = _catalog_file(Path(self._tmp.name))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp.cleanup()

    def agent(self, **cfg) -> A.Agent:
        return A.Agent(self.path, config=cfg or None)

    def turn(self, ag: A.Agent) -> dict:
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses. A key requirement is: silk.", 1, 5)
        ag.respond("s", "Hmm, hard to say really.", 2, 5)
        log = ag._sessions["s"].get("trace_log") or [{}]
        return log[-1]

    def test_the_default_is_control(self) -> None:
        # Adopted after every agreement, bit-exactness and performance gate
        # passed. Shipping a verified controller in "off" would be building the
        # thing and declining to use it.
        self.assertEqual(A.DEFAULTS["retrieval_context_mode"], "control")

    def test_off_computes_no_decision(self) -> None:
        trace = self.turn(self.agent(retrieval_context_mode="off"))
        self.assertNotIn("decided_starved", trace)

    def test_the_rule_exists_once(self) -> None:
        # _starved is an adapter now. If its body ever reacquires the rule,
        # patching decide_retrieval would stop changing what _starved returns.
        ag = self.agent()
        state = {"terms": ["t"], "slots": [], "dry_streak": 99, "rotate_pending": True}
        original = C.decide_retrieval
        try:
            C.decide_retrieval = lambda *a, **k: C.RetrievalDecision(
                starved=False, candidate_depth=1, retrieval_mode="standard", reasons=())
            self.assertFalse(ag._starved(state, ag.cfg),
                             "_starved did not go through decide_retrieval")
        finally:
            C.decide_retrieval = original
        self.assertTrue(ag._starved(state, ag.cfg))

    def test_shadow_records_the_comparison(self) -> None:
        trace = self.turn(self.agent(retrieval_context_mode="shadow"))
        for key in ("decided_starved", "legacy_starved", "starved_agrees",
                    "depth_agrees", "decided_reasons"):
            self.assertIn(key, trace)
        self.assertTrue(trace["starved_agrees"])
        self.assertTrue(trace["depth_agrees"])

    def test_an_unknown_mode_degrades_to_off_and_warns(self) -> None:
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            ag = self.agent(retrieval_context_mode="cotnrol")   # typo on purpose
            self.assertEqual(ag._retrieval_mode(ag.cfg), "off")
        self.assertIn("unknown retrieval_context_mode", buf.getvalue())

    def test_an_unknown_mode_never_becomes_control(self) -> None:
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            for bad in ("control ", "CONTROL", "on", "true", "", "shadow_mode"):
                ag = self.agent(retrieval_context_mode=bad)
                self.assertEqual(ag._retrieval_mode(ag.cfg), "off", bad)

    def test_control_works_without_trace(self) -> None:
        # Orchestration must not depend on telemetry. With trace off there is
        # no trace_log at all, and control must still drive retrieval.
        results = []
        for mode in ("off", "control"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={"trace": False, "retrieval_context_mode": mode})
            ag.reset("s", {})
            first = ag.respond("s", "I'm looking for Clothing Women Dresses, "
                                    "but I'm still exploring.", 1, 5)
            second = ag.respond("s", "Hmm, hard to say really.", 2, 5)
            self.assertIsNone(ag._sessions["s"].get("trace_log"))
            results.append([(r["recommendations"], r["ask_attribute"]) for r in (first, second)])
        self.assertEqual(results[0], results[1],
                         "control changed behaviour with trace off")

    def test_control_and_off_agree_end_to_end(self) -> None:
        seen = []
        for mode in ("off", "shadow", "control"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={"retrieval_context_mode": mode})
            ag.reset("s", {})
            out = [ag.respond("s", "I'm looking for Clothing Women Dresses, "
                                   "but I'm still exploring.", 1, 5),
                   ag.respond("s", "Hmm, hard to say really.", 2, 5),
                   ag.respond("s", "Can you show me more options?", 3, 5)]
            seen.append([(r["recommendations"], r["ask_attribute"], r["message"]) for r in out])
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0], seen[2], "control diverged from the legacy path")

    def test_the_policy_uses_the_same_config_as_retrieval(self) -> None:
        # _route_cfg is resolved once; a route override must reach both the
        # policy and the retrieval that follows it, or the decision would be
        # judged against a configuration that never ran.
        ag = self.agent(retrieval_context_mode="shadow",
                        route_overrides={"browsing": {"candidates": 250}})
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses, but I'm still exploring.", 1, 5)
        trace = ag._sessions["s"]["trace_log"][-1]
        self.assertEqual(trace["decided_depth"], trace["legacy_depth"])
        self.assertEqual(trace["decided_depth"], 250)


class DecisionCallCountTest(unittest.TestCase):
    """How many times the rule actually runs per turn.

    The first version of the dispatch computed the decision explicitly AND
    called _starved(), whose adapter computes it again -- so control ran the
    rule twice per turn and the component benchmark described half the path.
    """

    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = _catalog_file(Path(self._tmp.name))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp.cleanup()

    def calls(self, **cfg) -> int:
        ag = A.Agent(self.path, config=cfg)
        ag.reset("s", {})
        original, count = C.decide_retrieval, []

        def counting(snapshot, policy, _o=original):
            count.append(1)
            return _o(snapshot, policy)

        C.decide_retrieval = counting
        try:
            ag.respond("s", "I'm looking for Clothing Women Dresses, "
                            "but I'm still exploring.", 1, 5)
        finally:
            C.decide_retrieval = original
        return len(count)

    def test_control_runs_the_rule_once_with_trace_on(self) -> None:
        self.assertEqual(self.calls(retrieval_context_mode="control", trace=True), 1)

    def test_control_runs_the_rule_once_with_trace_off(self) -> None:
        self.assertEqual(self.calls(retrieval_context_mode="control", trace=False), 1)

    def test_off_runs_the_rule_once_through_the_adapter(self) -> None:
        self.assertEqual(self.calls(retrieval_context_mode="off"), 1)

    def test_shadow_runs_it_twice_and_that_is_diagnostic(self) -> None:
        # Deliberate: shadow computes the decision AND asks the adapter, to
        # report a comparison. After adoption both go through the same
        # function, so the comparison is no longer evidence of independence.
        self.assertEqual(self.calls(retrieval_context_mode="shadow"), 2)

    def test_control_records_no_agreement_fields(self) -> None:
        ag = A.Agent(self.path, config={"retrieval_context_mode": "control"})
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses, but I'm still exploring.", 1, 5)
        trace = ag._sessions["s"]["trace_log"][-1]
        self.assertIn("decided_starved", trace)
        self.assertNotIn("starved_agrees", trace,
                         "control reported an agreement it never computed")
