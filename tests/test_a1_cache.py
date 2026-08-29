"""Phase 7A-R1: the cache schema allowlist and the A0 replay gate.

A cache that dropped a feature, mis-mapped a weight or recorded candidates in a
different order would still produce a plausible MRR and would send the entire
search chasing an artefact. These are the checks that make that impossible to
miss, and they run before the first trial.
"""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

import starter.agent as A
from lab import a1cache as CACHE
from lab import a1search as S
from starter.evidence import SlotValue
from tests.test_indexes import _catalog_file

FEATURES = {k: 0.0 for k in CACHE.FEATURE_KEYS}


def vec(**kw) -> dict:
    return {**FEATURES, **kw}


def turn(n: int = 0, cands=("a", "b"), feats=None) -> dict:
    return {"turn": n, "candidates": list(cands), "target": "a",
            "features": list(feats or [vec(f_bm25=1.0), vec()])}


def session(*turns_, sample_id="s1") -> dict:
    return {"sample_id": sample_id, "scenario": "supplementary_dev",
            "turns": list(turns_ or [turn()])}


class SchemaAllowlistTest(unittest.TestCase):
    """An allowlist, not a grep for "score"."""

    def test_a_well_formed_cache_passes(self) -> None:
        self.assertEqual(CACHE.validate_schema([session()]), [])

    def test_an_unexpected_session_key_is_rejected(self) -> None:
        bad = {**session(), "notes": "hello"}
        self.assertTrue(any("unexpected keys" in p
                            for p in CACHE.validate_schema([bad])))

    def test_an_unexpected_turn_key_is_rejected(self) -> None:
        s = session({**turn(), "extra": 1})
        self.assertTrue(any("unexpected keys" in p
                            for p in CACHE.validate_schema([s])))

    def test_derived_result_fields_are_rejected_by_name(self) -> None:
        for field in ("score", "total", "rank", "a0_score", "order"):
            s = session({**turn(), field: 1})
            problems = CACHE.validate_schema([s])
            with self.subTest(field=field):
                self.assertTrue(problems, f"{field} was accepted")

    def test_a_missing_required_turn_field_is_rejected(self) -> None:
        for field in ("turn", "candidates", "features", "target"):
            t = turn()
            t.pop(field)
            problems = CACHE.validate_schema([session(t)])
            with self.subTest(field=field):
                self.assertTrue(any("missing" in p or "unexpected" in p
                                    for p in problems))

    def test_candidate_and_vector_counts_must_match(self) -> None:
        s = session(turn(cands=("a", "b", "c")))
        self.assertTrue(any("vectors" in p for p in CACHE.validate_schema([s])))

    def test_duplicate_candidates_are_rejected(self) -> None:
        s = session(turn(cands=("a", "a")))
        self.assertTrue(any("duplicate" in p for p in CACHE.validate_schema([s])))

    def test_feature_keys_must_equal_the_preregistered_set(self) -> None:
        short = dict(FEATURES)
        short.pop("f_slot")
        s = session(turn(feats=[short, dict(FEATURES)]))
        self.assertTrue(any("feature keys" in p for p in CACHE.validate_schema([s])))

        extra = {**FEATURES, "f_invented": 1.0}
        s2 = session(turn(feats=[extra, dict(FEATURES)]))
        self.assertTrue(any("feature keys" in p for p in CACHE.validate_schema([s2])))

    def test_the_feature_set_is_the_thirteen_rerank_features(self) -> None:
        self.assertEqual(CACHE.FEATURE_KEYS, {
            "f_bm25", "f_phrase", "f_idf", "f_cat", "f_pop", "f_exact",
            "f_field", "f_pos", "f_card", "f_soft", "f_slot", "f_profile",
            "f_neg"})

    def test_every_searched_weight_maps_to_a_cached_feature(self) -> None:
        for name in S.SEARCH_WEIGHTS:
            self.assertIn(S.FEATURE_OF[name], CACHE.FEATURE_KEYS, name)

    def test_a_malformed_cache_is_not_written(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cache.jsonl"
        got = CACHE.write([session({**turn(), "score": 1.0})], path)
        self.assertFalse(got["ok"])
        self.assertFalse(path.exists(), "a malformed cache reached disk")


class ReplayGateTest(unittest.TestCase):
    """Default-weight replay must reproduce A0's FULL order, not its Top-10."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def build(self, turns: int = 1):
        """Real turns, captured through the driver's own hook on `_rerank`."""
        ag = A.Agent(self.path)
        ag.reset("s", {})
        cap = CACHE.Capture()
        messages = ["I'm looking for Clothing Women Dresses, but I'm still exploring.",
                    "For that, what matters is: silk; color: black."]
        with CACHE.capturing(ag, cap):
            for turn in range(1, turns + 1):
                cap.key = ("s", turn)
                ag.respond("s", messages[turn - 1], turn, 10)
            cap.key = None
        turn_rows = []
        for turn in range(1, turns + 1):
            row = dict(cap.rows[("s", turn)])
            row["target"] = row["candidates"][0]
            turn_rows.append(row)
        sessions = [{"sample_id": "s", "scenario": "clean", "turns": turn_rows}]
        return ag, sessions, cap

    def test_the_collect_hook_records_every_candidate(self) -> None:
        _, sessions, cap = self.build()
        snap = cap.snapshots[("s", 1)]
        self.assertEqual(len(sessions[0]["turns"][0]["candidates"]), len(snap.cands))

    def test_the_hook_is_removed_when_the_block_exits(self) -> None:
        ag, _, _ = self.build()
        self.assertNotIn("_rerank", vars(ag),
                         "the instance attribute shadowing _rerank outlived the block")

    def test_the_cache_schema_is_valid(self) -> None:
        _, sessions, _ = self.build()
        self.assertEqual(CACHE.validate_schema(sessions), [])

    def test_default_weight_replay_reproduces_the_full_a0_order(self) -> None:
        ag, sessions, cap = self.build()
        got = CACHE.replay_gate(sessions, ag, cap.snapshots)
        self.assertTrue(got["ok"], got.get("reason"))
        self.assertGreater(got["checked_turns"], 0)
        self.assertEqual(got["mismatches"], 0)

    def test_cached_and_live_mrr_agree_exactly(self) -> None:
        ag, sessions, cap = self.build()
        got = CACHE.replay_gate(sessions, ag, cap.snapshots)
        # Not "close": the cache re-derives the same ranking from the same
        # features, so any difference at all is a defect and not a rounding
        # question.
        self.assertEqual(got["delta_mrr"], 0.0)
        self.assertEqual(got["cached_default_mrr"], got["live_a0_mrr"])

    def test_the_gate_catches_a_corrupted_cache(self) -> None:
        # A gate that cannot fail is not a gate: swap two candidates and
        # require the replay to notice.
        ag, sessions, cap = self.build()
        cands = sessions[0]["turns"][0]["candidates"]
        feats = sessions[0]["turns"][0]["features"]
        cands[0], cands[1] = cands[1], cands[0]
        feats[0], feats[1] = feats[1], feats[0]
        # Swapping BOTH keeps the pairing, so re-rank still matches. Now break
        # the pairing by dropping one feature value.
        feats[0] = {**feats[0], "f_bm25": feats[0]["f_bm25"] + 10.0}
        got = CACHE.replay_gate(sessions, ag, cap.snapshots)
        self.assertFalse(got["ok"])
        self.assertEqual(got["first_mismatch"]["check"], "cache")
        self.assertIn("diverges", got["reason"])

    def test_a_missing_a0_input_stops_the_gate(self) -> None:
        ag, sessions, _ = self.build()
        got = CACHE.replay_gate(sessions, ag, {})
        self.assertFalse(got["ok"])
        self.assertIn("no A0 input", got["reason"])


class SnapshotTest(unittest.TestCase):
    """The captured input must be the input AT THE CALL, not a live handle.

    This is the defect the class exists to make impossible: `states_by_key[key]
    = (cands, state)` stored the session dict itself, so every later turn kept
    editing it and the replay ran turn 1 against the state as it stood at the
    end of the session.
    """

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def two_turns(self):
        ag = A.Agent(self.path)
        ag.reset("s", {})
        cap = CACHE.Capture()
        with CACHE.capturing(ag, cap):
            cap.key = ("s", 1)
            ag.respond("s", "I'm looking for Clothing Women Dresses, "
                            "but I'm still exploring.", 1, 10)
            cap.key = ("s", 2)
            ag.respond("s", "For that, what matters is: silk; color: black.", 2, 10)
            cap.key = None
        return ag, cap

    def test_the_snapshot_is_not_the_live_state_object(self) -> None:
        ag, cap = self.two_turns()
        live = ag._sessions["s"]
        snap = cap.snapshots[("s", 1)]
        self.assertIsNot(snap.state, live)
        for key in ("slots", "terms", "phrases", "shown", "provenance"):
            self.assertIsNot(snap.state[key], live[key], key)

    def test_mutating_the_live_state_leaves_the_snapshot_unchanged(self) -> None:
        ag, cap = self.two_turns()
        live = ag._sessions["s"]
        snap = cap.snapshots[("s", 1)]
        before_terms = list(snap.state["terms"])
        before_phrases = list(snap.state["phrases"])
        before_slots = snap.slots

        live["terms"].append("invented")
        live["phrases"].append("invented phrase")
        live["shown"].append("ZZZ")
        live["slots"].append(SlotValue(attribute="material", value="leather"))
        for slot in live["slots"]:
            slot.active = False
            slot.polarity = -1
            slot.soft_ok = False
            slot.confidence = 0.01
            slot.hardness = "soft"
            slot.source_turn = 99

        self.assertEqual(snap.state["terms"], before_terms)
        self.assertEqual(snap.state["phrases"], before_phrases)
        self.assertEqual(CACHE.slot_fingerprint(snap.state), before_slots)
        self.assertNotIn("ZZZ", snap.state["shown"])

    def test_the_snapshot_records_slot_fields_as_they_were_at_the_call(self) -> None:
        _, cap = self.two_turns()
        turn2 = cap.snapshots[("s", 2)]
        self.assertTrue(turn2.slots, "turn 2 stated constraints and recorded no slot")
        for slot in turn2.slots:
            recorded = dict(slot)
            for name in ("active", "polarity", "hardness", "confidence",
                         "soft_ok", "source_turn"):
                self.assertIn(name, recorded)
            self.assertTrue(recorded["active"])
            self.assertEqual(recorded["polarity"], 1)
            self.assertEqual(recorded["source_turn"], 2)
        self.assertEqual(cap.snapshots[("s", 1)].slots, (),
                         "turn 1 stated no constraint and must record no slot")

    def test_slot_fields_cover_every_slotvalue_field(self) -> None:
        # A field added to SlotValue and not added here would be silently
        # absent from the fingerprint, so the fingerprint would stop being a
        # statement about the whole slot.
        self.assertEqual(set(CACHE.SLOT_FIELDS),
                         {f.name for f in dataclasses.fields(SlotValue)})

    def test_cands_keep_their_retrieval_order_and_bm25_score(self) -> None:
        ag, cap = self.two_turns()
        snap = cap.snapshots[("s", 1)]
        live_cands, _ = ag._candidates(snap.state, ag.cfg,
                                       int(ag.cfg["pool_depth"]))
        self.assertEqual([a for a, _ in snap.cands], [a for a, _ in live_cands])
        self.assertEqual([round(s, 10) for _, s in snap.cands],
                         [round(s, 10) for _, s in live_cands])

    def test_the_gate_catches_a_snapshot_that_kept_a_live_reference(self) -> None:
        # The regression test for the original defect, built by re-creating it:
        # point turn 1's snapshot at the LIVE state, which turn 2 has since
        # extended with `silk` and `color: black`, and require the gate to
        # notice that turn 1 no longer replays to the order A0 emitted.
        ag, cap = self.two_turns()
        live = ag._sessions["s"]
        snap = cap.snapshots[("s", 1)]
        poisoned = dict(cap.snapshots)
        poisoned[("s", 1)] = dataclasses.replace(snap, state=live)
        row = dict(cap.rows[("s", 1)])
        row["target"] = row["candidates"][0]
        sessions = [{"sample_id": "s", "scenario": "clean", "turns": [row]}]

        clean = CACHE.replay_gate(sessions, ag, cap.snapshots)
        self.assertTrue(clean["ok"], clean.get("reason"))

        got = CACHE.replay_gate(sessions, ag, poisoned)
        self.assertFalse(got["ok"], "a live-reference snapshot passed the gate")
        self.assertEqual(got["first_mismatch"]["check"], "snapshot")
        self.assertIn("no longer reproduces the live order", got["reason"])

    def test_a_second_rerank_for_one_turn_is_recorded_not_merged(self) -> None:
        ag = A.Agent(self.path)
        ag.reset("s", {})
        cap = CACHE.Capture()
        with CACHE.capturing(ag, cap):
            cap.key = ("s", 1)
            ag.respond("s", "I'm looking for Clothing Women Dresses.", 1, 10)
            ag.respond("s", "I'm looking for Clothing Women Dresses.", 2, 10)
            cap.key = None
        self.assertEqual(cap.duplicate_keys, [("s", 1)])


class SlotSoftTest(unittest.TestCase):
    """slot_soft is a weight AND a compute gate. Both are pinned."""

    @classmethod
    def setUpClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _catalog_file(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        A.clear_catalog_cache()
        cls._tmp.cleanup()

    def collect(self, **cfg):
        ag = A.Agent(self.path, config=cfg)
        ag.reset("s", {})
        ag.respond("s", "I'm looking for Clothing Women Dresses. "
                        "A key requirement is: silk.", 1, 10)
        state = ag._sessions["s"]
        cands, _ = ag._candidates(state, ag.cfg, 50)
        out: list = []
        order = ag._rerank(cands, state, collect=out)
        return ag, cands, state, out, order

    def test_f_slot_is_recorded_independently_of_the_trial_weight(self) -> None:
        # The cache is built ONCE under the shipped config, and a later trial
        # that sets slot_soft = 0 reweights the recorded f_slot rather than
        # re-deriving it. So f_slot must be present in the cache regardless of
        # what any trial later does with it.
        _, _, _, out, _ = self.collect()
        for _, features in out:
            self.assertIn("f_slot", features)

    def test_cached_rescoring_reproduces_a0_at_slot_soft_zero(self) -> None:
        # slot_soft = 0 skips computing `dead` in the full Agent. The cached
        # rescoring must still reproduce that ordering, because f_slot's
        # CONTRIBUTION is zero either way.
        ag, cands, state, out, order = self.collect(slot_soft=0.0)
        weights = {**S.default_weights(), "slot_soft": 0.0}
        cached = [a for a, _ in out]
        feats = [f for _, f in out]
        replayed = [cached[i] for i in sorted(
            range(len(cached)),
            key=lambda i: (-S.score_candidate(feats[i], weights), i))]
        self.assertEqual(replayed, order)

    def test_cached_rescoring_reproduces_a0_at_the_shipped_slot_soft(self) -> None:
        ag, cands, state, out, order = self.collect()
        weights = S.default_weights()
        cached = [a for a, _ in out]
        feats = [f for _, f in out]
        replayed = [cached[i] for i in sorted(
            range(len(cached)),
            key=lambda i: (-S.score_candidate(feats[i], weights), i))]
        self.assertEqual(replayed, order)

    def test_slot_soft_is_searched_and_documented_as_a_gate(self) -> None:
        self.assertIn("slot_soft", S.SEARCH_WEIGHTS)
        source = Path("lab/a1search.py").read_text().lower()
        self.assertTrue("compute gate" in source,
                        "slot_soft's control-flow role must be recorded")


if __name__ == "__main__":
    unittest.main()
