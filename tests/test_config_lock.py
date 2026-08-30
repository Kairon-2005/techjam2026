"""The final configuration lock. Phase 7 is closed; this is what closed means.

`notes/46` freezes `score_default` and forbids behavioural change. A frozen
default that is only frozen in prose drifts the first time someone flips a
convenient switch, so every claim the submission makes about its default
configuration is asserted here, by name and by value.

These tests are deliberately blunt. They do not check that the agent is GOOD --
tests/test_score_regression.py locks the score -- they check that it is the
CONFIGURATION whose score was measured, and that the optional capabilities the
write-up describes as off are actually off.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import starter.agent as A

# notes/46 section 1. The numbers the submission quotes, in one place.
PUBLIC_SCORE = 0.932067
PUBLIC_HR10 = 0.995
PUBLIC_MRR = 0.852556
PUBLIC_MTTC = 2.06
COMPAT_ANCHOR = 0.928708

# Every key whose value the write-up asserts. Read as: "the scored default is
# exactly this, and any change to it invalidates the number."
FROZEN_DEFAULT = {
    # --- the pipeline that IS score_default ---
    "ask_policy": "other_then_pool",
    "deep_funnel": True,
    "starvation_bypass": True,
    "rerank": True,
    "on_override": "keep",
    "retrieval_context_mode": "control",
    "question_context_mode": "control",
    # --- the nine ranking weights A1 tried and failed to improve ---
    "w_bm25": 0.3,
    "w_cat": 1.0,
    "w_exact": 1.5,
    "w_field": 2.0,
    "w_idf": 0.25,
    "w_neg": 2.0,
    "w_phrase": 5.0,
    "w_pop": 4.0,
    "slot_soft": 4.0,
    # --- implemented, measured, and OFF ---
    "semantic_rerank_mode": "off",
    "semantic_lambda": 0.0,
    "semantic_model_dir": "",
    "dense_browsing": False,
    "dense_mixed": False,
    "profile_context_mode": "off",
    "w_profile": 0.0,
    "w_profile_adaptive": 0.0,
    # --- weights excluded from the A1 search because they are already zero ---
    "w_pos": 0.0,
    "w_card": 0.0,
    "build_extras": None,
}


class FrozenDefaultTest(unittest.TestCase):
    """score_default, key by key."""

    def test_every_frozen_key_holds_its_value(self) -> None:
        for name, expected in FROZEN_DEFAULT.items():
            with self.subTest(key=name):
                self.assertIn(name, A.DEFAULTS, f"{name} vanished from DEFAULTS")
                self.assertEqual(A.DEFAULTS[name], expected)

    def test_score_default_is_the_empty_profile(self) -> None:
        # A profile that patched DEFAULTS would mean the scored configuration
        # is not the one a bare Agent() constructs.
        self.assertEqual(A.PROFILES["score_default"], {})

    def test_a_bare_agent_resolves_to_the_frozen_default(self) -> None:
        resolved = A._load_config(None)
        for name, expected in FROZEN_DEFAULT.items():
            with self.subTest(key=name):
                self.assertEqual(resolved[name], expected)


class OptionalCapabilitiesAreOffTest(unittest.TestCase):
    """The three implemented-but-off capabilities, each checked as OFF."""

    def test_the_semantic_cascade_is_off_and_needs_no_artifact(self) -> None:
        # score_default must never depend on the model file existing: an absent
        # artifact is a configuration fact for the showcase, and must be a
        # NON-EVENT for the scored path.
        self.assertEqual(A.DEFAULTS["semantic_rerank_mode"], "off")
        self.assertEqual(A.DEFAULTS["semantic_model_dir"], "")
        self.assertEqual(A.DEFAULTS["semantic_lambda"], 0.0)

    def test_dense_retrieval_is_off_on_every_plane(self) -> None:
        self.assertFalse(A.DEFAULTS["dense_browsing"])
        self.assertFalse(A.DEFAULTS["dense_mixed"])

    def test_the_profile_ranking_weight_is_zero(self) -> None:
        # Phase 6C1 found no demonstrated target alignment. Both the fixed and
        # the adaptive weight stay at zero, and the profile controller is off.
        self.assertEqual(A.DEFAULTS["w_profile"], 0.0)
        self.assertEqual(A.DEFAULTS["w_profile_adaptive"], 0.0)
        self.assertEqual(A.DEFAULTS["profile_context_mode"], "off")


class ContextControllersAreOnTest(unittest.TestCase):
    """The two Context Programming controllers ARE the default, not a showcase."""

    def test_both_controllers_are_in_control_mode(self) -> None:
        self.assertEqual(A.DEFAULTS["retrieval_context_mode"], "control")
        self.assertEqual(A.DEFAULTS["question_context_mode"], "control")

    def test_control_is_not_shadow(self) -> None:
        # "shadow" computes the decision and mutates nothing. Shipping shadow
        # while claiming the controller drives the pipeline would be the exact
        # overclaim Phase 6B1 was written to prevent.
        for name in ("retrieval_context_mode", "question_context_mode"):
            self.assertNotEqual(A.DEFAULTS[name], "shadow", name)
            self.assertNotEqual(A.DEFAULTS[name], "off", name)


class ProfileSeparationTest(unittest.TestCase):
    """Three profiles, disjoint, and no unmeasured combination among them."""

    def test_exactly_three_profiles_exist(self) -> None:
        self.assertEqual(sorted(A.PROFILES),
                         ["score_default", "showcase_dense", "showcase_semantic"])

    def test_showcase_dense_turns_on_dense_and_nothing_else(self) -> None:
        patch = A.PROFILES["showcase_dense"]
        self.assertTrue(patch["dense_browsing"] and patch["dense_mixed"])
        self.assertNotIn("semantic_rerank_mode", patch)
        self.assertNotIn("w_profile", patch)

    def test_showcase_semantic_turns_on_semantic_and_nothing_else(self) -> None:
        patch = A.PROFILES["showcase_semantic"]
        self.assertEqual(patch["semantic_rerank_mode"], "on")
        self.assertEqual(patch["semantic_rerank_k"], 10)
        self.assertEqual(patch["semantic_lambda"], 1.0)
        self.assertNotIn("dense_browsing", patch)
        self.assertNotIn("w_profile", patch)

    def test_no_profile_combines_two_showcase_capabilities(self) -> None:
        # A combined dense + semantic + personalization configuration was NEVER
        # measured, and a named label over an unmeasured combination is a claim
        # about it. There is no such label, by rule.
        for name in A.SHOWCASE_PROFILES:
            patch = A.PROFILES[name]
            dense = bool(patch.get("dense_browsing") or patch.get("dense_mixed"))
            semantic = patch.get("semantic_rerank_mode") == "on"
            profile = bool(patch.get("w_profile") or patch.get("w_profile_adaptive"))
            self.assertEqual(sum((dense, semantic, profile)), 1,
                             f"{name} enables more than one showcase capability")

    def test_every_profile_key_is_a_real_config_key(self) -> None:
        # A profile setting a key the agent does not read is a silently void
        # demonstration -- the same defect lab/sweep.py's `"route": false` was.
        for name, patch in A.PROFILES.items():
            for key in patch:
                self.assertIn(key, A.DEFAULTS, f"{name} sets unknown key {key}")

    def test_the_pinned_artifact_identity_lives_in_one_place(self) -> None:
        self.assertEqual(A.PROFILES["showcase_semantic"]["semantic_model_dir"],
                         A.SEMANTIC_MODEL_DIR)
        self.assertEqual(A.SEMANTIC_MODEL_ID,
                         "cross-encoder/ms-marco-TinyBERT-L2-v2")
        self.assertEqual(len(A.SEMANTIC_MODEL_REVISION), 40)


class StandardLibraryOnlyTest(unittest.TestCase):
    """The scored path imports nothing that is not in the standard library."""

    THIRD_PARTY = ("numpy", "onnxruntime", "tokenizers", "torch", "transformers",
                   "scipy", "sklearn", "faiss", "pandas", "requests")

    def test_the_scored_modules_import_no_third_party_package(self) -> None:
        # A source-level check, because an import that only fires on a branch
        # nobody took in this process would pass a runtime check.
        for name in ("agent", "retrieval", "dialogue", "context", "evidence",
                     "catalog", "semantic"):
            source = Path(f"starter/{name}.py").read_text(encoding="utf-8")
            top_level = [line for line in source.splitlines()
                         if line.startswith(("import ", "from "))]
            for line in top_level:
                for package in self.THIRD_PARTY:
                    self.assertNotIn(f" {package}", line,
                                     f"starter/{name}.py imports {package} at "
                                     f"module level: {line}")

    def test_third_party_imports_live_inside_the_semantic_loader(self) -> None:
        # starter/semantic.py MAY import onnxruntime -- inside Scorer.load,
        # which score_default never reaches. That is the whole design: the
        # dependency exists for the showcase and is unreachable by the default.
        source = Path("starter/semantic.py").read_text(encoding="utf-8")
        load = source.split("def load(", 1)[1].split("\n    def ", 1)[0]
        for package in ("numpy", "onnxruntime", "tokenizers"):
            self.assertIn(package, load,
                          f"{package} is not imported inside Scorer.load")

    def test_the_evaluator_needs_no_third_party_package(self) -> None:
        source = Path("evaluator/local_evaluator.py").read_text(encoding="utf-8")
        for package in self.THIRD_PARTY:
            self.assertNotIn(f"import {package}", source)


class QuotedNumbersTest(unittest.TestCase):
    """The numbers notes/46 fixes, kept next to the configuration they describe."""

    def test_the_public_numbers_match_the_score_regression_lock(self) -> None:
        from tests import test_score_regression as SR
        self.assertEqual(SR.EXPECTED["score"], PUBLIC_SCORE)
        self.assertEqual(SR.EXPECTED["hr10"], PUBLIC_HR10)
        self.assertEqual(SR.EXPECTED["mrr"], PUBLIC_MRR)
        self.assertEqual(SR.EXPECTED["mttc"], PUBLIC_MTTC)
        self.assertEqual(SR.EXPECTED_ASK_OTHER["score"], COMPAT_ANCHOR)

    def test_the_closure_note_quotes_the_same_numbers(self) -> None:
        note = Path("notes/46-phase7-closure.md").read_text(encoding="utf-8")
        for value in (PUBLIC_SCORE, PUBLIC_HR10, PUBLIC_MRR, PUBLIC_MTTC,
                      COMPAT_ANCHOR):
            self.assertIn(str(value), note, f"{value} is not in notes/46")


if __name__ == "__main__":
    unittest.main()
