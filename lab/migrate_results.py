"""Annotate historical result rows with their provenance validity.

Rows written before the provenance fix carry only `commit` (the working-tree
HEAD) and a dirty-path list whose first entry lost a character. Worse, rows
measuring an isolated older agent recorded the CURRENT commit, so they cannot
be reconstructed from the record alone.

This never rewrites or deletes measurements. It adds, to every row lacking
them, `schema_version` and `self_describing`, plus a `provenance_note` naming
what cannot be recovered. Old numbers stay readable; they simply stop claiming
a reproducibility guarantee they never had.

Usage:  python3 -m lab.migrate_results [--apply]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOG = Path("lab/results.jsonl")
NOTE = ("schema 1: `commit` is the working-tree HEAD at run time, not "
        "necessarily the agent that was evaluated; dirty paths may be "
        "truncated by one character. Not reproducible from this row alone.")


def migrate(apply: bool = False) -> None:
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    changed = 0
    changed_up: list = []
    for row in rows:
        # Rows carrying full provenance are schema 2 even if they predate the
        # version stamp itself -- classify by content, not by the stamp.
        if row.get("agent_commit") and row.get("agent_sha256") and row.get("harness_commit"):
            if row.get("schema_version") != 2:
                row["schema_version"] = 2
                row["self_describing"] = True
                row.pop("provenance_note", None)
                row.pop("superseded_by", None)
                changed_up.append(row.get("tag"))
            continue
        if row.get("schema_version") == 1:
            continue
        row["schema_version"] = 1
        row["self_describing"] = False
        row["provenance_note"] = NOTE
        # An isolated-agent run is identifiable by its config label.
        if "pre_phase1" in json.dumps(row.get("config", {})) or "pre-phase1" in row.get("tag", ""):
            row["agent_commit"] = "1d5718c (asserted by tag, NOT recorded at run time)"
            row["superseded_by"] = "holdout-baseline-verified"
        changed += 1
    v2 = sum(1 for r in rows if r.get("schema_version") == 2)
    print(f"{len(rows)} rows; schema 2 (self-describing): {v2}; "
          f"schema 1 (annotated, not reproducible alone): {len(rows) - v2}")
    if changed_up:
        print(f"  promoted to schema 2 by content: {len(changed_up)}")
    if not apply:
        print("dry run -- pass --apply to write")
        return
    LOG.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"written to {LOG}")


if __name__ == "__main__":
    migrate("--apply" in sys.argv)
