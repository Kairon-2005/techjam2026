"""The pre-registered benchmark fixtures, and nothing else.

Separated from lab/benchmark.py so the FIXTURE SET has its own hash. A
benchmark row records `fixture_sha256`, and a row whose fixtures changed must
not be comparable with one whose fixtures did not -- if the fixtures lived in
the runner, every edit to the runner's plumbing would silently look like a
change of what was measured, and every change of what was measured would look
like plumbing.

Each fixture is one BRANCH of the question controller. That is the whole point:
Phase 6B2 measured a single blended dispatch, reported one ratio, and could
therefore say nothing about which branch paid the cost -- the branch that does
no candidate work at all and the branch that scans every facet were averaged
into the same number. Phase 6B2-R2 registers a gate per branch class, so a
fixture must map to exactly one branch and must be asserted to do so.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json

# Branch classes. The gate a fixture is judged against comes from its class,
# not from its name, so adding a fixture cannot quietly invent a new gate.
NO_SCAN = "no_scan"                # zero candidate scans
CATEGORY_ONLY = "category_only"    # one bounded pass over cat.cats
POOL = "pool"                      # category summary + per-facet cat.text passes
# Phase 6C1. A bounded <= 8 x 30 = 240 shared-kernel match check. Its control
# arm does nothing at all, because profile shadow is purely ADDITIVE -- "off"
# takes no decision -- so the measured quantity is the decision's whole cost
# rather than a difference between two implementations of it.
PROFILE = "profile"

# The row schema names the two arms "legacy" and "pure" for historical reasons.
# They are really CONTROL and TREATMENT: for question fixtures that is the
# legacy controller against the staged one; for profile fixtures it is a no-op
# against the shadow decision.
QUESTION_COMPONENT = "question"
PROFILE_COMPONENT = "profile"


@dataclasses.dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    branch_class: str
    # The selection_mode the pure controller must report. Asserted at run time:
    # a fixture that stops exercising its branch (because a threshold moved, or
    # because the catalog changed) becomes a silently mislabelled measurement,
    # which is exactly the failure mode a per-branch gate is vulnerable to.
    expects_selection: str
    cfg: dict                      # config patch over Agent DEFAULTS
    state: dict                    # session-state patch over the blank state
    pool_size: int                 # candidates handed to the controller
    component: str = QUESTION_COMPONENT
    # Profile fixtures only: the raw preference_tags handed to the decision.
    profile_tags: tuple = ()


# `asked=["other","other"]` is what gets past the first-two-`other` guard under
# the shipped `other_then_pool` policy; without it every pool fixture would
# measure the first-two-`other` branch instead.
_PAST_GUARD = {"asked": ["other", "other"]}
_THREE_ASKED = {"asked": ["other", "other", "material", "color", "style"]}

FIXTURES: tuple[Fixture, ...] = (
    # --- no candidate evidence required -----------------------------------
    Fixture("first_two_other", NO_SCAN, "first_two_other",
            {"ask_policy": "other_then_pool"}, {"asked": []}, 30),
    Fixture("probe_cycle", NO_SCAN, "cycle",
            {"ask_policy": "probe_cycle"}, {"asked": ["other", "other"]}, 30),
    # --- category summary, no facet scans ---------------------------------
    # answerability_after=1 with uncertain_streak=3 takes the easier branch
    # BEFORE the give-up guard and before any facet is scanned.
    Fixture("easier", CATEGORY_ONLY, "easier",
            {"ask_policy": "pool", "answerability_after": 1},
            {**_PAST_GUARD, "uncertain_streak": 3}, 30),
    # overgeneral_cats=0 disables the over-generality suspension, which is what
    # lets the dry give-up fire; with the shipped 6 the pool is over-general and
    # the guard is suspended, so this branch would never be reached.
    Fixture("give_up", CATEGORY_ONLY, "give_up",
            {"ask_policy": "pool", "pool_give_up_after": 1, "overgeneral_cats": 0},
            {**_PAST_GUARD, "dry_streak": 3}, 30),
    # --- pool selection ----------------------------------------------------
    Fixture("pool_utility_on_none_asked", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": True, "pool_depth": 30},
            dict(_PAST_GUARD), 30),
    Fixture("pool_utility_off_none_asked", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": False, "pool_depth": 30},
            dict(_PAST_GUARD), 30),
    Fixture("pool_utility_on_three_asked", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": True, "pool_depth": 30},
            dict(_THREE_ASKED), 30),
    Fixture("pool_utility_off_three_asked", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": False, "pool_depth": 30},
            dict(_THREE_ASKED), 30),
    Fixture("pool_empty", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": True, "pool_depth": 30},
            dict(_PAST_GUARD), 0),
    Fixture("pool_one_item", POOL, "pool_selection",
            {"ask_policy": "pool", "question_utility": True, "pool_depth": 30},
            dict(_PAST_GUARD), 1),
)

# --- Phase 6C1 profile fixtures ------------------------------------------
# The control arm is a no-op, so these measure the decision's absolute cost.
# `_GENERIC` and `_MIXED` use words that do and do not occur in apparel text,
# so the fixtures exercise the generic and unsupported branches rather than
# only the cheap empty one. `_MAXIMAL` is the bound: eight tags at the
# MAX_PROFILE_TAG_CHARS limit, which is the worst case the decision can face.
_MAXIMAL = tuple(f"{'z' * 39}{i}" for i in range(8))

PROFILE_FIXTURES: tuple[Fixture, ...] = (
    Fixture("profile_no_tags", PROFILE, "n/a", {}, {}, 30,
            component=PROFILE_COMPONENT, profile_tags=()),
    Fixture("profile_generic", PROFILE, "n/a", {}, {}, 30,
            component=PROFILE_COMPONENT,
            profile_tags=("cotton", "black", "dress")),
    Fixture("profile_unsupported", PROFILE, "n/a", {}, {}, 30,
            component=PROFILE_COMPONENT,
            profile_tags=("titanium", "hydrofoil", "quartzite")),
    Fixture("profile_official_shape", PROFILE, "n/a", {}, {}, 30,
            component=PROFILE_COMPONENT,
            profile_tags=("fit", "material", "comfort", "style")),
    Fixture("profile_maximal", PROFILE, "n/a", {}, {}, 30,
            component=PROFILE_COMPONENT, profile_tags=_MAXIMAL),
)

FIXTURES = FIXTURES + PROFILE_FIXTURES

BY_NAME = {f.name: f for f in FIXTURES}

# Branch classes MUST be covered; a run that silently dropped every no-scan
# fixture would report a weighted aggregate that has no no-scan term in it.
REQUIRED_CLASSES = (NO_SCAN, CATEGORY_ONLY, POOL, PROFILE)


def spec() -> list[dict]:
    """The fixture set as plain data -- what gets hashed."""
    return [dataclasses.asdict(f) for f in FIXTURES]


def fixture_sha256() -> str:
    """Identity of the fixture set. Changes iff a fixture changes."""
    blob = json.dumps(spec(), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
