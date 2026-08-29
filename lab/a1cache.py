"""Phase 7A-R1: build the A1 feature cache, and PROVE it replays A0.

The cache is only trustworthy if re-scoring it with the default weights
reproduces A0 exactly. It does not merely need to look right: a cache that
dropped a feature, mis-mapped a weight, or recorded candidates in a different
order would still produce a plausible MRR and would send the whole search
chasing an artefact.

So the gate is mechanical and runs BEFORE the first trial:

  1. write the cache and freeze its sha256;
  2. re-rank every cached turn with the DEFAULT weights;
  3. require the full candidate order to match A0's `_rerank` element for
     element -- not the Top-10, the whole list;
  4. require session-level MRR to match;
  5. stop at the first mismatching turn. No search begins.

The features come from `_rerank`'s own kernel through its `collect` hook, so
there is no second copy of the nine formulas to drift.
"""
from __future__ import annotations

import json
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


def replay_gate(sessions, agent, states_by_key) -> dict:
    """Re-rank every cached turn with the DEFAULT weights against A0.

    `states_by_key` maps (sample_id, turn) -> the (cands, state) that produced
    it, so A0's `_rerank` can be re-run on exactly the same input.

    Stops at the first mismatch: a cache that diverges on one turn is not a
    cache with one bad row, it is a cache whose construction is wrong.
    """
    weights = S.default_weights()
    checked = 0
    for session in sessions:
        for turn in session.get("turns") or ():
            key = (session["sample_id"], turn["turn"])
            record = states_by_key.get(key)
            if record is None:
                return {"ok": False, "checked": checked,
                        "reason": f"no A0 input recorded for {key}"}
            cands, state = record
            a0_order = [a for a in agent._rerank(cands, state)]
            cached = turn["candidates"]
            feats = turn["features"]
            replayed = [cached[i] for i in sorted(
                range(len(cached)),
                key=lambda i: (-S.score_candidate(feats[i], weights), i))]
            if replayed != a0_order:
                first = next((i for i, (x, y) in enumerate(zip(replayed, a0_order))
                              if x != y), min(len(replayed), len(a0_order)))
                return {"ok": False, "checked": checked, "key": key,
                        "reason": f"order diverges at position {first}",
                        "replayed_head": replayed[:5], "a0_head": a0_order[:5]}
            checked += 1
    return {"ok": True, "checked": checked, "reason": ""}


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
