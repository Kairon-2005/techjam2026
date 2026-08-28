"""The one place a performance number is produced and recorded.

`lab/record.py` exists because Phase 1 was reported from an ad-hoc script whose
seed set nobody logged. This module exists because Phase 6B2's performance gate
was reported the same way: an uncommitted script, four of seven repetitions, no
ledger row, no fingerprints, and -- when the second batch stalled for four
hours -- not one process fact captured to explain it. The correctness gates
went through the lease and were citable; the number the phase actually turned
on did not. See notes/32-phase6b2-results.md, Correction 1.

What that costs, concretely: the four timings cannot be re-run, cannot be
invalidated (there is no row to invalidate), and cannot be compared against a
later measurement, because nothing records which agent, which catalog or which
fixtures produced them.

So:

  COMMITTED       the runner, the fixtures and the frozen branch weights are
                  all in the tree, and the exact invocation is one CLI line.
  FRESH PROCESS   every repetition runs in its own interpreter. A repetition is
                  the unit of independence, and two repetitions sharing a
                  process share a heap, a regex cache and a GC schedule.
  STREAMED        one JSON object per repetition, appended and FLUSHED the
                  moment that repetition ends. A run killed at repetition five
                  keeps four repetitions, rather than keeping nothing -- which
                  is what happened last time.
  BOUNDED         15 minutes per repetition, then killed. At 2x the child's own
                  projected duration the parent captures the child's PID state,
                  CPU%, CPU time and RSS. Four hours of no output is not a
                  measurement and is not a diagnosis either.
  CITABLE         rows carry the same provenance block as lab/record.py rows
                  and are filtered by the same lab.provenance.citable(), under
                  the same lab.lease. There is deliberately no second, weaker
                  standard for performance evidence.

    python3 -m lab.benchmark --tag p6b2r2-screen --reps 3 --measured 1000
    python3 -m lab.benchmark --tag p6b2r2-perf   --reps 7 --measured 10000
    python3 -m lab.benchmark --report --tag p6b2r2-perf
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import platform
import queue
import resource
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from lab import benchfixtures as F
from lab import benchweights as W
from lab import lease as L
from lab import provenance as P
from lab import record as R

LOG = Path("lab/benchmarks.jsonl")

# 1: first committed benchmark schema. Rows written before this module existed
#    do not exist -- that is the defect it was written for.
BENCH_SCHEMA_VERSION = 1

TIMEOUT_SECONDS = 15 * 60          # per repetition, then killed
WATCHDOG_FACTOR = 2.0              # of the child's own projected duration
CALIBRATION_DISPATCHES = 20        # per arm per fixture, to project a duration
ARMS = ("legacy", "pure")


# ---------------------------------------------------------------------------
# Process facts. Captured, never interpreted.
# ---------------------------------------------------------------------------

def ps_snapshot(pid: int) -> dict:
    """State, CPU%, CPU time and RSS for one pid, or why they are missing.

    Shelling out to ps rather than importing psutil: psutil is not installed on
    this host, and a diagnostic that only works where an optional dependency
    happens to be present is a diagnostic that will be absent exactly when it
    is needed. RSS is in KB on both darwin and linux.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,stat=,%cpu=,time=,rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as exc:
        return {"ts": _now(), "pid": pid, "error": f"{type(exc).__name__}: {exc}"}
    if not out:
        return {"ts": _now(), "pid": pid, "error": "no such process"}
    parts = out.split()
    if len(parts) < 5:
        return {"ts": _now(), "pid": pid, "raw": out}
    return {"ts": _now(), "pid": int(parts[0]), "stat": parts[1],
            "cpu_pct": float(parts[2]), "cpu_time": parts[3],
            "rss_kb": int(parts[4])}


def rss_bytes() -> dict:
    """This process's peak and steady RSS.

    ru_maxrss is BYTES on darwin and KILOBYTES on linux; recording the raw
    value with its platform means a row read on the other platform is still
    interpretable, and normalising silently would make the two indistinguishable.
    """
    ru = resource.getrusage(resource.RUSAGE_SELF)
    raw = ru.ru_maxrss
    peak = raw if sys.platform == "darwin" else raw * 1024
    steady = ps_snapshot(os.getpid()).get("rss_kb")
    return {"peak_rss_bytes": peak, "ru_maxrss_raw": raw,
            "ru_maxrss_units": "bytes" if sys.platform == "darwin" else "kilobytes",
            "steady_rss_bytes": steady * 1024 if steady else None}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The child: one repetition, in its own interpreter.
# ---------------------------------------------------------------------------

def _blank_state() -> dict:
    return {"asked": [], "dry_streak": 0, "dry_others": 0, "uncertain_streak": 0,
            "broad_options": [], "last_bits": 0.0, "last_coverage": 0.0,
            "last_weighed": False, "route": "buying", "slots": [], "terms": [],
            "phrases": [], "shown": []}


def _states(fixture: F.Fixture, n: int) -> list[dict]:
    """n independent session states, built OUTSIDE the timed region.

    Both arms mutate the four render fields, so a state cannot be reused
    without changing what the next dispatch sees. Building them inside the loop
    would put dict construction inside the measurement -- ~1us against a 0.7us
    no-scan dispatch, which is the branch whose gate is 0.10ms.

    A shallow copy is sufficient and 20x cheaper than deepcopy at 10,000
    states, and it is sufficient for a checkable reason rather than a hopeful
    one: neither arm mutates a contained object. `broad_options` is REPLACED
    (`state["broad_options"] = options` in _pick_attribute, `list(value)` in
    _apply_question_patch), `asked` is only read, and the remaining written
    fields are scalars. tests/test_benchmark.py asserts that no state shares a
    mutated list with its neighbour after a dispatch.
    """
    base = {**_blank_state(), **copy.deepcopy(fixture.state)}
    return [dict(base) for _ in range(n)]


def _arms(agent, cfg: dict, pool: list[str]):
    """(name -> callable) for the two implementations under comparison.

    Both take one state and perform the COMPLETE dispatch: the legacy arm is
    _pick_attribute, which resolves its own config through _route_cfg; the pure
    arm is the decision plus the application of its patch, because a decision
    that is never applied has not done the work the legacy path did.
    """
    def legacy(state):
        return agent._pick_attribute(state, pool)

    def pure(state):
        decision = agent._decide_question(state, pool, cfg)
        agent._apply_question_patch(state, decision.patch)
        return decision.attribute

    return {"legacy": legacy, "pure": pure}


def _agree(agent, fixture: F.Fixture, cfg: dict, pool: list[str]) -> dict:
    """Do the two arms produce the same attribute and the same state?

    Checked once per fixture per repetition, before timing. Comparing the speed
    of two implementations that disagree is measuring nothing, and a fixture
    that has drifted off its intended branch -- because a threshold moved, or
    the catalog changed -- would otherwise be reported under a gate it no
    longer belongs to.
    """
    base = {**_blank_state(), **copy.deepcopy(fixture.state)}
    a, b = copy.deepcopy(base), copy.deepcopy(base)
    legacy_attribute = agent._pick_attribute(a, pool)
    decision = agent._decide_question(b, pool, cfg)
    agent._apply_question_patch(b, decision.patch)
    fields = ("broad_options", "last_bits", "last_coverage", "last_weighed")
    return {
        "attribute_agrees": legacy_attribute == decision.attribute,
        "state_agrees": all(list(a[f] or []) == list(b[f] or []) if f == "broad_options"
                            else a[f] == b[f] for f in fields),
        "selection_mode": decision.selection_mode,
        "branch_as_registered": decision.selection_mode == fixture.expects_selection,
        "attribute": decision.attribute,
    }


def _time_arm(fn, states: list[dict]) -> dict:
    """Wall and CPU time for one arm over pre-built states."""
    w0, c0 = time.perf_counter(), time.process_time()
    for state in states:
        fn(state)
    wall = time.perf_counter() - w0
    cpu = time.process_time() - c0
    n = len(states)
    return {"wall_ms": wall / n * 1000, "cpu_ms": cpu / n * 1000,
            "wall_total_s": round(wall, 4), "cpu_total_s": round(cpu, 4),
            "dispatches": n}


def child(rep: int, warmup: int, measured: int, catalog: str) -> dict:
    """One repetition. Prints a plan line, then a result line, then exits.

    Arm order alternates with the repetition index. Any monotone drift over a
    repetition -- thermal, page-cache, an interpreter warming up -- lands on
    whichever arm runs first, so alternating makes that drift cancel across the
    seven repetitions instead of accumulating into one arm's mean.
    """
    from starter import agent as A

    load0 = time.perf_counter()
    agent = A.Agent(catalog)
    load_s = time.perf_counter() - load0
    catalog_pool = list(agent.cat.cats)

    order = list(ARMS) if rep % 2 == 0 else list(reversed(ARMS))
    prepared = []
    for fixture in F.FIXTURES:
        cfg = {**agent.cfg, **fixture.cfg}
        pool = catalog_pool[:fixture.pool_size]
        prepared.append((fixture, cfg, pool))

    # Calibrate, then publish a projected duration BEFORE doing the work, so
    # the parent's watchdog is set from what this fixture set actually costs on
    # this machine rather than from a constant that would be wrong the first
    # time the catalog or the host changed.
    projected = load_s
    for fixture, cfg, pool in prepared:
        agent.cfg = cfg
        arms = _arms(agent, cfg, pool)
        for name in order:
            sample = _time_arm(arms[name], _states(fixture, CALIBRATION_DISPATCHES))
            projected += sample["wall_ms"] / 1000 * (warmup + measured)
    _emit({"event": "plan", "rep": rep, "projected_seconds": round(projected, 1),
           "load_seconds": round(load_s, 2), "fixtures": len(prepared),
           "warmup": warmup, "measured": measured, "arm_order": order})

    fixtures: dict[str, dict] = {}
    for fixture, cfg, pool in prepared:
        agent.cfg = cfg
        arms = _arms(agent, cfg, pool)
        agreement = _agree(agent, fixture, cfg, pool)
        timings = {}
        for name in order:
            _time_arm(arms[name], _states(fixture, warmup))       # warm-up, discarded
            timings[name] = _time_arm(arms[name], _states(fixture, measured))
        legacy_ms, pure_ms = timings["legacy"]["wall_ms"], timings["pure"]["wall_ms"]
        fixtures[fixture.name] = {
            "branch_class": fixture.branch_class,
            "expects_selection": fixture.expects_selection,
            "arm_order": order,
            "legacy": timings["legacy"], "pure": timings["pure"],
            "legacy_ms": round(legacy_ms, 6), "pure_ms": round(pure_ms, 6),
            "overhead_ms": round(pure_ms - legacy_ms, 6),
            # A ratio against a ~1us baseline is a number with no useful
            # meaning; the no-scan gate is an absolute overhead for exactly
            # that reason, and None here says "not defined" rather than
            # printing a four-digit multiple of nothing.
            "ratio": round(pure_ms / legacy_ms, 6) if legacy_ms > 1e-6 else None,
            **agreement,
        }
        _emit({"event": "fixture", "rep": rep, "name": fixture.name,
               "legacy_ms": round(legacy_ms, 6), "pure_ms": round(pure_ms, 6)})

    return {"rep": rep, "arm_order": order, "warmup": warmup, "measured": measured,
            "load_seconds": round(load_s, 2), "projected_seconds": round(projected, 1),
            "fixtures": fixtures, "memory": rss_bytes(),
            "child_cpu_seconds": round(time.process_time(), 3),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}"}


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def child_main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--warmup", type=int, required=True)
    ap.add_argument("--measured", type=int, required=True)
    ap.add_argument("--catalog", default="data/catalog.jsonl")
    args = ap.parse_args(sys.argv[2:])
    try:
        _emit({"event": "result", "payload": child(args.rep, args.warmup,
                                                   args.measured, args.catalog)})
    except BaseException as exc:                      # KeyboardInterrupt included
        _emit({"event": "error", "rep": args.rep,
               "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


# ---------------------------------------------------------------------------
# The parent: spawn, watch, stream, journal.
# ---------------------------------------------------------------------------

def _drain(stream, sink: "queue.Queue") -> None:
    for line in iter(stream.readline, ""):
        sink.put(line)
    sink.put(None)


def run_repetition(rep: int, warmup: int, measured: int, catalog: str,
                   timeout: int = TIMEOUT_SECONDS) -> dict:
    """One repetition in a fresh interpreter, watched and bounded.

    Returns a record whether the child completed, timed out or died. An aborted
    repetition is RETAINED -- a repetition that stalled is evidence about the
    run, and the four-hour stall of Phase 6B2 produced no record at all because
    nothing was written until everything finished.
    """
    cmd = [sys.executable, "-u", "-m", "lab.benchmark", "--child",
           "--rep", str(rep), "--warmup", str(warmup), "--measured", str(measured),
           "--catalog", catalog]
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    lines: "queue.Queue" = queue.Queue()
    threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True).start()

    payload, events, probes, plan = None, [], [], None
    watchdog_at, watchdog_fired = None, False
    status, note = "completed", ""
    while True:
        elapsed = time.time() - started
        if elapsed > timeout:
            probes.append({"why": "timeout_kill", "elapsed_s": round(elapsed, 1),
                           **ps_snapshot(proc.pid)})
            proc.kill()
            status, note = "aborted", f"timeout after {timeout}s"
            break
        if watchdog_at and not watchdog_fired and elapsed > watchdog_at:
            watchdog_fired = True
            probes.append({"why": "watchdog_2x_projected", "elapsed_s": round(elapsed, 1),
                           "projected_s": plan and plan.get("projected_seconds"),
                           **ps_snapshot(proc.pid)})
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None and lines.empty():
                break
            continue
        if line is None:
            break
        try:
            obj = json.loads(line)
        except ValueError:
            events.append({"event": "unparsed", "text": line.rstrip()[:400]})
            continue
        if obj.get("event") == "plan":
            plan = obj
            watchdog_at = max(30.0, obj["projected_seconds"] * WATCHDOG_FACTOR)
            print(f"    rep {rep}: projected {obj['projected_seconds']:.0f}s "
                  f"(watchdog {watchdog_at:.0f}s, timeout {timeout}s), "
                  f"arm order {obj['arm_order']}", flush=True)
        elif obj.get("event") == "result":
            payload = obj["payload"]
        else:
            events.append(obj)

    code = proc.wait()
    stderr = (proc.stderr.read() or "")[-2000:] if proc.stderr else ""
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()
    if payload is None and status == "completed":
        status, note = "aborted", f"child produced no result (exit {code})"
    if code not in (0, -9) and status == "completed":
        status, note = "aborted", f"child exited {code}"

    record = {
        "rep": rep, "status": status, "note": note,
        "returncode": code,
        "elapsed_seconds": round(time.time() - started, 2),
        "warmup": warmup, "measured": measured,
        "plan": plan, "probes": probes, "events": events[-20:],
        "stderr_tail": stderr.strip()[-1000:],
        **(payload or {}),
    }
    if status != "completed":
        # Non-citable by the SAME predicate the score ledger uses. An aborted
        # repetition that stayed quotable would put us straight back where 6B2
        # was -- four rows presented as if they were the registered seven.
        record["invalid"] = {"reason": f"repetition_{status}", "note": note,
                             "marked_ts": _now()}
    return record


def _fixture_coverage(record: dict) -> str:
    """"" if the repetition measured every registered branch class correctly."""
    fixtures = record.get("fixtures") or {}
    missing = [f.name for f in F.FIXTURES if f.name not in fixtures]
    if missing:
        return "fixtures missing: " + ", ".join(missing)
    classes = {v["branch_class"] for v in fixtures.values()}
    absent = [c for c in F.REQUIRED_CLASSES if c not in classes]
    if absent:
        return "branch classes not measured: " + ", ".join(absent)
    for name, got in fixtures.items():
        if not got.get("attribute_agrees") or not got.get("state_agrees"):
            return f"{name}: the two arms disagree; a speed comparison is meaningless"
        if not got.get("branch_as_registered"):
            return (f"{name}: took branch {got.get('selection_mode')!r}, registered "
                    f"{got.get('expects_selection')!r}")
    return ""


def repetitions(tag: str, reps: int, warmup: int, measured: int, catalog: str,
                timeout: int = TIMEOUT_SECONDS) -> list[dict]:
    """Run `reps` repetitions, streaming one journalled row after each.

    Rows go to the lease journal when one is held and to the ledger directly
    otherwise, exactly as lab/record.py does -- an unleased row carries
    lease=None and is not citable, rather than being refused and lost.
    """
    journal = L.journal_path()
    target = journal if journal is not None else LOG
    rows = []
    for rep in range(reps):
        print(f"  === repetition {rep + 1}/{reps} ===", flush=True)
        record = run_repetition(rep, warmup, measured, catalog, timeout)
        broken = _fixture_coverage(record) if record["status"] == "completed" else ""
        if broken:
            record["status"] = "aborted"
            record["note"] = broken
            record["invalid"] = {"reason": "fixture_coverage", "note": broken,
                                 "marked_ts": _now()}
        row = {
            "schema_version": R.SCHEMA_VERSION,
            "benchmark_schema_version": BENCH_SCHEMA_VERSION,
            "kind": "benchmark", "tag": tag, "scenario": f"bench-rep-{rep}",
            "config": {"reps": reps, "warmup": warmup, "measured": measured,
                       "timeout_seconds": timeout},
            "ts": _now(),
            "python": platform.python_version(),
            "fixture_sha256": F.fixture_sha256(),
            "weights_frozen_ts": W.FROZEN_TS,
            "weights": dict(W.WEIGHTS),
            "seconds": record["elapsed_seconds"],
            **R.git_state(),
            **record,
        }
        if journal is None:
            row["lease"] = None
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())        # a killed parent must not lose a rep
        rows.append(row)
        got = row.get("fixtures") or {}
        print(f"    rep {rep}: {row['status']} in {row['elapsed_seconds']:.1f}s, "
              f"{len(got)} fixtures", flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lab.benchmark")
    ap.add_argument("--tag", default="")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1000)
    ap.add_argument("--measured", type=int, default=10000)
    ap.add_argument("--catalog", default="data/catalog.jsonl")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    ap.add_argument("--no-lease", action="store_true",
                    help="run without the lease; rows are recorded NON-CITABLE")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--show-excluded", action="store_true")
    args = ap.parse_args(argv)

    if args.report:
        from lab import benchreport
        print(benchreport.report(tag=args.tag or None,
                                 show_excluded=args.show_excluded))
        return 0
    if not args.tag:
        ap.error("--tag is required for a run")

    script = ("from lab import benchmark as B\n"
              f"B.repetitions({args.tag!r}, {args.reps}, {args.warmup}, "
              f"{args.measured}, {args.catalog!r}, {args.timeout})\n")
    if args.no_lease:
        print("WARNING: --no-lease; these rows will never be citable",
              file=sys.stderr)
        repetitions(args.tag, args.reps, args.warmup, args.measured,
                    args.catalog, args.timeout)
        return 0
    with L.lease(f"benchmark-{args.tag}", log=str(LOG)) as held:
        held.run(script, expected_cells=args.reps)
    print(f"\nlogged to {LOG}: lease {held.verdict} {held.broke}")
    return 0


if __name__ == "__main__":
    sys.exit(child_main() if "--child" in sys.argv[1:2] else main())
