# Phase 7A-R1 results — A0 keeps the default

**The answer: `score_default` stays A0.** A1 and A2-10 both qualified on
`sup-val`; A1 was the finalist; A1 failed **every one** of the section 9 public
gates. That outcome was pre-registered as acceptable (`notes/44` §7b Step 4) and
is reported as what it is. **Nothing was retuned and nothing was re-run.**

Every number below comes from a citable row — a valid lease, an isolated
worktree, every input fingerprint present (`lab/provenance.py`). Ledgers:
`lab/a1builds.jsonl`, `lab/a2builds.jsonl`, `lab/r1builds.jsonl`,
`lab/supval.jsonl`, `lab/public.jsonl`.

*This document replaced an earlier draft written while the A2 environment was
missing. The ledger rows are the immutable record; this is the report over
them, and §6 says what changed.*

## 1. Headline

| | `sup-train` MRR | `sup-val` MRR Δ | public `clean` MRR Δ |
|---|---|---|---|
| **A1** | 0.202827 → **0.422520** | **+0.228613** | **−0.116467** |
| **A2-10** | 0.202827 → **0.218575** | **+0.008248** | not run — see §5 |

**A1 is the same arm in all three columns.** It roughly doubles MRR on the
supplementary corpus and loses 14% of it on the public set. **The two corpora
disagree about what a good ranker is**, and that is the phase's real finding.

## 2. The split and the caches

| | |
|---|---|
| `sup-train` | 800 = 320/320/120/40, `48d14de25a4adf90adbcd9ad621ea2e1d143bd5632a8be67fed239ff4822290d` |
| `sup-val` | 200 = 80/80/30/10, `82e0470ee83d2cf8883399ededda11b5ddb4fa762685196b36a9fe521a105a73` |
| A1 cache | `41bd811310e5364abc83d7a6b531164f79726dd331e177c76e73824a5ed6f553` |
| A2 cache | `4b6092d98b4b7336ce3fa7934d09925b35307269d48d51ff4f1fbcf57792233c` |

The superseded 806/194 global-hash split is refused **by count alone**, and a
negative test rebuilds it — accidental strata included, `boundary` 38/12 — and
requires rejection.

**The A1 replay gate, before trial 0:** 800 sessions, 4402 turns, **0**
full-order mismatches, and cached MRR = live A0 MRR = evaluator MRR =
`0.20282688492063491`, both deltas **exactly 0**. Three independent leased
builds at three commits produced the same hash.

**The gate earned its keep.** The first build showed cached 0.199835 against the
evaluator's 0.202827. Revision 4 §0.4 had predicted such a gap would be
`_rotate`; **`_rotate` fired on 0 turns**. The difference was the evaluator's
`override_applied` rule, unimplemented in the cached objective: on an
`intent_override` session a Top-10 hit *before* the override turn scores
nothing. Wrong in both directions — it credited hits the evaluator discards, and
a credited hit also stopped the session so the later scoring hit was never
reached — and it disagreed on **35 of 120** override sessions. Corrected in
revision 5 §0.8, before any trial.

## 3. A1 — frozen, then falsified

189 trials (3 sweeps × 9 weights × 7 grid points, asserted). Deterministic: two
leased runs at different commits returned the identical vector, MRR and 16
accepted moves.

| weight | default | frozen | | weight | default | frozen |
|---|---|---|---|---|---|---|
| `w_bm25` | 0.3 | **2.4** | | `w_neg` | 2.0 | 2.0 |
| `w_cat` | 1.0 | **8.0** | | `w_phrase` | 5.0 | **1.25** |
| `w_exact` | 1.5 | **3.0** | | `w_pop` | 4.0 | **1.0** |
| `w_field` | 2.0 | **0.0** | | `slot_soft` | 4.0 | **0.0** |
| `w_idf` | 0.25 | **2.0** | | | | |

`delta_mrr` **+0.219693** → not a no-op → finalist-eligible.

**The off-policy caveat did not bite.** A1's on-policy `sup-val` gain
(**+0.228613** MRR) *exceeded* its off-policy cache gain (+0.219693), so the
cache understated the arm rather than inflating it.

**The `w_pop` caution did bite, exactly as written before the run.** `w_pop`
4.0 → 1.0 runs against the public set's strongest single feature (`NOTES.md`:
`w_pop = 0` costs −0.061 there), because public targets sit at the 99.5th
popularity percentile as an artefact of Amazon 5-core sampling.

## 4. A2-10 — feasible, invariant, and never spent

**Integrated feasibility on the real corpus** — the 390 `sup-train` queries and
3900 product texts `lab/r1_fields.py` wrote out, seven fresh processes:

| gate | limit | measured | |
|---|---|---|---|
| Top-10 p95 | ≤ 25 ms | **15.95 ms** | **PASS** (spread 0.18 ms) |
| additional cold load | ≤ +5 s | **+0.42 s** | **PASS** |
| RSS delta | ≤ +400 MB | **+131.6 MB** | **PASS** |
| offline load | required | yes | **PASS** |
| deterministic | required | one signature | **PASS** |
| bad permutations | — | **0** | — |

R0.1 measured 15.23 ms on synthetic fixtures; integration did not move it. The
field store costs **18.94 MB** and **+0.42 s** of catalog build.

**Thread sensitivity, diagnostic:** 1 → 16.29 ms, 2 → 11.83, 4 → 11.60, ORT
default → 15.95. The default is *slower* than 2 or 4 — oversubscription on a
batch of ten short sequences — and **every setting clears the 25 ms cap**, which
retires R0.1's recorded risk that a judging host with fewer cores might differ.

**λ, over a cache that is exact rather than approximate:**

| λ | `sup-train` MRR | Δ |
|---|---|---|
| 0.00 | 0.202826885 | +0.000000000 |
| 0.10 | 0.202826885 | +0.000000000 |
| 0.25 | 0.206078373 | +0.003251488 |
| 0.50 | 0.212678075 | +0.009851190 |
| **1.00** | **0.218575397** | **+0.015748512** |

Three checks rather than three arguments. HR@10 0.56125 and MTTC 5.94125 are
identical at **every** λ. The full Agent re-run at λ = 1.0 returns the cached
number **to the last digit**. The full Agent re-run at λ = 0 returns
`0.20282688492063491` — byte-identical to A0, and equal to the independent A1
cache's baseline, which cross-checks the two caches against each other.

**λ = 1.0 sits at the edge of the frozen grid**, so the optimum may lie outside
it. The grid is five points and extending it now would be searching more than
was registered.

## 5. `sup-val`, and the finalist

200 rows, **one leased process per arm** — the catalog cache, an ONNX session
and a tokenizer are module-level singletons, and a paired comparison is worth
nothing if the pairing is what broke it.

| arm | composite | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| A0 | 0.437447 | 0.5550 | 0.210155 | 6.1550 |
| **A1** | **0.717730** | **0.8600** | **0.438768** | **3.1950** |
| A2-10 | 0.439921 | 0.5550 | 0.218403 | 6.1550 |

| | composite Δ | MRR Δ | HR@10 Δ | MTTC Δ |
|---|---|---|---|---|
| A1 | +0.280283 | +0.228613 | +0.305 | −2.96 |
| A2-10 | +0.002474 | +0.008248 | **0.000** | **0.00** |

**A2's HR@10 and MTTC are exactly invariant in the full-Agent run** — the proof
`notes/44` §1 derived from the evaluator's source survives contact with the real
pipeline. Its telemetry carries **0 invalidating turns** and **0 λ = 0
violations**, with the model invoked on 97 of 1142 turns (8.5%, against 8.9% on
`sup-train`).

Both clear the Step 1 floors *and* the Step 1b qualification. **Step 2 selects
A1 on higher `sup-val` MRR.**

**A2-10 was therefore never run on the public 200 and never will be for this
phase.** Whether it would have passed is a question the pre-registration forbids
asking after the fact (§7b Step 3, §10): one finalist, one run, one number. Its
`sup-val` result is a supplementary result and is not promoted to a public
claim.

## 6. The one public confirmation — every gate fails

Run once, for A1 alone. `lab/public.py` enforces that mechanically: it refuses
to start if any confirmation row already exists, refuses any arm but the
selected finalist, and refuses to run at all if no arm qualified.

| arm | composite | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| A0 | **0.932067** | 0.9950 | 0.852556 | 2.0600 |
| A1 | 0.887327 | 0.9850 | 0.736089 | 2.3000 |

A0 reproduced the frozen anchor `0.932067` exactly, so the control did not
drift.

| gate | requirement | measured | |
|---|---|---|---|
| `clean` MRR | Δ ≥ **+0.010** | **−0.116467** | **FAIL** |
| composite | ≥ 0.932067 | 0.887327 | **FAIL** |
| official slices | no MRR regression | boundary −0.175, buying −0.136, browsing −0.118, intent_override −0.042 | **FAIL** |
| robustness | paired Δscore ≥ −0.005 | worst −0.130 (`uncooperative`) | **FAIL** |
| robustness | paired HR@10 drop ≤ 0.01 | worst −0.117 (`uncooperative`) | **FAIL** |

Per scenario: `vague_start` −0.071/−0.055, `uncooperative` −0.130/−0.117,
`override_genuine` −0.043/−0.008, `override_category` −0.046/−0.010,
`contradiction` −0.063/−0.040 (Δscore / ΔHR@10).

**`score_default` stays A0.** Not "the best of the two", not "the one that
regressed least" — A0. A challenger that cannot clear the bar it was measured
against has not earned the default, and the bar was set before it ran.

## 7. What this phase actually established

1. **The supplementary corpus and the public set want different rankers.** The
   same nine weights, fitted on one, lose 0.116 MRR on the other — and lose it
   on *every* slice and *every* robustness scenario. Any future claim fitted on
   supplementary data has to answer this before it means anything.
2. **`w_pop` is largely a public-set artefact**, and the arm that discovered
   that is the arm that failed because of it. Its 4.0 → 1.0 move is the single
   clearest expression of the distribution gap.
3. **A2-10 is feasible and provably safe**, at 15.95 ms p95 on real text, with
   HR@10 and MTTC exactly invariant, byte-exact A0 fallback, and no invalidating
   turn anywhere. It is also **worth about +0.008 MRR on `sup-val`** — real, and
   small. It bought a pinned model, an ONNX runtime and a vendored artifact for
   that.
4. **Off-policy caching did not inflate A1** — it understated it. The caveat was
   worth writing and was not what went wrong.
5. **Every gate that fired, fired before it could be argued with.** The `w_pop`
   caution, the off-policy caveat and the λ-edge caution were all committed
   before the runs that tested them.

## 8. What was not done

* **The sealed holdout was not touched at all** — including by the split guard,
  which tests corpus membership by namespace rather than by opening the file.
* **A2-10 did not reach public**, and must not now be run there as a
  consolation.
* **Nothing was retuned after a public number.** A1's weights, A2's λ and the
  gates all predate the runs that judged them.
