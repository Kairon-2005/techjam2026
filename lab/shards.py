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
        note="8,483-turn shadow comparison, seven scenarios"),
    "p6b2r2-official": Shard(
        scenarios=("clean",),
        configs={**MODES, "compat_anchor": COMPAT_ANCHOR},
        note="official public set, three modes plus the 0.928708 anchor"),
    "p6b2r2-supplementary": Shard(
        scenarios=("supplementary_dev",), configs=MODES,
        note="supplementary_dev, three modes; 1,000 sessions, run alone"),
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
