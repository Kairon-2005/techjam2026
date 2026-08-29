"""Phase 7A-R1 section 9: the ONE public confirmation, for the ONE finalist.

WHAT THIS FILE IS FOR. The public 200 has been used in every phase since 1, and
`notes/40`'s overfitting prohibition makes it a CONFIRMATION set: evaluated once,
after the arm is frozen and committed, never re-run to retune. `notes/44` 7b
Step 3 narrows that further -- one finalist, one run, one number -- because
confirming n correlated arms and reporting the best is the best of n draws.

So "exactly once" is enforced here rather than remembered. `guard()` refuses to
start if a public confirmation row already exists in the ledger for this phase,
and it refuses to run any arm but the finalist the sup-val verdict selected. The
sealed holdout is not touched by anything in this module.

PUBLIC RESULTS NEVER CAUSE RETUNING. A finalist that fails here is recorded as
failing; it is not adjusted and re-run. `score_default` moves only if every gate
in section 9 passes.

    ./.venv/bin/python -m lab.public --confirm
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from lab import provenance as P
from lab import shards as SH
from lab import supval as SV

LEDGER = Path("lab/public.jsonl")
OUT = Path("lab/public_verdict.json")
TAG_CLEAN = "p7a-r1-public-clean"
TAG_ROBUST = "p7a-r1-public-robustness"
TAGS = (TAG_CLEAN, TAG_ROBUST)

# notes/44 section 9. Every one of them, and none added here.
GATE_CLEAN_MRR = 0.010          # delta >= +0.010
GATE_COMPOSITE_FLOOR = 0.932067  # not below the shipped default's public score
GATE_ROBUST_SCORE = -0.005      # paired delta >= -0.005
GATE_ROBUST_HR10 = -0.01        # paired HR@10 drop <= 0.01


class PublicRunError(AssertionError):
    """The one run has already happened, or this is not the finalist."""


def guard(ledger: Path = LEDGER) -> dict:
    """The finalist, or a refusal. Checked before anything is evaluated."""
    verdict = SV.verdict()
    if not verdict.get("ok"):
        raise PublicRunError(f"sup-val is not complete: {verdict.get('reason')}")
    arm = verdict.get("finalist")
    if arm is None:
        raise PublicRunError(
            "no arm qualified on sup-val. A0 remains the default, NO public "
            "confirmation is run, and Phase 7A ends as a negative result "
            "(notes/44 7b Step 1b). That outcome is pre-registered as "
            "acceptable and is not a reason to spend the public run.")
    existing = [r for r in P.load(ledger) if r.get("tag") in TAGS]
    if existing:
        raise PublicRunError(
            f"{len(existing)} public confirmation row(s) already exist in "
            f"{ledger}. The public 200 is evaluated ONCE, for ONE finalist, and "
            f"is never re-run to retune (notes/40, notes/44 7b Step 3). Read "
            f"the existing rows; do not produce new ones.")
    return {"finalist": arm, "verdict": verdict}


def configs(arm: str) -> dict:
    """The control and the finalist. A0 is a BASELINE, not a competing arm."""
    return {"a0": {**SH._TRACE}, f"{arm}_finalist": {**SH._TRACE, **SV.arm_config(arm)}}


def run(arm: str) -> int:
    """Two leased shards: the deterministic official set, then robustness."""
    from lab import lease as L
    cfg = configs(arm)
    plan = ((TAG_CLEAN, ("clean",), (7,)),
            (TAG_ROBUST, SH.ROBUSTNESS_SCENARIOS, SH.SEEDS))
    for tag, scenarios, seeds in plan:
        cells = len(scenarios) * len(cfg)
        script = ("from lab import record as R\n"
                  f"R.matrix({list(scenarios)!r}, {cfg!r}, {seeds!r}, tag={tag!r})\n")
        print(f"=== {tag}: {cells} cells, seeds {list(seeds)} ===", flush=True)
        with L.lease(tag, log=str(LEDGER)) as held:
            held.run(script, expected_cells=cells)
        print(f"lease {held.verdict} {held.broke}")
        if held.verdict != "valid":
            return 1
    return 0


def _by(rows, tag: str, label: str) -> dict | None:
    for row in rows:
        if row.get("tag") == tag and row.get("config_label") == label:
            return row
    return None


def gates(arm: str, ledger: Path = LEDGER) -> dict:
    """Every section 9 gate, against the citable rows only."""
    rows = P.citable(P.load(ledger))
    label = f"{arm}_finalist"
    clean_a0 = _by(rows, TAG_CLEAN, "a0")
    clean_arm = _by(rows, TAG_CLEAN, label)
    if not clean_a0 or not clean_arm:
        why = P.reasons(P.load(ledger))
        return {"ok": False, "reason": "no citable clean rows for both arms",
                "excluded": {k: v for k, v in why.items() if v}}
    checks: dict[str, dict] = {}
    d_mrr = clean_arm["mrr"] - clean_a0["mrr"]
    checks["clean MRR delta >= +0.010"] = {
        "value": round(d_mrr, 6), "pass": d_mrr >= GATE_CLEAN_MRR}
    checks[f"composite not below {GATE_COMPOSITE_FLOOR}"] = {
        "value": clean_arm["score"], "pass": clean_arm["score"] >= GATE_COMPOSITE_FLOOR}
    slices = {}
    worst = 0.0
    for name, before in (clean_a0.get("slices") or {}).items():
        after = (clean_arm.get("slices") or {}).get(name)
        if after is None:
            continue
        delta = after["mrr"] - before["mrr"]
        slices[name] = round(delta, 6)
        worst = min(worst, delta)
    checks["no official slice MRR regression"] = {
        "value": slices, "pass": worst >= 0.0}

    robust: dict[str, dict] = {}
    worst_score = worst_hr10 = 0.0
    for scenario in SH.ROBUSTNESS_SCENARIOS:
        a0 = next((r for r in rows if r.get("tag") == TAG_ROBUST
                   and r.get("scenario") == scenario
                   and r.get("config_label") == "a0"), None)
        challenger = next((r for r in rows if r.get("tag") == TAG_ROBUST
                           and r.get("scenario") == scenario
                           and r.get("config_label") == label), None)
        if not a0 or not challenger:
            continue
        d_score = challenger["score"] - a0["score"]
        d_hr10 = challenger["hr10"] - a0["hr10"]
        robust[scenario] = {"d_score": round(d_score, 6),
                            "d_hr10": round(d_hr10, 6)}
        worst_score, worst_hr10 = min(worst_score, d_score), min(worst_hr10, d_hr10)
    checks["robustness paired score delta >= -0.005"] = {
        "value": robust, "pass": worst_score >= GATE_ROBUST_SCORE}
    checks["robustness paired HR@10 drop <= 0.01"] = {
        "value": round(worst_hr10, 6), "pass": worst_hr10 >= GATE_ROBUST_HR10}

    passed = all(c["pass"] for c in checks.values())
    return {
        "ok": True, "arm": arm,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "clean": {"a0": {k: clean_a0[k] for k in ("score", "hr10", "mrr", "mttc")},
                  arm: {k: clean_arm[k] for k in ("score", "hr10", "mrr", "mttc")}},
        "checks": checks, "all_gates_pass": passed,
        "score_default": (
            f"MOVES to {arm}: every section 9 gate passed." if passed else
            "STAYS A0. A challenger that cannot clear the bar it was measured "
            "against has not earned the default, and the bar was set before it "
            "ran. The result is recorded as failing; it is NOT adjusted and "
            "re-run (notes/44 7b Step 4)."),
    }


def report(got: dict) -> None:
    if not got.get("ok"):
        print(f"\npublic verdict UNAVAILABLE: {got['reason']}")
        for key, why in (got.get("excluded") or {}).items():
            print(f"  {key}  {why}")
        return
    arm = got["arm"]
    print(f"\n=== public confirmation, finalist {arm}, run ONCE ===")
    for name, row in got["clean"].items():
        print(f"  {name:<4} composite {row['score']:.6f}  HR@10 {row['hr10']:.4f}  "
              f"MRR {row['mrr']:.6f}  MTTC {row['mttc']:.4f}")
    print()
    for name, check in got["checks"].items():
        print(f"  {'PASS' if check['pass'] else 'FAIL'}  {name}")
        print(f"        {check['value']}")
    print(f"\n  ALL GATES: {'PASS' if got['all_gates_pass'] else 'FAIL'}")
    print(f"  {got['score_default']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.public")
    ap.add_argument("--confirm", action="store_true",
                    help="run the ONE public confirmation for the finalist")
    ap.add_argument("--verdict", action="store_true")
    args = ap.parse_args(argv)
    if args.verdict:
        verdict = SV.verdict()
        arm = verdict.get("finalist")
        if not arm:
            print("no finalist; nothing to confirm")
            return 1
        got = gates(arm)
        OUT.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        report(got)
        return 0 if got.get("ok") and got.get("all_gates_pass") else 1
    if not args.confirm:
        ap.error("give --confirm, or --verdict")
    chosen = guard()
    arm = chosen["finalist"]
    print(f"finalist: {arm}. This is the ONE public confirmation run.")
    return run(arm)


if __name__ == "__main__":
    sys.exit(main())
