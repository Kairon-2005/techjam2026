"""The shared catalog cache must never close an object a live Agent still holds.

THE DEFECT. `_catalog()` was keyed on the resolved path alone, and a request
for richer fields SUPERSEDED the cached entry -- closing its SQLite connection
on the way out. So constructing a semantic-on A2 Agent pulled the connection out
from under an A0 Agent that was still live, still holding the old object, and
had done nothing wrong. The A0 Agent then raised on its next query.

This is a shared-cache lifetime defect, not a test artefact: nothing in the A0
Agent's own code could have prevented it, and in Phase 7A-R1 the A0 and A2 arms
are constructed against the same catalog by design.

THE FIX. The key carries the capability -- (resolved path, extras,
semantic_fields) -- so capability versions coexist, and nothing is closed here
at all. `clear_catalog_cache()` is the single place that closes anything.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import starter.agent as A
from starter import catalog as C
from tests.test_indexes import _catalog_file

MESSAGE = "I'm looking for Clothing Women Dresses, but I'm still exploring."
SEMANTIC_ON = {"semantic_rerank_mode": "on", "semantic_rerank_k": 10,
               "semantic_lambda": 0.25}


class CatalogCacheLifetimeTest(unittest.TestCase):
    def setUp(self) -> None:
        A.clear_catalog_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(A.clear_catalog_cache)
        self.path = _catalog_file(Path(self._tmp.name))

    def responds(self, agent, session: str) -> None:
        """A full turn, which is what actually touches the SQLite handle."""
        agent.reset(session, {})
        out = agent.respond(session, MESSAGE, 1, 10)
        self.assertIsInstance(out, dict)
        self.assertIsInstance(out.get("recommendations"), list)

    # ---- the four orderings the review asked for ----------------------
    def test_a0_then_a2_both_still_respond(self) -> None:
        a0 = A.Agent(self.path)
        a2 = A.Agent(self.path, config=dict(SEMANTIC_ON))
        self.responds(a0, "a0")
        self.responds(a2, "a2")
        # And again, so a lazily-built index cannot mask a closed connection.
        self.responds(a0, "a0-again")

    def test_a2_then_a0_both_still_respond(self) -> None:
        a2 = A.Agent(self.path, config=dict(SEMANTIC_ON))
        a0 = A.Agent(self.path)
        self.responds(a2, "a2")
        self.responds(a0, "a0")
        self.responds(a2, "a2-again")

    def test_the_older_agent_survives_the_newer_agents_construction(self) -> None:
        # The exact failure: A0 is constructed and USED, then A2 is constructed
        # against the same catalog, and A0 is used again.
        a0 = A.Agent(self.path)
        self.responds(a0, "before")
        A.Agent(self.path, config=dict(SEMANTIC_ON))
        self.responds(a0, "after")

    def test_a_lean_agent_survives_an_extras_upgrade(self) -> None:
        # `build_extras` is the config key; w_pos/w_card imply it when unset.
        lean = A.Agent(self.path, config={"build_extras": False})
        self.responds(lean, "lean")
        A.Agent(self.path, config={"build_extras": True})
        self.responds(lean, "lean-after")

    # ---- what the cache is now allowed and not allowed to do ----------
    def test_the_key_carries_the_capability(self) -> None:
        C.clear_catalog_cache()
        C._catalog(self.path, extras=True, semantic_fields=False)
        C._catalog(self.path, extras=True, semantic_fields=True)
        keys = sorted(C._CATALOG_CACHE)
        self.assertEqual([k[1:] for k in keys], [(True, False), (True, True)])
        self.assertEqual({k[0] for k in keys}, {str(Path(self.path).resolve())})

    def test_nothing_cached_is_closed_by_a_richer_request(self) -> None:
        C.clear_catalog_cache()
        lean = C._catalog(self.path, extras=True, semantic_fields=False)
        C._catalog(self.path, extras=True, semantic_fields=True)
        # The connection is the thing that used to be closed. Querying it is
        # the only honest way to assert it is still open.
        self.assertEqual(lean.conn.execute("SELECT 1").fetchone()[0], 1)

    def test_a_superset_still_answers_a_leaner_request(self) -> None:
        # The reuse that made the old key worth having is kept: a 17.9 MB
        # semantic build must not be duplicated to serve a lean caller.
        C.clear_catalog_cache()
        rich = C._catalog(self.path, extras=True, semantic_fields=True)
        self.assertIs(C._catalog(self.path, extras=True, semantic_fields=False), rich)
        self.assertIs(C._catalog(self.path, extras=False, semantic_fields=False), rich)
        self.assertEqual(len(C._CATALOG_CACHE), 1)

    def test_a_lean_catalog_never_answers_a_semantic_request(self) -> None:
        # Reusing a lean build for a semantic one would leave sem_desc empty
        # and silently serialize products without their descriptions.
        C.clear_catalog_cache()
        lean = C._catalog(self.path, extras=True, semantic_fields=False)
        rich = C._catalog(self.path, extras=True, semantic_fields=True)
        self.assertIsNot(rich, lean)
        self.assertTrue(rich.semantic_fields)
        self.assertFalse(lean.semantic_fields)

    def test_an_identical_request_is_the_same_object(self) -> None:
        C.clear_catalog_cache()
        first = C._catalog(self.path, extras=True, semantic_fields=True)
        self.assertIs(C._catalog(self.path, extras=True, semantic_fields=True), first)

    def test_clear_catalog_cache_is_the_only_place_that_closes(self) -> None:
        source = Path("starter/catalog.py").read_text(encoding="utf-8")
        body = source.split("def _catalog(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn(".close()", body,
                         "_catalog() closed something; only clear_catalog_cache() may")

    def test_clear_catalog_cache_closes_every_capability_version(self) -> None:
        C.clear_catalog_cache()
        lean = C._catalog(self.path, extras=True, semantic_fields=False)
        rich = C._catalog(self.path, extras=True, semantic_fields=True)
        C.clear_catalog_cache()
        self.assertEqual(C._CATALOG_CACHE, {})
        for cat in (lean, rich):
            with self.assertRaises(Exception):
                cat.conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
