"""The one path from the benchmark ledger to a performance claim.

lab/report.py exists so a score cannot be quoted without passing
`lab.provenance.citable()`. This is the same rule for timings, plus one more
that scores did not need:

  A MEDIAN OVER FEWER REPETITIONS THAN WERE REGISTERED IS NOT THE REGISTERED
  STATISTIC.

Phase 6B2 registered seven paired repetitions, completed four, and reported the
median of four under the heading of the gate. The four agreed closely and the
conclusion happened to be robust -- but "the number came out stable" is a fact
discovered after the fact, and a report that will quote four when seven were
promised will also quote four when they do not agree. So `aggregate()` refuses:
it returns the per-branch medians with `sufficient=False` and a reason, and
`report()` prints them under a heading that says they are diagnostic.

Gates live in GATES, keyed by branch class, and are read from here rather than
from prose. A gate that is only written down in a notes file is a gate that
gets remembered slightly differently each time it is applied.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from lab import benchfixtures as F
from lab import benchweights as W
from lab import provenance as P

# Gate revision R2.1, notes/35-phase6b2-r2-gate-revision.md. A POST-RESULT
# SPECIFICATION CORRECTION: written after seeing the measurement it changes the
# verdict on. notes/33's original rule stands unedited, and the row it stopped
# (p6b2r2-screen) keeps its verdict; R2.1 is a new rule applied to new runs.
GATE_REVISION = "R2.1 (notes/35, post-result correction)"

# Absolute overhead budget per branch class, ABOVE the micro-path floor.
GATES: dict[str, dict] = {
    F.NO_SCAN:       {"max_overhead_ms": 0.10, "max_ratio": 1.10},
    F.CATEGORY_ONLY: {"max_overhead_ms": 0.25, "max_ratio": 1.10},
    F.POOL:          {"max_overhead_ms": 0.50, "max_ratio": 1.10},
    # Phase 6C1. The control arm is a no-op, so its median sits far below the
    # micro-path floor and the ratio is diagnostic by the R2.1 rule -- which is
    # correct here: a ratio against "doing nothing" is unbounded by
    # construction and says nothing. The absolute budget is what binds.
    F.PROFILE:       {"max_overhead_ms": 0.10, "max_ratio": 1.10},
}

# Below this legacy median, the ratio is DIAGNOSTIC ONLY and the requirement is
# an absolute overhead of MICRO_PATH_MAX_OVERHEAD_MS -- the tightest budget in
# the table. A dispatch already under the tightest absolute budget the project
# cares about is one whose absolute gate has already declared its variation
# irrelevant; a ratio of it can only re-describe that variation as a large
# number. pool_empty's 4.9us baseline is the case that exposed this, but it is
# not named here: notes/33 hand-assigned max_ratio=None to two of three
# classes, and this floor deletes that hand-assignment rather than adding a
# third exception. Note it also TIGHTENS the category-only class from 0.25ms to
# 0.10ms below the floor -- the correction is not uniformly permissive.
RATIO_BASELINE_FLOOR_MS = 0.10
MICRO_PATH_MAX_OVERHEAD_MS = 0.10

AGGREGATE_GATE_MS = 0.50           # live branch-weighted median overhead
FEASIBILITY_SLACK = 1.20           # a screen stops R2 at >20% over any gate


def _completed(rows: list[dict]) -> list[dict]:
    return [r for r in P.citable(rows) if r.get("status") == "completed"]


def per_branch(rows: list[dict]) -> dict[str, dict]:
    """Median wall time, overhead and ratio per FIXTURE, across repetitions."""
    legacy: dict[str, list[float]] = defaultdict(list)
    pure: dict[str, list[float]] = defaultdict(list)
    ratios: dict[str, list[float]] = defaultdict(list)
    klass: dict[str, str] = {}
    for row in rows:
        for name, got in (row.get("fixtures") or {}).items():
            klass[name] = got["branch_class"]
            legacy[name].append(got["legacy_ms"])
            pure[name].append(got["pure_ms"])
            if got.get("ratio") is not None:
                ratios[name].append(got["ratio"])
    out = {}
    for name in sorted(legacy):
        med_legacy = statistics.median(legacy[name])
        med_pure = statistics.median(pure[name])
        out[name] = {
            "branch_class": klass[name],
            "n": len(legacy[name]),
            "legacy_ms": med_legacy,
            "pure_ms": med_pure,
            # The median of the per-repetition overheads, not the difference of
            # the medians: each repetition is a PAIRED measurement, and pairing
            # is the whole reason the arms alternate.
            "overhead_ms": statistics.median(
                [p - l for p, l in zip(pure[name], legacy[name])]),
            "ratio": statistics.median(ratios[name]) if ratios[name] else None,
        }
    return out


def evaluate(branches: dict[str, dict]) -> list[dict]:
    """One verdict per fixture under R2.1.

    Below the floor the ratio is recorded with `diagnostic=True` and is not a
    verdict -- it still appears in the report, because a check that did not run
    must be visible as withheld rather than absent.
    """
    verdicts = []
    for name, got in branches.items():
        gate = GATES[got["branch_class"]]
        micro = got["legacy_ms"] < RATIO_BASELINE_FLOOR_MS
        limit = MICRO_PATH_MAX_OVERHEAD_MS if micro else gate["max_overhead_ms"]
        checks = [("overhead_ms", got["overhead_ms"], limit, False)]
        if got["ratio"] is not None:
            checks.append(("ratio", got["ratio"], gate["max_ratio"], micro))
        for metric, actual, limit, diagnostic in checks:
            verdicts.append({
                "fixture": name, "branch_class": got["branch_class"],
                "metric": metric, "actual": actual, "limit": limit,
                "legacy_ms": got["legacy_ms"], "diagnostic": diagnostic,
                "micro_path": micro,
                "passes": True if diagnostic else actual <= limit,
                "excess": None if diagnostic or not limit else actual / limit,
            })
    return verdicts


def registered_reps(rows: list[dict], default: int = 7) -> int:
    """How many repetitions the run itself said it would do.

    Taken from the rows rather than from the caller, so "four of seven" is
    reported as four of SEVEN even when whoever reads the ledger has forgotten
    what was registered. A caller that could choose this number could choose
    the one its evidence happens to satisfy.
    """
    declared = [int((r.get("config") or {}).get("reps") or 0) for r in rows]
    return max(declared) if any(declared) else default


def aggregate(rows: list[dict], required_reps: int | None = None) -> dict:
    """The live branch-weighted median overhead -- or a refusal to form one.

    `sufficient` is False when fewer than `required_reps` citable, completed
    repetitions exist. The numbers are still returned, because hiding them
    would be its own kind of dishonesty, but every caller must decide what to
    do with `sufficient=False` and `report()` prints them as diagnostic.
    """
    required_reps = registered_reps(rows) if required_reps is None else required_reps
    usable = _completed(rows)
    branches = per_branch(usable)
    by_branch_overhead = {}
    for branch, fixture in W.REPRESENTATIVE.items():
        if fixture in branches:
            by_branch_overhead[branch] = branches[fixture]["overhead_ms"]
    try:
        weighted = W.weighted(by_branch_overhead)
    except ValueError as exc:
        weighted, weighted_note = None, str(exc)
    else:
        weighted_note = ""
    return {
        "n_rows": len(rows), "n_citable_completed": len(usable),
        "required_reps": required_reps,
        "sufficient": len(usable) >= required_reps,
        "reason": ("" if len(usable) >= required_reps else
                   f"{len(usable)} citable completed repetitions, "
                   f"{required_reps} were pre-registered"),
        "branches": branches,
        "verdicts": evaluate(branches),
        "weighted_overhead_ms": weighted,
        "weighted_note": weighted_note,
        "weighted_passes": (weighted is not None and weighted <= AGGREGATE_GATE_MS),
    }


def screen(rows: list[dict]) -> dict:
    """Feasibility verdict: does any branch exceed its gate by more than 20%?

    Deliberately NOT the gate. A screen exists to stop an expensive matrix
    early, so it must be able to say "keep going" on evidence too thin to
    adopt on -- and must never be quoted as though it had said "passed".
    """
    usable = _completed(rows)
    verdicts = evaluate(per_branch(usable))
    blown = [v for v in verdicts
             if v["excess"] is not None and v["excess"] > FEASIBILITY_SLACK]
    return {"n_citable_completed": len(usable), "verdicts": verdicts,
            "blown": blown, "stop": bool(blown) or not usable,
            "reason": ("no citable completed repetition" if not usable else
                       "; ".join(f"{v['fixture']} {v['metric']} "
                                 f"{v['actual']:.4f} > {v['limit']} x1.2"
                                 for v in blown))}


def report(tag: str | None = None, required_reps: int | None = None,
           show_excluded: bool = False, rows: list[dict] | None = None) -> str:
    from lab import benchmark as B
    rows = P.load(B.LOG) if rows is None else rows
    if tag:
        rows = [r for r in rows if r.get("tag") == tag]
    rows = [r for r in rows if r.get("kind") == "benchmark"]
    if not rows:
        return "no benchmark rows"
    agg = aggregate(rows, required_reps)
    out = [f"\n=== benchmark: {tag or 'all tags'} ===",
           f"  rows {agg['n_rows']}, citable+completed {agg['n_citable_completed']}, "
           f"repetitions this run registered {agg['required_reps']}"]
    if not agg["sufficient"]:
        out += ["", "  *** NOT SUFFICIENT: " + agg["reason"] + " ***",
                "  The table below is DIAGNOSTIC. It is not the pre-registered",
                "  statistic and must not be quoted as a gate result."]
    out += ["", f"  {'fixture':<32}{'class':<15}{'n':>3}{'legacy':>10}{'pure':>10}"
                f"{'overhead':>10}{'ratio':>8}"]
    for name, got in agg["branches"].items():
        ratio = f"{got['ratio']:.3f}" if got["ratio"] is not None else "     --"
        out.append(f"  {name:<32}{got['branch_class']:<15}{got['n']:>3}"
                   f"{got['legacy_ms']:>10.4f}{got['pure_ms']:>10.4f}"
                   f"{got['overhead_ms']:>+10.4f}{ratio:>8}")
    out += ["", f"  gates -- {GATE_REVISION}"]
    for v in agg["verdicts"]:
        if v["diagnostic"]:
            out.append(f"    ....  {v['fixture']:<32}{v['metric']:<12}"
                       f"{v['actual']:>10.4f}    DIAGNOSTIC ONLY: legacy median "
                       f"{v['legacy_ms']:.4f} ms < {RATIO_BASELINE_FLOOR_MS} ms")
            continue
        mark = "PASS" if v["passes"] else "FAIL"
        micro = "  (micro-path budget)" if v["micro_path"] else ""
        out.append(f"    {mark}  {v['fixture']:<32}{v['metric']:<12}"
                   f"{v['actual']:>10.4f} <= {v['limit']}{micro}")
    if agg["weighted_overhead_ms"] is None:
        out.append(f"    ----  weighted aggregate: {agg['weighted_note']}")
    else:
        mark = "PASS" if agg["weighted_passes"] else "FAIL"
        out.append(f"    {mark}  {'live branch-weighted median':<32}"
                   f"{'overhead_ms':<12}{agg['weighted_overhead_ms']:>10.4f} "
                   f"<= {AGGREGATE_GATE_MS}")
        out.append(f"          weights frozen {W.FROZEN_TS} from {W.SOURCE_TAG}: "
                   + ", ".join(f"{k}={v:.4f}" for k, v in W.WEIGHTS.items()))
    if show_excluded:
        why = P.reasons(rows)
        bad = [(r, why[P.row_key(r)]) for r in rows if why[P.row_key(r)]]
        out.append(f"\n  excluded ({len(bad)} rows):")
        for row, reason in bad:
            out.append(f"    rep {row.get('rep')} {row.get('tag'):<24}{reason}")
    return "\n".join(out)
