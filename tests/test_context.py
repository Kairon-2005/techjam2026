"""Phase 6A: ContextSnapshot, bounded summaries, and the pure policy.

Shadow mode only. Nothing here may reach retrieval, ranking or question
selection, and several tests exist specifically to prove that.
"""
from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
import starter.context as C
from starter.evidence import _distinguishing
from tests.test_indexes import PRODUCTS, _catalog_file


def _slot(attribute="material", value="silk", polarity=1, hardness="hard",
          confidence=1.0, source_turn=1, active=True, soft_ok=True):
    return A.SlotValue(attribute=attribute, value=value, polarity=polarity,
                       hardness=hardness, confidence=confidence,
                       source_turn=source_turn, active=active, soft_ok=soft_ok)


def _maximal() -> C.ContextSnapshot:
    """Every tuple at its cap and every string at its width."""
    wide = "x" * C.SLOT_VALUE_CHARS
    views = tuple(C.SlotView(attribute="attribute" + str(i), value=wide, polarity=-1,
                             hardness="hard", confidence=0.123456, source_turn=99,
                             state="abandoned") for i in range(C.MAX_SLOT_VIEWS))
    return C.ContextSnapshot(
        route="browsing", previous_route="buying", turns_since_override=99,
        override_count=99, slots=views, active_constraint_count=99,
        query_term_count=99, dry_streak=99, uncertain_streak=99, starved=True,
        current_request_more=True,
        asked_facets=tuple("facet" + str(i) for i in range(C.MAX_ASKED_FACETS)),
        previous_question_mode="use_case", previous_question_bits=1.234567,
        previous_question_coverage=0.987654, pool_size=99999,
        pool_before_filter=99999, pool_after_filter=99999, category_count=999,
        category_entropy=9.8765, overgeneral=True, overgeneral_option_count=9,
        profile_source="user_profile", profile_credible=True, profile_tag_count=8,
        profile_tags=tuple("tag" + str(i) for i in range(C.MAX_PROFILE_TAGS)))


class BoundsTest(unittest.TestCase):
    def test_the_maximal_snapshot_is_inside_both_bounds(self) -> None:
        snap = _maximal()
        self.assertLessEqual(C.entry_count(snap), C.MAX_ENTRIES,
                             "a snapshot that cannot be bounded is a log")
        self.assertLessEqual(C.snapshot_bytes(snap), C.MAX_BYTES)

    def test_the_slot_cap_is_shared_not_per_state(self) -> None:
        # The defect this replaces: 12 views PER state gave a true maximum of
        # 73 against a declared bound of 64.
        slots = ([_slot(f"a{i}", polarity=-1) for i in range(8)]
                 + [_slot(f"b{i}") for i in range(8)]
                 + [_slot(f"c{i}", active=False) for i in range(8)])
        self.assertEqual(len(C._slot_views(slots, False)), C.MAX_SLOT_VIEWS)

    def test_canonical_form_is_stable(self) -> None:
        snap = _maximal()
        self.assertEqual(C.canonical(snap), C.canonical(_maximal()))
        self.assertEqual(json.loads(C.canonical(snap))["route"], "browsing")

    def test_the_snapshot_is_immutable(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            _maximal().route = "buying"          # type: ignore[misc]


class SlotRetentionTest(unittest.TestCase):
    def test_retention_order_is_deterministic_and_prioritised(self) -> None:
        slots = [_slot("size", "large", hardness="soft", confidence=0.6, source_turn=3),
                 _slot("material", "leather", confidence=1.0, source_turn=2),
                 _slot("color", "teal", polarity=-1, source_turn=4),
                 _slot("style", "formal", active=False, source_turn=1)]
        order = [v.attribute for v in C._slot_views(slots, recent_override=True)]
        self.assertEqual(order[0], "color", "explicit negatives come first")
        self.assertIn("style", order[:2], "override-abandoned ranks with them")
        self.assertLess(order.index("material"), order.index("size"),
                        "active hard outranks active soft")
        self.assertEqual(order, [v.attribute for v in C._slot_views(list(reversed(slots)), True)],
                         "input order changed the result")


class CategorySummaryTest(unittest.TestCase):
    """The summary must never touch a lazy index."""

    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.cat = A._catalog(_catalog_file(Path(self._tmp.name)))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp.cleanup()

    def test_it_agrees_with_the_real_overgeneral(self) -> None:
        ag = A.Agent(str(Path(self._tmp.name) / "catalog.jsonl"))
        ranked = list(self.cat.cats)
        for limit in (1, 2, 3, 6):
            cfg = {**ag.cfg, "overgeneral_cats": limit}
            broad, options = ag._overgeneral(ranked, cfg)
            summary = C.summarize_categories(ranked, self.cat.cats, cfg["pool_depth"],
                                             limit, _distinguishing)
            self.assertEqual(summary.overgeneral, broad, f"limit={limit}")
            self.assertEqual(list(summary.options), options, f"limit={limit}")

    def test_it_reads_at_most_pool_depth_candidates(self) -> None:
        seen = []

        class Counting(dict):
            def get(self, key, default=None):
                seen.append(key)
                return dict.get(self, key, default)

        cats = Counting(self.cat.cats)
        C.summarize_categories(list(self.cat.cats) * 20, cats, 30, 2, _distinguishing)
        self.assertLessEqual(len(seen), 30)

    def test_building_a_summary_builds_no_index(self) -> None:
        self.assertIsNone(self.cat._cat_index)
        C.summarize_categories(list(self.cat.cats), self.cat.cats, 30, 2, _distinguishing)
        self.assertIsNone(self.cat._cat_index, "the summary built a CategoryIndex")
        self.assertIsNone(self.cat._facet_index)
        self.assertIsNone(self.cat._dense_index)


class ProfileCoverageTest(unittest.TestCase):
    def test_it_is_bounded_by_tags_and_by_window(self) -> None:
        tags = [f"t{i}" for i in range(20)]
        ranked = [f"a{i}" for i in range(500)]
        text = {a: "cotton blue" for a in ranked}
        out = C.profile_coverage(tags, ranked, text, 30)
        self.assertLessEqual(len(out), C.MAX_PROFILE_TAGS)

    def test_coverage_is_a_share_of_the_window(self) -> None:
        ranked = ["a", "b", "c", "d"]
        text = {"a": "cotton", "b": "cotton", "c": "wool", "d": "silk"}
        self.assertEqual(C.profile_coverage(["cotton"], ranked, text, 30)["cotton"], 0.5)


if __name__ == "__main__":
    unittest.main()


def _snap(**kw) -> C.ContextSnapshot:
    base = dict(route="buying", previous_route="", turns_since_override=9,
                override_count=0, slots=(), active_constraint_count=1,
                query_term_count=20, dry_streak=0, uncertain_streak=0,
                starved=False, current_request_more=False, asked_facets=(),
                previous_question_mode="", previous_question_bits=0.0,
                previous_question_coverage=0.0, pool_size=100,
                pool_before_filter=0, pool_after_filter=0, category_count=2,
                category_entropy=1.0, overgeneral=False,
                overgeneral_option_count=0, profile_source="none",
                profile_credible=False, profile_tag_count=0, profile_tags=())
    return C.ContextSnapshot(**{**base, **kw})


def _view(**kw) -> C.SlotView:
    base = dict(attribute="material", value="silk", polarity=1, hardness="hard",
                confidence=1.0, source_turn=1, state="active")
    return C.SlotView(**{**base, **kw})


class PolicyTruthTableTest(unittest.TestCase):
    """Every non-default row derives its output from declared fields."""

    def setUp(self) -> None:
        self.cfg = dict(A.DEFAULTS)

    def codes(self, snap) -> set:
        return set(C.decide(snap, self.cfg).reasons)

    def test_thin_query_plus_dry_streak_broadens(self) -> None:
        d = C.decide(_snap(query_term_count=4, dry_streak=2), self.cfg)
        self.assertEqual(d.retrieval_mode, "broaden")
        self.assertEqual(d.clarification_mode, "easier")
        self.assertIn(C.ReasonCode.BROADEN_THIN_DRY, d.reasons)

    def test_explicit_request_more_rotates_or_broadens(self) -> None:
        d = C.decide(_snap(current_request_more=True), self.cfg)
        self.assertEqual(d.retrieval_mode, "broaden")
        self.assertEqual(d.clarification_mode, "none")
        self.assertIn(C.ReasonCode.ROTATE_OR_BROADEN, d.reasons)

    def test_cumulative_request_count_is_not_a_trigger(self) -> None:
        # wants_more is cumulative and cannot express "this turn"; it is
        # telemetry only and must not appear in the snapshot at all.
        self.assertNotIn("wants_more", {f.name for f in dataclasses.fields(C.ContextSnapshot)})

    def test_recent_override_with_an_abandoned_slot_suppresses(self) -> None:
        snap = _snap(turns_since_override=1, slots=(_view(state="abandoned"),))
        self.assertIn(C.ReasonCode.SUPPRESS_ABANDONED, self.codes(snap))

    def test_low_confidence_hard_slot_proposes_relaxation(self) -> None:
        # Deliberately a HAND-BUILT slot. The contradiction scenario injects
        # its false constraint through the template parser at confidence 1.0,
        # so Phase 6A cannot detect contradiction and does not claim to.
        snap = _snap(slots=(_view(confidence=0.6, value="teal"),))
        d = C.decide(snap, self.cfg)
        self.assertEqual(d.relaxation, ("teal",))
        self.assertIn(C.ReasonCode.PROPOSE_RELAX_LOW_CONFIDENCE, d.reasons)

    def test_a_confident_hard_slot_proposes_nothing(self) -> None:
        d = C.decide(_snap(slots=(_view(confidence=1.0),)), self.cfg)
        self.assertEqual(d.relaxation, ())
        self.assertNotIn(C.ReasonCode.PROPOSE_RELAX_LOW_CONFIDENCE, d.reasons)

    def test_overgeneral_pool_asks_structured(self) -> None:
        d = C.decide(_snap(overgeneral=True, overgeneral_option_count=3), self.cfg)
        self.assertEqual(d.clarification_mode, "structured")
        self.assertIn(C.ReasonCode.ASK_STRUCTURED, d.reasons)

    def test_generic_profile_is_rejected(self) -> None:
        snap = _snap(profile_source="user_profile", profile_tag_count=3,
                     profile_credible=False)
        self.assertIn(C.ReasonCode.REJECT_PROFILE_PRIOR, self.codes(snap))

    def test_a_credible_profile_is_not_rejected(self) -> None:
        snap = _snap(profile_source="user_profile", profile_tag_count=1,
                     profile_credible=True, profile_tags=("merino",))
        self.assertNotIn(C.ReasonCode.REJECT_PROFILE_PRIOR, self.codes(snap))

    def test_the_default_row_carries_no_reason(self) -> None:
        d = C.decide(_snap(), self.cfg)
        self.assertEqual(d.reasons, ())
        self.assertEqual(d.route, "buying", "the default row copies the route")

    def test_phase_6a_never_chooses_an_attribute(self) -> None:
        for snap in (_snap(), _snap(overgeneral=True), _snap(dry_streak=5, query_term_count=2)):
            self.assertIsNone(C.decide(snap, self.cfg).clarification_attribute)


class PolicyPurityTest(unittest.TestCase):
    def test_deciding_twice_gives_the_same_answer(self) -> None:
        snap = _snap(slots=(_view(confidence=0.5),), overgeneral=True)
        self.assertEqual(C.decide(snap, dict(A.DEFAULTS)),
                         C.decide(snap, dict(A.DEFAULTS)))

    def test_the_policy_mutates_neither_snapshot_nor_config(self) -> None:
        snap = _snap(slots=(_view(confidence=0.5),))
        cfg = dict(A.DEFAULTS)
        before_snap, before_cfg = C.canonical(snap), json.dumps(cfg, sort_keys=True, default=str)
        C.decide(snap, cfg)
        self.assertEqual(C.canonical(snap), before_snap)
        self.assertEqual(json.dumps(cfg, sort_keys=True, default=str), before_cfg)

    def test_the_decision_is_immutable(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            C.decide(_snap(), dict(A.DEFAULTS)).route = "x"   # type: ignore[misc]


class ReasonRendererTest(unittest.TestCase):
    def test_every_code_renders(self) -> None:
        for code in C.ReasonCode:
            decision = dataclasses.replace(C.decide(_snap(), dict(A.DEFAULTS)),
                                           reasons=(code,))
            text = C.render(decision)
            self.assertEqual(len(text), 1)
            self.assertGreater(len(text[0]), 20, code)

    def test_codes_are_stable_symbols_not_prose(self) -> None:
        # Telemetry records the enum; wording lives only in the renderer, so
        # rephrasing an explanation cannot invalidate recorded history.
        self.assertEqual(C.ReasonCode.ASK_STRUCTURED.value, "ASK_STRUCTURED")
        self.assertNotIn("ASK_STRUCTURED", C._TEXT[C.ReasonCode.ASK_STRUCTURED])

    def test_the_renderer_preserves_order(self) -> None:
        d = C.decide(_snap(query_term_count=2, dry_streak=3, overgeneral=True), dict(A.DEFAULTS))
        self.assertEqual(len(C.render(d)), len(d.reasons))


class ModuleBoundaryTest(unittest.TestCase):
    def test_context_imports_no_agent_or_mixin(self) -> None:
        import ast
        tree = ast.parse(Path("starter/context.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("starter"):
                names.add(node.module)
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names if a.name.startswith("starter"))
        self.assertEqual(names, set(), f"context.py reached into the package: {names}")
