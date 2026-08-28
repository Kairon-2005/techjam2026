"""The one place experiment results are produced and recorded.

Every number that reaches a report must come from here. The rule exists
because it was broken: Phase 1 was reported as uncooperative=0.8372 from an
ad-hoc script using seeds (7,11,23,42,101), while lab/capability.py's
documented default is range(7,12). Re-running the committed harness gives
0.829795. No code differed -- only the seed set, and the ad-hoc runs were
never logged, so the discrepancy was invisible until someone re-ran it.

Each row is one (scenario, config) cell and carries everything needed to
re-run it: commit, dirty state, the exact config, the exact seed list, every
per-seed metric, and mean/sd. Never report an aggregate without its seeds.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

from lab import lease as L
from lab import scenarios as S

LOG = Path("lab/results.jsonl")
# 1: commit/dirty only -- records the working-tree HEAD, which is NOT
#    necessarily the agent that ran, and mangles the first dirty path.
# 2: harness_commit + agent_commit/agent_sha256 + split code/result dirtiness.
SCHEMA_VERSION = 2
METRICS = ("score", "hr10", "mrr", "mttc")


RESULT_LOGS = {"lab/results.jsonl", "lab/capability.jsonl", "lab/experiments.jsonl",
               "lab/tuning_runs.jsonl", "lab/benchmarks.jsonl"}


def _sh(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def _porcelain() -> list[str]:
    """Paths git reports as modified, parsed without mangling the first one.

    `git status --porcelain` emits "XY PATH" where XY is two status columns --
    " M lab/results.jsonl" for an unstaged edit. The previous implementation
    called .strip() on the whole blob first, which ate the leading space of the
    FIRST line only, so line[3:] then removed a real character too and the log
    recorded "ab/results.jsonl" and "tarter/agent.py". -z avoids quoting and
    per-line stripping entirely.
    """
    raw = _sh("git", "status", "--porcelain=v1", "-z", "--", "starter", "lab", "tests")
    return [entry[3:] for entry in raw.split("\0") if len(entry) > 3]


def _sha256(path: str | Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return ""


_HASH_CACHE: dict[tuple, str] = {}


def _cached_sha256(path: str | Path) -> str:
    """Cache on (path, mtime, size) -- a path alone goes stale the moment the
    file is edited inside a long-running process, which is exactly what a
    multi-config sweep does."""
    try:
        st = os.stat(path)
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return ""
    if key not in _HASH_CACHE:
        _HASH_CACHE[key] = _sha256(path)
    return _HASH_CACHE[key]


def _blob_commit(path: str | Path, depth: int = 40) -> str:
    """Which recent commit holds this exact file content, if any.

    An isolated copy of an older agent (used to measure a baseline without
    swapping the working tree) would otherwise be recorded under the CURRENT
    commit, which is how the pre-Phase-1 rows came to claim bcfbca2 while
    actually running 1d5718c's agent.
    """
    blob = _sh("git", "hash-object", str(path)).strip()
    if not blob:
        return ""
    for commit in _sh("git", "rev-list", f"-{depth}", "HEAD").split():
        got = _sh("git", "rev-parse", f"{commit}:starter/agent.py").strip()
        if got == blob:
            return _sh("git", "rev-parse", "--short", commit).strip()
    return ""


def git_state(agent_module=None, dataset: str | None = None) -> dict:
    """Provenance for one measurement.

    code_dirty is what gates a citable result; result-log churn is tracked
    separately, because appending to lab/results.jsonl necessarily dirties the
    tree and must not make every subsequent row look untrustworthy.
    """
    paths = _porcelain()
    code_dirty_files = sorted(f for f in paths if f not in RESULT_LOGS)
    agent_src = getattr(agent_module or S.A, "__file__", "") or ""
    in_tree = agent_src.startswith(str(Path.cwd()))
    return {
        "harness_commit": _sh("git", "rev-parse", "--short", "HEAD").strip() or "nogit",
        "code_dirty": bool(code_dirty_files),
        "code_dirty_files": code_dirty_files,
        "result_log_dirty": sorted(f for f in paths if f in RESULT_LOGS),
        "agent_source": agent_src,
        "agent_in_worktree": in_tree,
        "agent_sha256": _cached_sha256(agent_src),
        "agent_commit": _blob_commit(agent_src) or ("worktree" if in_tree else "isolated"),
        "scenario_sha256": _cached_sha256("lab/scenarios.py"),
        "dataset_sha256": _cached_sha256(dataset or S.DATASET),
        "dataset": str(dataset or S.DATASET),
        "catalog_sha256": _cached_sha256(S.CATALOG),
    }


def _metrics(res: dict) -> dict:
    return {"score": res["recommended_technical_score"], "hr10": res["hit_rate_at_10"],
            "mrr": res["mrr"], "mttc": res["mttc"],
            "telemetry": res.get("telemetry") or {},
            # The official evaluator already splits by scenario_type. Carrying
            # its four slices means a route claim can be checked against the
            # organiser's own segmentation rather than against one we invented.
            "slices": res.get("scenario_metrics") or {}}


def cell(scenario_name: str, config: dict, seeds: tuple[int, ...],
         data, tag: str = "", label: str = "") -> dict:
    """Run one (scenario, config) over `seeds` and return a recorded row.

    `label` is the caller's name for this config, and it is a parameter rather
    than something the caller stamps on afterwards because this function
    serialises and writes the row BEFORE it returns. matrix() used to set
    row["config_label"] on the returned dict, so the label reached the caller
    and never the ledger.
    """
    samples, ids, cats, prods = data
    scenario = S.BY_NAME[scenario_name]
    started = time.time()
    per_seed = {}
    for sd in seeds:
        per_seed[sd] = _metrics(S.run(scenario, config, samples, ids, cats, prods, seed=sd))
    row = {
        "schema_version": SCHEMA_VERSION,
        "tag": tag, "scenario": scenario_name, "config": config,
        # Omitted entirely when unset: every row recorded before this existed
        # has no config_label, and report.py:_label() already falls back to a
        # config-derived string, so an empty one would be noise in the ledger.
        **({"config_label": label} if label else {}),
        "seeds": list(seeds),
        "per_seed": {str(k): v for k, v in per_seed.items()},
        "n_seeds": len(seeds),
        "seconds": round(time.time() - started, 1),
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        # A row must say which corpus it describes. Without this a
        # supplementary number and an official one are indistinguishable once
        # they are both in the ledger, which is exactly how a veto signal gets
        # quoted as a score.
        "source": scenario.source,
        "official": scenario.official,
        **git_state(dataset=scenario.dataset),
    }
    for key in METRICS:
        vals = [m[key] for m in per_seed.values()]
        row[key] = statistics.fmean(vals)
        row[key + "_sd"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    # Telemetry is averaged across seeds like everything else, but kept in its
    # own block: it describes HOW a score was produced and must never be
    # mistaken for the score.
    keys = {k for m in per_seed.values() for k, v in m["telemetry"].items()
            if isinstance(v, (int, float))}
    row["telemetry"] = {
        k: round(statistics.fmean([m["telemetry"][k] for m in per_seed.values()
                                   if k in m["telemetry"]]), 4)
        for k in sorted(keys)}
    # question_attribute_counts is a histogram, not a scalar, so the numeric
    # aggregation above drops it. Summing across seeds keeps the shape of what
    # was asked, which is the whole point of recording it.
    for field in ("question_attribute_counts", "plane_counts", "fusion_counts",
                  "final_route_counts", "shadow_reason_counts",
                  "shadow_mode_counts", "retrieval_reason_counts",
                  "question_reason_counts", "question_mode_pair_counts"):
        counts: dict[str, int] = {}
        for m in per_seed.values():
            for name, n in (m["telemetry"].get(field) or {}).items():
                counts[name] = counts.get(name, 0) + n
        if counts:
            row["telemetry"][field] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    names = {n for m in per_seed.values() for n in m["slices"]}
    row["slices"] = {
        name: {k: round(statistics.fmean(
                   [m["slices"][name][k] for m in per_seed.values() if name in m["slices"]]), 6)
               for k in ("hit_rate_at_10", "mrr", "mttc")}
        for name in sorted(names)}
    # Under a lease the row is journalled, not written: provenance is not
    # established until the lease has verified, after the last cell, that
    # nothing moved and that the matrix actually finished. The journal is a
    # file rather than a list because the run happens in a child interpreter
    # -- that is what makes the isolation cover the import path.
    # Without a lease the row is appended immediately, as before, carrying
    # lease=None so a reader can tell an unleased row from a verified one.
    journal = L.journal_path()
    target = journal if journal is not None else LOG
    if journal is None:
        row["lease"] = None
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def matrix(scenario_names, configs: dict[str, dict], seeds: tuple[int, ...],
           tag: str = "", data=None) -> list[dict]:
    """Run every (scenario, config) pair, printing and logging as it goes."""
    data = data or S.load()
    rows = []
    for name in scenario_names:
        # A scenario with no reply, mutate, init or sample_tf hook cannot use
        # its seed for anything, so running five is running one five times.
        # This was costing 5x on supplementary_dev, which is 1000 sessions.
        scen = S.BY_NAME[name]
        deterministic = not any((scen.reply, scen.mutate, scen.init, scen.sample_tf))
        use = (0,) if deterministic else seeds
        print(f"\n=== {name}  (seeds={list(use)}) ===")
        print(f"  {'config':<22}{'score':>10}{'sd':>8}{'HR@10':>8}{'MRR':>8}{'MTTC':>7}")
        for label, cfg in configs.items():
            row = cell(name, cfg, use, data, tag=tag or label, label=label)
            print(f"  {label:<22}{row['score']:>10.6f}{row['score_sd']:>8.4f}"
                  f"{row['hr10']:>8.3f}{row['mrr']:>8.3f}{row['mttc']:>7.2f}", flush=True)
            rows.append(row)
    return rows


if __name__ == "__main__":
    seeds = tuple(int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else
                                   ["7", "8", "9", "10", "11"]))
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else [s.name for s in S.LIBRARY]
    script = ("from lab import record as R\n"
              f"R.matrix({names!r}, {{'default': {{}}}}, {tuple(seeds)!r}, tag='cli')\n")
    with L.lease("record-cli") as held:
        held.run(script, expected_cells=len(names))
    print(f"\nlogged to {LOG}: lease {held.verdict} {held.broke}")
