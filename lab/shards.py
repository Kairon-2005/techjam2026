"""Named scenario/config matrices, so an "exact invocation" is a committed name.

lab/record.py is the sanctioned recorder and lab/lease.py the sanctioned
isolation, but WHICH scenarios and WHICH configs a phase ran has lived in
shell history and prose. That is the same gap the benchmark harness was
written to close, one level up: a note can say "seven scenarios, three modes"
and still leave a reader unable to reproduce the grid.

Each shard is one lease. They are deliberately NOT chained: Phase 6B2 ran three
shards in one command, and supplementary_dev -- 1,000 sessions across three
arms -- left the last shard without enough wall clock and journalled zero rows
(notes/32, and the p6b2-Q7 abort at 32744d1). One shard, one command, one
lease, one verdict.

    python3 -m lab.shards --list
    python3 -m lab.shards p6b2r2-shadow
"""
from __future__ import annotations

import argparse
import dataclasses
import sys

from lab import lease as L
from lab import record as R

# The three question modes, with tracing on so the shadow comparator and the
# question telemetry are populated. `off` is the legacy controller, `shadow`
# computes the staged decision and mutates nothing, `control` lets it drive.
_TRACE = {"trace_candidates": True, "trace": True}
MODES = {mode: {**_TRACE, "question_context_mode": mode}
         for mode in ("off", "shadow", "control")}

# The Phase 6B1 compat anchor: deep funnel and starvation bypass off, the
# `other` ask policy, question control on. Its score is 0.928708 and it exists
# to catch a relocation that moved behaviour only under a non-default pipeline.
COMPAT_ANCHOR = {"deep_funnel": False, "starvation_bypass": False,
                 "ask_policy": "other", "question_context_mode": "control"}

SHADOW_SCENARIOS = ("clean", "vague_start", "uncooperative", "override_genuine",
                    "override_category", "contradiction", "supplementary_dev")
ROBUSTNESS_SCENARIOS = ("vague_start", "uncooperative", "override_genuine",
                        "override_category", "contradiction")
SEEDS = (7, 8, 9, 10, 11)

# Phase 6C1 runs profile shadow against profile off. `question_context_mode` is
# left at its adopted default so the arms differ in ONE thing only.
PROFILE_MODES = {mode: {**_TRACE, "profile_context_mode": mode}
                 for mode in ("off", "shadow")}


@dataclasses.dataclass(frozen=True, slots=True)
class Shard:
    scenarios: tuple[str, ...]
    configs: dict
    seeds: tuple[int, ...] = SEEDS
    note: str = ""

    @property
    def cells(self) -> int:
        return len(self.scenarios) * len(self.configs)


SHARDS: dict[str, Shard] = {
    # Pre-adoption agreement. The ONLY independent behavioural evidence there
    # is: after adoption the adapter delegates to the same function, so the
    # comparison compares a value with itself and is tautological.
    "p6b2r2-shadow": Shard(
        scenarios=SHADOW_SCENARIOS, configs={"shadow": MODES["shadow"]},
        note="shadow comparison: 18,597 raw comparisons / 8,483.4 seed-normalised"),
    "p6b2r2-official": Shard(
        scenarios=("clean",),
        configs={**MODES, "compat_anchor": COMPAT_ANCHOR},
        note="official public set, three modes plus the 0.928708 anchor"),
    "p6b2r2-supplementary": Shard(
        scenarios=("supplementary_dev",), configs=MODES,
        note="supplementary_dev, three modes; 1,000 sessions, run alone"),
    # Post-adoption. The default is now "control", so these run with NO
    # question_context_mode set at all: the check is that an unconfigured agent
    # reproduces the measured control rows exactly. It catches a default that
    # was flipped to the wrong value, or flipped in DEFAULTS but read from
    # somewhere else -- which is the shape of the `"route": false` defect.
    "p6b2r2-adoption": Shard(
        scenarios=SHADOW_SCENARIOS, configs={"default": dict(_TRACE)},
        note="adoption: seven scenarios on the shipped default, no mode set"),
    "p6b2r2-adoption-anchor": Shard(
        scenarios=("clean",),
        configs={"compat_anchor_default": {k: v for k, v in COMPAT_ANCHOR.items()
                                           if k != "question_context_mode"}},
        note="adoption: the 0.928708 anchor with the mode left at its default"),
    # --- Phase 6C1: profile credibility, shadow only -----------------------
    # Both modes everywhere, because off-vs-shadow bit-exactness is a gate and
    # not an afterthought: shadow that moved a score would invalidate every
    # number in the phase, so every shard that measures also compares.
    "p6c1-arm-a": Shard(
        scenarios=("clean",), configs=PROFILE_MODES,
        note="Arm A: the 200 unique clean samples, the PRIMARY population"),
    "p6c1-arm-b2": Shard(
        scenarios=("profile_informative",), configs=PROFILE_MODES,
        note="Arm B2: oracle instrument check; upper bound, never a claim"),
    "p6c1-robustness": Shard(
        scenarios=ROBUSTNESS_SCENARIOS, configs=PROFILE_MODES,
        note="five stochastic scenarios, reported separately, NEVER pooled into a gate"),
    "p6c1-supplementary": Shard(
        scenarios=("supplementary_dev",), configs=PROFILE_MODES,
        note="supplementary_dev, both modes; 1,000 sessions, run alone"),
    "p6b2r2-robustness": Shard(
        scenarios=ROBUSTNESS_SCENARIOS, configs=MODES,
        note="five stochastic scenarios, three modes, five seeds"),
}


def run(name: str) -> int:
    shard = SHARDS[name]
    script = ("from lab import record as R\n"
              f"R.matrix({list(shard.scenarios)!r}, {shard.configs!r}, "
              f"{shard.seeds!r}, tag={name!r})\n")
    print(f"=== {name}: {shard.note} ===")
    print(f"    {len(shard.scenarios)} scenarios x {len(shard.configs)} configs "
          f"= {shard.cells} cells, seeds {list(shard.seeds)}")
    with L.lease(name) as held:
        held.run(script, expected_cells=shard.cells)
    print(f"\nlease {held.verdict} {held.broke}")
    return 0 if held.verdict == "valid" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lab.shards")
    ap.add_argument("shard", nargs="?", choices=sorted(SHARDS))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.list or not args.shard:
        for name, shard in sorted(SHARDS.items()):
            print(f"{name:<24}{shard.cells:>3} cells  {shard.note}")
        return 0
    return run(args.shard)


if __name__ == "__main__":
    sys.exit(main())
