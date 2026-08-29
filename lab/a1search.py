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

    Per session: turns in order; re-score and rank that turn's cached
    candidates; a target at rank 1-10 records 1/rank and STOPS the session; a
    rank past 10 continues to the next turn; never in the Top-10 records 0.
    """
    if not sessions:
        return 0.0
    scores: list[float] = []
    for session in sessions:
        best = 0.0
        for turn in session.get("turns") or ():
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


def is_no_op(weights: dict[str, float]) -> bool:
    """Did the search return the shipped weights unchanged?

    notes/44 revision 3, Step 0: a no-op is not a passing arm. It is recorded,
    it stops before `sup-val`, and it does not consume the public run.
    """
    base = default_weights()
    return all(abs(weights[name] - base[name]) < 1e-12 for name in SEARCH_WEIGHTS)


def coordinate_search(sessions, sweeps: int = SWEEPS) -> dict:
    """Deterministic coordinate descent over the cached feature matrices.

    Fixed sweep order, seven grid points per weight, no RNG. The tie-break is
    total: highest MRR, then lowest L1 norm, then the canonical weight tuple --
    so two runs over the same cache return the same vector.
    """
    original = default_weights()
    best = dict(original)
    best_mrr = cached_mrr(sessions, best)
    trials = 0
    for _ in range(int(sweeps)):
        for name in SEARCH_WEIGHTS:
            for value in candidate_values(name, original[name]):
                trials += 1
                trial = dict(best)
                trial[name] = value
                mrr = cached_mrr(sessions, trial)
                key = (mrr, -sum(abs(v) for v in trial.values()),
                       tuple(-trial[n] for n in SEARCH_WEIGHTS))
                best_key = (best_mrr, -sum(abs(v) for v in best.values()),
                            tuple(-best[n] for n in SEARCH_WEIGHTS))
                if key > best_key:
                    best, best_mrr = trial, mrr
    return {"weights": best, "mrr": best_mrr, "trials": trials,
            "sweeps": int(sweeps), "no_op": is_no_op(best),
            "searched": list(SEARCH_WEIGHTS), "multipliers": list(MULTIPLIERS)}


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
