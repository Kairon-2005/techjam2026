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

    def test_defaults_are_the_frozen_submission_config(self) -> None:
        cfg = A._load_config(None)
        self.assertEqual(cfg["ask_policy"], "other")
        self.assertEqual(cfg["on_override"], "keep")
        self.assertEqual(cfg["w_card"], 0.0)   # simulator-inversion feature stays off
        self.assertEqual(cfg["route_overrides"], {})


class AgentContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = _catalog_file(Path(cls._tmp.name))
        A._CATALOG_CACHE.clear()

    @classmethod
    def tearDownClass(cls) -> None:
        A._CATALOG_CACHE.clear()
        cls._tmp.cleanup()

    def agent(self, **cfg) -> A.Agent:
        A._CATALOG_CACHE.clear()
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
        ag = self.agent(ask_fallback_after=2)
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
            ("I'm looking for x. I prefer silk.", "override"),
        ]:
            self.assertEqual(A.Agent._route(message), route)

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
        A._CATALOG_CACHE.clear()
        cls._tmp.cleanup()

    def primed(self, **cfg) -> A.Agent:
        A._CATALOG_CACHE.clear()
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
        A._CATALOG_CACHE.clear()
        cls._tmp.cleanup()

    def test_entropy_is_zero_when_the_pool_agrees(self) -> None:
        A._CATALOG_CACHE.clear()
        ag = A.Agent(self.catalog, config={"ask_policy": "pool"})
        self.assertEqual(ag._pool_entropy(["P1"], A.ATTR_VOCAB["color"]), 0.0)
        self.assertGreater(ag._pool_entropy(["P1", "P2", "P3"], A.ATTR_VOCAB["color"]), 0.0)

    def test_pool_policy_returns_a_legal_attribute(self) -> None:
        A._CATALOG_CACHE.clear()
        ag = A.Agent(self.catalog, config={"ask_policy": "pool"})
        ag.reset("s", {})
        out = ag.respond("s", "I'm looking for Accessories Belts, but I'm still exploring.", 1, 10)
        self.assertIn(out["ask_attribute"], ALLOWED_ATTRIBUTES)


if __name__ == "__main__":
    unittest.main()
