# 06 — Three-way adversarial review: findings, fixes and rulings

An archive of the cross-checks by three independent subagents (code correctness /
statistical methodology / submission compliance). Every finding was re-verified
on the main line, and after the fixes 0.9280 reproduces exactly.

## A. Code review — fixed

| severity | finding | disposition |
|---|---|---|
| MED | `w_pos` branch variable shadowing: `order` (the tie-break index) was overwritten by the product's feature list, so ranking ties broke on feature-string lexicographic order rather than recall order. The historical `w_pos` ablation numbers are therefore untrustworthy | renamed to `ordered_vals`; `w_pos` was already off by default |
| MED | `Agent.__init__` runs outside the evaluator's try/except: a wrong working directory gives FileNotFoundError, and a stale bad `TJ_CONFIG` on the host gives JSONDecodeError. Either kills the whole evaluation rather than one turn | catalog paths now resolve relative to `__file__`; `TJ_CONFIG` is parsed tolerantly |
| LOW | the contract's `maxItems:100` could be exceeded with an unusual `top_k`; a non-string message raised AttributeError; `term_cap=0` triggered an FTS5 syntax error | all clamped or guarded |
| — | the review confirmed as safe: FTS5 injection is impossible (`TOKEN_RE` allowlist), division by zero is guarded everywhere, no catastrophic regex backtracking (a 200KB message takes under 118ms), standard-library-only is accurate, every return structure is contract-compliant, and 8-thread concurrency passes |

## B. Methodology review — wording rulings (the most important part)

1. **[adopted] "CV 0.9280" is a restatement of the training split.** The same
   configuration wins on all 5 training folds, so the fold mean is identically
   the full-set score; and the stopword list, cue regular expressions, feature
   definitions and the decision path of roughly 112 full-set evaluations were all
   made while looking at the same 200 sessions, which the fold procedure cannot
   cover. Correct phrasing: *"development-set score 0.9280; a 5-fold check
   confirms the weight configuration is stable under 160-session subsampling
   (fold scores 0.898-0.959), but this is not an unbiased estimate of
   generalization."*
2. **[adopted] The +/-0.005 does not hold.** The standard deviation has only 4
   degrees of freedom (95% CI [0.012, 0.058]), and roughly 112 adaptive
   comparisons add winner's-curse inflation of about 0.02-0.03. Honest interval:
   **0.90-0.93 on an in-distribution private set; 0.67-0.86 under unseen
   paraphrase styles (the pre-fix stress numbers); no lower bound at all if the
   simulator's internals differ.**
3. **[adopted] The stress test is circular.** The cue and noise regular
   expressions correspond one-to-one with the three test styles (`any \w+ is
   fine` is word-for-word identical to test sentence s2). `stress.py` has been
   relabelled as a regression suite, and the pre-fix numbers are presented
   alongside as the honest estimate for unseen styles.
4. **[adopted] "Constraint text is preserved verbatim" is our assumption, not a
   guarantee in the specification.** The sentence in the spec is about scoring,
   not about message wording. This is now declared explicitly as an assumption at
   the top of `stress.py`.
5. **[adopted and fixed] The `ask="other"` disaster branch.** If the private
   simulator implemented "other" as "match only unclassified constraints" (which
   for this data is 0), the old agent would ask dry questions forever. A runtime
   fallback was added: after 2 consecutive dry replies it degrades to cycling
   concrete attributes. Zero cost on the clean simulator (0.9280 measured
   unchanged).
6. **[adopted] The popularity-prior wording is downgraded.** The direction
   transfers (real recommender systems also have popularity bias), but the
   magnitude (targets at the 99.5th percentile) is a constructive artefact of
   5-core sampling, and the README does not promise the private set is
   identically distributed. Basis for the downgrade: `no_pop` still scores 0.867
   and `half_pop` 0.925, so failure is gradual.
7. **[adopted] The upper-bound derivation was wrong.** Browsing and boundary
   sessions do hit on turn 1 in the measured data, so min MTTC is 1.390, not
   1.890. notes/04 has been corrected.
8. All number checks passed (baseline, 0.7536, 0.9280, each ablation delta,
   super-additivity, `w_card` +0.0033). One record correction: the tuning run was
   58 runs, not the 30 written in the notes.

## C. Compliance review — the gap list

**Clean:** `evaluator/` byte-for-byte unchanged (`git diff` empty), no secrets, no
networking code, the official test suite passing 3/3, and
`python3 -m evaluator.local_evaluator` reproducing 0.927958 in one command.

**Gaps, ordered by risk:**
1. **The Devpost layer is at 0%:** project description, our own public repository
   with a README, and a YouTube demo video. However good the agent is, the
   submission is invalid without these.
2. **Competition-window rules:** "significantly updated during the submission
   period" -- the core system was built 4 days before the window. Mitigation:
   commit honestly before the window so it is timestamped, do substantive work
   inside it (the MRR push, the verbose residual, the information-gain
   experiment, the report, the video), and say so plainly in the Devpost
   description.
3. **The report and disclosures are unwritten:** method / model choice /
   limitations, latency + token + cost disclosure, the network-access statement,
   and the offline-fallback description. The material is all in `notes/` and needs
   assembling in English.
4. **Reproduction packaging:** `requirements.txt` (noting stdlib-only plus the
   FTS5 dependency), a declared Python version, documented `TJ_CONFIG`, a
   `submission/` layout, and a demonstration session transcript.
5. Documentation consistency: 0.9291 and 0.9280 have been unified to 0.9280; the
   `_card4` reverse-engineered feature must be disclosed proactively in the
   report (it is off by default) rather than left for a judge to discover.
