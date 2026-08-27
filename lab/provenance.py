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


def reasons(rows: list[dict], marks: dict[str, dict] | None = None) -> dict[str, str]:
    """Why each non-citable row cannot be quoted. Empty string means it can."""
    marks = invalidations() if marks is None else marks
    out: dict[str, str] = {}
    for row in rows:
        key = row_key(row)
        if row.get("schema_version", 0) < 2:
            out[key] = "schema_version < 2 (provenance fields absent or wrong)"
        elif row.get("code_dirty"):
            out[key] = "code_dirty: measured a tree with uncommitted edits"
        elif row.get("invalid"):
            out[key] = f"invalid: {row['invalid'].get('reason', 'unspecified')}"
        elif key in marks:
            out[key] = f"invalid: {marks[key].get('reason', 'unspecified')}"
        else:
            out[key] = ""
    return out


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
