"""Agent-level regression tests.

tests/test_evaluator.py covers the organizer's harness; nothing covered the
agent itself. These run against a tiny synthetic catalog so the whole file
finishes in well under a second -- the 50k-product catalog is only needed for
the end-to-end score check in tests/test_score_regression.py.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import starter.agent as A
from evaluator.local_evaluator import ALLOWED_ATTRIBUTES

PRODUCTS = [
    {"parent_asin": "P1", "title": "Leather Belt", "categories": ["Accessories", "Belts"],
     "features": ["genuine leather", "black"], "details": {"Material": "leather"},
     "store": "Acme", "description": "A black leather belt for work",
     "average_rating": 4.5, "rating_number": 9000, "price": 25.0},
    {"parent_asin": "P2", "title": "Silk Scarf", "categories": ["Accessories", "Scarves"],
     "features": ["100% silk", "blue"], "details": {"Material": "silk"},
     "store": "Acme", "description": "A blue silk scarf for formal wear",
     "average_rating": 4.0, "rating_number": 50, "price": 15.0},
    {"parent_asin": "P3", "title": "Running Shoes", "categories": ["Shoes", "Athletic"],
     "features": ["rubber sole", "white"], "details": {"Material": "polyester"},
     "store": "Zoom", "description": "White running shoes for the gym",
     "average_rating": 4.8, "rating_number": 4000, "price": 60.0},
]


def _catalog_file(tmp: Path) -> str:
    path = tmp / "catalog.jsonl"
    path.write_text("\n".join(json.dumps(p) for p in PRODUCTS), encoding="utf-8")
    return str(path)


class ParseMessageTest(unittest.TestCase):
    """The four simulator templates plus template-independent cue fallback."""

    def test_buying_template(self) -> None:
        cat, phrases = A.parse_message(
            "I'm looking for Accessories Belts. A key requirement is: genuine leather.")
        self.assertEqual(cat, "Accessories Belts")
        self.assertEqual(phrases, ["genuine leather"])

    def test_browsing_template_yields_no_constraints(self) -> None:
        cat, phrases = A.parse_message("I'm looking for Shoes Athletic, but I'm still exploring.")
        self.assertEqual(cat, "Shoes Athletic")
        self.assertEqual(phrases, [])

    def test_reply_template_splits_and_keeps_whole_body(self) -> None:
        _, phrases = A.parse_message("For that, what matters is: rubber sole; white.")
        self.assertIn("rubber sole", phrases)
        self.assertIn("white", phrases)
        self.assertIn("rubber sole; white", phrases)  # a constraint may contain "; "

    def test_override_template(self) -> None:
        _, phrases = A.parse_message(
            "Actually, ignore my earlier preference. What I need is: leather.")
        self.assertEqual(phrases, ["leather"])

    def test_cue_fallback_on_unknown_phrasing(self) -> None:
        _, phrases = A.parse_message("Honestly what i care about is waterproof stitching, if that helps")
        self.assertTrue(any("waterproof stitching" in p for p in phrases))

    def test_empty_and_non_template_messages_are_safe(self) -> None:
        self.assertEqual(A.parse_message(""), (None, []))
        self.assertEqual(A.parse_message("hello there")[1], [])


class SlotTest(unittest.TestCase):
    def test_slots_use_contract_attribute_names(self) -> None:
        for phrase, slot in [("genuine leather", "material"), ("color: black", "color"),
                             ("budget around $25", "budget"), ("size medium", "size"),
                             ("for hiking", "use_case"), ("rubber sole", "feature")]:
            self.assertEqual(A.slot_of(phrase), slot, phrase)

    def test_every_slot_is_a_legal_ask_attribute(self) -> None:
        for name, _ in A.SLOT_RES:
            self.assertIn(name, ALLOWED_ATTRIBUTES)


class ConfigTest(unittest.TestCase):
    def test_unknown_keys_warn_but_do_not_crash(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = A._load_config({"route": False, "w_pop": 3.0})
        self.assertIn("route", buf.getvalue())
        self.assertEqual(cfg["w_pop"], 3.0)

    def test_defaults_are_the_submission_config(self) -> None:
        cfg = A._load_config(None)
        # Pool-aware asking adopted at 0.928508; slot override measured and
        # rejected (keep wins even under genuine contradiction). notes/08.
        self.assertEqual(cfg["ask_policy"], "other_then_pool")
        self.assertEqual(cfg["pool_give_up_after"], 1)
        self.assertEqual(cfg["on_override"], "keep")
        self.assertEqual(cfg["w_card"], 0.0)   # simulator-inversion feature stays off
        self.assertEqual(cfg["route_overrides"], {})


class AgentContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))
        A.clear_catalog_cache()

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def agent(self, **cfg) -> A.Agent:
        A.clear_catalog_cache()
        return A.Agent(self.catalog, config=cfg or None)

    def test_response_matches_the_api_contract(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        out = ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: leather.", 1, 10)
        self.assertIsInstance(out["message"], str)
        self.assertTrue(out["message"])
        self.assertIn(out["ask_attribute"], ALLOWED_ATTRIBUTES)
        self.assertLessEqual(len(out["recommendations"]), 10)
        self.assertTrue(all(set(r) == {"parent_asin"} for r in out["recommendations"]))
        self.assertEqual(out["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_top_k_is_clamped_to_the_contract_maximum(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        out = ag.respond("s", "I'm looking for Shoes Athletic, but I'm still exploring.", 1, 5000)
        self.assertLessEqual(len(out["recommendations"]), 100)

    def test_hostile_inputs_do_not_raise(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        for turn, message in enumerate(["", None, 12345, "   ", "?" * 5000], start=1):
            out = ag.respond("s", message, turn, 10)
            self.assertIsInstance(out["message"], str)
            self.assertIn(out["ask_attribute"], ALLOWED_ATTRIBUTES)

    def test_respond_without_reset_still_works(self) -> None:
        ag = self.agent()
        out = ag.respond("never-reset", "I'm looking for Shoes Athletic, but I'm still exploring.", 1, 10)
        self.assertIsInstance(out["recommendations"], list)

    def test_noise_replies_do_not_pollute_state(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: leather.", 1, 10)
        before = list(ag._sessions["s"]["phrases"])
        ag.respond("s", "I don't have an additional preference for other.", 2, 10)
        self.assertEqual(ag._sessions["s"]["phrases"], before)

    def test_dry_other_replies_fall_back_to_concrete_attributes(self) -> None:
        ag = self.agent(ask_policy="other", ask_fallback_after=2)
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: leather.", 1, 10)
        asked = []
        for turn in range(2, 6):
            out = ag.respond("s", "I don't have an additional preference for other.", turn, 10)
            asked.append(out["ask_attribute"])
        self.assertNotEqual(asked[-1], "other",
                            "two dry 'other' replies must degrade to concrete probing")

    def test_route_is_classified_from_the_first_message(self) -> None:
        for message, route in [
            ("I'm looking for x. A key requirement is: leather.", "buying"),
            ("I'm looking for x, but I'm still exploring.", "browsing"),
            ("Actually, ignore my earlier preference. What I need is: silk.", "override"),
        ]:
            category, phrases = A.parse_message(message)
            self.assertEqual(A.Agent._route(message, phrases, category), route, message)

    def test_unknown_opening_degrades_to_mixed_not_override(self) -> None:
        # Previously 100% of vague openings were labelled "override", a claim
        # the customer never made.
        for message in ("Show me something good.",
                        "Hi, I need to buy a gift but I'm not sure what.",
                        "I'm just looking around, nothing specific in mind yet."):
            category, phrases = A.parse_message(message)
            self.assertEqual(A.Agent._route(message, phrases, category), "mixed", message)

    def test_route_firms_up_from_browsing_to_buying(self) -> None:
        state = A.Agent._blank_state()
        state["route"] = "browsing"
        self.assertEqual(A.Agent._retarget(state), "browsing")
        state["slots"].append(A.SlotValue(attribute="material", value="leather"))
        self.assertEqual(A.Agent._retarget(state), "buying",
                         "a stated constraint means the shopper is no longer browsing")

    def test_mixed_becomes_browsing_once_a_category_is_known(self) -> None:
        state = A.Agent._blank_state()
        state["route"] = "mixed"
        self.assertEqual(A.Agent._retarget(state), "mixed")
        state["category"] = "Shoes Athletic"
        self.assertEqual(A.Agent._retarget(state), "browsing")

    def test_retrieval_honours_the_route_config(self) -> None:
        # term_cap and the bm25 field weights are per-route retrieval topology;
        # reading them from self.cfg made any route patch silently void.
        ag = self.agent(route_overrides={"browsing": {"term_cap": 1}})
        ag.reset("s", {})
        seen = {}
        original = ag._retrieve
        ag._retrieve = lambda terms, limit, cfg=None, _o=original: (
            seen.update(cap=(cfg or ag.cfg)["term_cap"]) or _o(terms, limit, cfg))
        ag.respond("s", "I'm looking for Shoes Athletic, but I'm still exploring.", 1, 10)
        self.assertEqual(seen["cap"], 1, "a route patch on term_cap must reach _retrieve")

    def test_route_overrides_reach_every_stage_not_just_rerank(self) -> None:
        ag = self.agent(route_overrides={"browsing": {"ask_policy": "probe_cycle"}})
        ag.reset("s", {})
        out = ag.respond("s", "I'm looking for Shoes Athletic, but I'm still exploring.", 1, 10)
        self.assertNotEqual(out["ask_attribute"], "other",
                            "a route patch on ask_policy must not silently no-op")


class OverrideStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def primed(self, **cfg) -> A.Agent:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config=cfg)
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Scarves. A key requirement is: 100% silk.", 1, 10)
        ag.respond("s", "For that, what matters is: color: blue.", 2, 10)
        return ag

    def test_keep_retains_everything(self) -> None:
        ag = self.primed(on_override="keep")
        ag.respond("s", "Actually, ignore my earlier preference. What I need is: genuine leather.", 3, 10)
        self.assertIn("100% silk", ag._sessions["s"]["phrases"])

    def test_erase_drops_everything(self) -> None:
        ag = self.primed(on_override="erase")
        ag.respond("s", "Actually, ignore my earlier preference. What I need is: genuine leather.", 3, 10)
        self.assertNotIn("100% silk", ag._sessions["s"]["phrases"])

    def test_slot_drops_only_the_superseded_slot(self) -> None:
        ag = self.primed(on_override="slot")
        ag.respond("s", "Actually, ignore my earlier preference. What I need is: genuine leather.", 3, 10)
        phrases = ag._sessions["s"]["phrases"]
        self.assertNotIn("100% silk", phrases, "the superseded material slot must go")
        self.assertIn("color: blue", phrases, "an unrelated colour slot must survive")

    def test_slot_also_drops_terms_the_dead_phrase_contributed(self) -> None:
        ag = self.primed(on_override="slot")
        ag.respond("s", "Actually, ignore my earlier preference. What I need is: genuine leather.", 3, 10)
        self.assertNotIn("silk", ag._sessions["s"]["terms"])
        self.assertIn("blue", ag._sessions["s"]["terms"])

    def test_decay_keeps_the_most_recent_evidence(self) -> None:
        ag = self.primed(on_override="decay")
        ag.respond("s", "Actually, ignore my earlier preference. What I need is: genuine leather.", 3, 10)
        self.assertIn("color: blue", ag._sessions["s"]["phrases"])


class PoolAskerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def test_entropy_is_zero_when_the_pool_agrees(self) -> None:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config={"ask_policy": "pool"})
        self.assertEqual(ag._pool_entropy(["P1"], A.ATTR_VOCAB["color"]), 0.0)
        self.assertGreater(ag._pool_entropy(["P1", "P2", "P3"], A.ATTR_VOCAB["color"]), 0.0)

    def test_pool_policy_returns_a_legal_attribute(self) -> None:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config={"ask_policy": "pool"})
        ag.reset("s", {})
        out = ag.respond("s", "I'm looking for Accessories Belts, but I'm still exploring.", 1, 10)
        self.assertIn(out["ask_attribute"], ALLOWED_ATTRIBUTES)


class ReplyOutcomeTest(unittest.TestCase):
    """Phase 1B: an uninformative turn must not become product evidence."""

    def test_each_outcome_is_recognised(self) -> None:
        cases = [
            ("For that, what matters is: rubber sole.", A.Outcome.INFORMATIVE),
            ("I'm looking for boots, but I'm still exploring.", A.Outcome.INFORMATIVE),
            ("Actually, ignore my earlier preference. What I need is: leather.", A.Outcome.OVERRIDE),
            ("I don't have an additional preference for color.", A.Outcome.NO_PREFERENCE),
            ("Can you just show me more options?", A.Outcome.REQUEST_MORE),
            ("Hmm, hard to say really.", A.Outcome.UNCERTAIN),
            ("I'm not sure, what do you think?", A.Outcome.UNCERTAIN),
            ("I'd rather not say.", A.Outcome.REFUSAL),
        ]
        for message, expected in cases:
            category, phrases = A.parse_message(message)
            self.assertEqual(A.classify_reply(message, phrases, category), expected, message)

    def test_a_stated_category_alone_counts_as_evidence(self) -> None:
        # Regression: browsing openings carry a category but no constraint. If
        # those are not evidence, 90 sessions silently lose their category.
        message = "I'm looking for Shoes Athletic, but I'm still exploring."
        category, phrases = A.parse_message(message)
        self.assertEqual(phrases, [])
        self.assertEqual(A.classify_reply(message, phrases, category), A.Outcome.INFORMATIVE)


class Phase1StateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def primed(self, **cfg) -> A.Agent:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config=cfg or None)
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: genuine leather.", 1, 10)
        return ag

    def test_evidence_becomes_a_typed_slot(self) -> None:
        ag = self.primed()
        slot = ag._sessions["s"]["slots"][0]
        self.assertEqual(slot.attribute, "material")
        self.assertEqual(slot.polarity, 1)
        self.assertEqual(slot.source_turn, 1)
        self.assertTrue(slot.usable)
        self.assertIn("leather", slot.provenance)

    def test_uncooperative_reply_contributes_no_query_terms(self) -> None:
        ag = self.primed()
        before = list(ag._sessions["s"]["terms"])
        ag.respond("s", "Hmm, hard to say really.", 2, 10)
        after = ag._sessions["s"]["terms"]
        self.assertEqual(after, before)
        for junk in ("hmm", "hard", "say", "really"):
            self.assertNotIn(junk, after)

    def test_query_is_rebuilt_from_active_evidence_only(self) -> None:
        ag = self.primed()
        terms = ag._sessions["s"]["terms"]
        self.assertIn("leather", terms)
        self.assertIn("belts", terms)          # from the stated category

    def test_request_for_more_options_is_recorded(self) -> None:
        ag = self.primed()
        ag.respond("s", "Can you just show me more options?", 2, 10)
        self.assertEqual(ag._sessions["s"]["wants_more"], 1)

    def test_rotation_protects_the_confident_head(self) -> None:
        ag = self.primed(rotate_keep_top=2)
        state = ag._sessions["s"]
        state["rotate_pending"] = True
        state["shown"] = ["P1", "P2", "P3"]
        rotated = ag._rotate(["P1", "P2", "P3", "P4"], state, ag.cfg)
        self.assertEqual(rotated[:2], ["P1", "P2"], "top of the list must be pinned")
        self.assertEqual(rotated[2], "P4", "unseen candidates come before seen ones")

    def test_rotation_is_one_shot(self) -> None:
        ag = self.primed(rotate_keep_top=2)
        state = ag._sessions["s"]
        state["rotate_pending"] = True
        state["shown"] = ["P1", "P2", "P3"]
        first = ag._rotate(["P1", "P2", "P3", "P4"], state, ag.cfg)
        self.assertFalse(state["rotate_pending"], "one request must arm exactly one rotation")
        again = ag._rotate(["P1", "P2", "P3", "P4"], state, ag.cfg)
        self.assertEqual(again, ["P1", "P2", "P3", "P4"], "must not keep rotating forever")
        self.assertNotEqual(first, again)

    def test_new_evidence_resets_pagination(self) -> None:
        ag = self.primed()
        ag.respond("s", "Can you just show me more options?", 2, 10)
        self.assertTrue(ag._sessions["s"]["shown"])
        ag.respond("s", "For that, what matters is: black.", 3, 10)
        state = ag._sessions["s"]
        self.assertFalse(state["rotate_pending"])
        # "shown" is repopulated for the NEW result set, not carried over
        self.assertLessEqual(len(state["shown"]), 10)

    def test_a_rich_but_stalled_query_is_not_widened(self) -> None:
        # Clean-set stalls carry ~17 terms and ~7 constraints; widening those
        # trades away MRR for recall that is not the binding constraint.
        ag = self.primed()
        state = ag._sessions["s"]
        state["dry_streak"] = 5
        state["terms"] = [f"t{i}" for i in range(20)]
        state["slots"] = [A.SlotValue(attribute="feature", value=f"v{i}") for i in range(7)]
        self.assertFalse(ag._starved(state, ag.cfg))
        state["terms"] = ["belt", "leather"]
        self.assertTrue(ag._starved(state, ag.cfg))

    def test_starved_evidence_widens_the_candidate_pool(self) -> None:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config={"starved_after": 1, "starved_candidates": 500})
        limits: list[int] = []
        original = ag._retrieve
        ag._retrieve = lambda terms, limit, cfg=None, _o=original: (
            limits.append(limit) or _o(terms, limit, cfg))
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: genuine leather.", 1, 10)
        ag.respond("s", "Hmm, hard to say really.", 2, 10)
        self.assertEqual(limits[0], 100, "a well-fed query keeps the tight pool")
        self.assertEqual(limits[1], 500, "a starved query widens recall")

    def test_widening_does_not_trigger_while_the_customer_cooperates(self) -> None:
        A.clear_catalog_cache()
        ag = A.Agent(self.catalog, config={"starved_after": 1, "starved_candidates": 500})
        limits: list[int] = []
        original = ag._retrieve
        ag._retrieve = lambda terms, limit, cfg=None, _o=original: (
            limits.append(limit) or _o(terms, limit, cfg))
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Accessories Belts. A key requirement is: genuine leather.", 1, 10)
        ag.respond("s", "For that, what matters is: black.", 2, 10)
        self.assertEqual(limits, [100, 100])

    def test_uncertainty_asks_an_easier_question_than_no_preference(self) -> None:
        ag = self.primed(ask_policy="other_then_pool")
        state = ag._sessions["s"]
        state["asked"] = ["other", "other"]
        state["uncertain_streak"] = 2
        self.assertEqual(ag._easiest_unasked(state), "use_case")


def tearDownModule() -> None:
    """Close the shared SQLite handles so the run ends without ResourceWarnings."""
    A.clear_catalog_cache()


if __name__ == "__main__":
    unittest.main()
