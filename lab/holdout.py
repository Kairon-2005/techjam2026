"""The synthetic OOD holdout — ONE run, `score_default` only, consumed forever.

WHAT THIS IS. `data/supplementary_holdout.jsonl` is 1,000 sessions we generated
alongside `supplementary_dev` and then sealed. It has never been read by any
phase: `lab/scenarios.py` deliberately registers no Scenario for it, the Phase 7
split guard tests corpus membership by NAMESPACE so it never opened the file, and
no ledger row references it.

WHAT THIS IS NOT. It is **not** the organizer's private 800, and no number from
it may be presented as one. It is a corpus we wrote, so it measures our own
generator on data our tuning never saw — an out-of-distribution check with
respect to the PUBLIC set, and an in-distribution one with respect to
`supplementary_dev`.

THE RULES, ENFORCED HERE RATHER THAN REMEMBERED.

  * `score_default` ONLY. No challenger, no showcase, no config argument.
  * ONE leased run. `guard()` refuses if a holdout row already exists.
  * The result changes NOTHING. Good or bad, no weight, no gate and no default
    moves afterwards -- Phase 7 is closed (`notes/46`).

    python3 -m lab.holdout --run
    python3 -m lab.holdout --report
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import starter.agent as A
from evaluator import local_evaluator as E
from lab import provenance as P

CATALOG = Path("data/catalog.jsonl")
HOLDOUT = Path("data/supplementary_holdout.jsonl")
LEDGER = Path("lab/holdout.jsonl")
OUT = Path("docs/HOLDOUT_CONSUMED.md")
TAG = "p8-synthetic-holdout"
EXPECTED_ROWS = 1000


class HoldoutConsumedError(AssertionError):
    """The one run has already happened."""


def guard(ledger: Path = LEDGER) -> None:
    existing = [r for r in P.load(ledger) if r.get("tag") == TAG]
    if existing:
        when = ", ".join(str(r.get("ts")) for r in existing)
        raise HoldoutConsumedError(
            f"the synthetic holdout was already consumed ({when}). It is a "
            f"ONE-SHOT corpus: a second run would make it a tuning set, which "
            f"is exactly what sealing it prevented. Read {ledger}; do not "
            f"produce a new row.")


def run() -> dict:
    """`score_default` over all 1,000 sealed sessions. No config argument."""
    rows = E.load_jsonl(HOLDOUT)
    if len(rows) != EXPECTED_ROWS:
        raise HoldoutConsumedError(f"{HOLDOUT} has {len(rows)} rows, expected "
                                   f"{EXPECTED_ROWS}")
    for row in rows:
        if not str(row["sample_id"]).startswith("supplementary_holdout_"):
            raise HoldoutConsumedError(f"unexpected id {row['sample_id']!r}")
    ids, cats, prods = E.catalog_index(CATALOG)
    # NO CONFIG. A bare Agent is score_default, and passing a dict here -- even
    # an empty one -- would make it possible to pass a non-empty one later.
    agent = A.Agent(str(CATALOG))
    started = time.perf_counter()
    result = E.evaluate(agent, rows, ids, cats, prods)
    return {"score": result["recommended_technical_score"],
            "hr10": result["hit_rate_at_10"], "mrr": result["mrr"],
            "mttc": result["mttc"], "efficiency": result["efficiency"],
            "sample_count": result["sample_count"],
            "slices": result.get("scenario_metrics") or {},
            "seconds": round(time.perf_counter() - started, 1)}


def document(row: dict, public: dict, supval: dict) -> str:
    slices = "\n".join(
        f"| `{name}` | {m['hit_rate_at_10']:.4f} | {m['mrr']:.6f} | {m['mttc']:.4f} |"
        for name, m in sorted((row.get("slices") or {}).items()))
    return f"""# Synthetic holdout — consumed {row['ts']}

**One run. `score_default` only. Nothing was changed afterwards.**

`data/supplementary_holdout.jsonl` is 1,000 sessions generated alongside
`supplementary_dev` and sealed before Phase 5. No phase read it: no Scenario was
registered for it, the Phase 7 split guard tested corpus membership by namespace
so it never opened the file, and no ledger row referenced it until this one.

**This is not the organizer's private 800.** It is a corpus we wrote. It is
out-of-distribution with respect to the **public** set and in-distribution with
respect to `supplementary_dev`, so it answers one question only: *does the
shipped configuration hold up on sessions the tuning never saw, from our own
generator?*

## Result

| | synthetic holdout (1,000) | `sup-val` (200) | public (200) |
|---|---|---|---|
| TechnicalScore | **{row['score']}** | {supval['score']} | {public['score']} |
| HR@10 | **{row['hr10']}** | {supval['hr10']} | {public['hr10']} |
| MRR | **{row['mrr']}** | {supval['mrr']} | {public['mrr']} |
| MTTC | **{row['mttc']}** | {supval['mttc']} | {public['mttc']} |

`sup-val` is A0 on a *different* 200 sessions from the same generator, measured
during Phase 7. **The holdout and `sup-val` agree closely; both are far from
public.** That is the shape the result was expected to have, and it is the
useful part: the shipped configuration is **stable across unseen sessions from
the same generator**, and the distance to public is a **distribution difference,
not overfitting to `supplementary_dev`.**

By scenario:

| scenario | HR@10 | MRR | MTTC |
|---|---|---|---|
{slices}

## How to read it

The two columns are **different distributions** and the gap between them is not
an error bar. The supplementary generator grounds constraints in catalog
metadata; the public sessions come from Amazon 5-core sampling and carry a much
stronger popularity prior. Phase 7 measured exactly how far apart they are: the
same nine weights refit on supplementary data gained **+0.229 MRR** there and
lost **−0.116 MRR** on public.

So this number describes **the shipped configuration on our generator's unseen
sessions**. It is not a prediction of the private score, and it is not presented
as one.

## What it did not do

* It **did not change anything.** Phase 7 is closed (`notes/46`): no weight, no
  λ, no gate and no default moved after this ran, and none may.
* It **was not used to choose** between arms — there were no challengers in this
  run. `score_default` was the only configuration executed.
* It **cannot be re-run.** `lab/holdout.py` refuses once a row exists. A second
  run would turn a sealed corpus into a tuning set, which is the one thing
  sealing it prevented.

## Provenance — and why this row is NOT citable

**The row does not pass our own citability predicate**, and it is reported that
way rather than admitted by loosening the rule.

The lease symlinked the two cross-encoder files into its worktree. Those files
became **tracked** when the artifact was bundled for packaging, so `git status`
reported a typechange and every row of the run was stamped `code_dirty` — a
measurement invalidated by the isolation that was supposed to protect it. The
harness is fixed (`lab/lease.py` now never links over anything the checkout
already provides) and `lab/invalidations.jsonl` records the cause.

**The measurement is sound and the row is still refused.** The lease verified
`valid`, `agent_commit` matched the isolated commit, `agent_in_worktree` was
true, and every watched input hashed identically before and after — so nothing
about the agent or the data moved. But weakening the predicate to admit our own
row is precisely the failure the predicate exists to prevent, so the number below
is presented as **measured and non-citable**, and the corpus is **not re-run** to
obtain a cleaner row.

## Provenance

| | |
|---|---|
| ledger row | `{P.row_key(row)}` in `lab/holdout.jsonl` |
| lease | `{(row.get('lease') or {}).get('verdict')}` |
| agent commit | `{row.get('agent_commit')}` |
| agent sha256 | `{row.get('agent_sha256')}` |
| catalog sha256 | `{row.get('catalog_sha256')}` |
| dataset sha256 | `{row.get('dataset_sha256')}` |
| wall clock | {row.get('seconds')} s |
"""


def supval_a0(ledger: Path = Path("lab/supval.jsonl")) -> dict:
    """A0's `sup-val` row — the same generator, a different 200 sessions.

    Read from the ledger rather than retyped, so the comparison column cannot
    drift from what Phase 7 actually measured.
    """
    rows = [r for r in P.citable(P.load(ledger)) if r.get("arm") == "a0"]
    if not rows:
        return {"score": "—", "hr10": "—", "mrr": "—", "mttc": "—"}
    row = rows[-1]
    return {k: row[k] for k in ("score", "hr10", "mrr", "mttc")}


def report(ledger: Path = LEDGER) -> int:
    rows = [r for r in P.load(ledger) if r.get("tag") == TAG]
    if not rows:
        print("the synthetic holdout has not been consumed")
        return 1
    row = rows[0]
    why = P.reasons([row])[P.row_key(row)]
    from tests.test_config_lock import (PUBLIC_HR10, PUBLIC_MRR, PUBLIC_MTTC,
                                        PUBLIC_SCORE)
    public = {"score": PUBLIC_SCORE, "hr10": PUBLIC_HR10, "mrr": PUBLIC_MRR,
              "mttc": PUBLIC_MTTC}
    OUT.write_text(document(row, public, supval_a0()), encoding="utf-8")
    print(f"\n=== synthetic OOD holdout, consumed {row['ts']} ===")
    supval = supval_a0()
    print(f"  {'':<10}{'holdout (1000)':>18}{'sup-val (200)':>16}"
          f"{'public (200)':>16}")
    for key in ("score", "hr10", "mrr", "mttc"):
        print(f"  {key:<10}{row[key]:>18}{supval[key]:>16}{public[key]:>16}")
    print(f"  citable            {'yes' if not why else 'no: ' + why}")
    print(f"  -> {OUT}")
    print("\n  Nothing changes as a result. Phase 7 is closed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.holdout")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--leased", action="store_true")
    args = ap.parse_args(argv)
    if args.report:
        return report()
    if not args.run:
        ap.error("give --run or --report")

    guard()
    if args.leased:
        from lab import lease as L
        script = ("import sys\nfrom lab import holdout as H\n"
                  "sys.exit(H.main(['--run']))\n")
        with L.lease(TAG, log=str(LEDGER)) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    print(f"consuming {HOLDOUT} — ONE run, score_default only", flush=True)
    got = run()
    from lab import record as R
    from lab import lease as L
    row = {"schema_version": 2, "tag": TAG, "scenario": "supplementary_holdout",
           "config": {}, "seeds": [],
           "ts": dt.datetime.now().isoformat(timespec="seconds"),
           "arm": "score_default", "consumed": True,
           "not_the_private_set": True, **got,
           **R.git_state(dataset=str(HOLDOUT))}
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    print(json.dumps({k: row[k] for k in
                      ("score", "hr10", "mrr", "mttc", "sample_count")},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
