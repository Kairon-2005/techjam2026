"""Row identity, the invalidation ledger, and the one citability predicate.

Two rules, both learned the hard way:

  * A RESULT IS NEVER REWRITTEN. The first invalidation pass rewrote
    lab/results.jsonl in place, unlocked, while a leased run was appending to
    it -- the same class of race the lease exists to prevent, committed by the
    tool built to record that race. Invalidations are now appended to their
    own ledger and joined at read time.

  * PROVENANCE IS LEARNED LATE. A run is sometimes only known to be
    untrustworthy hours after it finished. A row that is known to be
    untrustworthy must not keep looking trustworthy, and the only place that
    can be enforced is the read path -- so there is exactly one predicate,
    `citable()`, and every table goes through it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

RESULTS = Path("lab/results.jsonl")
INVALIDATIONS = Path("lab/invalidations.jsonl")


def row_key(row: dict) -> str:
    """Stable identity for a recorded row.

    Derived from what the row IS -- tag, scenario, config, timestamp -- rather
    than from an id field, so rows written before this module existed can be
    referred to just as precisely as rows written after it.
    """
    ident = json.dumps({"tag": row.get("tag"), "scenario": row.get("scenario"),
                        "config": row.get("config"), "ts": row.get("ts")},
                       sort_keys=True)
    return hashlib.sha256(ident.encode()).hexdigest()[:16]


def load(path: Path = RESULTS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def invalidations(path: Path = INVALIDATIONS) -> dict[str, dict]:
    """row_key -> the first invalidation recorded against it."""
    out: dict[str, dict] = {}
    for rec in load(path):
        for key in rec.get("row_keys", []):
            out.setdefault(key, rec)
    return out


# The inputs whose hashes must be present for a row to describe a rerunnable
# measurement. A row missing any of them cannot say what it measured.
FINGERPRINTS = ("agent_sha256", "scenario_sha256", "dataset_sha256", "catalog_sha256")


def _why(row: dict, marks: dict[str, dict]) -> str:
    """The first reason this row cannot be quoted, or "" if it can.

    Ordered from the cheapest and most damning to the most specific, so the
    reason a reader is shown is the one that matters most. Every clause here
    corresponds to a way a row has actually reached this ledger looking
    trustworthy while being nothing of the kind.
    """
    if row.get("schema_version", 0) < 2:
        return "schema_version < 2 (provenance fields absent or wrong)"
    if row.get("code_dirty"):
        return "code_dirty: measured a tree carrying uncommitted edits"
    if row.get("invalid"):
        return f"invalid: {row['invalid'].get('reason', 'unspecified')}"
    if row_key(row) in marks:
        return f"invalid: {marks[row_key(row)].get('reason', 'unspecified')}"

    lease = row.get("lease")
    if not isinstance(lease, dict) or not lease:
        # Pre-lease rows land here. They were recorded by a process that could
        # not tell whether anything moved underneath it, which is exactly how
        # a matrix came to carry three different agent_commit values.
        return "no lease: the run was not exclusive, isolated or verified"
    if lease.get("verdict") != "valid":
        return f"lease verdict is {lease.get('verdict')!r}, not 'valid'"
    if lease.get("matrix_complete") is not True:
        return "matrix_complete is not true: the run did not finish"
    expected, completed = lease.get("expected_cells"), lease.get("completed_cells")
    if not isinstance(expected, int) or not isinstance(completed, int):
        return "cell counts missing: completeness cannot be checked"
    if expected <= 0 or expected != completed:
        return f"cell count mismatch: {completed} completed of {expected} expected"

    # Isolation has to be evidenced per row, not just claimed by the lease:
    # the first version of the lease chdir'd without moving the import path,
    # so rows recorded an isolation the run never had.
    if row.get("agent_in_worktree") is not True:
        return "agent_in_worktree is not true: the agent measured was outside the isolated tree"
    if row.get("agent_commit") != lease.get("isolated"):
        return (f"agent_commit {row.get('agent_commit')!r} does not match the isolated "
                f"commit {lease.get('isolated')!r}")

    missing = [f for f in FINGERPRINTS if not row.get(f)]
    if missing:
        return "missing input fingerprints: " + ", ".join(missing)

    # Phase 7A-R1 arm A2. A shard that fell back to A0 on even one turn did not
    # measure the semantic arm, and the production path's fail-open is exactly
    # what makes that invisible in the score: the row looks like a clean A2
    # result and is A0's quality wearing A2's name. The four invalidating
    # reasons are model_absent, load_failure, inference_failure and
    # bad_permutation (starter/semantic.py). Fix the environment and re-run from
    # a fixed commit -- the row is never repaired in place, because results are
    # never rewritten.
    telemetry = row.get("telemetry") or {}
    fell_back = telemetry.get("semantic_invalidating_turns") or 0
    if fell_back:
        return (f"semantic fallback on {fell_back} turn(s): the shard did not "
                f"measure arm A2 and yields no A2 quality verdict "
                f"({telemetry.get('semantic_reason_counts') or {}})")
    broke = telemetry.get("semantic_lambda_zero_violations") or 0
    if broke:
        return (f"lambda=0 failed to reproduce A0 byte-for-byte on {broke} "
                f"turn(s): the fallback's correctness proof did not hold")
    return ""


def reasons(rows: list[dict], marks: dict[str, dict] | None = None) -> dict[str, str]:
    """Why each non-citable row cannot be quoted. Empty string means it can."""
    marks = invalidations() if marks is None else marks
    return {row_key(row): _why(row, marks) for row in rows}


def citable(rows: list[dict], marks: dict[str, dict] | None = None) -> list[dict]:
    """The only rows a report, table or claim is allowed to quote."""
    why = reasons(rows, marks)
    return [r for r in rows if not why[row_key(r)]]


def mark(rows: list[dict], reason: str, note: str = "",
         path: Path = INVALIDATIONS) -> dict:
    """Append one invalidation record covering `rows`. Never rewrites results."""
    rec = {"reason": reason, "note": note,
           "marked_ts": dt.datetime.now().isoformat(timespec="seconds"),
           "row_keys": [row_key(r) for r in rows],
           "covers": sorted({r.get("tag", "") for r in rows})}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:      # append-only: no race
        fh.write(json.dumps(rec) + "\n")
    return rec
