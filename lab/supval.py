"""Phase 7A-R1 section 7: the paired `sup-val` run, one leased process per arm.

ONE PROCESS PER ARM, DELIBERATELY. The catalog cache, an ONNX session and a
tokenizer are module-level singletons, and a paired comparison is worth nothing
if the pairing is the thing that broke it. Each arm gets its own lease, its own
interpreter and its own catalog build; the pairing comes from the 200 rows being
identical and the evaluator being the same, not from sharing an address space.

BOTH ARMS ARE ALREADY FROZEN before this runs, and this module refuses to start
otherwise: A1's weights come from `lab/a1weights.json` and A2's lambda from
`lab/a2lambda.json`, each of which had to record a non-no-op verdict on
`sup-train`. Nothing here selects anything.

THE VERDICT IS A PURE FUNCTION OF THE THREE ROWS, computed afterwards by
`verdict()` from the ledger, so the gates cannot be influenced by the order the
arms happened to run in.

    ./.venv/bin/python -m lab.supval --arm a0     # each under its own lease
    ./.venv/bin/python -m lab.supval --verdict    # reads the ledger only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

import starter.agent as A
from evaluator import local_evaluator as E
from lab import scenarios as SC
from lab import split as SPLIT

CATALOG = Path("data/catalog.jsonl")
A1_WEIGHTS = Path("lab/a1weights.json")
A2_LAMBDA = Path("lab/a2lambda.json")
MODEL_DIR = "lab/r0/artifacts/ms-marco-TinyBERT-L2-v2"
LEDGER = "lab/supval.jsonl"
OUT = Path("lab/supval_verdict.json")
ARMS = ("a0", "a1", "a2")

# notes/44 7b Step 1 -- the floors. "Did not regress badly".
FLOOR_COMPOSITE = -0.005
FLOOR_MRR = -0.010
# notes/44 7b Step 1b -- challenger qualification. A floor is not a signal.
QUALIFY_MRR = 0.0              # strictly greater
QUALIFY_COMPOSITE = 0.0        # greater or equal


class FrozenArmError(AssertionError):
    """An arm that is not frozen cannot be validated."""


def a1_config() -> dict:
    if not A1_WEIGHTS.exists():
        raise FrozenArmError(f"{A1_WEIGHTS} is absent: A1 is not frozen")
    row = json.loads(A1_WEIGHTS.read_text(encoding="utf-8"))
    if row.get("no_op"):
        raise FrozenArmError("A1 recorded a no-op on sup-train and is not "
                             "finalist-eligible (notes/44 7b Step 0)")
    if int(row.get("trials", 0)) != 189:
        raise FrozenArmError(f"A1 recorded {row.get('trials')} trials, not 189")
    return dict(row["weights"])


def a2_config() -> dict:
    if not A2_LAMBDA.exists():
        raise FrozenArmError(f"{A2_LAMBDA} is absent: A2 is not frozen")
    row = json.loads(A2_LAMBDA.read_text(encoding="utf-8"))
    if row.get("no_op"):
        raise FrozenArmError("A2 selected lambda = 0 on sup-train; the semantic "
                             "signal failed to beat A0 and A2 is not "
                             "finalist-eligible (notes/44 7b Step 0)")
    if row.get("smoke"):
        raise FrozenArmError("A2's lambda comes from a SMOKE run")
    if not row.get("ok"):
        raise FrozenArmError("A2's sup-train run did not pass its own checks")
    return {"semantic_rerank_mode": "on", "semantic_rerank_k": 10,
            "semantic_lambda": float(row["best_lambda"]),
            "semantic_model_dir": MODEL_DIR, "semantic_max_length": 256}


def arm_config(arm: str) -> dict:
    """Every arm traces, so the arms differ in ONE thing and not two."""
    base = {"trace": True, "trace_candidates": True}
    if arm == "a0":
        return base
    if arm == "a1":
        return {**base, **a1_config()}
    if arm == "a2":
        return {**base, **a2_config()}
    raise FrozenArmError(f"unknown arm {arm!r}")


def run_arm(arm: str, rows) -> dict:
    """One arm over the 200 sup-val rows, through the evaluator itself."""
    config = arm_config(arm)
    agent = A.Agent(str(CATALOG), config=config)
    ids, cats, prods = E.catalog_index(CATALOG)
    started = time.perf_counter()
    result = E.evaluate(agent, rows, ids, cats, prods)
    turns = [t for state in agent._sessions.values()
             for t in (state.get("trace_log") or [])]
    telemetry = SC._semantic_telemetry(turns)
    return {"arm": arm, "config": config,
            "score": result["recommended_technical_score"],
            "hr10": result["hit_rate_at_10"], "mrr": result["mrr"],
            "mttc": result["mttc"], "efficiency": result["efficiency"],
            "slices": result.get("scenario_metrics") or {},
            "sample_count": result["sample_count"],
            "telemetry": telemetry,
            "seconds": round(time.perf_counter() - started, 1)}


def _pair(challenger: dict, control: dict) -> dict:
    return {"d_score": challenger["score"] - control["score"],
            "d_mrr": challenger["mrr"] - control["mrr"],
            "d_hr10": challenger["hr10"] - control["hr10"],
            "d_mttc": challenger["mttc"] - control["mttc"]}


def judge(arm: str, row: dict, a0: dict) -> dict:
    """Step 1 floors, then Step 1b qualification. Independently, per arm."""
    delta = _pair(row, a0)
    floors = {
        "composite >= -0.005": delta["d_score"] >= FLOOR_COMPOSITE,
        "mrr >= -0.010": delta["d_mrr"] >= FLOOR_MRR,
    }
    if arm == "a2":
        # notes/44 section 9: a permutation cannot move either, so any movement
        # is an implementation defect and is investigated as a bug.
        floors["hr10 exactly invariant"] = delta["d_hr10"] == 0.0
        floors["mttc exactly invariant"] = delta["d_mttc"] == 0.0
    qualify = {
        "mrr > 0 strictly": delta["d_mrr"] > QUALIFY_MRR,
        "composite >= 0": delta["d_score"] >= QUALIFY_COMPOSITE,
    }
    passed_floors = all(floors.values())
    qualified = passed_floors and all(qualify.values())
    if not passed_floors:
        note = "FAILS THE FLOORS: stops here, does not reach public."
    elif not qualified:
        note = ("no severe regression, no demonstrated positive signal: NOT a "
                "challenger, and not a reason to spend the public run.")
    else:
        note = "QUALIFIES as a challenger."
    return {"arm": arm, **delta, "floors": floors, "qualify": qualify,
            "passed_floors": passed_floors, "qualified": qualified,
            "note": note}


def finalist(judgements: dict, rows: dict) -> dict:
    """Step 2. One finalist, by the fixed order, on `sup-val` alone."""
    qualified = [arm for arm in ("a1", "a2") if judgements[arm]["qualified"]]
    if not qualified:
        return {"finalist": None,
                "reason": ("neither A1 nor A2-10 qualified. A0 remains the "
                           "default, no public confirmation is run, and Phase "
                           "7A ends as a negative result -- which is a finding, "
                           "pre-registered as acceptable (notes/44 7b Step 1b).")}
    if len(qualified) == 1:
        arm = qualified[0]
        return {"finalist": arm,
                "reason": f"{arm} is the only qualifying arm."}
    # 1: sup-val MRR. 2: composite. 3: fewer moving parts, A1 over A2-10.
    # 4: canonical name order.
    ranked = sorted(qualified, key=lambda arm: (
        -rows[arm]["mrr"], -rows[arm]["score"], 0 if arm == "a1" else 1, arm))
    top, second = ranked[0], ranked[1]
    if rows[top]["mrr"] != rows[second]["mrr"]:
        why = f"higher sup-val MRR ({rows[top]['mrr']} vs {rows[second]['mrr']})"
    elif rows[top]["score"] != rows[second]["score"]:
        why = f"tie on MRR, higher composite ({rows[top]['score']})"
    elif top == "a1":
        why = ("tie on MRR and composite; A1 wins on fewer moving parts -- no "
               "dependency, no artifact, no runtime. A dead heat on quality "
               "should not buy a dependency.")
    else:
        why = "tie on everything above; canonical arm-name order"
    return {"finalist": top, "reason": why, "ranked": ranked}


def verdict(ledger: Path = Path(LEDGER)) -> dict:
    """Read the three leased rows and apply the gates. Selects nothing."""
    from lab import provenance as P
    rows_all = P.load(ledger)
    citable = P.citable(rows_all)
    latest: dict[str, dict] = {}
    for row in citable:
        if row.get("arm") in ARMS:
            latest[row["arm"]] = row          # last citable row per arm wins
    missing = [arm for arm in ARMS if arm not in latest]
    if missing:
        why = P.reasons(rows_all)
        return {"ok": False,
                "reason": f"no citable sup-val row for {missing}",
                "excluded": {P.row_key(r): why[P.row_key(r)]
                             for r in rows_all if why[P.row_key(r)]}}
    a0 = latest["a0"]
    judgements = {arm: judge(arm, latest[arm], a0) for arm in ("a1", "a2")}
    chosen = finalist(judgements, latest)
    return {"ok": True,
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "rows": {arm: {k: latest[arm][k] for k in
                           ("score", "hr10", "mrr", "mttc", "sample_count")}
                     for arm in ARMS},
            "slices": {arm: latest[arm].get("slices") for arm in ARMS},
            "judgements": judgements, **chosen}


def report(got: dict) -> None:
    if not got.get("ok"):
        print(f"\nsup-val verdict UNAVAILABLE: {got['reason']}")
        for key, why in (got.get("excluded") or {}).items():
            print(f"  {key}  {why}")
        return
    print("\n=== sup-val, 200 rows, one leased process per arm ===")
    print(f"  {'arm':<5}{'composite':>12}{'HR@10':>10}{'MRR':>12}{'MTTC':>10}")
    for arm in ARMS:
        row = got["rows"][arm]
        print(f"  {arm:<5}{row['score']:>12.6f}{row['hr10']:>10.4f}"
              f"{row['mrr']:>12.6f}{row['mttc']:>10.4f}")
    for arm in ("a1", "a2"):
        j = got["judgements"][arm]
        print(f"\n  {arm} vs a0:  composite {j['d_score']:+.6f}   "
              f"MRR {j['d_mrr']:+.6f}   HR@10 {j['d_hr10']:+.6f}   "
              f"MTTC {j['d_mttc']:+.6f}")
        for name, ok in j["floors"].items():
            print(f"    floor    {name:<28} {'pass' if ok else 'FAIL'}")
        for name, ok in j["qualify"].items():
            print(f"    qualify  {name:<28} {'pass' if ok else 'FAIL'}")
        print(f"    {j['note']}")
    print(f"\n  FINALIST: {got.get('finalist') or 'NONE'}")
    print(f"  {got['reason']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.supval")
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--leased", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    args = ap.parse_args(argv)

    if args.verdict:
        got = verdict()
        OUT.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        report(got)
        return 0 if got.get("ok") else 1
    if not args.arm:
        ap.error("give --arm, or --verdict")

    if args.leased:
        from lab import lease as L
        script = ("import sys\nfrom lab import supval as S\n"
                  f"sys.exit(S.main(['--arm', {args.arm!r}]))\n")
        with L.lease(f"p7a-r1-supval-{args.arm}", log=LEDGER) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    split, rows_all = SPLIT.operative()
    by_id = {str(r["sample_id"]): r for r in rows_all}
    val = [by_id[i] for i in split.val]
    if len(val) != 200 or SPLIT.id_hash(split.val) != SPLIT.VAL_HASH:
        raise SPLIT.SplitError("this is not the operative sup-val split")
    print(f"sup-val {len(val)} rows, hash {split.val_hash}, arm {args.arm}",
          flush=True)
    got = run_arm(args.arm, val)
    from lab import record as R
    row = {"schema_version": 2, "tag": f"p7a-r1-supval-{args.arm}",
           "scenario": "supplementary_dev", "seeds": [],
           "ts": dt.datetime.now().isoformat(timespec="seconds"),
           "split": "sup-val", "split_val_hash": split.val_hash, **got,
           **R.git_state(dataset="data/supplementary_dev.jsonl")}
    from lab import lease as L
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in
                      ("arm", "score", "hr10", "mrr", "mttc", "sample_count")},
                     indent=2, sort_keys=True))
    if row.get("telemetry"):
        print(json.dumps({"telemetry": row["telemetry"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
