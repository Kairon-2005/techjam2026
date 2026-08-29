"""An exclusive, verified, isolated lease over one experiment run.

Two sessions ran experiments against this repository at the same time. One was
rewriting starter/agent.py while the other imported it; both appended to
lab/results.jsonl; and one committed twice while an eight-cell matrix was in
flight. Nothing in the recorder could see it, because `git_state()` samples the
tree ONCE, when each row is written. A run whose inputs move underneath it
records whatever state happens to exist at the end -- and if the tree is clean
by then, every row reports code_dirty=false. The matrix that provoked this
module carried three different agent_commit values and looked perfectly citable.

A lease closes four holes:

  EXCLUSION      an O_EXCL lock; a second run refuses to start rather than
                 interleaving. Stale locks from dead holders are broken once,
                 loudly; live holders are never evicted.
  ISOLATION      the run happens in a detached git worktree at a fixed commit,
                 IN A SEPARATE INTERPRETER whose cwd and PYTHONPATH are that
                 worktree. The first version of this module only chdir'd, so
                 `import starter.agent` had already resolved against the origin
                 tree before the lease was entered: rows claimed isolation
                 while measuring the working copy. Isolation that does not
                 cover the import path is not isolation.
  VERIFICATION   HEAD, the dirty set, and the hash of every file that can
                 change a number -- the 58 MB catalog included, reached
                 through its symlink -- fingerprinted before the run and
                 checked after it.
  COMPLETION     a run that raises, is killed, or produces fewer cells than it
                 promised is marked invalid. Unchanged hashes only prove
                 nothing was edited; they say nothing about whether the
                 experiment finished.

Rows are journalled by the child and appended to the ledger only after all
four checks pass, so the ledger cannot gain a row whose provenance was never
established.

    with lease("phase15b") as ls:
        ls.run(SCRIPT, expected_cells=25)
    print(ls.verdict, ls.broke)
"""
from __future__ import annotations

import contextlib
import dataclasses
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOCK_PATH = Path("lab/.experiment.lock")

# Every file whose content can change a recorded number. The catalog is here
# because it is the largest single input and, in an isolated run, it is
# reached through a symlink -- exactly the kind of indirection that hides a
# swapped file. public_set.jsonl is here because a silently regenerated
# dataset would otherwise be invisible.
WATCHED = (
    "starter/agent.py",
    "lab/scenarios.py",
    "lab/record.py",
    # The benchmark runner, its fixtures and its frozen weights decide what a
    # timing MEANS as surely as the agent decides what it costs. A run that
    # edited its own fixtures halfway through would otherwise report two
    # different experiments under one tag.
    "lab/benchmark.py",
    "lab/benchfixtures.py",
    "lab/benchweights.py",
    "evaluator/local_evaluator.py",
    "data/public_set.jsonl",
    "data/catalog.jsonl",
    # Phase 7A-R1. The A1 feature cache is an INPUT to the coordinate search
    # exactly as the catalog is an input to a scored run: it is gitignored,
    # linked into the tree rather than copied, and watched so a swap between
    # the gate that passed it and the search that consumes it cannot go
    # unnoticed. lab/split.py and lab/a1search.py decide what the search means.
    "lab/a1cache.jsonl",
    "lab/split.py",
    "lab/a1search.py",
    "lab/a1cache.py",
)

# Gitignored inputs the checkout has no copy of. Linked rather than copied --
# the runs only read them, and the link targets are fingerprinted above, so a
# repoint mid-flight is caught.
LINKED_INPUTS = ("data/catalog.jsonl", "lab/a1cache.jsonl")

# Ledgers churn by definition -- appending a row, or an invalidation for one,
# dirties the tree -- so they are excluded from the fingerprint's dirty set.
# invalidations.jsonl was missing here, so recording an aborted run made the
# lease refuse to start the re-run of that same experiment. benchmarks.jsonl is
# here for the same reason and was added with it, not after being caught by it.
# Append-only ledgers. Appending to one necessarily dirties the tree, so a run
# must not be blocked by the record of the previous run. REWRITTEN artefacts --
# lab/a1cache.meta.json, lab/r1_fields.json -- are deliberately NOT here: those
# must be committed before the next experiment, because a changed artefact is a
# changed input.
LEDGER_PREFIXES = ("lab/results", "lab/invalidations", "lab/benchmarks",
                   "lab/a1builds", "lab/r1builds")
JOURNAL_ENV = "LAB_JOURNAL"


class LeaseBusy(RuntimeError):
    """Another experiment holds the lease, or the tree is unfit to isolate."""


def _sh(*args: str, cwd: str | Path | None = None) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=60, cwd=cwd).stdout
    except Exception:
        return ""


def _sha256(path: str | Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:                 # follows symlinks
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _dirty(cwd: str | Path | None = None) -> list[str]:
    raw = _sh("git", "status", "--porcelain=v1", "-z", "--",
              "starter", "lab", "tests", "evaluator", "data", cwd=cwd)
    return sorted(e[3:] for e in raw.split("\0")
                  if len(e) > 3 and not e[3:].startswith(LEDGER_PREFIXES))


def fingerprint(cwd: str | Path | None = None) -> dict:
    """Everything that must hold still while an experiment runs.

    `links` records where each symlinked input actually pointed. A run whose
    catalog symlink is repointed mid-flight has different data with an
    identical path, and the hash alone would not say which file moved.
    """
    root = Path(cwd or ".")
    return {
        "head": _sh("git", "rev-parse", "HEAD", cwd=cwd).strip() or "nogit",
        "dirty": _dirty(cwd),
        "files": {p: _sha256(root / p) for p in WATCHED},
        "links": {p: os.path.realpath(root / p)
                  for p in WATCHED if (root / p).is_symlink()},
    }


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM              # exists, not ours
    except (TypeError, ValueError):
        return False
    return True


def _read_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _acquire(purpose: str) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (0, 1):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            held = _read_lock()
            if attempt == 0 and not _alive(held.get("pid", -1)):
                print(f"lease: breaking stale lock left by dead pid "
                      f"{held.get('pid')} ({held.get('purpose')})")
                LOCK_PATH.unlink(missing_ok=True)
                continue
            raise LeaseBusy(
                f"another experiment holds the lease: {held or 'unreadable'}. "
                f"Wait for it to finish, or delete {LOCK_PATH} if you are "
                f"certain that process is gone.")
        with os.fdopen(fd, "w") as fh:
            json.dump({"pid": os.getpid(), "purpose": purpose, "started": now()}, fh)
        return


@contextlib.contextmanager
def _worktree(commit: str, origin: Path):
    """A detached checkout of `commit`, with the untracked catalog linked in."""
    # realpath: on macOS mkdtemp hands back /var/folders/... while every path
    # the child reports comes back as /private/var/folders/..., and a run_dir
    # that does not match what the child sees makes the isolation assertion
    # unfalsifiable in the direction that matters.
    tmp = Path(tempfile.mkdtemp(prefix="techjam-lease-")).resolve()
    tree = tmp / "tree"
    proc = subprocess.run(["git", "worktree", "add", "--detach", str(tree), commit],
                          capture_output=True, text=True)
    if proc.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        raise LeaseBusy(f"could not isolate: {proc.stderr.strip()}")
    try:
        # The gitignored inputs the checkout has no copy of: the 58 MB catalog
        # and the 420 MB A1 feature cache. Link rather than copy -- the runs
        # only read them, and the link targets are fingerprinted so a swap
        # cannot pass unnoticed.
        for relative in LINKED_INPUTS:
            source = origin / relative
            if not source.exists():
                continue
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(source)
        yield tree
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                       capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


@dataclasses.dataclass
class Lease:
    purpose: str
    before: dict
    origin: Path
    run_dir: Path
    journal_path: Path
    isolated: str = ""
    expected_cells: int = 0
    completed_cells: int = 0
    returncode: int = 0
    aborted: str = ""
    verdict: str = "pending"
    broke: str = ""

    # ---- running -------------------------------------------------------
    def run(self, script: str, expected_cells: int) -> int:
        """Execute `script` in the isolated tree, in its own interpreter.

        A separate process is the point, not an implementation detail: it is
        the only way the import path can be the worktree rather than whatever
        the parent had already imported.
        """
        self.expected_cells += int(expected_cells)
        env = {**os.environ,
               JOURNAL_ENV: str(self.journal_path),
               "PYTHONPATH": str(self.run_dir),
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, "-u", "-c", script],
                              cwd=str(self.run_dir), env=env)
        self.returncode = proc.returncode
        if proc.returncode != 0:
            self.aborted = f"run exited {proc.returncode}"
        return proc.returncode

    # ---- verdict -------------------------------------------------------
    def rows(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line in
                self.journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @property
    def matrix_complete(self) -> bool:
        return (not self.aborted
                and self.expected_cells > 0
                and self.completed_cells == self.expected_cells)

    def verify(self) -> str:
        """"" if nothing moved AND the run finished; else what went wrong."""
        if self.aborted:
            return self.aborted
        after = fingerprint(self.run_dir)
        if after["head"] != self.before["head"]:
            return (f"HEAD moved during the run: {self.before['head'][:7]} -> "
                    f"{after['head'][:7]}")
        moved = [p for p, h in after["files"].items()
                 if h != self.before["files"].get(p)]
        if moved:
            return "inputs changed during the run: " + ", ".join(moved)
        relinked = [p for p, t in after["links"].items()
                    if t != self.before["links"].get(p)]
        if relinked:
            return "symlinked inputs were repointed during the run: " + ", ".join(relinked)
        if after["dirty"] != self.before["dirty"]:
            gained = sorted(set(after["dirty"]) - set(self.before["dirty"]))
            lost = sorted(set(self.before["dirty"]) - set(after["dirty"]))
            return f"working tree changed during the run: +{gained} -{lost}"
        if not self.matrix_complete:
            return (f"matrix incomplete: {self.completed_cells} of "
                    f"{self.expected_cells} cells")
        return ""


@contextlib.contextmanager
def lease(purpose: str, log: str | Path = "lab/results.jsonl"):
    """Hold the experiment lease for the duration of the block.

    The tree must be committed: a run cannot claim to be at a commit while
    carrying edits that commit does not have.
    """
    origin = Path.cwd().resolve()
    log_path = (origin / log).resolve()
    outstanding = _dirty()
    if outstanding:
        raise LeaseBusy("an experiment needs a committed tree; uncommitted: "
                        + ", ".join(outstanding))
    _acquire(purpose)
    stack = contextlib.ExitStack()
    obj = None
    try:
        head = _sh("git", "rev-parse", "HEAD").strip()
        tree = stack.enter_context(_worktree(head, origin))
        journal = Path(tempfile.mkdtemp(prefix="techjam-journal-")) / "rows.jsonl"
        stack.callback(shutil.rmtree, journal.parent, ignore_errors=True)
        obj = Lease(purpose=purpose, before=fingerprint(tree), origin=origin,
                    run_dir=tree, journal_path=journal, isolated=head[:7])
        yield obj
    except BaseException as exc:                     # KeyboardInterrupt included
        if obj is not None and not obj.aborted:
            obj.aborted = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if obj is not None:
            _settle(obj, log_path)
        LOCK_PATH.unlink(missing_ok=True)
        stack.close()


def _settle(obj: Lease, log_path: Path) -> None:
    """Verify, stamp and append whatever the run produced.

    Partial rows are kept, not discarded -- a half-finished matrix is evidence
    about what was running -- but they are never citable, because a run that
    did not finish cannot be compared against one that did.
    """
    rows = obj.rows()
    obj.completed_cells = len(rows)
    obj.broke = obj.verify()
    obj.verdict = "valid" if not obj.broke else "invalid"
    reason = ("run_aborted" if obj.aborted else
              "matrix_incomplete" if not obj.matrix_complete else
              "lease_broken")
    for row in rows:
        row.setdefault("lease", {}).update({
            "purpose": obj.purpose, "isolated": obj.isolated,
            "head": obj.before["head"][:7], "verdict": obj.verdict,
            "verified_ts": now(),
            "expected_cells": obj.expected_cells,
            "completed_cells": obj.completed_cells,
            "matrix_complete": obj.matrix_complete,
        })
        if obj.broke:
            row["invalid"] = {"reason": reason, "note": obj.broke,
                              "marked_ts": now()}
    if rows:
        with log_path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    state = "VALID" if obj.verdict == "valid" else f"INVALID ({reason}) -- {obj.broke}"
    print(f"\nlease({obj.purpose}): {len(rows)}/{obj.expected_cells} cells, {state}")


def journal_path() -> "Path | None":
    """Where the recorder should journal rows, if it is running under a lease."""
    raw = os.environ.get(JOURNAL_ENV)
    return Path(raw) if raw else None
