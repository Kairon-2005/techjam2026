"""An exclusive, verified lease over the tree for the duration of one experiment.

Two sessions ran experiments against this repository at the same time. One was
rewriting starter/agent.py while the other imported it; both appended to
lab/results.jsonl; and one of them committed twice while an eight-cell matrix
was in flight. Nothing in the recorder could see any of it, because
`git_state()` samples the tree ONCE, at the moment each row is written. A run
whose code moved underneath it therefore records whatever state happened to
exist by the end -- and if the tree is clean by then, every row reports
code_dirty=false. The resulting matrix carried three different agent_commit
values and looked perfectly citable.

A lease closes three holes:

  EXCLUSION      an O_EXCL lock file, so a second run refuses to start rather
                 than interleaving with the first.
  VERIFICATION   a fingerprint -- HEAD, the dirty set, and the hash of every
                 file that can change a number -- taken before the first cell
                 and checked again after the last. A run that was mutated
                 mid-flight stamps its own rows invalid instead of looking
                 trustworthy.
  ISOLATION      by default the run happens in a detached git worktree at a
                 fixed commit, so there is nothing in its import path for
                 another session to edit even in principle.

Rows are journalled in memory and appended only after verification passes, so
the ledger never gains a row whose provenance has not been checked.

    from lab import lease, record
    with lease.lease("phase15b-baseline") as ls:
        record.matrix(["clean"], {"default": {}}, (0,), tag="phase15b")
    print(ls.verdict)
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
import tempfile
import time
from pathlib import Path

LOCK_PATH = Path("lab/.experiment.lock")

# Every file whose content can change a recorded number. public_set.jsonl is
# included because a silently regenerated dataset would otherwise be invisible.
WATCHED = (
    "starter/agent.py",
    "lab/scenarios.py",
    "lab/record.py",
    "evaluator/local_evaluator.py",
    "data/public_set.jsonl",
)

# The result logs churn by definition -- appending a row dirties the tree --
# so they are excluded from the fingerprint's dirty set.
RESULT_PREFIX = "lab/results"

_ACTIVE: "Lease | None" = None


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
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _dirty(cwd: str | Path | None = None) -> list[str]:
    raw = _sh("git", "status", "--porcelain=v1", "-z", "--",
              "starter", "lab", "tests", "evaluator", "data", cwd=cwd)
    return sorted(e[3:] for e in raw.split("\0")
                  if len(e) > 3 and not e[3:].startswith(RESULT_PREFIX))


def fingerprint(cwd: str | Path | None = None) -> dict:
    """Everything that must hold still while an experiment runs."""
    root = Path(cwd or ".")
    return {
        "head": _sh("git", "rev-parse", "HEAD", cwd=cwd).strip() or "nogit",
        "dirty": _dirty(cwd),
        "files": {p: _sha256(root / p) for p in WATCHED},
    }


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM   # exists, not ours
    except (TypeError, ValueError):
        return False
    return True


def _read_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
            json.dump({"pid": os.getpid(), "purpose": purpose,
                       "started": dt_now()}, fh)
        return


def dt_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@contextlib.contextmanager
def _worktree(commit: str, origin: Path):
    """A detached checkout of `commit`, with the untracked catalog linked in."""
    tmp = Path(tempfile.mkdtemp(prefix="techjam-lease-"))
    tree = tmp / "tree"
    proc = subprocess.run(["git", "worktree", "add", "--detach", str(tree), commit],
                          capture_output=True, text=True)
    if proc.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        raise LeaseBusy(f"could not isolate: {proc.stderr.strip()}")
    try:
        # data/catalog.jsonl is 58 MB and gitignored, so the checkout has no
        # copy of it. Link rather than copy: it is read-only to the run.
        catalog = origin / "data" / "catalog.jsonl"
        if catalog.exists():
            (tree / "data").mkdir(parents=True, exist_ok=True)
            (tree / "data" / "catalog.jsonl").symlink_to(catalog)
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
    isolated: str = ""            # commit the isolated worktree runs, if any
    journal: list = dataclasses.field(default_factory=list)
    verdict: str = "pending"
    broke: str = ""

    def stamp(self) -> dict:
        """Provenance fields the recorder merges into every row."""
        return {"lease": {"purpose": self.purpose, "isolated": self.isolated,
                          "head": self.before["head"][:7],
                          "started": dt_now()}}

    def verify(self) -> str:
        """"" if the run's inputs never moved, else what changed."""
        after = fingerprint(self.run_dir)
        if after["head"] != self.before["head"]:
            return (f"HEAD moved during the run: {self.before['head'][:7]} -> "
                    f"{after['head'][:7]}")
        moved = [p for p, h in after["files"].items()
                 if h != self.before["files"].get(p)]
        if moved:
            return "files changed during the run: " + ", ".join(moved)
        if after["dirty"] != self.before["dirty"]:
            gained = sorted(set(after["dirty"]) - set(self.before["dirty"]))
            lost = sorted(set(self.before["dirty"]) - set(after["dirty"]))
            return f"working tree changed during the run: +{gained} -{lost}"
        return ""


def current() -> "Lease | None":
    return _ACTIVE


@contextlib.contextmanager
def lease(purpose: str, isolate: bool = True,
          log: str | Path = "lab/results.jsonl"):
    """Hold the experiment lease for the duration of the block.

    With isolate=True (the default) the tree must be committed, and the run
    happens in a detached worktree at HEAD -- the strongest available promise
    that the code which produced a number is the code at that commit.
    """
    global _ACTIVE
    if _ACTIVE is not None:
        raise LeaseBusy("this process already holds the lease")
    origin = Path.cwd().resolve()
    log_path = (origin / log).resolve()
    _acquire(purpose)
    stack = contextlib.ExitStack()
    obj = None
    try:
        head = _sh("git", "rev-parse", "HEAD").strip()
        run_dir, isolated = origin, ""
        if isolate:
            outstanding = _dirty()
            if outstanding:
                raise LeaseBusy(
                    "isolate=True needs a committed tree; uncommitted: "
                    + ", ".join(outstanding))
            run_dir = stack.enter_context(_worktree(head, origin))
            isolated = head[:7]
            os.chdir(run_dir)
        obj = Lease(purpose=purpose, before=fingerprint(run_dir), origin=origin,
                    run_dir=run_dir, isolated=isolated)
        _ACTIVE = obj
        yield obj
    finally:
        try:
            if obj is not None:
                obj.broke = obj.verify()
                obj.verdict = "invalid" if obj.broke else "valid"
                _flush(obj, log_path)
        finally:
            _ACTIVE = None
            os.chdir(origin)
            stack.close()
            LOCK_PATH.unlink(missing_ok=True)


def _flush(obj: Lease, log_path: Path) -> None:
    """Append the journalled rows, stamped with the post-run verdict."""
    if not obj.journal:
        return
    for row in obj.journal:
        row.setdefault("lease", {})["verdict"] = obj.verdict
        row["lease"]["verified_ts"] = dt_now()
        if obj.broke:
            row["invalid"] = {"reason": "lease_broken", "note": obj.broke,
                              "marked_ts": dt_now()}
    with log_path.open("a", encoding="utf-8") as fh:
        for row in obj.journal:
            fh.write(json.dumps(row) + "\n")
    state = "VALID" if obj.verdict == "valid" else f"INVALID -- {obj.broke}"
    print(f"\nlease({obj.purpose}): {len(obj.journal)} rows appended, {state}")
