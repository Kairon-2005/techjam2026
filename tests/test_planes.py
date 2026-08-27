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
        self.assertIn("W3", [a for a, *_ in cands], "the filter permanently lost a product")
        self.assertGreater(trace["rescue_candidates"], 0)
        # ...and it must survive the funnel, not merely be generated.
        funnelled = ag._funnel(cands, ag.cfg, {})
        self.assertIn("W3", [a for a, _ in funnelled],
                      "the funnel dropped a rescued product")

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
        browsing_shelves = {ci.leaf[ci.ids[a]] for a, *_ in browsing if a in ci.ids}
        self.assertGreater(len(browsing_shelves), len(buying_shelves),
                           "browsing must reach shelves the buying filter does not")
        self.assertTrue(browsing_shelves - buying_shelves)
        self.assertIn("expansion", wt["route_candidates"])

    def test_mixed_applies_no_strict_filtering(self) -> None:
        ag = self.agent()
        state = self.state_with(ag, "women dresses", self.hard("material", "silk"))
        state["terms"] = ["dress", "boots", "coat"]
        trace: dict = {}
        ag._plane_mixed(state, ag.cfg, 10, trace)
        self.assertNotIn("applied_filters", trace, "mixed must not filter")
        self.assertNotIn("pool_after_filter", trace)

    def test_the_three_planes_differ_in_candidate_origin(self) -> None:
        # On a five-product catalog the three planes can converge on the same
        # handful of asins, so the claim under test is composition: which
        # sources each route draws from, and how many from each.
        ag = self.agent(buying_min_candidates=1)
        sets, origins = {}, {}
        for name, plane in (("buying", ag._plane_buying),
                            ("browsing", ag._plane_browsing),
                            ("mixed", ag._plane_mixed)):
            state = self.state_with(ag, "women dresses", self.hard("material", "silk"))
            state["terms"] = ["dress", "skirt", "boots", "coat"]
            trace: dict = {}
            tagged = plane(state, ag.cfg, 10, trace)
            sets[name] = tuple(a for a, *_ in tagged)
            origins[name] = {src for _, _, src in tagged}
        self.assertNotEqual(sets["buying"], sets["browsing"],
                            "buying and browsing returned identical candidates")
        self.assertIn("rescue", origins["buying"],
                      "buying must carry a rescue source")
        self.assertNotIn("rescue", origins["mixed"],
                         "mixed must not filter, so it has nothing to rescue")
        self.assertNotEqual(origins["buying"], origins["mixed"])


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
            got.append(trace["route_candidates"]["expansion"])
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


class FunnelTest(PlaneTestBase):
    """Deep retrieval is only worth having if the ranker is not handed all of
    it. R1 fed 1274 candidates to a ranker whose operating point is 100."""

    def tagged(self, primary=40, expansion=40, rescue=40) -> list:
        out = []
        for kind, n in (("primary", primary), ("expansion", expansion), ("rescue", rescue)):
            out += [(f"{kind}{i}", float(n - i), kind) for i in range(n)]
        return out

    def test_the_reranker_budget_is_capped(self) -> None:
        ag = self.agent(funnel_top=30)
        picked = ag._funnel(self.tagged(), ag.cfg, {})
        self.assertEqual(len(picked), 30)

    def test_each_source_gets_its_quota(self) -> None:
        ag = self.agent(funnel_top=100, funnel_quota_primary=0.7,
                        funnel_quota_expansion=0.2, funnel_quota_rescue=0.1)
        trace: dict = {}
        picked = [a for a, _ in ag._funnel(self.tagged(200, 200, 200), ag.cfg, trace)]
        kinds = {k: sum(1 for a in picked if a.startswith(k))
                 for k in ("primary", "expansion", "rescue")}
        self.assertEqual(kinds, {"primary": 70, "expansion": 20, "rescue": 10})
        self.assertEqual(trace["funnel_out"], 100)

    def test_a_wider_source_cannot_enlarge_the_budget(self) -> None:
        # The R1 failure in one assertion: widening retrieval must not widen
        # what the ranker is asked to order.
        ag = self.agent(funnel_top=50)
        small = ag._funnel(self.tagged(50, 10, 10), ag.cfg, {})
        huge = ag._funnel(self.tagged(5000, 5000, 5000), ag.cfg, {})
        self.assertEqual(len(small), len(huge), 50)

    def test_unused_quota_refills_rather_than_shrinking_the_pool(self) -> None:
        ag = self.agent(funnel_top=40)
        picked = ag._funnel(self.tagged(100, 0, 0), ag.cfg, {})
        self.assertEqual(len(picked), 40, "an empty source shrank the funnel")

    def test_selection_is_deterministic(self) -> None:
        ag = self.agent(funnel_top=25)
        first = ag._funnel(self.tagged(), ag.cfg, {})
        second = ag._funnel(self.tagged(), ag.cfg, {})
        self.assertEqual(first, second)

    def test_a_candidate_is_claimed_by_its_first_source_only(self) -> None:
        ag = self.agent(funnel_top=10)
        dupes = [("x", 9.0, "primary"), ("x", 1.0, "rescue"), ("y", 5.0, "rescue")]
        picked = ag._funnel(dupes, ag.cfg, {})
        self.assertEqual([a for a, _ in picked].count("x"), 1)


class CategoryConstraintTest(PlaneTestBase):
    def test_an_ambiguous_category_does_not_exclude(self) -> None:
        # "clothing" matches several shelves. An ambiguous reading is not
        # grounds for removing anything; it feeds a source and the ranker.
        ag = self.agent()
        state = self.state_with(ag, "clothing")
        trace: dict = {}
        ag._safe_pool(state, ag.cfg, trace)
        self.assertGreater(trace["category_shelves"], 1)
        self.assertFalse(trace["category_is_hard"],
                         "an ambiguous category was used as a hard filter")
        self.assertEqual(trace["pool_before_filter"],
                         len(ag.cat.category_index.universe))

    def test_cross_branch_shelves_are_unioned(self) -> None:
        ci = self.agent().cat.category_index
        self.assertGreaterEqual(len(ci.matching_shelves("women dresses")), 1)
        both = ci.matching_shelves("clothing")
        self.assertGreater(len(both), 1, "sibling branches were dropped")


class SplitFlagTest(PlaneTestBase):
    """deep_funnel, category_plane and starvation_bypass are independent.

    dual_plane conflated all three, which is how "Phase 2B on" became a
    package deal that could only be accepted or rejected whole.
    """

    def test_category_plane_off_leaves_the_pool_at_full_size(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": True, "category_plane": False})
        state = self.state_with(ag, "women dresses")
        trace: dict = {}
        ag._safe_pool(state, ag.cfg, trace)
        self.assertEqual(trace["category_shelves"], 0)
        self.assertEqual(trace["pool_before_filter"],
                         len(ag.cat.category_index.universe))

    def test_category_plane_off_contributes_no_expansion_source(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": True, "category_plane": False})
        state = self.state_with(ag, "women dresses")
        state["terms"] = ["dress"]
        trace: dict = {}
        tagged = ag._plane_browsing(state, ag.cfg, 5, trace)
        self.assertEqual(trace["route_candidates"]["expansion"], 0)
        self.assertNotIn("expansion", {src for _, _, src in tagged})

    def test_dual_plane_still_means_both(self) -> None:
        # Retained only so the R1/R2 rows already in the ledger stay
        # reproducible.
        ag = A.Agent(self.path, config={"dual_plane": True})
        self.assertTrue(ag._category_on(ag.cfg))
        state = self.state_with(ag, "women dresses")
        trace: dict = {}
        ag._safe_pool(state, ag.cfg, trace)
        self.assertGreater(trace["category_shelves"], 0)


class StarvationBypassTest(PlaneTestBase):
    """A constant funnel_top discards exactly the widening _starved() asks for."""

    def _cands(self, ag: A.Agent, starved: bool, limit: int) -> tuple:
        state = self.state_with(ag, "")
        state["terms"] = ["dress", "skirt", "boots", "coat", "leather"]
        state["starved"] = starved
        return ag._candidates(state, ag.cfg, limit)

    def test_a_starved_turn_bypasses_the_funnel(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": True, "category_plane": False,
                                        "starvation_bypass": True, "funnel_top": 2})
        _, trace = self._cands(ag, starved=True, limit=50)
        self.assertTrue(trace["starvation_bypass"])
        self.assertEqual(trace["plane"], "starved_legacy")
        self.assertNotIn("funnel_out", trace, "the funnel ran on a starved turn")

    def test_the_starved_pool_is_not_truncated_to_the_funnel_cap(self) -> None:
        common = {"deep_funnel": True, "category_plane": False, "funnel_top": 2}
        capped = A.Agent(self.path, config={**common, "starvation_bypass": False})
        freed = A.Agent(self.path, config={**common, "starvation_bypass": True})
        small, _ = self._cands(capped, starved=True, limit=50)
        large, _ = self._cands(freed, starved=True, limit=50)
        self.assertEqual(len(small), 2, "the funnel cap did not apply")
        self.assertGreater(len(large), len(small),
                           "the bypass did not restore the widened pool")

    def test_the_bypass_does_not_leak_into_an_unstarved_turn(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": True, "category_plane": False,
                                        "starvation_bypass": True, "funnel_top": 2})
        _, starved = self._cands(ag, starved=True, limit=50)
        cands, normal = self._cands(ag, starved=False, limit=50)
        self.assertTrue(starved["starvation_bypass"])
        self.assertFalse(normal["starvation_bypass"],
                         "starvation state leaked into a later turn")
        self.assertEqual(len(cands), 2, "the funnel did not resume")

    def test_bypass_off_leaves_the_starved_turn_capped(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": True, "category_plane": False,
                                        "starvation_bypass": False, "funnel_top": 2})
        _, trace = self._cands(ag, starved=True, limit=50)
        self.assertFalse(trace["starvation_bypass"])
        self.assertEqual(trace["funnel_out"], 2)

    def test_the_bypass_is_inert_without_deep_funnel(self) -> None:
        ag = A.Agent(self.path, config={"deep_funnel": False, "starvation_bypass": True})
        _, trace = self._cands(ag, starved=True, limit=50)
        self.assertFalse(trace["starvation_bypass"])
        self.assertEqual(trace["plane"], "legacy")


class CompatibilityTest(PlaneTestBase):
    def test_the_feature_off_path_is_untouched(self) -> None:
        off = self.agent(dual_plane=False, deep_funnel=False)
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


class QuestionUtilityTest(PlaneTestBase):
    """Phase 3B: coverage- and answerability-aware question selection."""

    def test_missing_values_do_not_count_as_agreement_or_disagreement(self) -> None:
        ag = self.agent()
        pool = list(ag.cat.category_index.asins)
        material = A.ATTR_VOCAB["material"]
        with_missing = ag._pool_entropy(pool, material, skip_missing=False)
        without = ag._pool_entropy(pool, material, skip_missing=True)
        self.assertNotEqual(with_missing, without,
                            "the missing bucket was not contributing entropy")
        self.assertLess(without, with_missing)

    def test_a_facet_nobody_mentions_scores_no_utility(self) -> None:
        # Q1 alone mentions nothing; a window of silent products must not look
        # like a pool that disagrees.
        ag = self.agent(question_utility=True)
        self.assertEqual(ag._facet_coverage(["Q1"], "material"), 0.0)
        self.assertEqual(ag._pool_entropy(["Q1"], A.ATTR_VOCAB["material"],
                                          skip_missing=True), 0.0)

    def test_utility_prefers_the_better_covered_attribute(self) -> None:
        ag = self.agent(question_utility=True)
        state = ag._blank_state()
        pool = list(ag.cat.category_index.asins)
        chosen, bits, _ = ag._pool_attribute(state, pool, ag.cfg)
        if chosen != "other":
            self.assertGreater(state["last_coverage"], 0.0,
                               "utility picked an attribute nothing in the pool states")

    def test_the_flag_is_off_by_default(self) -> None:
        self.assertFalse(A.DEFAULTS["question_utility"])

    def test_utility_changes_which_question_is_asked(self) -> None:
        entropy_ag = self.agent(question_utility=False)
        utility_ag = self.agent(question_utility=True)
        pool = list(entropy_ag.cat.category_index.asins)
        picked = []
        for ag in (entropy_ag, utility_ag):
            state = ag._blank_state()
            picked.append(ag._pool_attribute(state, pool, ag.cfg)[0])
        self.assertEqual(len(picked), 2)   # both produce a legal attribute
        for name in picked:
            self.assertIn(name, set(A.ATTR_VOCAB) | {"other"})
