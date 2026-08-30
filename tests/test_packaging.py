"""Packaging claims, made mechanical.

The README, the model card and requirements.txt all assert things about what
this repository contains and what the scored path needs. Those are the claims a
judge checks first and the ones most likely to rot silently, so each of them is
a test.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

import starter.agent as A

ARTIFACT = Path("lab/r0/artifacts/ms-marco-TinyBERT-L2-v2")
DIGESTS = Path("lab/r0/artifacts/digests.json")
MANIFEST = Path("lab/r0/artifacts/manifest.json")
MODEL_ID = "cross-encoder/ms-marco-TinyBERT-L2-v2"


class RequirementsTest(unittest.TestCase):
    """Two files, and the difference between them is the whole point."""

    def packages(self, path: str) -> list[str]:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return [l.strip() for l in lines
                if l.strip() and not l.strip().startswith("#")]

    def test_the_scored_default_needs_no_package(self) -> None:
        self.assertEqual(self.packages("requirements.txt"), [],
                         "score_default is standard library only; a package "
                         "here contradicts docs/FINAL_VERIFICATION.md")

    def test_the_semantic_extra_pins_exact_versions(self) -> None:
        pinned = self.packages("requirements-semantic.txt")
        self.assertEqual(sorted(pinned),
                         ["numpy==2.5.2", "onnxruntime==1.24.1",
                          "tokenizers==0.22.2"])
        for line in pinned:
            self.assertIn("==", line, f"{line} is not pinned")

    def test_torch_and_transformers_are_deliberately_absent(self) -> None:
        # Phase 7A-R0 measured 53.2 MB for numpy+onnxruntime+tokenizers against
        # 346.1 MB once transformers pulled torch in.
        text = Path("requirements-semantic.txt").read_text(encoding="utf-8")
        for package in ("torch", "transformers"):
            self.assertNotIn(f"\n{package}", text)


class BundledArtifactTest(unittest.TestCase):
    """The bundle is Apache-2.0, complete, and byte-identical to what was pinned."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.digests = json.loads(DIGESTS.read_text(encoding="utf-8"))[MODEL_ID]

    def test_every_pinned_file_is_present_and_matches_its_sha256(self) -> None:
        for name, expected in sorted(self.digests["sha256"].items()):
            path = ARTIFACT / name
            with self.subTest(file=name):
                self.assertTrue(path.exists(), f"{path} is missing")
                got = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(got, expected)

    def test_the_byte_counts_match_the_r0_manifest(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))[MODEL_ID]
        for name, expected in sorted(manifest["files"].items()):
            with self.subTest(file=name):
                self.assertEqual((ARTIFACT / name).stat().st_size, expected)

    def test_the_revision_is_pinned_and_consistent_everywhere(self) -> None:
        revision = self.digests["revision"]
        self.assertEqual(len(revision), 40)
        self.assertEqual(revision, A.SEMANTIC_MODEL_REVISION)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))[MODEL_ID]
        self.assertEqual(manifest["revision"], revision)
        card = Path("docs/MODEL_CARD.md").read_text(encoding="utf-8")
        self.assertIn(revision, card)

    def test_the_apache_license_ships_with_the_weights(self) -> None:
        # Apache-2.0 section 4(a): recipients get a copy of the License.
        text = (ARTIFACT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))[MODEL_ID]
        self.assertEqual(manifest["license"], "apache-2.0")

    def test_the_model_card_states_license_source_and_revision(self) -> None:
        card = Path("docs/MODEL_CARD.md").read_text(encoding="utf-8")
        for needed in ("Apache-2.0", "huggingface.co/cross-encoder/ms-marco-TinyBERT-L2-v2",
                       MODEL_ID, "not a generative LLM"):
            self.assertIn(needed, card)

    def test_the_agent_points_at_the_bundled_directory(self) -> None:
        self.assertEqual(Path(A.SEMANTIC_MODEL_DIR), ARTIFACT)
        self.assertTrue(ARTIFACT.is_dir())


class ScoredPathIndependenceTest(unittest.TestCase):
    """score_default must not need the artifact, even though it ships."""

    def test_the_default_names_no_model_directory(self) -> None:
        self.assertEqual(A.DEFAULTS["semantic_model_dir"], "")
        self.assertEqual(A.DEFAULTS["semantic_rerank_mode"], "off")

    def test_an_absent_artifact_cannot_affect_the_scored_path(self) -> None:
        # The cascade is not merely off; at mode "off" `reorder` returns before
        # it looks at any directory at all.
        from starter import semantic as SEM
        ordered = ["A", "B", "C"]
        got, reason, k = SEM.reorder(
            ordered, cat=None, cfg=dict(A.DEFAULTS), state={}, top_k=10,
            scorer_for=lambda *a: (_ for _ in ()).throw(
                AssertionError("the scored path tried to load a model")))
        self.assertEqual(got, ordered)
        self.assertEqual(reason, SEM.REASON_MODE_OFF)


class ReproductionCommandTest(unittest.TestCase):
    """The commands the docs hand a judge have to be real."""

    def test_the_evaluator_exposes_the_documented_flags(self) -> None:
        source = Path("evaluator/local_evaluator.py").read_text(encoding="utf-8")
        for flag in ("--catalog", "--dataset", "--output"):
            self.assertIn(flag, source)
        self.assertIn('if __name__ == "__main__"', source)

    def test_the_model_card_reproduction_command_names_real_paths(self) -> None:
        card = Path("docs/MODEL_CARD.md").read_text(encoding="utf-8")
        command = next(l for l in card.splitlines()
                       if "local_evaluator" in l and "--catalog" in l)
        for path in re.findall(r"data/[\w.]+", command):
            self.assertIn(path, ("data/catalog.jsonl", "data/public_set.jsonl"))
        self.assertIn("requirements-semantic.txt", card)


if __name__ == "__main__":
    unittest.main()
