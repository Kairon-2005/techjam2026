"""Phase 7A-R1: the A2 cascade, and the invariants that make it safe.

Written before the implementation. Every case here is a rule from
notes/44-phase7a-r1-prereg.md revision 3, and the ones that matter most are the
two that revision 1 of that document got WRONG:

  * "every state key identical" is FALSE -- state["shown"] records display
    order, so a permuted Top-10 permutes it. The invariant is membership.
  * set equality cannot see a duplicated or dropped ASIN. Every output
    assertion here uses length + Counter + explicit duplicate checks.
"""
from __future__ import annotations

import collections
import copy
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import starter.agent as A
import starter.context as C
import starter.semantic as SEM
from tests.test_indexes import _catalog_file

OPENING = "I'm looking for Clothing Women Dresses, but I'm still exploring."
FOLLOW = "Hmm, hard to say really."
MORE = "Show me something else."


class SemanticBase(unittest.TestCase):
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

    def turns(self, ag, messages=(OPENING, FOLLOW), top_k: int = 10):
        ag.reset("s", {"preference_tags": ["fit"]})
        return [ag.respond("s", m, i + 1, top_k) for i, m in enumerate(messages)]

    def recs(self, reply) -> list[str]:
        return [d["parent_asin"] for d in reply["recommendations"]]

    def outputs(self, mode_cfg: dict, messages=(OPENING, FOLLOW), top_k: int = 10):
        A.clear_catalog_cache()
        ag = self.agent(**mode_cfg)
        replies = self.turns(ag, messages, top_k)
        state = {k: copy.deepcopy(v) for k, v in ag._sessions["s"].items()}
        return replies, state


class ModeAndDefaultTest(SemanticBase):
    def test_the_default_is_off(self) -> None:
        self.assertEqual(A.DEFAULTS["semantic_rerank_mode"], "off")
        self.assertEqual(A.DEFAULTS["semantic_rerank_k"], 10)
        self.assertEqual(A.DEFAULTS["semantic_lambda"], 0.0)

    def test_an_unknown_mode_degrades_to_off_and_warns(self) -> None:
        with redirect_stderr(io.StringIO()) as buf:
            ag = self.agent(semantic_rerank_mode="ON")
            self.assertEqual(ag._semantic_mode(ag.cfg), "off")
        self.assertIn("unknown semantic_rerank_mode", buf.getvalue())


class EffectiveKTest(SemanticBase):
    """effective_k = min(semantic_rerank_k, top_k, len(ordered))."""

    def test_effective_k_is_clamped_by_top_k(self) -> None:
        # The clamp is what preserves the returned SET for a caller passing a
        # smaller top_k: reordering the first 10 could promote a rank-7 item
        # into a returned five and change the set.
        for k, top_k, n, expected in ((10, 5, 30, 5), (10, 20, 30, 10),
                                      (10, 10, 30, 10), (10, 10, 3, 3),
                                      (0, 10, 30, 0), (30, 10, 30, 10)):
            with self.subTest(k=k, top_k=top_k, n=n):
                self.assertEqual(SEM.effective_k(k, top_k, n), expected)

    def test_effective_k_is_never_negative(self) -> None:
        self.assertEqual(SEM.effective_k(-5, 10, 30), 0)
        self.assertEqual(SEM.effective_k(10, 0, 30), 0)


class FusionTest(SemanticBase):
    """Weighted RRF over ranks. Never over raw logits."""

    def test_lambda_zero_reproduces_a0_order_exactly(self) -> None:
        a0 = ["a", "b", "c", "d"]
        got = C.rrf_fuse(a0, ["d", "c", "b", "a"], lam=0.0)
        self.assertEqual(got, a0)

    def test_lambda_one_is_semantic_order(self) -> None:
        got = C.rrf_fuse(["a", "b", "c"], ["c", "b", "a"], lam=1.0)
        self.assertEqual(got, ["c", "b", "a"])

    def test_fusion_is_a_permutation_of_its_input(self) -> None:
        a0 = list("abcdefghij")
        for lam in (0.0, 0.1, 0.25, 0.5, 1.0):
            got = C.rrf_fuse(a0, list(reversed(a0)), lam=lam)
            with self.subTest(lam=lam):
                self.assertEqual(collections.Counter(got), collections.Counter(a0))
                self.assertEqual(len(got), len(a0))

    def test_ties_break_on_ascending_a0_rank(self) -> None:
        # Identical orders tie on every candidate at any lambda; the A0 rank
        # must decide, so the result is total and stable.
        a0 = list("abcd")
        self.assertEqual(C.rrf_fuse(a0, a0, lam=0.5), a0)

    def test_fusion_uses_ranks_not_scores(self) -> None:
        # Two semantic orders that differ only in (absent) magnitude must give
        # the same fusion: the function takes an ORDER, not scores.
        import inspect
        params = set(inspect.signature(C.rrf_fuse).parameters)
        for banned in ("scores", "logits", "a0_scores", "sem_scores"):
            self.assertNotIn(banned, params)


class InertnessTest(SemanticBase):
    """semantic_rerank_mode="off" must be byte-identical to today's agent."""

    def test_off_is_byte_identical_to_the_baseline(self) -> None:
        base, base_state = self.outputs({})
        got, got_state = self.outputs({"semantic_rerank_mode": "off"})
        self.assertEqual([(r["message"], r["ask_attribute"], self.recs(r)) for r in base],
                         [(r["message"], r["ask_attribute"], self.recs(r)) for r in got])
        for key in base_state:
            if key == "trace_log":
                continue
            self.assertEqual(base_state[key], got_state[key], key)


class StubbedSemanticTest(SemanticBase):
    """A2 with a deterministic stub scorer -- no model, no ONNX, no network.

    The stub REVERSES the prefix, which is the most aggressive permutation
    available and therefore the strongest test of the invariants.
    """

    def agent_a2(self, lam: float = 1.0, k: int = 10, **cfg) -> A.Agent:
        ag = self.agent(semantic_rerank_mode="on", semantic_rerank_k=k,
                        semantic_lambda=lam, **cfg)
        ag._semantic_score_order = lambda query, asins: list(reversed(asins))
        return ag

    def paired(self, lam: float = 1.0, messages=(OPENING, FOLLOW), top_k: int = 10):
        A.clear_catalog_cache()
        a0 = self.agent()
        a0_replies = self.turns(a0, messages, top_k)
        a0_state = {k: copy.deepcopy(v) for k, v in a0._sessions["s"].items()}
        A.clear_catalog_cache()
        a2 = self.agent_a2(lam=lam)
        a2_replies = self.turns(a2, messages, top_k)
        a2_state = {k: copy.deepcopy(v) for k, v in a2._sessions["s"].items()}
        return (a0_replies, a0_state), (a2_replies, a2_state)

    # ---- final emitted output: length + Counter, never set ----------------
    def test_emitted_recommendations_are_a_permutation(self) -> None:
        (a0r, _), (a2r, _) = self.paired()
        for i, (x, y) in enumerate(zip(a0r, a2r)):
            a, b = self.recs(x), self.recs(y)
            with self.subTest(turn=i + 1):
                self.assertEqual(len(a), len(b))
                self.assertEqual(collections.Counter(a), collections.Counter(b))
                self.assertEqual(len(set(b)), len(b), "duplicate ASIN emitted")
                self.assertTrue(set(b) <= set(a), "an ASIN appeared from nowhere")

    def test_every_emitted_asin_is_in_the_catalog(self) -> None:
        (_, _), (a2r, _) = self.paired()
        A.clear_catalog_cache()
        ag = self.agent()
        for reply in a2r:
            for asin in self.recs(reply):
                self.assertIn(asin, ag.cat.cats)

    def test_the_a0_tail_order_is_unchanged(self) -> None:
        # top_k 20 with k 10: positions 10..19 must be element-wise identical.
        (a0r, _), (a2r, _) = self.paired(top_k=20)
        for i, (x, y) in enumerate(zip(a0r, a2r)):
            a, b = self.recs(x), self.recs(y)
            with self.subTest(turn=i + 1):
                self.assertEqual(a[10:], b[10:])

    def test_top_k_variants_preserve_the_returned_multiset(self) -> None:
        for top_k in (1, 5, 10, 20):
            (a0r, _), (a2r, _) = self.paired(top_k=top_k)
            for i, (x, y) in enumerate(zip(a0r, a2r)):
                with self.subTest(top_k=top_k, turn=i + 1):
                    self.assertEqual(collections.Counter(self.recs(x)),
                                     collections.Counter(self.recs(y)))

    # ---- state: everything but `shown` ------------------------------------
    def test_every_state_key_except_shown_is_bit_exact(self) -> None:
        (_, a0s), (_, a2s) = self.paired()
        for key in a0s:
            if key in ("trace_log", "shown"):
                continue
            with self.subTest(key=key):
                self.assertEqual(a0s[key], a2s[key])

    def test_shown_matches_on_counter_and_length_not_order(self) -> None:
        (_, a0s), (_, a2s) = self.paired()
        self.assertEqual(len(a0s["shown"]), len(a2s["shown"]))
        self.assertEqual(collections.Counter(a0s["shown"]),
                         collections.Counter(a2s["shown"]))

    def test_rotate_consumes_shown_as_a_set(self) -> None:
        # This is what makes a permuted `shown` provably safe. retrieval.py
        # reads `seen = set(state.get("shown") or ())`, so permuting it must
        # not change _rotate's output.
        A.clear_catalog_cache()
        ag = self.agent()
        ranked = list(ag.cat.cats)[:12]
        cfg = {**ag.cfg, "rotate_on_request": True, "rotate_keep_top": 3}
        base = {"shown": ranked[4:8], "rotate_pending": True}
        first = ag._rotate(list(ranked), dict(base), cfg)
        flipped = {"shown": list(reversed(base["shown"])), "rotate_pending": True}
        second = ag._rotate(list(ranked), flipped, cfg)
        self.assertEqual(first, second)

    # ---- question decision is taken from a0_ranked ------------------------
    def test_ask_attribute_and_question_patch_are_identical(self) -> None:
        (a0r, a0s), (a2r, a2s) = self.paired()
        self.assertEqual([r["ask_attribute"] for r in a0r],
                         [r["ask_attribute"] for r in a2r])
        for key in C.PATCH_FIELDS:
            self.assertEqual(a0s.get(key), a2s.get(key), key)

    def test_the_top_product_sentence_may_differ(self) -> None:
        # Explicitly ALLOWED at lambda > 0: _compose reads shown[0], so a
        # reordered Top-10 names a different product. An intended
        # customer-visible consequence, not a defect.
        (a0r, _), (a2r, _) = self.paired()
        self.assertNotEqual(self.recs(a0r[0])[0], self.recs(a2r[0])[0],
                            "the stub did not actually reorder anything")

    # ---- two turns, including the rotation path ---------------------------
    def test_two_turns_agree_on_route_and_membership(self) -> None:
        (a0r, a0s), (a2r, a2s) = self.paired(messages=(OPENING, FOLLOW))
        self.assertEqual(a0s["route"], a2s["route"])
        self.assertEqual(len(a0r), len(a2r))
        for i, (x, y) in enumerate(zip(a0r, a2r)):
            with self.subTest(turn=i + 1):
                self.assertEqual(x["ask_attribute"], y["ask_attribute"])
                self.assertEqual(collections.Counter(self.recs(x)),
                                 collections.Counter(self.recs(y)))

    def test_a_rotation_turn_agrees_after_a_permuted_first_turn(self) -> None:
        # The shape a `shown`-order bug would take: turn 1 permutes what is
        # displayed, turn 2 asks for alternatives and rotates on `shown`.
        (a0r, a0s), (a2r, a2s) = self.paired(
            messages=(OPENING, MORE), lam=1.0)
        self.assertEqual(collections.Counter(self.recs(a0r[1])),
                         collections.Counter(self.recs(a2r[1])))
        self.assertEqual(collections.Counter(a0s["shown"]),
                         collections.Counter(a2s["shown"]))


class FallbackTest(SemanticBase):
    """Absent, failing-to-load and failing-to-infer are all bit-exact A0."""

    def baseline(self):
        A.clear_catalog_cache()
        ag = self.agent()
        replies = self.turns(ag)
        return [(r["message"], r["ask_attribute"], self.recs(r)) for r in replies]

    def compare(self, make_agent) -> None:
        """`make_agent` is a FACTORY, not an agent.

        Building the agent first would hand it a catalog connection that
        baseline()'s clear_catalog_cache() then closes -- the agent would raise
        "Cannot operate on a closed database" before reaching the code under
        test.
        """
        base = self.baseline()
        A.clear_catalog_cache()
        replies = self.turns(make_agent())
        got = [(r["message"], r["ask_attribute"], self.recs(r)) for r in replies]
        self.assertEqual(base, got)

    def test_lambda_zero_is_byte_exact_a0(self) -> None:
        def make():
            ag = self.agent(semantic_rerank_mode="on", semantic_lambda=0.0)
            ag._semantic_score_order = lambda q, a: list(reversed(a))
            return ag
        self.compare(make)

    def test_semantic_rerank_k_zero_is_byte_exact_a0(self) -> None:
        def make():
            ag = self.agent(semantic_rerank_mode="on", semantic_rerank_k=0,
                            semantic_lambda=1.0)
            ag._semantic_score_order = lambda q, a: list(reversed(a))
            return ag
        self.compare(make)

    def test_a_missing_model_is_byte_exact_a0(self) -> None:
        self.compare(lambda: self.agent(
            semantic_rerank_mode="on", semantic_lambda=1.0,
            semantic_model_dir="/nonexistent/model"))

    def test_a_load_failure_is_byte_exact_a0(self) -> None:
        def make():
            ag = self.agent(semantic_rerank_mode="on", semantic_lambda=1.0)
            ag._semantic_scorer = lambda d, m: (_ for _ in ()).throw(RuntimeError("boom"))
            return ag
        self.compare(make)

    def test_an_inference_failure_is_byte_exact_a0(self) -> None:
        def make():
            ag = self.agent(semantic_rerank_mode="on", semantic_lambda=1.0)
            ag._semantic_score_order = lambda q, a: (_ for _ in ()).throw(RuntimeError("boom"))
            return ag
        self.compare(make)

    def test_an_empty_query_is_byte_exact_a0(self) -> None:
        import starter.semantic as _S
        original = _S.build_query
        _S.build_query = lambda *a, **k: ""
        try:
            def make():
                ag = self.agent(semantic_rerank_mode="on", semantic_lambda=1.0)
                ag._semantic_score_order = lambda q, a: list(reversed(a))
                return ag
            self.compare(make)
        finally:
            _S.build_query = original

    def test_a_scorer_returning_a_bad_permutation_falls_back(self) -> None:
        # A scorer that drops or invents an ASIN must not reach the customer.
        for bad in (lambda q, a: a[:-1], lambda q, a: a + ["GHOST"],
                    lambda q, a: [a[0]] * len(a)):
            with self.subTest():
                def make(_bad=bad):
                    ag = self.agent(semantic_rerank_mode="on", semantic_lambda=1.0)
                    ag._semantic_score_order = _bad
                    return ag
                self.compare(make)


class EligibilityTest(SemanticBase):
    """The robustness gate is product logic and is asserted, not searched."""

    def eligible(self, **state) -> bool:
        base = {"route": "browsing", "slots": [], "last_override_turn": 0,
                "category": None}
        return SEM.eligible({**base, **state})

    def slot(self, **kw):
        return A.SlotValue(**{"attribute": "material", "value": "silk",
                              "polarity": 1, "hardness": "soft",
                              "confidence": 1.0, "source_turn": 1,
                              "provenance": (), "active": True,
                              "soft_ok": True, "catalog_support": 1,
                              "contradiction": False, **kw})

    def test_browsing_with_no_evidence_is_eligible(self) -> None:
        self.assertTrue(self.eligible())

    def test_mixed_is_eligible(self) -> None:
        self.assertTrue(self.eligible(route="mixed"))

    def test_buying_is_not_eligible(self) -> None:
        self.assertFalse(self.eligible(route="buying"))

    def test_override_route_is_not_eligible(self) -> None:
        self.assertFalse(self.eligible(route="override"))

    def test_a_past_override_turn_is_not_eligible(self) -> None:
        self.assertFalse(self.eligible(last_override_turn=2))

    def test_an_active_negative_slot_blocks(self) -> None:
        self.assertFalse(self.eligible(slots=[self.slot(polarity=-1)]))

    def test_a_suppressed_value_blocks(self) -> None:
        self.assertFalse(self.eligible(slots=[self.slot(soft_ok=False)]))

    def test_two_active_hard_slots_block(self) -> None:
        hard = [self.slot(hardness="hard", value="silk"),
                self.slot(hardness="hard", value="blue", attribute="color")]
        self.assertFalse(self.eligible(slots=hard))

    def test_one_active_hard_slot_is_allowed(self) -> None:
        self.assertTrue(self.eligible(slots=[self.slot(hardness="hard")]))

    def test_a_contested_value_blocks(self) -> None:
        # The gate the first draft omitted. _uncredible(state) is DialogueMixin's
        # contested / pre-override set, and a value the session has contested is
        # exactly where A0's structured handling is doing work this relevance
        # model has not been validated to replicate.
        self.assertFalse(SEM.eligible({"route": "browsing", "slots": [],
                                       "last_override_turn": 0},
                                      uncredible=frozenset({"silk"})))

    def test_a_contested_single_valued_slot_blocks_end_to_end(self) -> None:
        # Two values of a SINGLE_VALUED attribute: the older is superseded and
        # lands in _uncredible, so the whole turn is ineligible.
        A.clear_catalog_cache()
        ag = self.agent()
        state = {"route": "browsing", "last_override_turn": 0, "slots": [
            self.slot(attribute="color", value="blue", source_turn=1),
            self.slot(attribute="color", value="red", source_turn=2)]}
        blocked = ag._uncredible(state)
        self.assertTrue(blocked, "the superseded value should be uncredible")
        self.assertFalse(SEM.eligible(state, blocked))

    def test_a_pre_override_slot_blocks_end_to_end(self) -> None:
        A.clear_catalog_cache()
        ag = self.agent()
        state = {"route": "browsing", "last_override_turn": 2,
                 "slots": [self.slot(value="silk", source_turn=1)]}
        blocked = ag._uncredible(state)
        self.assertIn("silk", blocked)
        self.assertFalse(SEM.eligible(state, blocked))

    def test_contested_values_never_reach_the_semantic_query(self) -> None:
        # Even where the gate is bypassed, the query must not re-introduce a
        # value the session contested.
        state = {"category": None, "terms": ["silk", "cotton"],
                 "slots": [self.slot(value="silk")], "last_override_turn": 0}
        got = SEM.build_query(state, uncredible=frozenset({"silk"}))
        self.assertNotIn("silk", got)
        self.assertIn("cotton", got)


class TelemetryTest(SemanticBase):
    """Ten distinct reasons, recorded. Never collapsed."""

    def trace(self, **cfg) -> dict:
        A.clear_catalog_cache()
        ag = self.agent(semantic_rerank_mode="on", trace=True, **cfg)
        if cfg.get("semantic_lambda"):
            ag._semantic_score_order = lambda q, a: list(reversed(a))
        self.turns(ag)
        return ag._sessions["s"]["trace_log"][-1]

    def test_the_reason_reaches_the_trace(self) -> None:
        got = self.trace(semantic_lambda=0.0)
        self.assertIn("semantic_reason", got)
        self.assertIn(got["semantic_reason"], SEM.REASONS)

    def test_the_frozen_fields_are_all_present(self) -> None:
        got = self.trace(semantic_lambda=0.0)
        for key in ("semantic_reason", "semantic_effective_k",
                    "semantic_eligible", "semantic_invoked",
                    "semantic_invalidating", "semantic_lambda_zero_exact"):
            self.assertIn(key, got)

    def test_lambda_zero_records_an_exact_degeneracy_assertion(self) -> None:
        got = self.trace(semantic_lambda=0.0)
        self.assertEqual(got["semantic_reason"], SEM.REASON_LAMBDA_ZERO)
        self.assertIs(got["semantic_lambda_zero_exact"], True)

    def test_a_missing_model_is_recorded_as_model_absent(self) -> None:
        A.clear_catalog_cache()
        ag = self.agent(semantic_rerank_mode="on", semantic_lambda=1.0,
                        semantic_model_dir="/nonexistent", trace=True)
        self.turns(ag)
        reasons = {t.get("semantic_reason")
                   for t in ag._sessions["s"]["trace_log"]}
        self.assertIn(SEM.REASON_MODEL_ABSENT, reasons)

    def test_the_three_failure_modes_are_distinct_values(self) -> None:
        # Collapsing them would let a shard where the model never ran be
        # reported as a quality result.
        self.assertEqual(len({SEM.REASON_MODEL_ABSENT, SEM.REASON_LOAD_FAILURE,
                              SEM.REASON_INFERENCE_FAILURE}), 3)

    def test_the_invalidating_set_excludes_model_absent(self) -> None:
        # A model that was never installed is a configuration fact; a model
        # that failed to load or infer invalidates the shard.
        self.assertNotIn(SEM.REASON_MODEL_ABSENT, SEM.INVALIDATING_REASONS)
        for reason in (SEM.REASON_LOAD_FAILURE, SEM.REASON_INFERENCE_FAILURE,
                       SEM.REASON_BAD_PERMUTATION):
            self.assertIn(reason, SEM.INVALIDATING_REASONS)

    def test_off_records_no_semantic_telemetry(self) -> None:
        A.clear_catalog_cache()
        ag = self.agent(trace=True)
        self.turns(ag)
        self.assertNotIn("semantic_reason", ag._sessions["s"]["trace_log"][-1])


class ModuleBoundaryTest(unittest.TestCase):
    """Candidate ordering is retrieval's domain (Phase 5B)."""

    def test_dialogue_owns_no_semantic_machinery(self) -> None:
        source = Path("starter/dialogue.py").read_text()
        for banned in ("onnxruntime", "tokenizers", "InferenceSession",
                       "rrf_fuse", "semantic_rerank", "product_text"):
            self.assertNotIn(banned, source, f"dialogue.py still owns {banned}")

    def test_retrieval_calls_the_semantic_module(self) -> None:
        source = Path("starter/retrieval.py").read_text()
        self.assertIn("_semantic.reorder", source)

    def test_the_semantic_module_imports_only_context(self) -> None:
        import ast
        tree = ast.parse(Path("starter/semantic.py").read_text())
        starter_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("starter"):
                starter_imports.add(node.module.split(".")[-1])
            elif isinstance(node, ast.ImportFrom) and node.module == "starter":
                starter_imports.update(a.name for a in node.names)
        self.assertLessEqual(starter_imports, {"starter", "context"},
                             f"semantic.py imports {starter_imports}")

    def test_the_mro_is_unchanged(self) -> None:
        self.assertEqual([c.__name__ for c in A.Agent.__mro__],
                         ["Agent", "RetrievalMixin", "DialogueMixin", "object"])


class QueryConstructionTest(SemanticBase):
    """One canonical query. Order fixed, exclusions enforced."""

    def slot(self, **kw):
        return A.SlotValue(**{"attribute": "material", "value": "silk",
                              "polarity": 1, "hardness": "soft",
                              "confidence": 1.0, "source_turn": 1,
                              "provenance": (), "active": True,
                              "soft_ok": True, "catalog_support": 1,
                              "contradiction": False, **kw})

    def query(self, **state) -> str:
        A.clear_catalog_cache()
        ag = self.agent()
        base = {"category": None, "slots": [], "terms": [],
                "last_override_turn": 0}
        return SEM.build_query({**base, **state},
                               ag._uncredible({**base, **state}))

    def test_category_comes_first(self) -> None:
        got = self.query(category="dresses", terms=["cotton"])
        self.assertTrue(got.startswith("dresses"))

    def test_use_case_precedes_other_constraints(self) -> None:
        slots = [self.slot(attribute="material", value="silk", source_turn=1),
                 self.slot(attribute="use_case", value="running", source_turn=2)]
        got = self.query(slots=slots)
        self.assertLess(got.index("running"), got.index("silk"))

    def test_negative_values_are_excluded(self) -> None:
        got = self.query(slots=[self.slot(polarity=-1, value="leather")])
        self.assertNotIn("leather", got)

    def test_suppressed_values_are_excluded(self) -> None:
        got = self.query(slots=[self.slot(soft_ok=False, value="leather")])
        self.assertNotIn("leather", got)

    def test_profile_tags_are_excluded(self) -> None:
        got = self.query(profile={"preference_tags": ["fit", "comfort"]})
        self.assertNotIn("comfort", got)

    def test_the_result_is_deduplicated_and_bounded(self) -> None:
        got = self.query(category="silk", slots=[self.slot(value="silk")],
                         terms=["silk"] + [f"t{i}" for i in range(200)])
        self.assertEqual(got.count("silk"), 1)
        self.assertLessEqual(len(got), 200)

    def test_the_query_is_deterministic(self) -> None:
        slots = [self.slot(value="silk", source_turn=2),
                 self.slot(value="blue", attribute="color", source_turn=1)]
        self.assertEqual(self.query(slots=slots), self.query(slots=slots))

    def test_an_empty_state_gives_an_empty_query(self) -> None:
        self.assertEqual(self.query(), "")


class ProductSerializationTest(SemanticBase):
    def sem_agent(self) -> A.Agent:
        # sem_title / sem_desc are built only when the cascade is armed, so a
        # lean agent would serialize products without their title.
        A.clear_catalog_cache()
        return self.agent(semantic_rerank_mode="on")

    def test_the_field_order_is_canonical(self) -> None:
        # title -> full category path -> features/details -> description, each
        # EXACTLY once. Asserted on positions, not on membership: a duplicated
        # field still "contains" everything.
        ag = self.sem_agent()
        asin = next(a for a in ag.cat.cats
                    if ag.cat.sem_title.get(a) and ag.cat.cats.get(a)
                    and ag.cat.feat.get(a) and ag.cat.sem_desc.get(a))
        got = SEM.product_text(ag.cat, asin)
        title = ag.cat.sem_title[asin]
        cats = ag.cat.cats[asin]
        feat = ag.cat.feat[asin]
        desc = ag.cat.sem_desc[asin]
        self.assertTrue(got.startswith(title), "title must come first")
        self.assertLess(got.index(cats), got.index(feat))
        self.assertLess(got.index(feat), got.index(desc))

    def test_no_field_appears_twice(self) -> None:
        # The defect this replaces: building from cat.text -- which is already
        # " ".join(title, categories, features, details, store, description) --
        # while ALSO prepending title/categories/features duplicated three
        # fields and pushed the description out of a 256-token window.
        ag = self.sem_agent()
        asin = next(a for a in ag.cat.cats
                    if ag.cat.sem_title.get(a) and ag.cat.cats.get(a))
        got = SEM.product_text(ag.cat, asin)
        self.assertEqual(got.count(ag.cat.sem_title[asin]), 1)
        self.assertEqual(got.count(ag.cat.cats[asin]), 1)

    def test_store_is_never_included(self) -> None:
        # `store` IS in cat.text and is NOT in the pre-registered field list,
        # so a serialization built from cat.text would leak the brand name into
        # the relevance model.
        from tests.test_indexes import PRODUCTS
        ag = self.sem_agent()
        checked = 0
        for product in PRODUCTS:
            asin = str(product.get("parent_asin") or "")
            store = str(product.get("store") or "").strip().casefold()
            if not asin or not store or asin not in ag.cat.cats:
                continue
            blob = ag.cat.text.get(asin, "")
            if store not in blob:
                continue
            got = SEM.product_text(ag.cat, asin)
            # Only meaningful where the store string is not also a legitimate
            # part of a kept field.
            kept = (ag.cat.sem_title.get(asin, ""), ag.cat.cats.get(asin, ""),
                    ag.cat.feat.get(asin, ""), ag.cat.sem_desc.get(asin, ""))
            if any(store in field for field in kept):
                continue
            checked += 1
            self.assertNotIn(store, got, f"{asin}: store leaked into semantic text")
        self.assertGreater(checked, 0, "no product exercised the store exclusion")

    def test_the_semantic_store_is_opt_in(self) -> None:
        A.clear_catalog_cache()
        lean = self.agent()
        self.assertEqual(len(lean.cat.sem_title), 0)
        self.assertEqual(len(lean.cat.sem_desc), 0)

    def test_a_missing_asin_gives_empty_text(self) -> None:
        self.assertEqual(SEM.product_text(self.sem_agent().cat, "NOSUCHASIN"), "")

    def test_serialization_is_deterministic(self) -> None:
        ag = self.sem_agent()
        asin = next(iter(ag.cat.cats))
        self.assertEqual(SEM.product_text(ag.cat, asin),
                         SEM.product_text(ag.cat, asin))


if __name__ == "__main__":
    unittest.main()
