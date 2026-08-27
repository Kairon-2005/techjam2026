"""Route data planes: eligibility, safe filtering, rescue, and divergence.

Phase 2A shipped route LABELS over one candidate set. These tests exist to
stop that from being mistaken for dual-track again: several of them fail if
the routes produce the same candidates, and one fails if a route parameter can
be changed without changing anything.
"""
from __future__ import annotations

import unittest

import starter.agent as A
from tests.test_indexes import PRODUCTS, _catalog_file   # noqa: F401
import json
import tempfile
from pathlib import Path


def _catalog(tmp: Path) -> str:
    path = tmp / "catalog.jsonl"
    path.write_text("\n".join(json.dumps(p) for p in PRODUCTS), encoding="utf-8")
    return str(path)


class PlaneTestBase(unittest.TestCase):
    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = _catalog(Path(self._tmp.name))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp.cleanup()

    def agent(self, **cfg) -> A.Agent:
        # No cache clear here: several tests hold two agents at once to vary a
        # single route parameter, and clearing closes the first one's catalog.
        # Sharing one catalog across configs is also what production does.
        return A.Agent(self.path, config={"dual_plane": True, **cfg})

    def state_with(self, ag: A.Agent, category: str, *slots) -> dict:
        state = ag._blank_state()
        state["category"] = category
        state["slots"] = list(slots)
        state["phrases"] = [sl.value for sl in slots if sl.usable]
        state["terms"] = [t for sl in slots if sl.usable for t in ag._terms(sl.value)]
        return state

    @staticmethod
    def hard(attribute: str, value: str, **kw) -> A.SlotValue:
        base = {"hardness": "hard", "confidence": 1.0, "source_turn": 1}
        return A.SlotValue(attribute=attribute, value=value, **{**base, **kw})


class FilterEligibilityTest(PlaneTestBase):
    """Six gates, each of which must be able to refuse on its own."""

    def _reasons(self, ag: A.Agent, slot: A.SlotValue) -> str:
        state = self.state_with(ag, "women dresses", slot)
        _, skipped = ag._eligible_filters(state, ag.cfg)
        return " ".join(r for _, r in skipped)

    def test_a_qualifying_slot_is_eligible(self) -> None:
        ag = self.agent()
        state = self.state_with(ag, "women dresses", self.hard("material", "silk"))
        eligible, skipped = ag._eligible_filters(state, ag.cfg)
        self.assertEqual([sl.value for sl in eligible], ["silk"], skipped)

    def test_a_negative_slot_is_never_a_positive_filter(self) -> None:
        # Filtering FOR a rejected value is the worst available bug: it
        # returns exactly what the customer refused.
        ag = self.agent()
        slot = self.hard("material", "silk", polarity=-1)
        state = self.state_with(ag, "women dresses", slot)
        eligible, _ = ag._eligible_filters(state, ag.cfg)
        self.assertEqual(eligible, [])
        self.assertIn("negative polarity", self._reasons(ag, slot))

    def test_an_abandoned_slot_does_not_filter(self) -> None:
        ag = self.agent()
        slot = self.hard("material", "silk", soft_ok=False)
        self.assertIn("abandoned", self._reasons(ag, slot))

    def test_an_inactive_slot_does_not_filter(self) -> None:
        ag = self.agent()
        slot = self.hard("material", "silk", active=False, contradiction="superseded")
        self.assertIn("inactive", self._reasons(ag, slot))

    def test_a_soft_slot_does_not_filter(self) -> None:
        ag = self.agent()
        self.assertIn("preference", self._reasons(ag, self.hard("material", "silk",
                                                                hardness="soft")))

    def test_low_confidence_does_not_filter(self) -> None:
        ag = self.agent()
        self.assertIn("confidence", self._reasons(ag, self.hard("material", "silk",
                                                                confidence=0.6)))

    def test_a_low_coverage_facet_does_not_filter(self) -> None:
        ag = self.agent(facet_min_coverage=0.99)
        self.assertIn("coverage", self._reasons(ag, self.hard("material", "silk")))

    def test_a_value_the_catalog_never_uses_does_not_filter(self) -> None:
        ag = self.agent()
        self.assertIn("no catalog support",
                      self._reasons(ag, self.hard("material", "adamantium")))


class BuyingPlaneTest(PlaneTestBase):
    def test_the_safe_filter_is_actually_applied(self) -> None:
        # No category, so the filter works against the whole catalog and its
        # effect is visible. Inside the dresses shelf both products are
        # CONSISTENT with silk -- one matches, one is silent -- so narrowing
        # there would mean the presence-aware rule had been broken.
        ag = self.agent(buying_min_candidates=1)
        state = self.state_with(ag, "", self.hard("material", "silk"))
        trace: dict = {}
        pool = ag._safe_pool(state, ag.cfg, trace)
        names = {ag.cat.category_index.asins[i] for i in pool}
        self.assertEqual(trace["applied_filters"], ["silk"])
        self.assertIn("W1", names, "the silk dress must survive its own filter")
        self.assertIn("Q1", names, "silence about material is not refusal")
        self.assertNotIn("M1", names, "the wool coat contradicts silk")
        self.assertLess(trace["pool_after_filter"], trace["pool_before_filter"])

    def test_the_rescue_lane_carries_excluded_products_every_turn(self) -> None:
        # W3 is leather, so a silk filter excludes it. It must still be
        # reachable: a filter is a hypothesis, and a wrong one may cost
        # ranking position but never reachability.
        ag = self.agent(buying_min_candidates=1, buying_rescue_budget=50)
        state = self.state_with(ag, "", self.hard("material", "silk"))
        state["terms"] = ["leather", "boots", "silk", "dress"]
        trace: dict = {}
        cands = ag._plane_buying(state, ag.cfg, 2, trace)
        self.assertIn("W3", [a for a, _ in cands], "the filter permanently lost a product")
        self.assertGreater(trace["rescue_carried"], 0)

    def test_relaxation_runs_before_the_pool_starves(self) -> None:
        ag = self.agent(buying_min_candidates=4)
        state = self.state_with(ag, "women dresses",
                                self.hard("material", "silk"),
                                self.hard("color", "black"))
        trace: dict = {}
        pool = ag._safe_pool(state, ag.cfg, trace)
        self.assertTrue(trace["surrendered_filters"], "nothing was surrendered")
        self.assertGreaterEqual(len(pool), 1)

    def test_a_broken_facet_reading_cannot_empty_the_pool(self) -> None:
        ag = self.agent()
        state = self.state_with(ag, "no such category",
                                self.hard("material", "adamantium"))
        state["terms"] = ["dress"]
        trace: dict = {}
        self.assertTrue(ag._plane_buying(state, ag.cfg, 5, trace))


class BrowsingAndMixedTest(PlaneTestBase):
    def test_browsing_spans_more_shelves_than_the_filtered_buying_lane(self) -> None:
        # buying_rescue_budget=0 isolates the filtered lane. With the standing
        # rescue budget on, buying also reaches everything -- deliberately --
        # so this comparison would say nothing about topology.
        ag = self.agent(buying_min_candidates=1, buying_rescue_budget=0)
        slot = self.hard("material", "silk")
        terms = ["dress", "skirt", "boots", "coat"]
        buy_state = self.state_with(ag, "women dresses", slot)
        buy_state["terms"] = terms
        brw_state = self.state_with(ag, "women dresses", slot)
        brw_state["terms"] = terms
        bt, wt = {}, {}
        # The filtered POOL, not the returned list: the rescue floor tops any
        # short result back up to the page size, so the returned list cannot
        # isolate the filtered lane.
        pool = ag._safe_pool(buy_state, ag.cfg, bt)
        browsing = ag._plane_browsing(brw_state, ag.cfg, 10, wt)
        ci = ag.cat.category_index
        buying_shelves = {ci.leaf[i] for i in pool}
        browsing_shelves = {ci.leaf[ci.ids[a]] for a, _ in browsing if a in ci.ids}
        self.assertGreater(len(browsing_shelves), len(buying_shelves),
                           "browsing must reach shelves the buying filter does not")
        self.assertTrue(browsing_shelves - buying_shelves)
        self.assertIn("category_expansion", wt["route_candidates"])

    def test_mixed_applies_no_strict_filtering(self) -> None:
        ag = self.agent()
        state = self.state_with(ag, "women dresses", self.hard("material", "silk"))
        state["terms"] = ["dress", "boots", "coat"]
        trace: dict = {}
        ag._plane_mixed(state, ag.cfg, 10, trace)
        self.assertNotIn("applied_filters", trace, "mixed must not filter")
        self.assertNotIn("pool_after_filter", trace)

    def test_the_three_planes_produce_different_candidate_sets(self) -> None:
        ag = self.agent(buying_min_candidates=1)
        sets = {}
        for name, plane in (("buying", ag._plane_buying),
                            ("browsing", ag._plane_browsing),
                            ("mixed", ag._plane_mixed)):
            state = self.state_with(ag, "women dresses", self.hard("material", "silk"))
            state["terms"] = ["dress"]
            sets[name] = tuple(a for a, _ in plane(state, ag.cfg, 10, {}))
        self.assertNotEqual(sets["buying"], sets["browsing"],
                            "buying and browsing returned identical candidates")
        self.assertNotEqual(sets["buying"], sets["mixed"])


class RouteWiringTest(PlaneTestBase):
    def test_a_route_budget_change_changes_the_candidates(self) -> None:
        # Not "the config was read" -- that passed for the route_overrides bug
        # too. The parameter has to reach the candidate set.
        wide = self.agent(browsing_neighbour_budget=100)
        narrow = self.agent(browsing_neighbour_budget=0)
        got = []
        for ag in (wide, narrow):
            state = self.state_with(ag, "women dresses")
            state["terms"] = ["dress"]
            trace: dict = {}
            ag._plane_browsing(state, ag.cfg, 5, trace)
            got.append(trace["route_candidates"]["category_expansion"])
        self.assertNotEqual(got[0], got[1], "the neighbour budget did nothing")

    def test_expansion_depth_reaches_the_candidate_pool(self) -> None:
        deep = self.agent(browsing_expand_up=1, browsing_expand_down=2)
        flat = self.agent(browsing_expand_up=0, browsing_expand_down=0)
        pools = []
        for ag in (deep, flat):
            state = self.state_with(ag, "women dresses")
            state["terms"] = ["dress"]
            trace: dict = {}
            ag._plane_browsing(state, ag.cfg, 5, trace)
            pools.append(trace["category_pool"])
        self.assertGreater(pools[0], pools[1])

    def test_an_unknown_opening_falls_through_to_mixed(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        ag.respond("s", "hgfd qwertz lorem ipsum", 1, 5)
        self.assertEqual(ag._sessions["s"]["route"], "mixed")

    def test_browsing_firms_up_into_buying(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women, but I'm still exploring.", 1, 5)
        self.assertEqual(ag._sessions["s"]["route"], "browsing")
        ag.respond("s", "For that, what matters is: silk.", 2, 5)
        self.assertEqual(ag._sessions["s"]["route"], "buying")

    def test_a_superseded_constraint_stops_being_filter_eligible(self) -> None:
        ag = self.agent()
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses. A key requirement is: silk.", 1, 5)
        ag.respond("s", "Actually, forget silk. What I need is: wool.", 2, 5)
        state = ag._sessions["s"]
        self.assertNotIn("silk", state["terms"])
        by_value = {sl.value: sl for sl in state["slots"]}
        self.assertFalse(by_value["silk"].usable)
        eligible, skipped = ag._eligible_filters(state, ag.cfg)
        self.assertNotIn("silk", [sl.value for sl in eligible],
                         "a superseded constraint is still filter-eligible")
        self.assertIn("silk", [v for v, _ in skipped])

    def test_a_category_pivot_moves_the_shelf(self) -> None:
        # The customer abandons the CATEGORY, not the material. The buying
        # pool must follow them to the new shelf.
        ag = self.agent()
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses. A key requirement is: silk.", 1, 5)
        before = ag.cat.category_index.shelves(ag._sessions["s"]["category"])
        ag.respond("s", "Actually, forget dresses entirely. What I need is: Clothing Men Coats.", 2, 5)
        after = ag.cat.category_index.shelves(ag._sessions["s"]["category"])
        self.assertNotEqual(before, after, "the old shelf survived the pivot")


class CompatibilityTest(PlaneTestBase):
    def test_the_feature_off_path_is_untouched(self) -> None:
        off = self.agent(dual_plane=False)
        state = self.state_with(off, "women dresses", self.hard("material", "silk"))
        state["terms"] = ["dress", "silk"]
        cands, trace = off._candidates(state, off.cfg, 10)
        self.assertEqual(trace["plane"], "legacy")
        self.assertEqual(cands, off._retrieve(state["terms"], 10, off.cfg))

    def test_telemetry_does_not_change_the_ranking(self) -> None:
        rankings = []
        for trace_on in (True, False):
            ag = self.agent(trace=trace_on)
            ag.reset("s", {})
            ag.respond("s", "I'm looking for Clothing Women Dresses. "
                            "A key requirement is: silk.", 1, 5)
            reply = ag.respond("s", "For that, what matters is: black.", 2, 5)
            rankings.append([r["parent_asin"] for r in reply["recommendations"]])
        self.assertEqual(rankings[0], rankings[1],
                         "recording the trace changed the result")


if __name__ == "__main__":
    unittest.main()
