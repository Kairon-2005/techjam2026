"""CategoryIndex and FacetIndex: lookup, expansion, coverage, missingness.

The governing constraint is that this catalog's structured attributes are too
sparse to filter on -- details.Color is present on 4.9% of products, Material
on 4.1%, Size on 1.9% -- so facet values are read out of product text, and
every filter is presence-aware. A product that never mentions a material is
not a product that lacks one, and these tests exist to keep that distinction
from eroding.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import starter.agent as A

# Two departments, three shelves under Women, one under Men, so parent
# expansion has real siblings to find.
PRODUCTS = [
    {"parent_asin": "W1", "title": "Silk Wrap Dress",
     "categories": ["Clothing", "Women", "Dresses"],
     "features": ["100% silk", "blue"], "details": {"Material": "silk", "Department": "womens"},
     "store": "Aurelia", "description": "A blue silk dress for formal wear",
     "average_rating": 4.4, "rating_number": 900, "price": 80.0},
    {"parent_asin": "W2", "title": "Cotton Midi Skirt",
     "categories": ["Clothing", "Women", "Skirts"],
     "features": ["cotton", "black"], "details": {"Department": "womens"},
     "store": "Aurelia", "description": "A black cotton skirt, casual",
     "average_rating": 4.1, "rating_number": 400, "price": 40.0},
    {"parent_asin": "W3", "title": "Leather Ankle Boots",
     "categories": ["Clothing", "Women", "Shoes", "Boots"],
     "features": ["genuine leather", "black"], "details": {"Department": "womens"},
     "store": "Bexley", "description": "Black leather boots for hiking",
     "average_rating": 4.8, "rating_number": 5000, "price": 150.0},
    {"parent_asin": "M1", "title": "Wool Overcoat",
     "categories": ["Clothing", "Men", "Coats"],
     "features": ["wool"], "details": {"Department": "mens"},
     "store": "Bexley", "description": "A warm wool coat for winter",
     "average_rating": 4.6, "rating_number": 2000, "price": 300.0},
    # Says nothing about material or colour anywhere. The presence-aware
    # filter must never exclude it for a material it did not mention.
    {"parent_asin": "Q1", "title": "Mystery Item",
     "categories": ["Clothing", "Women", "Dresses"],
     "features": [], "details": {}, "store": "",
     "description": "An item", "average_rating": 3.0, "rating_number": 5},
]


def _catalog_file(tmp: Path) -> str:
    path = tmp / "catalog.jsonl"
    path.write_text("\n".join(json.dumps(p) for p in PRODUCTS), encoding="utf-8")
    return str(path)


class IndexTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cat = A._catalog(_catalog_file(Path(cls._tmp.name)))
        cls.ci = cls.cat.category_index
        cls.fi = cls.cat.facet_index

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def asins(self, ids) -> set:
        return {self.ci.asins[i] for i in ids}


class CategoryIndexTest(IndexTestBase):
    def test_exact_lookup_finds_the_deepest_matching_shelf(self) -> None:
        node = self.ci.lookup("women dresses")
        self.assertEqual(node, ("clothing", "women", "dresses"))
        self.assertEqual(self.asins(self.ci.members(node)), {"W1", "Q1"})

    def test_parent_expansion_brings_in_siblings(self) -> None:
        node = self.ci.lookup("women dresses")
        near = self.asins(self.ci.expand(node, up=1, down=0))
        self.assertIn("W2", near, "one level up from dresses must reach skirts")
        self.assertNotIn("M1", near, "expansion must not cross into menswear")

    def test_child_expansion_reaches_deeper_shelves(self) -> None:
        node = self.ci.lookup("women shoes")
        self.assertEqual(node, ("clothing", "women", "shoes"))
        self.assertIn("W3", self.asins(self.ci.expand(node, up=0, down=1)))

    def test_products_attach_at_every_level_not_only_the_leaf(self) -> None:
        self.assertIn("W3", self.asins(self.ci.members(("clothing", "women"))))

    def test_an_unknown_category_returns_none_not_an_empty_shelf(self) -> None:
        # The distinction that matters: None lets the caller drop the category
        # constraint, an empty set would silently empty the candidate pool.
        for text in ("scuba regulators", "", "   ", "zzz"):
            self.assertIsNone(self.ci.lookup(text), text)
        self.assertEqual(self.ci.members(None), frozenset())
        self.assertEqual(self.ci.expand(None), frozenset())

    def test_coverage_describes_the_spread_of_a_candidate_set(self) -> None:
        every = self.ci.coverage(range(len(self.ci.asins)))
        self.assertEqual(every["categories"], 4)
        self.assertGreater(every["entropy"], 0.0)
        one = self.ci.coverage([self.ci.ids["M1"]])
        self.assertEqual(one["categories"], 1)
        self.assertEqual(one["entropy"], 0.0)
        self.assertEqual(one["top_share"], 1.0)
        self.assertEqual(self.ci.coverage([])["categories"], 0)


class FacetIndexTest(IndexTestBase):
    def test_values_are_read_from_text_and_normalised(self) -> None:
        self.assertEqual(self.asins(self.fi.match("material", "silk")), {"W1"})
        self.assertEqual(self.asins(self.fi.match("material", "wool")), {"M1"})

    def test_a_stated_phrase_matches_the_indexed_value_inside_it(self) -> None:
        # The customer says "genuine leather"; the catalog indexes "leather".
        self.assertEqual(self.asins(self.fi.match("material", "genuine leather")), {"W3"})

    def test_coverage_and_missingness_are_reported_per_facet(self) -> None:
        self.assertAlmostEqual(self.fi.coverage["material"], 4 / 5)
        self.assertAlmostEqual(self.fi.missing_rate("material"), 1 / 5)
        self.assertAlmostEqual(self.fi.coverage["brand"], 4 / 5)

    def test_a_low_coverage_facet_is_refused_as_a_hard_filter(self) -> None:
        self.assertTrue(self.fi.hard_ok("material", 0.5))
        self.assertFalse(self.fi.hard_ok("material", 0.95))

    def test_silence_is_not_refusal(self) -> None:
        # Q1 mentions no material. Filtering for silk must keep it as a
        # possibility rather than excluding it for having said nothing.
        universe = self.ci.universe
        kept = self.asins(self.fi.safe_keep("material", "silk", universe))
        self.assertIn("W1", kept, "the match must survive")
        self.assertIn("Q1", kept, "a product with no material must not be excluded")
        self.assertNotIn("M1", kept, "a product that says wool must be excluded")

    def test_an_unknown_facet_or_value_never_empties_the_pool(self) -> None:
        universe = self.ci.universe
        self.assertEqual(self.fi.match("material", "adamantium"), frozenset())
        self.assertEqual(self.fi.match("nonexistent_facet", "x"), frozenset())
        # No product claims adamantium, so every product is still consistent.
        self.assertEqual(self.fi.safe_keep("material", "adamantium", universe),
                         frozenset(universe - self.fi.present["material"]))
        self.assertEqual(self.fi.safe_keep("nonexistent_facet", "x", universe), universe)

    def test_index_stats_reports_shape_without_running_a_query(self) -> None:
        stats = self.cat.index_stats()
        self.assertEqual(stats["category_nodes"], len(self.ci.node))
        self.assertIn("material", stats["facets"])
        self.assertIn("coverage", stats["facets"]["material"])


class IndexReuseTest(unittest.TestCase):
    def test_the_indexes_are_built_once_and_shared(self) -> None:
        A.clear_catalog_cache()
        tmp = tempfile.TemporaryDirectory()
        try:
            path = _catalog_file(Path(tmp.name))
            cat = A._catalog(path)
            self.assertIsNone(cat._cat_index, "indexes must be lazy, not built eagerly")
            first = cat.category_index
            self.assertIs(cat.category_index, first, "rebuilt instead of reused")
            self.assertIs(A._catalog(path).facet_index, cat.facet_index)
        finally:
            A.clear_catalog_cache()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()


class LazyFacetBuildTest(IndexTestBase):
    """Facets are built per attribute, on first use, and never eagerly.

    Building all seven eagerly cost 6.19 s on the first turn that stated a
    constraint -- in the shipped default, where category_plane is off and most
    of those facets are never consulted.
    """

    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp2 = tempfile.TemporaryDirectory()
        self.cat2 = A._catalog(_catalog_file(Path(self._tmp2.name)))

    def tearDown(self) -> None:
        A.clear_catalog_cache()
        self._tmp2.cleanup()

    def test_constructing_the_index_scans_nothing(self) -> None:
        self.assertEqual(self.cat2.facet_index.built, frozenset())

    def test_asking_whether_an_attribute_is_a_facet_builds_nothing(self) -> None:
        # The eligibility gate asks this for EVERY slot, including budget and
        # feature. It must not cost a catalog scan to answer.
        fi = self.cat2.facet_index
        self.assertIn("material", fi.coverage)
        self.assertNotIn("budget", fi.coverage)
        self.assertNotIn("feature", fi.coverage)
        self.assertEqual(fi.built, frozenset())

    def test_reading_one_facet_builds_only_that_one(self) -> None:
        fi = self.cat2.facet_index
        fi.coverage["material"]
        self.assertEqual(fi.built, frozenset({"material"}))
        fi.match("color", "black")
        self.assertEqual(fi.built, frozenset({"material", "color"}))

    def test_lazy_values_match_the_eager_ones(self) -> None:
        # Same data, same vocabularies, built later. Every value must agree.
        fi = self.cat2.facet_index
        self.assertAlmostEqual(fi.coverage["material"], 4 / 5)
        self.assertEqual(self.asins2(fi.match("material", "silk")), {"W1"})
        self.assertEqual(self.asins2(fi.match("material", "genuine leather")), {"W3"})
        universe = self.cat2.category_index.universe
        kept = self.asins2(fi.safe_keep("material", "silk", universe))
        self.assertIn("W1", kept)
        self.assertIn("Q1", kept, "silence is still not refusal")
        self.assertNotIn("M1", kept)

    def test_an_unknown_facet_never_builds_and_never_empties(self) -> None:
        fi = self.cat2.facet_index
        universe = self.cat2.category_index.universe
        self.assertEqual(fi.safe_keep("nonexistent", "x", universe), universe)
        self.assertEqual(fi.coverage.get("nonexistent"), 0.0)
        self.assertEqual(fi.built, frozenset())

    def asins2(self, ids) -> set:
        return {self.cat2.category_index.asins[i] for i in ids}
