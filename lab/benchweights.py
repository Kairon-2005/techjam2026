"""The frozen live branch mix, and the one place a weighted aggregate is formed.

FROZEN 2026-08-28, from the completed Phase 6B2 shadow matrix (tag
`p6b2b-shadow`, seven scenarios), BEFORE any Phase 6B2-R2
implementation existed and therefore before any R2 timing could influence them.
`derive()` recomputes them from the ledger so the constants below can be
checked against their source; it is a verification path, not the value the
gate uses.

Weights are per-seed normalised. The ledger stores per-scenario counts summed
across seeds, and the scenarios do not share a seed count -- `clean` and
`supplementary_dev` are deterministic and run once, the other five run five
seeds. Summing raw counts would weight `contradiction` five times as heavily as
`clean` for no reason but the seed schedule, so each scenario's counts are
divided by its own `n_seeds` first. That reproduces the 8,483.4 seed-normalised
total the 6B2 results document reports, which is the check that the
normalisation is the one that document's turn counts were built from.

The comparator actually executed 18,597 raw turn-comparisons. The normalised
figure is the right basis for WEIGHTS specifically -- a mix is a rate, and a
rate must not weight `contradiction` five times as heavily as `clean` because
of the seed schedule -- while 18,597 is the right count for "how many
comparisons found zero disagreements". Both appear in notes/36; this module
needs the normalised one.

WHY THESE ARE FROZEN. A branch-weighted aggregate whose weights are chosen
after the per-branch numbers are known is not a measurement, it is a knob: the
branch that happens to look worst can always be down-weighted into
irrelevance. So the weights are constants in a committed file, dated, with
their derivation runnable. If a future phase's live mix differs, that is a
NEW pre-registration with new weights -- not an edit to these.
"""
from __future__ import annotations

import collections

SOURCE_TAG = "p6b2b-shadow"
FROZEN_TS = "2026-08-28"
FROZEN_TURNS = 8483.4          # seed-normalised; 18,597 raw comparisons

# selection_mode -> share of live turns. Sums to 1.0.
#
# `cycle` and `fallback` are ABSENT, not zero-by-omission: the shipped policy
# is `other_then_pool`, so neither branch fires in the live mix. They are still
# benchmarked -- a branch that costs nothing today must not be allowed to start
# costing something unobserved -- but they contribute nothing to an aggregate
# that claims to describe live cost, and giving them an invented weight would
# be describing traffic that does not exist.
WEIGHTS: dict[str, float] = {
    "pool_selection":  0.454393,
    "first_two_other": 0.452507,
    "give_up":         0.082184,
    "easier":          0.010915,
}

# Which benchmark fixture stands in for each live branch when the aggregate is
# formed. The pool term uses the utility-ON, nothing-asked fixture because that
# is the shipped configuration (`question_utility=True`, `pool_depth=30`) and
# the most expensive pool shape, so the aggregate cannot be flattered by
# averaging in the cheaper pool fixtures that no live turn resembles.
REPRESENTATIVE: dict[str, str] = {
    "pool_selection":  "pool_utility_on_none_asked",
    "first_two_other": "first_two_other",
    "give_up":         "give_up",
    "easier":          "easier",
}


def derive(rows: list[dict], tag: str = SOURCE_TAG) -> dict[str, float]:
    """Recompute the mix from ledger rows. For verification, not for gating."""
    tot: collections.Counter = collections.Counter()
    for row in rows:
        if row.get("tag") != tag:
            continue
        seeds = row.get("n_seeds") or 1
        pairs = (row.get("telemetry") or {}).get("question_mode_pair_counts") or {}
        for pair, count in pairs.items():
            tot[pair.split("/")[0]] += count / seeds
    grand = sum(tot.values())
    return {k: v / grand for k, v in tot.items()} if grand else {}


def weighted(per_branch: dict[str, float]) -> float:
    """Branch-weighted mean of a per-branch quantity (an overhead, in ms).

    Raises rather than skipping a missing branch. A weighted aggregate formed
    over a subset of its own weights is a different statistic wearing the same
    name, and silently dropping the branch that failed to measure is the
    shortest path from "one fixture aborted" to "the aggregate passed".
    """
    missing = [b for b in WEIGHTS if b not in per_branch]
    if missing:
        raise ValueError(f"no measurement for weighted branches: {sorted(missing)}")
    return sum(WEIGHTS[b] * per_branch[b] for b in WEIGHTS)
