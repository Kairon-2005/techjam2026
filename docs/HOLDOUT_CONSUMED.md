# Synthetic holdout — consumed 2026-08-30T10:21:52

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
| TechnicalScore | **0.455057** | 0.437447 | 0.932067 |
| HR@10 | **0.58** | 0.555 | 0.995 |
| MRR | **0.205322** | 0.210155 | 0.852556 |
| MTTC | **5.827** | 6.155 | 2.06 |

`sup-val` is A0 on a *different* 200 sessions from the same generator, measured
during Phase 7. **The holdout and `sup-val` agree closely; both are far from
public.** That is the shape the result was expected to have, and it is the
useful part: the shipped configuration is **stable across unseen sessions from
the same generator**, and the distance to public is a **distribution difference,
not overfitting to `supplementary_dev`.**

By scenario:

| scenario | HR@10 | MRR | MTTC |
|---|---|---|---|
| `boundary` | 0.5800 | 0.240413 | 6.0400 |
| `browsing` | 0.5850 | 0.197347 | 5.6100 |
| `buying` | 0.5800 | 0.211438 | 5.5800 |
| `intent_override` | 0.5667 | 0.198582 | 6.9933 |

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
| ledger row | `24f703dc750a918a` in `lab/holdout.jsonl` |
| lease | `valid` |
| agent commit | `bff3b2d` |
| agent sha256 | `cc3fc262c1fbaf30` |
| catalog sha256 | `da979b05a68af864` |
| dataset sha256 | `8f0dae7f2ab39d45` |
| wall clock | 143.9 s |
