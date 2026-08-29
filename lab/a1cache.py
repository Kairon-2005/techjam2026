"""Phase 7A-R1: build the A1 feature cache, and PROVE it replays A0.

The cache is only trustworthy if re-scoring it with the default weights
reproduces A0 exactly. It does not merely need to look right: a cache that
dropped a feature, mis-mapped a weight, or recorded candidates in a different
order would still produce a plausible MRR and would send the whole search
chasing an artefact.

So the gate is mechanical and runs BEFORE the first trial:

  1. write the cache and freeze its sha256;
  2. re-run `_rerank` on each turn's FROZEN SNAPSHOT and require A0's live
     order back -- this is what catches a snapshot that was a live reference
     and got mutated by later turns;
  3. re-rank every cached turn with the DEFAULT weights and require the same
     live order -- the full candidate list, element for element, not the
     Top-10;
  4. require session-level MRR from the cache to equal A0's own, exactly;
  5. check every turn, report every mismatch, and begin no search if there is
     one. A count of zero is only a claim about the cache if everything in it
     was looked at.

The features come from `_rerank`'s own kernel through its `collect` hook, so
there is no second copy of the nine formulas to drift.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import statistics
from pathlib import Path

from lab import a1search as S

# Exactly the keys a cached row may carry. An allowlist, not a grep: a
# forbidden field is one this set does not mention, so a future addition has to
# be declared here rather than slipping in unnoticed.
SESSION_KEYS = frozenset({"sample_id", "scenario", "turns"})
TURN_KEYS = frozenset({"turn", "candidates", "features", "target"})
FEATURE_KEYS = frozenset({"f_bm25", "f_phrase", "f_idf", "f_cat", "f_pop",
                          "f_exact", "f_field", "f_pos", "f_card", "f_soft",
                          "f_slot", "f_profile", "f_neg"})
# Derived results have no place in a cache the search must RE-derive from.
FORBIDDEN_TURN_KEYS = frozenset({"score", "total", "rank", "scores", "ranks",
                                 "a0_score", "a0_rank", "order"})


def validate_schema(sessions) -> list[str]:
    """Every schema violation, as messages. Empty means the cache is well formed."""
    problems: list[str] = []
    for si, session in enumerate(sessions):
        keys = set(session)
        if not keys <= SESSION_KEYS:
            problems.append(f"session {si}: unexpected keys {sorted(keys - SESSION_KEYS)}")
        if "sample_id" not in keys:
            problems.append(f"session {si}: no sample_id")
        for ti, turn in enumerate(session.get("turns") or ()):
            where = f"session {si} turn {ti}"
            tkeys = set(turn)
            if not tkeys <= TURN_KEYS:
                problems.append(f"{where}: unexpected keys {sorted(tkeys - TURN_KEYS)}")
            bad = tkeys & FORBIDDEN_TURN_KEYS
            if bad:
                problems.append(f"{where}: derived fields {sorted(bad)}")
            for required in ("turn", "candidates", "features", "target"):
                if required not in tkeys:
                    problems.append(f"{where}: missing {required}")
            cands = turn.get("candidates") or []
            feats = turn.get("features") or []
            if len(cands) != len(feats):
                problems.append(f"{where}: {len(cands)} candidates, {len(feats)} vectors")
            if len(set(cands)) != len(cands):
                problems.append(f"{where}: duplicate candidate")
            for fi, vec in enumerate(feats):
                if set(vec) != FEATURE_KEYS:
                    problems.append(
                        f"{where} vector {fi}: feature keys "
                        f"{sorted(set(vec) ^ FEATURE_KEYS)} differ from the "
                        f"pre-registered set")
                    break
    return problems


# Every SlotValue field, so the fingerprint is a whitelist of what a snapshot
# must carry rather than "whatever the dataclass had that day". A field added to
# SlotValue and not added here is caught by tests/test_a1_cache.py, which
# compares this tuple against dataclasses.fields(SlotValue).
SLOT_FIELDS = ("attribute", "value", "polarity", "hardness", "confidence",
               "source_turn", "provenance", "active", "soft_ok",
               "catalog_support", "contradiction")


def slot_fingerprint(state) -> tuple:
    """Each slot's field values AT THIS MOMENT, as a comparable tuple.

    The deep copy already carries these; this is the assertion surface. A slot
    whose `active` flipped, whose `polarity` was inverted by a later override,
    or whose `soft_ok` was cleared by an abandonment is exactly the kind of
    change that a shared reference would back-date onto every earlier turn, and
    a tuple makes that visible in a diff instead of buried in an object graph.
    """
    return tuple(tuple((f, getattr(slot, f)) for f in SLOT_FIELDS)
                 for slot in (state.get("slots") or ()))


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshot:
    """What `_rerank` WAS CALLED WITH, frozen at the instant of the call.

    NOT A REFERENCE. `state` is a deep copy taken before `_rerank` runs, because
    the live session dict keeps being mutated for the rest of the session: the
    next turn appends slots, flips `active`, extends `terms` and rewrites
    `shown`. Storing the live object would mean every cached turn replayed
    against the session's FINAL state, so the gate would compare A0's turn-2
    ranking against a turn-2 input that only existed after turn 5 -- and it
    would pass, because both sides would be wrong in the same way.

    `cands` keeps the retrieval order and the BM25 score exactly as `_rerank`
    received them. Nothing here is reconstructed from a trace afterwards.
    """
    sample_id: str
    turn: int
    cands: tuple[tuple[str, float], ...]
    state: dict
    slots: tuple
    live_order: tuple[str, ...]

    @property
    def cand_list(self) -> list[tuple[str, float]]:
        """The list `_rerank` wants back, rebuilt per call so the frozen tuple
        cannot be mutated through the thing handed to the replay."""
        return [(a, s) for a, s in self.cands]


class Capture:
    """Snapshots and cache rows, keyed by (sample_id, turn).

    `key` is set by the driver immediately before each `respond()` and cleared
    after, so a `_rerank` call from anywhere else is not silently attributed to
    the wrong turn.
    """

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.snapshots: dict[tuple, Snapshot] = {}
        self.rows: dict[tuple, dict] = {}
        self.duplicate_keys: list[tuple] = []
        self.orphan_calls = 0

    def record(self, key, cands, state, slots, live_order, collected) -> None:
        """One turn. `cands` and `state` are ALREADY frozen copies -- this
        method must never be handed the live objects."""
        if key in self.snapshots:
            # Two `_rerank` calls for one turn would make "the input to this
            # turn" ambiguous. Recorded rather than resolved by a rule.
            self.duplicate_keys.append(key)
            return
        self.snapshots[key] = Snapshot(
            sample_id=str(key[0]), turn=int(key[1]), cands=tuple(cands),
            state=state, slots=slots, live_order=tuple(live_order))
        self.rows[key] = {"turn": int(key[1]),
                          "candidates": [a for a, _ in collected],
                          "features": [dict(f) for _, f in collected],
                          "target": None}


@contextlib.contextmanager
def capturing(agent, capture: Capture):
    """Hook `_rerank` ON THIS AGENT for the duration of the block.

    The hook wraps the exact call site: the snapshot is taken from the live
    arguments BEFORE `_rerank` is entered, which is the only moment at which
    "the state this turn was ranked under" exists as a distinct object.

    One `_rerank` call, not two. The features come from the same call through
    the `collect` hook, so the cache and the live order are produced by one
    execution of one kernel and cannot disagree about which turn they describe.
    """
    original = agent._rerank                       # bound class method
    def hooked(cands, state, want_scores=False, collect=None):
        if capture.key is None or want_scores or collect is not None:
            if capture.key is None and not want_scores:
                capture.orphan_calls += 1
            return original(cands, state, want_scores=want_scores, collect=collect)
        # THE SNAPSHOT IS TAKEN HERE, before `_rerank` is entered and before
        # anything downstream can touch the live objects.
        snap_cands = tuple((str(a), float(s)) for a, s in cands)
        snap_state = copy.deepcopy(state)
        fingerprint = slot_fingerprint(state)
        collected: list = []
        live = original(cands, state, collect=collected)
        capture.record(capture.key, snap_cands, snap_state, fingerprint,
                       live, collected)
        return live
    agent._rerank = hooked                         # instance attribute shadow
    try:
        yield capture
    finally:
        del agent._rerank                          # class method restored


def live_mrr(sessions, snapshots, top_k: int = S.TOP_K) -> float:
    """A0's own session-level MRR, from the orders `_rerank` ACTUALLY returned.

    Identical semantics to `a1search.cached_mrr` -- turns in order, first
    Top-10 hit records 1/rank and stops the session, a miss continues, never in
    the Top-10 scores 0 -- but read off the LIVE order rather than re-derived
    from cached features. That is what makes the delta between them meaningful:
    same metric, two independent sources.
    """
    if not sessions:
        return 0.0
    per: list[float] = []
    for session in sessions:
        best = 0.0
        for turn in session.get("turns") or ():
            snap = snapshots.get((session["sample_id"], turn["turn"]))
            if snap is None:
                continue
            order = list(snap.live_order)
            target = turn.get("target")
            rank = order.index(target) + 1 if target in order else None
            if rank is not None and rank <= top_k:
                best = 1.0 / rank
                break
        per.append(best)
    return statistics.fmean(per)


def replay_gate(sessions, agent, snapshots) -> dict:
    """Prove the cache reproduces A0, from the FROZEN snapshots. Three ways.

    `snapshots` maps (sample_id, turn) -> Snapshot. Each turn is checked twice
    against one ground truth -- `live_order`, the list A0's `_rerank` actually
    returned during the session:

      1. RE-RUNNING `_rerank` on the frozen snapshot must return `live_order`.
         This is the check that fails if the snapshot was a live reference: the
         session's later turns would have mutated it, and replaying turn 2
         against turn 5's state gives a different order.
      2. RE-SCORING the cached features with the DEFAULT weights must return
         `live_order` too. This is the check that fails if the cache dropped a
         feature, mis-mapped a weight or recorded candidates out of order.

    Both compare the FULL candidate order, element for element -- not the
    Top-10. Every turn is checked, so a reported count of zero mismatches is a
    statement about the whole cache; the first divergence is reported in full,
    and `ok` is False if there is even one.
    """
    weights = S.default_weights()
    sessions_checked = turns_checked = 0
    mismatches: list[dict] = []
    for session in sessions:
        sessions_checked += 1
        for turn in session.get("turns") or ():
            key = (session["sample_id"], turn["turn"])
            snap = snapshots.get(key)
            if snap is None:
                mismatches.append({"key": list(key),
                                   "reason": f"no A0 input recorded for {key}"})
                continue
            turns_checked += 1
            live = list(snap.live_order)
            resnapped = list(agent._rerank(snap.cand_list, snap.state))
            if resnapped != live:
                mismatches.append({
                    "key": list(key), "check": "snapshot",
                    "reason": f"the frozen snapshot no longer reproduces the "
                              f"live order; diverges at position "
                              f"{_first_diff(resnapped, live)}",
                    "replayed_head": resnapped[:5], "a0_head": live[:5]})
                continue
            cached, feats = turn["candidates"], turn["features"]
            replayed = [cached[i] for i in sorted(
                range(len(cached)),
                key=lambda i: (-S.score_candidate(feats[i], weights), i))]
            if replayed != live:
                mismatches.append({
                    "key": list(key), "check": "cache",
                    "reason": f"cached default-weight order diverges at "
                              f"position {_first_diff(replayed, live)}",
                    "replayed_head": replayed[:5], "a0_head": live[:5]})
    cached_default = S.cached_mrr(sessions, weights)
    live = live_mrr(sessions, snapshots)
    return {"ok": not mismatches,
            "checked_sessions": sessions_checked,
            "checked_turns": turns_checked,
            "mismatches": len(mismatches),
            "first_mismatch": mismatches[0] if mismatches else None,
            "cached_default_mrr": cached_default,
            "live_a0_mrr": live,
            "delta_mrr": cached_default - live,
            "reason": mismatches[0]["reason"] if mismatches else "",
            # Kept for readers of the old field name; same number.
            "checked": turns_checked}


def _first_diff(a: list, b: list) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y),
                min(len(a), len(b)))


def write(sessions, path: Path) -> dict:
    """Validate, write, hash. Refuses to write a malformed cache."""
    problems = validate_schema(sessions)
    if problems:
        return {"ok": False, "problems": problems[:10], "sha256": ""}
    digest = S.write_cache(sessions, Path(path))
    return {"ok": True, "problems": [], "sha256": digest,
            "sessions": len(sessions),
            "turns": sum(len(s.get("turns") or ()) for s in sessions)}


def load(path: Path) -> list[dict]:
    return S.read_cache(Path(path))
