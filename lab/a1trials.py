"""Phase 7A-R1 arm A1: the 189 coordinate trials, over the FROZEN cache.

The cache is an input to this search, so it is checked like one. Before trial 0
this module re-hashes `lab/a1cache.jsonl` and requires it to equal the sha256
`lab/a1cache.meta.json` recorded when the replay gate passed. A search run
against a cache that has moved since it was gated is a search whose objective
nobody verified.

Then: deterministic coordinate descent, 3 sweeps over 9 weights at 7 grid points
each -- **189 trials, asserted, not counted afterwards** -- and Step 0 of
notes/44 section 7b applied to the result. A no-op stops A1 here: no `sup-val`
run, not finalist-eligible.

    python3 -m lab.a1trials --leased
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

from lab import a1search as S

CACHE = Path("lab/a1cache.jsonl")
MANIFEST = Path("lab/a1cache.meta.json")
WEIGHTS_OUT = "a1weights.json"
BUILD_LOG = "lab/a1builds.jsonl"

# 3 sweeps x 9 searched weights x 7 grid points. Written out rather than
# recomputed from the constants it is meant to check.
EXPECTED_TRIALS = 189


class FrozenInputError(AssertionError):
    """The cache is not the cache the replay gate passed."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_inputs(cache: Path = CACHE, manifest: Path = MANIFEST) -> dict:
    """The manifest, once the cache on disk has been proved to match it."""
    if not manifest.exists():
        raise FrozenInputError(f"{manifest} is absent: no gated cache to search")
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if not meta.get("ok"):
        raise FrozenInputError("the recorded replay gate did not pass; "
                               f"first_mismatch={meta.get('first_mismatch')}")
    if meta.get("smoke"):
        raise FrozenInputError("the manifest describes a SMOKE run, not a build")
    if int(meta.get("checked_sessions", 0)) != 800:
        raise FrozenInputError(f"the gate checked {meta.get('checked_sessions')} "
                               f"sessions, not 800")
    if meta.get("delta_mrr") != 0.0 or meta.get("evaluator_delta") != 0.0:
        raise FrozenInputError("the gate's deltas are not both exactly zero: "
                               f"delta_mrr={meta.get('delta_mrr')} "
                               f"evaluator_delta={meta.get('evaluator_delta')}")
    actual = sha256_of(cache)
    if actual != meta.get("cache_sha256"):
        raise FrozenInputError(
            f"{cache} hashes to {actual}, but the gate passed on "
            f"{meta.get('cache_sha256')}. The cache has moved since it was "
            f"gated; rebuild it rather than searching this one.")
    return meta


def run(sessions, sweeps: int = S.SWEEPS) -> dict:
    started = time.perf_counter()
    result = S.coordinate_search(sessions, sweeps=sweeps)
    if result["trials"] != EXPECTED_TRIALS:
        raise FrozenInputError(f"{result['trials']} trials ran, not "
                               f"{EXPECTED_TRIALS}: the searched set or the grid "
                               f"is not the pre-registered one")
    result["seconds"] = round(time.perf_counter() - started, 1)
    return result


def by_scenario(sessions, weights) -> dict:
    """Cached MRR per `scenario_type`. DESCRIPTIVE, and computed AFTER freezing.

    The weights are already fixed by the pre-registered rule before this runs,
    so this describes the frozen vector rather than helping choose it. notes/44
    section 9b's prohibition is on letting telemetry move a gate; it is not a
    prohibition on knowing where a number came from. A gain that turned out to
    live entirely in one scenario would be a thing to report, not a thing to
    tune away.
    """
    groups: dict[str, list] = {}
    for session in sessions:
        groups.setdefault(str(session.get("scenario") or "?"), []).append(session)
    return {name: round(S.cached_mrr(rows, weights), 6)
            for name, rows in sorted(groups.items())}


def verdict(result: dict, meta: dict) -> dict:
    """Step 0 of notes/44 section 7b, applied and recorded.

    The no-op condition is `delta_mrr <= 0` (revision 4, section 0.1). Weight
    equality is carried alongside as a DIAGNOSTIC: when the two disagree, the
    verdict is delta_mrr.
    """
    no_op = S.is_no_op(result)
    return {
        "phase": "7A-R1", "arm": "A1", "step": "sup-train coordinate search",
        "tag": "p7a-r1-a1trials", "scenario": "supplementary_dev",
        "config": {}, "seeds": [], "schema_version": 2,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "trials": result["trials"], "sweeps": result["sweeps"],
        "searched": result["searched"], "multipliers": result["multipliers"],
        "baseline_mrr": result["baseline_mrr"],
        "best_mrr": result["best_mrr"],
        "delta_mrr": result["delta_mrr"],
        "weights": result["weights"],
        "default_weights": S.default_weights(),
        "l1_distance_from_defaults": round(
            S.l1_distance(result["weights"], S.default_weights()), 10),
        "weights_unchanged_diagnostic": result["weights_unchanged"],
        "accepted_moves": result["accepted"],
        "no_op": no_op,
        "verdict": ("NO-OP: recorded as a no-op. A1 stops here -- no sup-val "
                    "run, not finalist-eligible (notes/44 7b Step 0)."
                    if no_op else
                    "CHALLENGER: A1 is frozen at these weights and proceeds to "
                    "sup-val, where it must still clear the floors and show a "
                    "strictly positive MRR delta to qualify."),
        "cache_sha256": meta["cache_sha256"],
        "split_train_hash": meta["split_train_hash"],
        "cache_build_ts": meta.get("ts"),
        "seconds": result["seconds"],
    }


def report(row: dict) -> None:
    print("\n=== A1 coordinate search, sup-train ===")
    for field in ("trials", "sweeps", "baseline_mrr", "best_mrr", "delta_mrr",
                  "l1_distance_from_defaults", "weights_unchanged_diagnostic",
                  "no_op", "cache_sha256", "split_train_hash", "seconds"):
        print(f"  {field:<28} {row[field]}")
    print("\n  weights (default -> frozen):")
    for name in row["searched"]:
        before, after = row["default_weights"][name], row["weights"][name]
        mark = "" if before == after else "   <-- moved"
        print(f"    {name:<12} {before:<10} -> {after}{mark}")
    if row["accepted_moves"]:
        print("\n  accepted moves:")
        for move in row["accepted_moves"]:
            print(f"    sweep {move['sweep']} trial {move['trial']:>3} "
                  f"{move['weight']:<12} {move['from']} -> {move['to']}   "
                  f"MRR {move['mrr_from']:.6f} -> {move['mrr_to']:.6f}")
    else:
        print("\n  accepted moves: none -- no grid point beat the shipped vector")
    if row.get("mrr_by_scenario_delta"):
        print("\n  cached MRR by scenario (descriptive, after freezing):")
        for name in sorted(row["mrr_by_scenario_default"]):
            print(f"    {name:<18} {row['mrr_by_scenario_default'][name]:>9.6f} -> "
                  f"{row['mrr_by_scenario_frozen'][name]:>9.6f}   "
                  f"{row['mrr_by_scenario_delta'][name]:+.6f}   "
                  f"n={row['sessions_by_scenario'].get(name)}")
    print(f"\n  {row['verdict']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.a1trials")
    ap.add_argument("--leased", action="store_true")
    ap.add_argument("--out", default="lab")
    args = ap.parse_args(argv)
    if args.leased:
        from lab import lease as L
        origin = Path.cwd().resolve()
        script = ("import sys\nfrom lab import a1trials as T\n"
                  f"sys.exit(T.main(['--out', {str(origin / 'lab')!r}]))\n")
        with L.lease("p7a-r1-a1trials", log=BUILD_LOG) as held:
            held.run(script, expected_cells=1)
        print(f"lease {held.verdict} {held.broke}")
        return 0 if held.verdict == "valid" else 1

    meta = frozen_inputs()
    print(f"cache {meta['cache_sha256']}  verified against {MANIFEST}")
    print(f"split {meta['split_train_hash']}  {meta['checked_sessions']} sessions, "
          f"{meta['checked_turns']} turns")
    t0 = time.perf_counter()
    sessions = S.read_cache(CACHE)
    print(f"loaded {len(sessions)} sessions in {time.perf_counter() - t0:.1f}s",
          flush=True)
    result = run(sessions)
    row = verdict(result, meta)
    row["sessions_by_scenario"] = {
        name: len(rows) for name, rows in sorted(
            {s.get("scenario"): [x for x in sessions if x.get("scenario") == s.get("scenario")]
             for s in sessions}.items())}
    row["mrr_by_scenario_default"] = by_scenario(sessions, S.default_weights())
    row["mrr_by_scenario_frozen"] = by_scenario(sessions, result["weights"])
    row["mrr_by_scenario_delta"] = {
        name: round(row["mrr_by_scenario_frozen"][name]
                    - row["mrr_by_scenario_default"][name], 6)
        for name in row["mrr_by_scenario_default"]}

    from lab import record as R
    row.update(R.git_state(dataset="data/supplementary_dev.jsonl"))
    out = Path(args.out) / WEIGHTS_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    from lab import lease as L
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    report(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
