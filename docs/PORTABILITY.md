# Portability

Reported **separately for the two paths**, because they have different
dependencies and different evidence. Nothing here is claimed beyond what was
actually executed.

## `score_default` — the submitted, scored configuration

Standard library only, so there is no compiled dependency to be incompatible
with. The one platform requirement is **SQLite compiled with FTS5**, which
CPython's bundled SQLite provides on all mainstream builds.

| environment | status | evidence |
|---|---|---|
| **Python 3.14.6, Darwin arm64** | **VERIFIED** | full clean-checkout verification: 0.932067 / 0.995 / 0.852556 / 2.06, **828 tests executed, 0 skipped**, zero third-party imports — `docs/FINAL_VERIFICATION.md` |
| **Python 3.13.7, Darwin arm64** | **VERIFIED** | **813 tests executed, 0 skipped**; the official evaluator returns 0.932067 / 0.995 / 0.852556 / 2.06 exactly; third-party packages loaded: none |
| **Python 3.9.6, Darwin arm64** | **FAILS, and this is the floor** | `starter/context.py` uses `dataclasses.dataclass(slots=True)`, added in 3.10: `TypeError: dataclass() got an unexpected keyword argument 'slots'` |
| **Linux (Ubuntu), Python 3.10 / 3.11** | **VERIFIED** | both jobs succeeded in [GitHub Actions run 33294760426](https://github.com/Kairon-2005/techjam2026/actions/runs/33294760426): full suite, exact TechnicalScore 0.932067 reproduction, and zero third-party imports |

**On the current test count.** The release suite has **828 tests and every one
executes** in the current committed-tree verification. The earlier Python 3.13.7
portability run exercised the 813-test suite that existed at that commit. One
test -- `LeaseIsolationTest`, which needs a
committed tree in order to isolate a worktree -- skips itself when the working
copy is dirty, so a run taken mid-edit reports `813 ... OK (skipped=1)`. Both
verification environments above were committed trees, so both report 0 skipped;
`docs/FINAL_VERIFICATION.md` records exactly that.

**Minimum supported version: Python 3.10**, established by a concrete failure at
3.9.6 rather than by inspection. This matches the organizer's own guidance that
"Python 3.10 or later is recommended".

The headline performance set is still the detached clean-checkout run at commit
`6b3cd8b77bcc26f7aec7ef9598d38d3acc90d564` on Python 3.14.6 / Darwin arm64:
TechnicalScore 0.932067, HR@10 0.995, MRR 0.852556, MTTC 2.06, cold start
11.62 s, warm-turn p50/p95 25.571/30.445 ms, full evaluation 30.79 s and peak
RSS 710.3 MB. The Linux CI is an independent portability result on published
commit `ff25c6ff46219ce059afd729cc125cbf846a9714`; it confirms the exact score
and dependency/test gates, not Darwin latency or memory.

## `showcase_semantic` — the optional A2-10 cascade

| environment | status | evidence |
|---|---|---|
| **Darwin arm64** | **VERIFIED** | Top-10 p95 15.95 ms over 7 fresh processes, +0.42 s cold load, +131.6 MB RSS, offline load, deterministic output, 0 bad permutations — `notes/45` §4 |
| **any other architecture** | **NOT MEASURED, and not claimed** | the bundled file is `onnx/model_qint8_arm64.onnx`: the quantization targets **arm64**. A different architecture needs its own artifact and its own measurement |

The Linux workflow **deliberately excludes** this path. Running an
arm64-quantized artifact on an x86-64 runner would either fail or measure
something we do not ship, and neither outcome is informative.

If the model is unavailable on any host, the cascade returns **byte-exact A0
ordering** with a distinct reason code, so an unsupported platform degrades to
the scored path rather than breaking. That is a property of this one component,
verified by test -- not a general claim about the system.

## What would change this page

* A `model_qint8_x86.onnx` or equivalent artifact, which would need its own
  pinned revision, checksum and latency measurement before any claim.
