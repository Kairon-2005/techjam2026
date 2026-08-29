"""The one path from profile telemetry to a Phase 6C1 gate verdict.

Three rules, all of them learned somewhere else in this project and applied
here before they had to be:

  ONE OBSERVATION PER UNIQUE SAMPLE. All seven scenarios are built from the
  same 200 public samples, and five of them run five seeds, so a sample's
  profile and its target recur up to 35 times. Pooling would put the same user,
  the same nine tags and the same target into one binomial denominator that
  many times -- shrinking D5's interval by roughly six while adding no
  independent evidence, so a null effect would clear alpha=0.01 on replication
  alone. Primary inference is `clean` only, which is the one arm where sample,
  profile and target each appear exactly once.

  A GATE THAT CANNOT FAIL IS NOT A GATE. D5 has three negative controls and one
  positive control (tests/test_profile_gates.py), because tie handling, test
  choice and the effect-size conjunct are the three ways it could silently
  become unfailable.

  BELOW THE MINIMUM IS NEITHER PASS NOR FAIL. `insufficient_data` is a third
  verdict and may not be reported as either of the other two.

The target is joined HERE, in the lab, after the ProfileDecision exists. It
never reaches the Agent.
"""
from __future__ import annotations

import dataclasses
import math
import statistics
from collections import Counter

import starter.context as C

PASS = "pass"
FAIL = "fail"
INSUFFICIENT = "insufficient_data"

# Pre-registered in notes/37-phase6c-design.md revision 3. Read from here, not
# from prose: a gate written down only in a notes file is remembered slightly
# differently each time it is applied.
D1_MIN_CREDIBLE_SESSION_RATE = 0.20
D2_MAX_MEDIAN_JACCARD = 0.30
D5_ALPHA = 0.01
D5_MIN_MEDIAN_MARGIN = 0.10
MIN_SESSIONS = 30

# The primary population. Not a preference -- pooling is a stop condition.
PRIMARY_SCENARIO = "clean"


@dataclasses.dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    verdict: str
    n: int
    detail: dict = dataclasses.field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == PASS


@dataclasses.dataclass(frozen=True, slots=True)
class SessionObservation:
    """One session's FIRST recommendation turn. One row per unique sample."""
    sample_id: str
    scenario: str
    tags: tuple[C.ProfileTagVerdict, ...] = ()
    credible: tuple[str, ...] = ()
    verdict: str = C.ProfileSessionVerdict.NO_SIGNAL.value
    window_size: int = 0


def first_turn_observations(rows) -> list[SessionObservation]:
    """The first-recommendation-turn row of each session, one per session.

    Later turns are evolution telemetry and are never a gate input: a gate fed
    every turn could be met by one verbose session contributing twenty rows.
    """
    out: list[SessionObservation] = []
    for row in rows:
        if not row.get("profile_first_recommendation_turn"):
            continue
        out.append(SessionObservation(
            sample_id=str(row.get("sample_id") or ""),
            scenario=str(row.get("scenario") or ""),
            tags=tuple(C.ProfileTagVerdict(
                tag=t["tag"], category=C.ProfileTagCategory(t["category"]),
                match_count=int(t["match_count"]), coverage=float(t["coverage"]))
                for t in (row.get("profile_tags") or ())),
            credible=tuple(row.get("profile_credible_tags") or ()),
            verdict=str(row.get("profile_session_verdict") or ""),
            window_size=int(row.get("profile_window_size") or 0)))
    return out


def primary(observations, scenario: str = PRIMARY_SCENARIO) -> list[SessionObservation]:
    """The primary population: one observation per unique sample, one scenario.

    Raises on a duplicated sample_id rather than de-duplicating quietly. A
    duplicate means the caller pooled seeds or scenarios, and silently fixing
    it would hide the exact error this module exists to prevent.
    """
    kept = [o for o in observations if o.scenario == scenario]
    counts = Counter(o.sample_id for o in kept)
    dupes = sorted(s for s, n in counts.items() if n > 1)
    if dupes:
        raise ValueError(
            f"pseudo-replication: {len(dupes)} sample_id(s) appear more than "
            f"once in {scenario!r} (first: {dupes[:3]}). Primary inference "
            f"takes ONE observation per unique sample.")
    return kept


def category_counts(observations) -> dict[str, int]:
    out = {c.value: 0 for c in C.PROFILE_CATEGORY_PRECEDENCE}
    for obs in observations:
        for verdict in obs.tags:
            out[verdict.category.value] += 1
    return out


# ---------------------------------------------------------------------------
# D1 / D2 / D3
# ---------------------------------------------------------------------------

def d1_credible_session_rate(observations) -> GateResult:
    """Could personalization fire often enough to matter?"""
    n = len(observations)
    if n < MIN_SESSIONS:
        return GateResult("D1", INSUFFICIENT, n, {"reason": "below minimum"})
    with_credible = sum(1 for o in observations if o.credible)
    rate = with_credible / n
    return GateResult("D1", PASS if rate >= D1_MIN_CREDIBLE_SESSION_RATE else FAIL,
                      n, {"sessions_with_credible_tag": with_credible,
                          "rate": round(rate, 4),
                          "threshold": D1_MIN_CREDIBLE_SESSION_RATE})


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def d2_between_user_separation(observations) -> GateResult:
    """Would it say anything DIFFERENT per user?

    Only non-empty credible sets: Jaccard is undefined on two empty sets, and
    imputing 1.0 (identical) or 0.0 (separated) rigs the gate in opposite
    directions. Empty-credible sessions are already counted by D1.
    """
    sets = [frozenset(o.credible) for o in observations if o.credible]
    n = len(sets)
    if n < MIN_SESSIONS:
        return GateResult("D2", INSUFFICIENT, n,
                          {"reason": "fewer than 30 non-empty credible sets"})
    pairs = [_jaccard(sets[i], sets[j])
             for i in range(n) for j in range(i + 1, n)]
    median = statistics.median(pairs)
    return GateResult("D2", PASS if median <= D2_MAX_MEDIAN_JACCARD else FAIL, n,
                      {"median_jaccard": round(median, 4),
                       "threshold": D2_MAX_MEDIAN_JACCARD, "pairs": len(pairs)})


def d3_support_and_ceiling(observations, ceiling: float = 0.5) -> GateResult:
    """Every credible tag has support and is not ubiquitous.

    This is exactly the `specific_informative` condition -- match_count >= 1 and
    coverage <= ceiling -- re-checked from the recorded per-tag numbers rather
    than trusted from the category label. Revision 1's [0.05, 0.50] lower bound
    rejected the singleton hit, which is the most informative case available.
    """
    n = len(observations)
    if n < MIN_SESSIONS:
        return GateResult("D3", INSUFFICIENT, n, {"reason": "below minimum"})
    checked = violations = 0
    for obs in observations:
        for verdict in obs.tags:
            if verdict.category is not C.ProfileTagCategory.SPECIFIC_INFORMATIVE:
                continue
            checked += 1
            if verdict.match_count < 1 or verdict.coverage > ceiling:
                violations += 1
    if not checked:
        return GateResult("D3", FAIL, n, {"reason": "no credible tag to check"})
    return GateResult("D3", PASS if violations == 0 else FAIL, n,
                      {"credible_tags_checked": checked, "violations": violations})


# ---------------------------------------------------------------------------
# D5 -- target alignment. Lab only.
# ---------------------------------------------------------------------------

def alignment(credible_tags, text: str) -> float:
    """Share of credible tags matching this candidate. Uses the SHARED kernel."""
    tags = tuple(credible_tags)
    if not tags:
        return 0.0
    return sum(1 for t in tags if C.profile_match(t, text)) / len(tags)


def session_margin(credible_tags, target_text: str,
                   other_texts) -> float | None:
    """align(target) - median align(non-target). None when not computable."""
    others = list(other_texts)
    if not credible_tags or not others:
        return None
    baseline = statistics.median([alignment(credible_tags, t) for t in others])
    return alignment(credible_tags, target_text) - baseline


def binomial_p_value(wins: int, n: int) -> float:
    """One-sided exact binomial, H0: p = 0.5. Exact, never approximated.

    A normal approximation is anti-conservative at these n -- it would pass
    borderline cases the exact test rejects, which is the direction that
    matters for a gate.
    """
    if n <= 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / (2 ** n)


def d5_target_alignment(margins) -> GateResult:
    """Do the credible tags point at the RIGHT product?

    D1-D3 ask whether credible tags exist, differ between users and have usable
    support. Rare random noise satisfies all three by construction. This asks
    the question they do not.

    Wins and median margin are CONJUNCTIVE. Significance alone passes any
    trivial effect at large n; margin alone passes noise at small n.
    Exact ties count as NOT a win -- with few credible tags `alignment` is
    coarse and ties are common, so the conservative choice is load-bearing.
    """
    values = [m for m in margins if m is not None]
    n = len(values)
    if n < MIN_SESSIONS:
        return GateResult("D5", INSUFFICIENT, n, {"reason": "below minimum"})
    wins = sum(1 for m in values if m > 0)
    p = binomial_p_value(wins, n)
    median = statistics.median(values)
    ok = p <= D5_ALPHA and median >= D5_MIN_MEDIAN_MARGIN
    return GateResult("D5", PASS if ok else FAIL, n, {
        "wins": wins, "win_rate": round(wins / n, 4), "p_value": p,
        "alpha": D5_ALPHA, "median_margin": round(median, 4),
        "min_median_margin": D5_MIN_MEDIAN_MARGIN,
        "significant": p <= D5_ALPHA,
        "margin_sufficient": median >= D5_MIN_MEDIAN_MARGIN})


# ---------------------------------------------------------------------------
# D4 -- the instrument check
# ---------------------------------------------------------------------------

def d4_instrument(b1_all_pass: bool, b2_gates, b2_eligible: int) -> GateResult:
    """B1 every fixture, AND B2 eligible >= 30 passing D1, D2, D3 and D5.

    B2 must clear D5 and not merely D1-D3: an instrument check that skipped it
    would certify a classifier unable to tell the target from anything else,
    which is the exact failure D5 exists to catch on Arm A. B0 is diagnostic
    and contributes nothing in either direction.
    """
    named = {g.name: g for g in b2_gates}
    required = ("D1", "D2", "D3", "D5")
    missing = [k for k in required if k not in named]
    if missing:
        return GateResult("D4", INSUFFICIENT, b2_eligible,
                          {"reason": f"B2 gates missing: {missing}"})
    if b2_eligible < MIN_SESSIONS:
        return GateResult("D4", INSUFFICIENT, b2_eligible,
                          {"reason": "B2 eligible sessions below minimum"})
    insufficient = [k for k in required if named[k].verdict == INSUFFICIENT]
    if insufficient:
        return GateResult("D4", INSUFFICIENT, b2_eligible,
                          {"reason": f"B2 gates insufficient: {insufficient}"})
    failed = [k for k in required if not named[k].passed]
    ok = b1_all_pass and not failed
    return GateResult("D4", PASS if ok else FAIL, b2_eligible, {
        "b1_all_fixtures_pass": bool(b1_all_pass),
        "b2_failed_gates": failed, "b2_eligible": b2_eligible})


def phase_verdict(d1, d2, d3, d5, d4) -> dict:
    """The pre-registered outcome, with its wording fixed.

    A D5 failure says target alignment WAS NOT DEMONSTRATED on this set. It
    does not say the profile signal does not exist: absence of demonstrated
    alignment on 200 samples of one public corpus is not proof of absence, and
    the stronger sentence is not one this evidence can carry.
    """
    gates = {g.name: g for g in (d1, d2, d3, d5, d4)}
    if any(g.verdict == INSUFFICIENT for g in gates.values()):
        thin = sorted(k for k, g in gates.items() if g.verdict == INSUFFICIENT)
        return {"design_6c2": False, "verdict": INSUFFICIENT,
                "reason": f"{thin} returned insufficient_data; a gate below its "
                          f"minimum is neither passed nor failed",
                "gates": gates}
    if not d4.passed:
        return {"design_6c2": False, "verdict": FAIL,
                "reason": "the instrument check (D4) did not hold, so no "
                          "conclusion about Arm A is admissible",
                "gates": gates}
    if not d5.passed:
        return {"design_6c2": False, "verdict": FAIL,
                "reason": "target alignment was not demonstrated on the public "
                          "clean set",
                "gates": gates}
    if not (d1.passed and d2.passed and d3.passed):
        failed = sorted(k for k in ("D1", "D2", "D3") if not gates[k].passed)
        return {"design_6c2": False, "verdict": FAIL,
                "reason": f"{failed} did not pass on the official arm",
                "gates": gates}
    return {"design_6c2": True, "verdict": PASS,
            "reason": "D1-D3 and D5 pass on Arm A and D4 holds",
            "gates": gates}
