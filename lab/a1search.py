"""Phase 7A-R1 arm A1: the frozen feature cache and its coordinate search.

Two things here are easy to get plausibly wrong, and both are pinned by tests.

THE OBJECTIVE IS THE EVALUATOR'S, NOT A RANKING METRIC. `TOP_K = 10`
(`evaluator/local_evaluator.py:16`) and the composite is
`0.50*HR@10 + 0.30*MRR + 0.20*efficiency`, so a target at rank 40 scores
**zero**, not 1/40. A session stops at its first Top-10 hit -- the evaluator
breaks there -- and continues past a miss, which is what keeps MTTC-shaped
behaviour visible.

THE GRID MULTIPLIES THE ORIGINAL DEFAULT, NOT THE CURRENT VALUE. With a grid
over the current value every multiplier of 0 is 0, so a weight zeroed in sweep
1 would be pinned at 0 for every later sweep and sweep order could permanently
delete a feature.

The cache stores FEATURE VECTORS, never final scores: a cached score cannot be
re-weighted, and re-weighting is the only thing the search does. It is an
OFF-POLICY approximation -- generated under A0's behaviour, while different
weights would reorder candidates and could change which question is asked and
how later turns go -- so full-Agent `sup-val` is the validation, never this.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path

import starter.agent as A

TOP_K = 10                     # the evaluator's, not ours
SWEEPS = 3
MULTIPLIERS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)

# The nine non-zero weights, in the fixed alphabetical sweep order with
# slot_soft last (it is not a `w_` name). Excluded as currently zero: w_pos,
# w_card, w_soft -- the grid is multiplicative, so it could never leave zero,
# and admitting one would mean introducing a feature rather than reweighting
# it. Excluded by rule: w_profile after Phase 6C1. Excluded as structural:
# phrase_idf, soft_adaptive, w_soft_lo, w_soft_hi choose HOW a feature is
# computed rather than how much it counts.
SEARCH_WEIGHTS: tuple[str, ...] = (
    "w_bm25", "w_cat", "w_exact", "w_field", "w_idf",
    "w_neg", "w_phrase", "w_pop", "slot_soft",
)

# feature key -> the weight that scales it, mirroring retrieval.py:_rerank.
FEATURE_OF = {"w_bm25": "f_bm25", "w_cat": "f_cat", "w_exact": "f_exact",
              "w_field": "f_field", "w_idf": "f_idf", "w_neg": "f_neg",
              "w_phrase": "f_phrase", "w_pop": "f_pop", "slot_soft": "f_slot"}

# f_neg is SUBTRACTED in _rerank; the cache stores the raw feature, so the
# sign lives here and in exactly one place.
NEGATIVE = frozenset({"w_neg"})

# slot_soft is a WEIGHT AND A COMPUTE GATE. retrieval.py reads
# `if cfg["slot_soft"] and phrases:` before computing `dead`, so setting it to
# 0 in the full Agent SKIPS that work rather than merely zero-weighting
# f_slot. It stays in the searched set because the SCORED outcome is identical
# either way -- f_slot contributes 0 whether it was computed and multiplied by
# zero, or never computed -- and tests/test_a1_cache.py pins that both
# configurations replay A0 exactly. The cache records f_slot from the kernel
# regardless of any trial weight, so a trial setting slot_soft = 0 reweights a
# recorded feature rather than losing it. Any latency difference from skipping
# `dead` is an implementation detail and is NOT a quality gain.


def default_weights() -> dict[str, float]:
    return {name: float(A.DEFAULTS[name]) for name in SEARCH_WEIGHTS}


def candidate_values(name: str, original: float) -> list[float]:
    """The seven grid points for one weight, relative to its ORIGINAL default."""
    return [round(m * float(original), 10) for m in MULTIPLIERS]


def score_candidate(features: dict, weights: dict[str, float]) -> float:
    """One candidate's A0-shaped linear score from cached features."""
    total = 0.0
    for name, value in weights.items():
        feature = features.get(FEATURE_OF[name], 0.0)
        total += (-value if name in NEGATIVE else value) * feature
    return total


def cached_mrr(sessions, weights: dict[str, float]) -> float:
    """Session-level MRR under the evaluator's Top-10 semantics.

    Per session: turns in order from `scoring_from_turn`; re-score and rank that
    turn's cached candidates; a target at rank 1-10 records 1/rank and STOPS the
    session; a rank past 10 continues to the next turn; never in the Top-10
    records 0.

    `scoring_from_turn` IS PART OF THE EVALUATOR'S SEMANTICS, not an extra.
    `local_evaluator.py:236` holds `override_applied = scenario_type !=
    "intent_override"` and gates the hit test on it, so on an `intent_override`
    session a Top-10 hit BEFORE the override turn scores nothing -- the customer
    has not yet said what they actually want, and finding the old target is not
    a success. Omitting it credited hits the evaluator throws away and
    disagreed with it on 35 of 120 `sup-train` override sessions, in both
    directions.
    """
    if not sessions:
        return 0.0
    scores: list[float] = []
    for session in sessions:
        best = 0.0
        first_scoring = int(session.get("scoring_from_turn", 1))
        for turn in session.get("turns") or ():
            # turn["turn"], not .get(): the schema requires it and a default
            # would either skip every turn or score every turn, both silently.
            if int(turn["turn"]) < first_scoring:
                continue
            target = turn.get("target")
            cands = turn.get("candidates") or ()
            feats = turn.get("features") or ()
            ranked = sorted(
                range(len(cands)),
                key=lambda i: (-score_candidate(feats[i], weights), i))
            rank = next((pos + 1 for pos, i in enumerate(ranked)
                         if cands[i] == target), None)
            if rank is not None and rank <= TOP_K:
                best = 1.0 / rank
                break                     # the evaluator breaks here too
        scores.append(best)
    return statistics.fmean(scores)


def weights_unchanged(weights: dict[str, float]) -> bool:
    """Are these literally the shipped weights? Diagnostic, NOT the no-op test."""
    base = default_weights()
    return all(abs(weights[name] - base[name]) < 1e-12 for name in SEARCH_WEIGHTS)


def is_no_op(result: dict) -> bool:
    """Did the search find any QUALITY improvement? Judged on delta_mrr.

    Weight equality is NOT sufficient and was the first draft's error. The
    tie-break can move many weights without improving the objective -- on a
    corpus where no weight separates anything, every candidate ties and the
    tie-break picks whichever vector it prefers. That vector differs from
    DEFAULTS, so a `weights != DEFAULTS` test would call it a challenger while
    `best_mrr == baseline_mrr`: an arm with no quality gain consuming the
    single public confirmation.

    notes/44 revision 4, section 0.1 and section 7b Step 0: a no-op stops
    before `sup-val`, and the condition is delta_mrr -- not weight equality.
    """
    return float(result.get("delta_mrr", 0.0)) <= 0.0


def l1_distance(weights: dict[str, float], reference: dict[str, float]) -> float:
    return sum(abs(weights[n] - reference[n]) for n in SEARCH_WEIGHTS)


def coordinate_search(sessions, sweeps: int = SWEEPS) -> dict:
    """Deterministic coordinate descent over the cached feature matrices.

    Fixed sweep order, seven grid points per weight, no RNG. The tie-break is
    total: highest MRR, then lowest L1 DISTANCE FROM THE DEFAULTS, then the
    canonical weight tuple -- so two runs over the same cache return the same
    vector, and a run that finds no gain returns the shipped one.
    """
    original = default_weights()
    baseline_mrr = cached_mrr(sessions, original)
    best = dict(original)
    best_mrr = baseline_mrr
    trials = 0

    def rank(weights: dict[str, float], mrr: float):
        """Higher is better. Tie-break 1 is DISTANCE FROM THE DEFAULTS, not the
        absolute L1 norm: with no quality gain the shipped vector must win, so
        a tie cannot drift the weights for free. Tie-break 2 is the canonical
        tuple, which makes the order total."""
        return (mrr, -l1_distance(weights, original),
                tuple(-weights[n] for n in SEARCH_WEIGHTS))

    # Which coordinates actually moved, in order. Additive telemetry: it is
    # appended to when a move is accepted and read by nobody in the loop, so
    # the search it describes is the search that would have run without it.
    accepted: list[dict] = []
    for sweep in range(int(sweeps)):
        for name in SEARCH_WEIGHTS:
            for value in candidate_values(name, original[name]):
                trials += 1
                trial = dict(best)
                trial[name] = value
                mrr = cached_mrr(sessions, trial)
                if rank(trial, mrr) > rank(best, best_mrr):
                    accepted.append({"sweep": sweep + 1, "trial": trials,
                                     "weight": name, "from": best[name],
                                     "to": value, "mrr_from": best_mrr,
                                     "mrr_to": mrr})
                    best, best_mrr = trial, mrr
    result = {"weights": best, "baseline_mrr": baseline_mrr, "best_mrr": best_mrr,
              "delta_mrr": best_mrr - baseline_mrr, "mrr": best_mrr,
              "trials": trials, "sweeps": int(sweeps),
              "weights_unchanged": weights_unchanged(best),
              "searched": list(SEARCH_WEIGHTS), "multipliers": list(MULTIPLIERS),
              "accepted": accepted}
    result["no_op"] = is_no_op(result)
    return result


def write_cache(sessions, path: Path) -> str:
    """Write the cache and return its sha256. Content-addressed, so the hash
    can be committed before the first trial and checked after the last."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "".join(json.dumps(s, sort_keys=True) + "\n" for s in sessions)
    path.write_text(blob, encoding="utf-8")
    return hashlib.sha256(blob.encode()).hexdigest()


def read_cache(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
