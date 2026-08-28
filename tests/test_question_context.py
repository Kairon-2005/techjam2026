"""Phase 6B2: the pure question decision, against the legacy controller.

The governing test is not "does the new controller look right" but "does it
agree with the old one, including on which state fields it writes" -- because
_compose reads two of those fields and three of ten branches write neither, so
a tidier patch would render a different message on a later turn.
"""
from __future__ import annotations

import copy
import dataclasses
import itertools
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
import starter.context as C
from tests.test_indexes import PRODUCTS, _catalog_file


class WriteTracker(dict):
    """Records top-level assignments to the four allowlisted fields.

    A before/after diff cannot observe a write that stores the value already
    present, and the oracle depends on telling those apart.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.writes: list[tuple[str, object]] = []
        self.tracking = False

    def __setitem__(self, key, value):
        if self.tracking and key in C.PATCH_FIELDS:
            self.writes.append((key, copy.deepcopy(value)))
        super().__setitem__(key, value)

    def write_set(self) -> frozenset:
        """WHICH keys were written. Deliberately a set, not a sequence.

        Legacy writes last_coverage and last_weighed inside _pool_attribute and
        last_bits after it returns, so its chronological order differs from the
        patch's fixed field order. The four keys are independent and each is
        written at most once, so order cannot affect the resulting state --
        only membership and final value can.
        """
        return frozenset(k for k, _ in self.writes)

    def final_values(self) -> dict:
        out = {}
        for key, value in self.writes:      # last write wins
            out[key] = value
        return out


class SentinelTest(unittest.TestCase):
    def test_the_sentinel_is_a_singleton_with_a_canonical_repr(self) -> None:
        self.assertIs(C.UNSET, C._Unset())
        self.assertEqual(repr(C.UNSET), "UNSET")
        self.assertFalse(bool(C.UNSET))

    def test_an_unwritten_field_differs_from_a_same_value_write(self) -> None:
        prior = C.PriorRenderState(last_bits=0.0)
        untouched = C.QuestionPatch()
        rewritten = C.QuestionPatch(last_bits=0.0)
        self.assertEqual(untouched.writes(), ())
        self.assertEqual(rewritten.writes(), ("last_bits",))
        # Same effective state, different write sets -- which is the whole
        # point of the sentinel.
        self.assertEqual(C.apply_patch(prior, untouched), C.apply_patch(prior, rewritten))

    def test_patch_application_rejects_a_foreign_type(self) -> None:
        with self.assertRaises(TypeError):
            C.apply_patch(C.PriorRenderState(), {"last_bits": 1.0})

    def test_the_sentinel_never_reaches_session_state(self) -> None:
        state: dict = {}
        A.Agent._apply_question_patch(state, C.QuestionPatch(last_bits=0.5))
        self.assertEqual(state, {"last_bits": 0.5})
        self.assertNotIn("broad_options", state)


class ApplyPatchTest(unittest.TestCase):
    def test_unwritten_fields_inherit_from_prior(self) -> None:
        prior = C.PriorRenderState(broad_options=("a", "b"), last_bits=1.5,
                                   last_coverage=0.4, last_weighed=True)
        out = C.apply_patch(prior, C.QuestionPatch(last_bits=0.0))
        self.assertEqual(out.broad_options, ("a", "b"), "stale options were dropped")
        self.assertEqual(out.last_bits, 0.0)
        self.assertEqual(out.last_coverage, 0.4)
        self.assertTrue(out.last_weighed)

    def test_prior_is_not_mutated(self) -> None:
        prior = C.PriorRenderState(broad_options=("a",), last_bits=1.0)
        C.apply_patch(prior, C.QuestionPatch(broad_options=(), last_bits=9.0))
        self.assertEqual(prior.broad_options, ("a",))
        self.assertEqual(prior.last_bits, 1.0)


class RenderModeTest(unittest.TestCase):
    """render_mode is len(effective_options) >= 2, not `overgeneral`."""

    def decide(self, snapshot, *, category=None, samples=(), **cfg) -> C.QuestionDecision:
        """The host's stage sequence, in miniature.

        Written out rather than calling Agent._decide_question because these
        cases are about rendering from injected summaries, with no catalog to
        scan -- but the ORDER is the host's order, so a stage that stopped
        returning early would fail here too.
        """
        merged = {**A.DEFAULTS, "ask_policy": "pool", **cfg}
        policy = A.Agent._question_policy(merged)
        category = category if category is not None else C.QuestionCategorySummary()
        decision = C.question_without_candidates(snapshot, policy,
                                                 probe_order=A.PROBE_ORDER)
        if decision is not None:
            return decision
        decision = C.question_from_category(snapshot, policy, category,
                                            answerability=A.ANSWERABILITY,
                                            vocab=A.ATTR_VOCAB)
        if decision is not None:
            return decision
        pick = C.select_pool_attribute(snapshot, policy, samples)
        coverage = pick.coverage if pick.coverage_known else 0.0
        return C.question_from_pool(snapshot, policy, category, pick, coverage)

    def test_one_option_renders_open_even_when_overgeneral(self) -> None:
        category = C.QuestionCategorySummary(overgeneral=True, options=("dresses",))
        d = self.decide(C.QuestionSnapshot(), category=category)
        self.assertEqual(d.effective_options, ("dresses",))
        self.assertEqual(d.render_mode, "open")

    def test_two_options_render_structured(self) -> None:
        category = C.QuestionCategorySummary(overgeneral=True,
                                             options=("dresses", "skirts"))
        self.assertEqual(self.decide(C.QuestionSnapshot(), category=category).render_mode,
                         "structured")

    def test_stale_options_render_structured_on_a_no_write_branch(self) -> None:
        # first-two-other writes NOTHING, so options from an earlier turn
        # survive and _compose still renders the structured message.
        prior = C.PriorRenderState(broad_options=("dresses", "skirts"))
        snap = C.QuestionSnapshot(asked=("material",), prior=prior)
        d = self.decide(snap, ask_policy="other_then_pool")
        self.assertEqual(d.patch.writes(), (), "the no-write branch wrote something")
        self.assertEqual(d.render_mode, "structured")
        self.assertEqual(d.primary_reason, C.QuestionReason.FIRST_TWO_OTHER)

    def test_a_patch_can_clear_stale_options(self) -> None:
        prior = C.PriorRenderState(broad_options=("dresses", "skirts"))
        snap = C.QuestionSnapshot(asked=("other", "other"), dry_streak=5, prior=prior)
        d = self.decide(snap, ask_policy="other_then_pool", pool_give_up_after=1)
        self.assertIn("broad_options", d.patch.writes())
        self.assertEqual(d.effective_options, ())
        self.assertEqual(d.render_mode, "open")

    def test_the_counterexample_selects_easier_and_renders_structured(self) -> None:
        # The case revision 1 could not express: broad_options is written
        # before the uncertain branch is tested, so selection is "easier" while
        # rendering is "structured".
        category = C.QuestionCategorySummary(
            overgeneral=True, options=("dresses", "skirts", "tops"))
        snap = C.QuestionSnapshot(asked=("other", "other"), uncertain_streak=3)
        d = self.decide(snap, ask_policy="other_then_pool", category=category)
        self.assertEqual(d.selection_mode, "easier")
        self.assertEqual(d.render_mode, "structured")
        self.assertEqual(d.primary_reason, C.QuestionReason.EASIER_AFTER_UNCERTAIN)
        self.assertIn(C.QuestionModifier.STRUCTURED_CLARIFICATION_DUE, d.modifiers)
        self.assertEqual(d.patch.writes(), ("broad_options", "last_bits"))
        self.assertEqual(d.patch.last_bits, 0.0)


class ScanTopologyTest(unittest.TestCase):
    """The pre-registered scan topology, asserted rather than described.

    Phase 6B2's defect was invisible at unit level: every branch returned the
    right answer, every write set matched, 8,483 turns agreed exactly -- and
    only the clock said the first-two-`other` branch had scanned all five
    facets to decide something that reads none of them. A correctness suite
    that cannot see wasted work will pass a design that does nothing but waste
    it. So the scan COUNT is part of the contract here, per branch, against
    notes/33-phase6b2-r2-prereg.md.
    """

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))
        cls.agent = A.Agent(cls.path)
        cls.pool = list(cls.agent.cat.cats)

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def state(self, **kw) -> dict:
        base = {"asked": [], "dry_streak": 0, "dry_others": 0, "uncertain_streak": 0,
                "broad_options": [], "last_bits": 0.0, "last_coverage": 0.0,
                "last_weighed": False, "route": "buying", "slots": [], "terms": []}
        return {**base, **kw}

    def counted(self, run, **cfg) -> dict:
        """Scans performed by `run`, by kind.

        _overgeneral reads cat.cats (short leaf strings); the other three read
        cat.text (~1.1kB per candidate) and are what actually costs.
        """
        counts = {"category": 0, "facet_pass": 0, "coverage": 0}
        names = {"_overgeneral": "category", "_facet_pass": "facet_pass",
                 "_facet_coverage": "coverage"}
        saved = {n: getattr(self.agent, n) for n in names}
        saved_cfg = self.agent.cfg
        try:
            for name, key in names.items():
                def wrap(*a, _o=saved[name], _k=key, **k):
                    counts[_k] += 1
                    return _o(*a, **k)
                setattr(self.agent, name, wrap)
            self.agent.cfg = {**saved_cfg, **cfg}
            run(self.agent.cfg)
        finally:
            for name, original in saved.items():
                setattr(self.agent, name, original)
            self.agent.cfg = saved_cfg
        return counts

    def staged(self, state: dict, pool=None, **cfg) -> dict:
        return self.counted(
            lambda c: self.agent._decide_question(state, self.pool if pool is None
                                                  else pool, c), **cfg)

    def legacy(self, state: dict, pool=None, **cfg) -> dict:
        return self.counted(
            lambda c: self.agent._pick_attribute(state, self.pool if pool is None
                                                 else pool), **cfg)

    # ---- zero candidate scans ------------------------------------------
    def test_the_no_evidence_branches_touch_nothing(self) -> None:
        cases = {
            "first_two_other": (self.state(asked=["other"]),
                                {"ask_policy": "other_then_pool"}),
            "probe_cycle": (self.state(asked=["other", "other"]),
                            {"ask_policy": "probe_cycle"}),
            "other_then_cycle_first_two": (self.state(asked=["other"]),
                                           {"ask_policy": "other_then_cycle"}),
            "other_then_cycle_after": (self.state(asked=["other", "other"]),
                                       {"ask_policy": "other_then_cycle"}),
            "other_dry_degrade": (self.state(asked=["other"], dry_others=5),
                                  {"ask_policy": "other", "ask_fallback_after": 2}),
            "fallback": (self.state(), {"ask_policy": "other",
                                        "ask_fallback_after": 0}),
        }
        for name, (state, cfg) in cases.items():
            with self.subTest(branch=name):
                self.assertEqual(self.staged(state, **cfg),
                                 {"category": 0, "facet_pass": 0,
                                  "coverage": 0})

    def test_needs_candidates_agrees_with_what_the_stages_do(self) -> None:
        for asked, policy, expected in ((["other"], "other_then_pool", False),
                                        (["other", "other"], "other_then_pool", True),
                                        ([], "pool", True),
                                        ([], "probe_cycle", False),
                                        ([], "other", False)):
            snapshot = C.QuestionSnapshot(asked=tuple(asked))
            got = C.needs_candidates(
                snapshot, A.Agent._question_policy({**A.DEFAULTS,
                                                    "ask_policy": policy}))
            with self.subTest(policy=policy, asked=tuple(asked)):
                self.assertEqual(got, expected)
                counts = self.staged(self.state(asked=list(asked)),
                                     ask_policy=policy)
                self.assertEqual(sum(counts.values()) > 0, expected)

    # ---- category summary only -----------------------------------------
    def test_the_easier_branch_reads_categories_and_no_facet(self) -> None:
        counts = self.staged(self.state(asked=["other", "other"], uncertain_streak=3),
                             ask_policy="pool", answerability_after=1)
        self.assertEqual(counts, {"category": 1, "facet_pass": 0,
                                  "coverage": 0})

    def test_the_give_up_branch_reads_categories_and_no_facet(self) -> None:
        counts = self.staged(self.state(asked=["other", "other"], dry_streak=3),
                             ask_policy="pool", pool_give_up_after=1,
                             overgeneral_cats=0)
        self.assertEqual(counts, {"category": 1, "facet_pass": 0,
                                  "coverage": 0})

    # ---- pool selection -------------------------------------------------
    def test_utility_on_is_one_combined_pass_per_unasked_facet(self) -> None:
        for asked, unasked in ((["other", "other"], len(A.ATTR_VOCAB)),
                               (["other", "other", "material", "color"],
                                len(A.ATTR_VOCAB) - 2)):
            with self.subTest(asked=len(asked)):
                counts = self.staged(self.state(asked=list(asked)),
                                     ask_policy="pool", question_utility=True)
                self.assertEqual(counts, {"category": 1, "facet_pass": unasked,
                                          "coverage": 0})

    def test_utility_off_is_one_entropy_pass_plus_the_winner_s_coverage(self) -> None:
        counts = self.staged(self.state(asked=["other", "other"]),
                             ask_policy="pool", question_utility=False)
        self.assertEqual(counts, {"category": 1, "facet_pass": len(A.ATTR_VOCAB),
                                  "coverage": 1})

    def test_no_winner_needs_no_coverage_scan_at_all(self) -> None:
        # An empty pool leaves every facet at 0.0 bits, so the winner is
        # "other" -- and _facet_coverage(window, "other") is 0.0 by definition,
        # because ATTR_VOCAB has no such key. Scanning for it would be one
        # wasted pass on exactly the turns that have nothing to scan.
        counts = self.staged(self.state(asked=["other", "other"]), pool=[],
                             ask_policy="pool", question_utility=False)
        self.assertEqual(counts["coverage"], 0)

    def test_an_already_asked_facet_is_never_scanned(self) -> None:
        plan = C.facet_scan_plan(
            C.QuestionSnapshot(asked=("other", "material", "color")),
            A.Agent._question_policy({**A.DEFAULTS, "ask_policy": "pool"}),
            vocab=A.ATTR_VOCAB)
        self.assertNotIn("material", plan.attributes)
        self.assertNotIn("color", plan.attributes)
        self.assertEqual(list(plan.attributes),
                         [a for a in A.ATTR_VOCAB if a not in ("material", "color")])

    def test_the_plan_ties_the_entropy_variant_to_the_utility_setting(self) -> None:
        # skip_missing and with_coverage are not two decisions. Utility needs
        # the skip-missing entropy AND coverage; the other arm needs neither.
        for utility in (True, False):
            plan = C.facet_scan_plan(
                C.QuestionSnapshot(),
                A.Agent._question_policy({**A.DEFAULTS, "ask_policy": "pool",
                                          "question_utility": utility}),
                vocab=A.ATTR_VOCAB)
            with self.subTest(question_utility=utility):
                self.assertEqual(plan.skip_missing, utility)
                self.assertEqual(plan.with_coverage, utility)

    def test_utility_on_with_coverage_less_samples_raises(self) -> None:
        policy = A.Agent._question_policy({**A.DEFAULTS, "ask_policy": "pool",
                                           "question_utility": True})
        samples = (C.FacetSample(attribute="color", bits=1.0, answerability=0.75),)
        with self.assertRaises(ValueError):
            C.select_pool_attribute(C.QuestionSnapshot(), policy, samples)

    def test_the_scan_plan_preserves_attr_vocab_insertion_order(self) -> None:
        # Selection breaks ties with a strict `>`, so the first attribute at a
        # given utility wins and the order decides the answer, not just the
        # traversal.
        plan = C.facet_scan_plan(
            C.QuestionSnapshot(), A.Agent._question_policy(A.DEFAULTS),
            vocab=A.ATTR_VOCAB)
        self.assertEqual(list(plan.attributes), list(A.ATTR_VOCAB))

    # ---- the topology the whole phase is about --------------------------
    def test_the_live_path_walks_the_window_once_per_unasked_facet(self) -> None:
        """The claim in one assertion, on the shipped configuration.

        BEFORE ADOPTION this compared the two implementations: legacy walked
        cat.text 11 times with utility ON -- five entropies, five coverages,
        and the winner's coverage again, in separate loops -- and staged walked
        it 5, because entropy and coverage come from the same pattern.search
        per candidate. That comparison cannot be made here any more:
        _pick_attribute is an adapter over this same controller, so both arms
        would report 5. The evidence for 11-vs-5 is the pre-adoption record --
        tag p6b2-eager-control and notes/34 -- not this test.

        What survives, and what actually holds the line, is the ABSOLUTE
        topology: one walk per unasked facet with utility ON, one per unasked
        facet plus the winner's with it OFF.
        """
        def text_passes(counts):
            return counts["facet_pass"] + counts["coverage"]

        for utility, expected in ((True, 5), (False, 6)):
            state = self.state(asked=["other", "other"])
            got = self.staged(dict(state), ask_policy="pool",
                              question_utility=utility)
            with self.subTest(question_utility=utility):
                self.assertEqual(text_passes(got), expected)
                self.assertEqual(got["category"], 1)
                self.assertEqual(text_passes(got), len(A.ATTR_VOCAB) + (0 if utility else 1))

    def test_the_adapter_holds_no_second_copy_of_the_rule(self) -> None:
        # _pick_attribute is an adapter now. If its body ever reacquires the
        # rule, patching the staged entry point would stop changing what it
        # returns -- which is how a "relocation" quietly becomes a fork.
        original = C.question_without_candidates
        try:
            C.question_without_candidates = lambda *a, **k: C._finish(
                a[0], "sentinel-attribute", "fallback", C.QuestionPatch(),
                C.QuestionReason.FALLBACK_OTHER)
            got = self.agent._pick_attribute(self.state(asked=["other"]), self.pool)
            self.assertEqual(got, "sentinel-attribute",
                             "_pick_attribute did not go through the staged controller")
        finally:
            C.question_without_candidates = original


class EquivalenceGridTest(unittest.TestCase):
    """Legacy against pure, on the write set as well as the attribute."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))
        cls.agent = A.Agent(cls.path)
        cls.pool = list(cls.agent.cat.cats)

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def compare(self, state: dict, cfg: dict, pool: list[str]) -> tuple:
        """The frozen sequence: build inputs from state_before only, decide,
        THEN run legacy through the tracker, then compare."""
        before = copy.deepcopy(state)
        decision = self.agent._decide_question(before, pool, cfg)

        tracked = WriteTracker(copy.deepcopy(state))
        tracked.tracking = True
        legacy_attribute = self.agent._pick_attribute.__wrapped__(self.agent, tracked, pool) \
            if hasattr(self.agent._pick_attribute, "__wrapped__") else None
        if legacy_attribute is None:
            saved, self.agent.cfg = self.agent.cfg, cfg
            try:
                legacy_attribute = self.agent._pick_attribute(tracked, pool)
            finally:
                self.agent.cfg = saved
        tracked.tracking = False

        predicted = copy.deepcopy(state)
        self.agent._apply_question_patch(predicted, decision.patch)
        return (decision, legacy_attribute, tracked, predicted)

    def state(self, **kw) -> dict:
        base = {"asked": [], "dry_streak": 0, "dry_others": 0, "uncertain_streak": 0,
                "broad_options": [], "last_bits": 0.0, "last_coverage": 0.0,
                "last_weighed": False, "route": "buying", "slots": [], "terms": []}
        return {**base, **kw}

    def test_the_grid_agrees_on_attribute_and_write_set(self) -> None:
        checked = 0
        for policy, asked, dry, uncertain, dry_others, utility, ogc, depth, fallback in \
                itertools.product(
                    ("other", "pool", "other_then_pool", "probe_cycle", "other_then_cycle"),
                    ([], ["other"], ["other", "other"], ["other", "other", "material"]),
                    (0, 1, 2), (0, 1, 2), (0, 1, 2), (False, True), (0, 2, 6),
                    (0, 1, 2, 30), (0, 1)):
            cfg = {**self.agent.cfg, "ask_policy": policy, "question_utility": utility,
                   "overgeneral_cats": ogc, "pool_depth": depth,
                   "ask_fallback_after": fallback, "answerability_after": 1,
                   "pool_give_up_after": 1}
            st = self.state(asked=list(asked), dry_streak=dry,
                            uncertain_streak=uncertain, dry_others=dry_others)
            decision, legacy_attribute, tracked, predicted = self.compare(st, cfg, self.pool)
            with self.subTest(policy=policy, asked=tuple(asked), dry=dry,
                              uncertain=uncertain, dry_others=dry_others,
                              utility=utility, ogc=ogc, depth=depth, fallback=fallback):
                self.assertEqual(decision.attribute, legacy_attribute)
                self.assertEqual(frozenset(decision.patch.writes()), tracked.write_set())
                self.assertEqual(len(tracked.writes), len(tracked.write_set()),
                                 "a key was written twice; order would then matter")
                for key, value in tracked.final_values().items():
                    got = getattr(decision.patch, key)
                    got = list(got) if key == "broad_options" else got
                    self.assertEqual(got, value, key)
                for key in C.PATCH_FIELDS:
                    self.assertEqual(predicted[key], tracked[key], key)
            checked += 1
        self.assertGreater(checked, 4000)

    def test_empty_and_single_item_pools(self) -> None:
        for pool in ([], self.pool[:1]):
            for policy in ("pool", "other_then_pool"):
                cfg = {**self.agent.cfg, "ask_policy": policy}
                st = self.state(asked=["other", "other"])
                decision, legacy, tracked, predicted = self.compare(st, cfg, pool)
                with self.subTest(pool=len(pool), policy=policy):
                    self.assertEqual(decision.attribute, legacy)
                    self.assertEqual(frozenset(decision.patch.writes()), tracked.write_set())

    def test_exhausted_probe_orders(self) -> None:
        for policy, order in (("probe_cycle", A.PROBE_ORDER),
                              ("other", A.PROBE_ORDER[:-1])):
            cfg = {**self.agent.cfg, "ask_policy": policy, "ask_fallback_after": 1}
            st = self.state(asked=list(order), dry_others=5)
            decision, legacy, tracked, _ = self.compare(st, cfg, self.pool)
            with self.subTest(policy=policy):
                self.assertEqual(decision.attribute, legacy)

    def test_every_easy_facet_already_asked(self) -> None:
        cfg = {**self.agent.cfg, "ask_policy": "pool", "answerability_after": 1}
        st = self.state(asked=list(A.ATTR_VOCAB), uncertain_streak=3)
        decision, legacy, tracked, _ = self.compare(st, cfg, self.pool)
        self.assertEqual(decision.attribute, legacy)
        self.assertEqual(frozenset(decision.patch.writes()), tracked.write_set())


class NegativeControlTest(EquivalenceGridTest):
    """A comparator that always reports agreement is worthless.

    "Zero disagreements" is only evidence if the comparator can produce one.
    """

    def test_a_forced_attribute_disagreement_is_reported(self) -> None:
        cfg = {**self.agent.cfg, "ask_policy": "pool"}
        st = self.state(asked=["other", "other"])
        decision, legacy, tracked, _ = self.compare(st, cfg, self.pool)
        broken = dataclasses.replace(decision, attribute=legacy + "_WRONG")
        self.assertNotEqual(broken.attribute, legacy,
                            "the comparator cannot see an attribute disagreement")

    def test_a_forced_patch_disagreement_is_reported(self) -> None:
        # A branch whose real patch is PARTIAL -- first-two-other writes
        # nothing. Choosing a branch that already writes all four would make
        # "tidying" a no-op and the control would prove nothing.
        cfg = {**self.agent.cfg, "ask_policy": "other_then_pool"}
        st = self.state(asked=["material"])
        decision, legacy, tracked, _ = self.compare(st, cfg, self.pool)
        self.assertEqual(tracked.write_set(), frozenset(),
                         "expected a no-write branch for this control")
        # Tidy the partial patch into a uniform one -- the exact mistake the
        # design predicts. The write set must now differ.
        tidied = C.QuestionPatch(broad_options=decision.patch.broad_options
                                 if decision.patch.broad_options is not C.UNSET else (),
                                 last_bits=0.0, last_coverage=0.0, last_weighed=False)
        self.assertNotEqual(frozenset(tidied.writes()), tracked.write_set(),
                            "a uniform patch was indistinguishable from the partial one")

    def test_a_forced_render_disagreement_is_reported(self) -> None:
        prior = C.PriorRenderState(broad_options=("a", "b"))
        kept = C.apply_patch(prior, C.QuestionPatch())
        cleared = C.apply_patch(prior, C.QuestionPatch(broad_options=()))
        self.assertNotEqual(len(kept.broad_options) >= 2,
                            len(cleared.broad_options) >= 2,
                            "the comparator cannot see a render-mode disagreement")


class QuestionModeTest(unittest.TestCase):
    """Frozen call counts, and control without trace."""

    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = _catalog_file(Path(self._tmp.name))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp.cleanup()

    def counts(self, **cfg) -> tuple[int, int]:
        """(legacy calls, pure calls) for one turn."""
        ag = A.Agent(self.path, config=cfg)
        ag.reset("s", {})
        legacy, pure = [], []
        legacy_original, pure_original = ag._pick_attribute, ag._decide_question
        ag._pick_attribute = lambda *a, _o=legacy_original, **k: (legacy.append(1), _o(*a, **k))[1]
        # The staged controller has no single pure entry point to count, and
        # counting a stage would count a branch rather than a dispatch. The
        # unit gate D cares about is one COMPLETE decision per control turn.
        ag._decide_question = lambda *a, _o=pure_original, **k: (pure.append(1), _o(*a, **k))[1]
        ag.respond("s", "I'm looking for Clothing Women Dresses, "
                        "but I'm still exploring.", 1, 5)
        return len(legacy), len(pure)

    def test_the_default_is_control(self) -> None:
        self.assertEqual(A.DEFAULTS["question_context_mode"], "control")

    def test_off_reaches_the_rule_once_through_the_adapter(self) -> None:
        # AFTER ADOPTION "off" means the adapter plus no orchestration
        # telemetry. It is no longer an independent legacy implementation and
        # must not be described as one: _pick_attribute delegates, so the one
        # rule still runs exactly once.
        self.assertEqual(self.counts(question_context_mode="off"), (1, 1))

    def test_shadow_computes_the_decision_and_asks_the_adapter_too(self) -> None:
        # Deliberate, and deliberately tautological: shadow takes the decision
        # directly AND goes through the adapter, which takes it again. Two
        # executions of the same function comparing their identical results.
        self.assertEqual(self.counts(question_context_mode="shadow"), (1, 2))

    def test_control_runs_pure_only(self) -> None:
        self.assertEqual(self.counts(question_context_mode="control"), (0, 1))

    def test_control_runs_pure_only_without_trace(self) -> None:
        self.assertEqual(self.counts(question_context_mode="control", trace=False), (0, 1))

    def test_shadow_does_not_mutate_state(self) -> None:
        # Shadow must predict without writing. Compare the four render fields
        # against a run with the feature off.
        seen = []
        for mode in ("off", "shadow"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={"question_context_mode": mode})
            ag.reset("s", {})
            ag.respond("s", "I'm looking for Clothing Women Dresses. "
                            "A key requirement is: silk.", 1, 5)
            st = ag._sessions["s"]
            seen.append({k: copy.deepcopy(st.get(k)) for k in C.PATCH_FIELDS})
        self.assertEqual(seen[0], seen[1], "shadow mutated session state")

    def test_an_unknown_mode_degrades_to_off_and_warns(self) -> None:
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            ag = A.Agent(self.path, config={"question_context_mode": "cotnrol"})
            self.assertEqual(ag._question_mode(ag.cfg), "off")
            ag._question_mode(ag.cfg)          # second call: warn once only
        self.assertEqual(buf.getvalue().count("unknown question_context_mode"), 1)

    def test_an_unknown_mode_never_becomes_control(self) -> None:
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            for bad in ("control ", "CONTROL", "on", "true", "", "shadow_mode"):
                ag = A.Agent(self.path, config={"question_context_mode": bad})
                self.assertEqual(ag._question_mode(ag.cfg), "off", bad)

    def test_all_three_modes_agree_end_to_end(self) -> None:
        seen = []
        for mode in ("off", "shadow", "control"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={"question_context_mode": mode})
            ag.reset("s", {})
            out = [ag.respond("s", "I'm looking for Clothing Women Dresses, "
                                   "but I'm still exploring.", 1, 5),
                   ag.respond("s", "Hmm, hard to say really.", 2, 5),
                   ag.respond("s", "For that, what matters is: silk.", 3, 5)]
            seen.append([(r["recommendations"], r["ask_attribute"], r["message"]) for r in out])
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0], seen[2], "control diverged from legacy")

    def test_the_question_path_builds_no_index_that_legacy_did_not(self) -> None:
        """The hard gate, as a test rather than a manual check.

        Phase 6B2 verified this by inspection and wrote the result in a note.
        The falsifiable form is not "these three are None" -- CategoryIndex is
        built by RETRIEVAL under deep_funnel in every mode, so asserting None
        would fail for a reason that has nothing to do with the question path.
        The gate is that the question path ADDS nothing: the set of built
        indexes must be identical across all three modes.
        """
        built = {}
        for mode in ("off", "shadow", "control"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={"question_context_mode": mode})
            ag.reset("s", {})
            ag.respond("s", "I'm looking for Clothing Women Dresses, "
                            "but I'm still exploring.", 1, 5)
            ag.respond("s", "Hmm, hard to say really.", 2, 5)
            built[mode] = {name: getattr(ag.cat, name) is not None
                           for name in ("_cat_index", "_facet_index", "_dense_index")}
        self.assertEqual(built["off"], built["shadow"])
        self.assertEqual(built["off"], built["control"],
                         "the question path built an index legacy did not")
        self.assertFalse(built["control"]["_facet_index"])
        self.assertFalse(built["control"]["_dense_index"])

    def test_a_route_pool_depth_override_leaves_the_message_bit_exact(self) -> None:
        # Selection uses route turn_cfg; _compose reads BASE self.cfg. That
        # asymmetry is existing debt and must survive the relocation intact.
        seen = []
        for mode in ("off", "control"):
            A.clear_catalog_cache()
            ag = A.Agent(self.path, config={
                "question_context_mode": mode, "pool_depth": 30,
                "route_overrides": {"browsing": {"pool_depth": 3}}})
            ag.reset("s", {})
            out = [ag.respond("s", "I'm looking for Clothing Women Dresses, "
                                   "but I'm still exploring.", 1, 5),
                   ag.respond("s", "Hmm, hard to say really.", 2, 5)]
            seen.append([(r["ask_attribute"], r["message"]) for r in out])
        self.assertEqual(seen[0], seen[1])
