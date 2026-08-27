"""Module boundaries introduced by the Phase 5B split.

These lock the contract the split had to preserve, and the one property it
deliberately does NOT claim: the import graph is acyclic, the domain graph is
not. See notes/25-phase5b-design.md.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path("starter")
CONTRACT = [
    "Agent", "clear_catalog_cache", "SlotValue", "parse_message", "Outcome",
    "open_world_evidence", "DEFAULTS", "hardness_of", "classify_reply",
    "ATTR_VOCAB", "_catalog", "is_override", "relaxation_order",
    "abandoned_span", "_load_config", "slot_of", "_norm", "TOKEN_RE",
    "SLOT_RES", "terms_of",
]


def _imports(module: str) -> set[str]:
    tree = ast.parse((SRC / f"{module}.py").read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("starter"):
            out.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            out.update(a.name.split(".")[-1] for a in node.names if a.name.startswith("starter"))
    return out


class ContractTest(unittest.TestCase):
    def test_every_public_name_still_resolves_from_starter_agent(self) -> None:
        import starter.agent as A
        missing = [n for n in CONTRACT if not hasattr(A, n)]
        self.assertEqual(missing, [], "the split dropped part of the public contract")

    def test_the_default_is_unchanged_by_the_split(self) -> None:
        import starter.agent as A
        self.assertTrue(A.DEFAULTS["deep_funnel"])
        self.assertTrue(A.DEFAULTS["starvation_bypass"])
        self.assertTrue(A.DEFAULTS["question_utility"])
        self.assertFalse(A.DEFAULTS["category_plane"])
        self.assertFalse(A.DEFAULTS["dense_browsing"])
        self.assertFalse(A.DEFAULTS["dense_mixed"])

    def test_profiles_are_labels_and_change_nothing(self) -> None:
        import starter.agent as A
        self.assertEqual(A.PROFILES["score_default"], {})
        self.assertFalse(A.DEFAULTS["dense_browsing"],
                         "naming a showcase profile must not arm dense")
        self.assertEqual(set(A.PROFILES["showcase_dense"]),
                         {"dense_browsing", "dense_mixed", "dense_fusion"})

    def test_the_agent_composes_both_mixins(self) -> None:
        import starter.agent as A
        self.assertEqual([c.__name__ for c in A.Agent.__mro__],
                         ["Agent", "RetrievalMixin", "DialogueMixin", "object"])


class ImportGraphTest(unittest.TestCase):
    def test_evidence_is_a_leaf(self) -> None:
        self.assertEqual(_imports("evidence"), set())

    def test_catalog_depends_only_on_evidence(self) -> None:
        self.assertEqual(_imports("catalog"), {"evidence"})

    def test_the_two_mixins_never_import_each_other(self) -> None:
        # The constraint that makes the split safe: they call across through
        # `self`, so neither module can pull the other in.
        self.assertNotIn("retrieval", _imports("dialogue"))
        self.assertNotIn("dialogue", _imports("retrieval"))

    def test_evidence_imports_without_a_catalog_present(self) -> None:
        # A true leaf must load with no data files and nothing else from the
        # package already imported.
        code = "import starter.evidence as E; assert E.slot_of('silk')"
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class DomainGraphTest(unittest.TestCase):
    """The split does not claim the domain dependencies are gone."""

    def test_the_bidirectional_calls_still_exist_and_are_declared(self) -> None:
        dialogue = (SRC / "dialogue.py").read_text()
        retrieval = (SRC / "retrieval.py").read_text()
        # dialogue -> retrieval, and retrieval -> dialogue, both live.
        self.assertIn("self._route_cfg", dialogue)
        self.assertIn("self._uncredible", retrieval)
        # ...and both headers say so, so the edges cannot go quiet.
        self.assertIn("RetrievalMixin", dialogue.split('"""')[1])
        self.assertIn("DialogueMixin", retrieval.split('"""')[1])

    def test_each_mixin_documents_its_host_capabilities(self) -> None:
        for module in ("dialogue", "retrieval"):
            head = (SRC / f"{module}.py").read_text().split('"""')[1]
            self.assertIn("HOST CAPABILITIES REQUIRED", head, module)
            self.assertIn("self.cfg", head, module)
            self.assertIn("self.cat", head, module)


if __name__ == "__main__":
    unittest.main()
