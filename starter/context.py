"""Context Programming foundation -- SHADOW MODE ONLY.

Makes explicit the context that is otherwise implicit in a 22-key session
dict, the slot list, route history and the per-turn trace. Nothing here
controls retrieval, ranking, question selection, slot mutation or
personalization: `decide()` returns a value and its only caller writes that
value to the trace.

The session dict remains the source of truth. `ContextSnapshot` is a VIEW over
it -- a second source of truth would be a correctness hazard, not a
foundation.

This module must not import Agent, DialogueMixin or RetrievalMixin. Every
function takes primitives, so it cannot reach a catalog, a lazy index or a
session even by accident. See notes/27-phase6a-design.md.
"""
from __future__ import annotations

import dataclasses
import json
import math
from enum import Enum
from typing import Mapping, Sequence

# Bounds. A snapshot that cannot be bounded is a log, not a context.
MAX_SLOT_VIEWS = 12          # SHARED across active/negative/abandoned
MAX_ASKED_FACETS = 10
MAX_PROFILE_TAGS = 8
MAX_ENTRIES = 64
MAX_BYTES = 4096
SLOT_VALUE_CHARS = 40


@dataclasses.dataclass(frozen=True, slots=True)
class SlotView:
    """One piece of evidence, flattened and truncated."""
    attribute: str
    value: str
    polarity: int
    hardness: str
    confidence: float
    source_turn: int
    state: str                       # "active" | "negative" | "abandoned"


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateCategorySummary:
    """Category spread of the ranked window, from ONE bounded pass."""
    category_count: int
    entropy: float
    overgeneral: bool
    option_count: int
    options: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ContextSnapshot:
    route: str
    previous_route: str
    turns_since_override: int
    override_count: int
    slots: tuple[SlotView, ...]
    active_constraint_count: int
    query_term_count: int
    dry_streak: int
    uncertain_streak: int
    starved: bool
    current_request_more: bool
    asked_facets: tuple[str, ...]
    previous_question_mode: str
    previous_question_bits: float
    previous_question_coverage: float
    pool_size: int
    pool_before_filter: int
    pool_after_filter: int
    category_count: int
    category_entropy: float
    overgeneral: bool
    overgeneral_option_count: int
    profile_source: str
    profile_credible: bool
    profile_tag_count: int
    profile_tags: tuple[str, ...]


class ReasonCode(str, Enum):
    """Stable symbols. Wording lives in `render`, so telemetry recorded today
    stays comparable after the phrasing changes."""
    BROADEN_THIN_DRY = "BROADEN_THIN_DRY"
    ROTATE_OR_BROADEN = "ROTATE_OR_BROADEN"
    SUPPRESS_ABANDONED = "SUPPRESS_ABANDONED"
    PROPOSE_RELAX_LOW_CONFIDENCE = "PROPOSE_RELAX_LOW_CONFIDENCE"
    ASK_STRUCTURED = "ASK_STRUCTURED"
    REJECT_PROFILE_PRIOR = "REJECT_PROFILE_PRIOR"


@dataclasses.dataclass(frozen=True, slots=True)
class ContextDecision:
    route: str
    retrieval_mode: str              # "standard" | "broaden"
    retrieval_depth: int
    clarification_mode: str          # "open" | "structured" | "easier" | "none"
    # Phase 6A cannot choose an ATTRIBUTE: the snapshot carries no per-facet
    # entropy or utility, and copying the real _pick_attribute result would be
    # a prediction that reads the answer. Phase 6B, after _pool_attribute is
    # refactored into a pure function over explicit facet statistics.
    clarification_attribute: None
    relaxation: tuple[str, ...]      # proposal only; nothing consumes it
    profile_credible: bool
    reasons: tuple[ReasonCode, ...]


# ---- canonical form -------------------------------------------------------

def canonical(snapshot: ContextSnapshot) -> str:
    """One serialization, so `snapshot_bytes` is reproducible across runs."""
    return json.dumps(dataclasses.asdict(snapshot), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def snapshot_bytes(snapshot: ContextSnapshot) -> int:
    return len(canonical(snapshot).encode("utf-8"))


def entry_count(snapshot: ContextSnapshot) -> int:
    """Scalars plus tuple members. A SlotView counts once, not per field."""
    total = 0
    for field in dataclasses.fields(snapshot):
        value = getattr(snapshot, field.name)
        total += len(value) if isinstance(value, tuple) else 1
    return total


# ---- bounded summaries ----------------------------------------------------

def summarize_categories(ranked: Sequence[str], category_by_asin: Mapping[str, str],
                         pool_depth: int, overgeneral_limit: int,
                         distinguishing) -> CandidateCategorySummary:
    """Category spread of the ranked window, in ONE pass over <= pool_depth.

    Reads `cat.cats` -- populated in _Catalog.__init__ and therefore always
    resident -- and NEVER `cat.category_index`, which is a lazy property whose
    first read builds the whole index: a catalog-wide scan and a hidden cold
    start on the compat path, which never otherwise needs it.

    The leaf is derived exactly as `_overgeneral()` derives it, so the count
    and the flag describe the same grouping. Taking the mapping as an argument
    rather than a catalog means this cannot reach an index even by accident.
    """
    if not overgeneral_limit or not ranked:
        return CandidateCategorySummary(0, 0.0, False, 0, ())
    head = list(ranked)[: max(2, int(pool_depth))]
    counts: dict[str, int] = {}
    for asin in head:
        leaf = (category_by_asin.get(asin, "").split(",")[-1] or "").strip()
        if leaf:
            counts[leaf] = counts.get(leaf, 0) + 1
    total = sum(counts.values())
    entropy = 0.0
    if total > 1 and len(counts) > 1:
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c)
    if len(counts) < overgeneral_limit:
        return CandidateCategorySummary(len(counts), round(entropy, 4), False, 0, ())
    top = [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    options = tuple(distinguishing(top))
    return CandidateCategorySummary(len(counts), round(entropy, 4), True,
                                    len(options), options)


def profile_coverage(tags: Sequence[str], ranked: Sequence[str],
                     text_by_asin: Mapping[str, str], pool_depth: int) -> dict[str, float]:
    """Share of the ranked window mentioning each tag.

    Bounded twice over: at most MAX_PROFILE_TAGS tags against at most
    pool_depth candidates, so at most 8 x 30 = 240 substring checks against
    text already in memory. No catalog-wide scan.
    """
    head = list(ranked)[: max(1, int(pool_depth))]
    kept = list(tags)[:MAX_PROFILE_TAGS]
    if not head or not kept:
        return {}
    blobs = [text_by_asin.get(asin, "") for asin in head]
    return {tag: round(sum(1 for b in blobs if tag in b) / len(blobs), 4)
            for tag in kept}


def _slot_views(slots: Sequence, recent_override: bool) -> tuple[SlotView, ...]:
    """The <= MAX_SLOT_VIEWS most decision-relevant slots, deterministically.

    The cap is SHARED across the three states, not per state: an earlier
    design allowed 12 each and so declared a 64-entry bound it could exceed
    at 73.

    Priority: recent explicit negatives and abandoned-by-override first,
    then active hard by confidence and recency, then active soft, then the
    rest of the abandoned. Ties break on source_turn descending, then
    attribute ascending, so the result never depends on dict ordering.
    """
    def state_of(slot) -> str:
        if slot.polarity < 0:
            return "negative"
        if not slot.active or not getattr(slot, "soft_ok", True):
            return "abandoned"
        return "active"

    def rank(slot) -> tuple:
        state = state_of(slot)
        if state == "negative" or (state == "abandoned" and recent_override):
            tier = 0
        elif state == "active" and slot.hardness == "hard":
            tier = 1
        elif state == "active":
            tier = 2
        else:
            tier = 3
        return (tier, -float(slot.confidence), -int(slot.source_turn), slot.attribute)

    chosen = sorted(slots, key=rank)[:MAX_SLOT_VIEWS]
    return tuple(SlotView(attribute=s.attribute,
                          value=str(s.value)[:SLOT_VALUE_CHARS],
                          polarity=int(s.polarity), hardness=s.hardness,
                          confidence=round(float(s.confidence), 4),
                          source_turn=int(s.source_turn), state=state_of(s))
                 for s in chosen)


def build_snapshot(*, state: Mapping, trace: Mapping, ranked: Sequence[str],
                   turn: int, cfg: Mapping, category_by_asin: Mapping[str, str],
                   text_by_asin: Mapping[str, str], distinguishing) -> ContextSnapshot:
    """Assemble the view. Pure: every input is a primitive or a mapping.

    Called after _rotate() and BEFORE _pick_attribute(), so `state["asked"]`
    and the last_* fields hold PRIOR-turn history and this turn's question is
    not visible -- it has not been chosen yet.
    """
    slots = list(state.get("slots") or ())
    last_override = int(state.get("last_override_turn") or 0)
    since = turn - last_override if last_override else turn
    recent_override = bool(last_override and since <= 1)
    views = _slot_views(slots, recent_override)

    history = list(state.get("route_history") or ())
    tags = [t for t in (state.get("profile") or {}).get("preference_tags") or [] if t]
    tags = [str(t).lower() for t in tags][:MAX_PROFILE_TAGS]
    coverage = profile_coverage(tags, ranked, text_by_asin, int(cfg["pool_depth"]))
    stated = {str(s.value).lower() for s in slots if s.polarity > 0 and s.active}
    ceiling = float(cfg["profile_max_coverage"])
    credible = tuple(t for t in tags
                     if t not in stated and coverage.get(t, 1.0) <= ceiling)

    summary = summarize_categories(ranked, category_by_asin, int(cfg["pool_depth"]),
                                   int(cfg["overgeneral_cats"]), distinguishing)
    return ContextSnapshot(
        route=str(state.get("route") or "mixed"),
        previous_route=str(history[-2] if len(history) > 1 else (history[0] if history else "")),
        turns_since_override=int(since),
        override_count=int(state.get("overrides") or 0),
        slots=views,
        active_constraint_count=sum(1 for s in slots if s.usable),
        query_term_count=len(state.get("terms") or ()),
        dry_streak=int(state.get("dry_streak") or 0),
        uncertain_streak=int(state.get("uncertain_streak") or 0),
        starved=bool(state.get("starved")),
        # THIS turn, not the cumulative wants_more counter, and not
        # rotate_pending -- which _rotate has already consumed by now.
        current_request_more=str(state.get("outcome") or "") == "request_more_options",
        asked_facets=tuple(str(a) for a in (state.get("asked") or ()))[-MAX_ASKED_FACETS:],
        previous_question_mode=str((state.get("asked") or [""])[-1] if state.get("asked") else ""),
        previous_question_bits=round(float(state.get("last_bits") or 0.0), 4),
        previous_question_coverage=round(float(state.get("last_coverage") or 0.0), 4),
        pool_size=int(trace.get("fused_unique") or 0),
        pool_before_filter=int(trace.get("pool_before_filter") or 0),
        pool_after_filter=int(trace.get("pool_after_filter") or 0),
        category_count=summary.category_count,
        category_entropy=summary.entropy,
        overgeneral=summary.overgeneral,
        overgeneral_option_count=summary.option_count,
        profile_source="user_profile" if tags else "none",
        profile_credible=bool(credible),
        profile_tag_count=len(tags),
        profile_tags=tuple(credible),
    )


# ---- the policy -----------------------------------------------------------

def decide(snapshot: ContextSnapshot, cfg: Mapping) -> ContextDecision:
    """Pure. Handed no state, no catalog, no session -- so it cannot mutate one.

    Every row below derives its output from declared snapshot fields. The
    route is copied only in the default row, where no signal justifies
    anything else; that is why shadow/actual route agreement is a DIAGNOSTIC
    and never a success gate.
    """
    reasons: list[ReasonCode] = []
    retrieval_mode, clarification = "standard", "open"
    depth = int(cfg["candidates"])
    route = snapshot.route

    if not snapshot.active_constraint_count and snapshot.route == "browsing":
        clarification = "open"
    if snapshot.query_term_count <= 8 and snapshot.dry_streak >= 2:
        retrieval_mode, clarification = "broaden", "easier"
        depth = int(cfg["starved_candidates"]) or depth
        reasons.append(ReasonCode.BROADEN_THIN_DRY)
    if snapshot.current_request_more:
        retrieval_mode, clarification = "broaden", "none"
        reasons.append(ReasonCode.ROTATE_OR_BROADEN)
    if snapshot.turns_since_override <= 1 and any(v.state == "abandoned" for v in snapshot.slots):
        reasons.append(ReasonCode.SUPPRESS_ABANDONED)
    relaxation = tuple(v.value for v in snapshot.slots
                       if v.state == "active" and v.hardness == "hard"
                       and v.confidence < float(cfg["filter_min_confidence"]))
    if relaxation:
        reasons.append(ReasonCode.PROPOSE_RELAX_LOW_CONFIDENCE)
    if snapshot.overgeneral:
        clarification = "structured"
        reasons.append(ReasonCode.ASK_STRUCTURED)
    if snapshot.profile_tag_count and not snapshot.profile_credible:
        reasons.append(ReasonCode.REJECT_PROFILE_PRIOR)
    return ContextDecision(route=route, retrieval_mode=retrieval_mode,
                           retrieval_depth=depth, clarification_mode=clarification,
                           clarification_attribute=None, relaxation=relaxation,
                           profile_credible=snapshot.profile_credible,
                           reasons=tuple(reasons))


# ---- renderer, deliberately separate --------------------------------------

_TEXT = {
    ReasonCode.BROADEN_THIN_DRY:
        "thin query and the customer has gone quiet -> recommend broadening",
    ReasonCode.ROTATE_OR_BROADEN:
        "customer asked for more options -> rotate or broaden",
    ReasonCode.SUPPRESS_ABANDONED:
        "recent override -> suppress the abandoned preference",
    ReasonCode.PROPOSE_RELAX_LOW_CONFIDENCE:
        "a hard constraint is below the confidence threshold -> propose relaxing it",
    ReasonCode.ASK_STRUCTURED:
        "candidate pool spans too many shelves -> ask a structured question",
    ReasonCode.REJECT_PROFILE_PRIOR:
        "profile tags are generic or already covered by session evidence "
        "-> reject the profile prior",
}


def render(decision: ContextDecision) -> tuple[str, ...]:
    """Human-readable explanation. Separate from the enum on purpose: wording
    can change without invalidating telemetry recorded under the codes."""
    return tuple(_TEXT[code] for code in decision.reasons)


# ---------------------------------------------------------------------------
# Phase 6B1: pre-retrieval context and the retrieval decision.
#
# This is a strictly EARLIER stage than ContextSnapshot above, which reads
# pool_size, category_count and overgeneral -- all produced BY the retrieval it
# would otherwise have to decide. Nothing post-retrieval may appear here.
# ---------------------------------------------------------------------------

RETRIEVAL_MODES = ("off", "shadow", "control")


@dataclasses.dataclass(frozen=True, slots=True)
class PreRetrievalSnapshot:
    """What the session IS at the insertion point. Observation only."""
    route: str
    query_term_count: int
    active_slot_count: int
    dry_streak: int
    rotate_pending: bool


@dataclasses.dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """What the configuration SAYS. Kept apart from the snapshot so that two
    runs showing the same observation and different decisions have somewhere
    to show why."""
    candidates: int
    starved_candidates: int
    starved_after: int
    starved_max_terms: int
    starved_max_slots: int


class RetrievalReason(str, Enum):
    """ACTION codes, deliberately disjoint from the observation codes above.
    Sharing an enum would let an observation code quietly acquire control."""
    WIDEN_THIN_EVIDENCE = "WIDEN_THIN_EVIDENCE"
    WIDEN_REQUEST_MORE = "WIDEN_REQUEST_MORE"
    DEPTH_STANDARD = "DEPTH_STANDARD"
    WIDEN_DISABLED = "WIDEN_DISABLED"


@dataclasses.dataclass(frozen=True, slots=True)
class RetrievalDecision:
    starved: bool
    candidate_depth: int
    retrieval_mode: str                  # "standard" | "widened"
    reasons: tuple[RetrievalReason, ...]


def policy_from(cfg: Mapping) -> RetrievalPolicy:
    return RetrievalPolicy(
        candidates=int(cfg["candidates"]),
        starved_candidates=int(cfg["starved_candidates"]),
        starved_after=int(cfg["starved_after"]),
        starved_max_terms=int(cfg["starved_max_terms"]),
        starved_max_slots=int(cfg["starved_max_slots"]))


def decide_retrieval(snapshot: PreRetrievalSnapshot,
                     policy: RetrievalPolicy) -> RetrievalDecision:
    """Starvation and candidate depth, as one pure function.

    This REPRODUCES the existing rule branch for branch rather than proposing
    a new one: 6B1 relocates a decision, so any behavioural difference is a
    defect and not a result.
    """
    def out(starved: bool, reason: RetrievalReason) -> RetrievalDecision:
        depth = (max(policy.candidates, policy.starved_candidates)
                 if starved else policy.candidates)
        return RetrievalDecision(starved=starved, candidate_depth=depth,
                                 retrieval_mode="widened" if starved else "standard",
                                 reasons=(reason,))

    if not policy.starved_candidates:
        return out(False, RetrievalReason.WIDEN_DISABLED)
    stalled = snapshot.dry_streak >= policy.starved_after
    if not (stalled or snapshot.rotate_pending):
        return out(False, RetrievalReason.DEPTH_STANDARD)
    thin = (snapshot.query_term_count <= policy.starved_max_terms
            or snapshot.active_slot_count <= policy.starved_max_slots)
    if not thin:
        return out(False, RetrievalReason.DEPTH_STANDARD)
    # Attribution only. Both paths give the same verdict and the same depth;
    # calling an explicit request for more options a "stall" would misdescribe
    # the turn in telemetry and change nothing in behaviour.
    return out(True, RetrievalReason.WIDEN_REQUEST_MORE if snapshot.rotate_pending
               else RetrievalReason.WIDEN_THIN_EVIDENCE)


# ---------------------------------------------------------------------------
# Phase 6B2: clarification / question selection.
#
# Selection and RENDERING are different questions, and one field cannot hold
# both. _pick_attribute writes broad_options BEFORE testing the uncertain
# branch, so a turn can select via the "easier" path while _compose renders the
# structured message from options the same call just wrote. Both facts are
# represented.
# ---------------------------------------------------------------------------

QUESTION_MODES = ("off", "shadow", "control")
PATCH_FIELDS = ("broad_options", "last_bits", "last_coverage", "last_weighed")


class _Unset:
    """Singleton 'this key was not written'.

    A plain dict cannot distinguish "not written" from "written with the value
    it already held", and the legacy oracle depends on that difference. This is
    a type with one instance so equality is deterministic and its repr is
    canonical in telemetry. It is never written into session state.
    """
    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


@dataclasses.dataclass(frozen=True, slots=True)
class QuestionPatch:
    """Exactly the fields the matching legacy branch writes -- no more.

    Three of the ten branches write nothing at all, and _compose reads two of
    these fields, so a patch that always wrote all four would render a
    different message on later turns while looking obviously equivalent.
    """
    broad_options: object = UNSET
    last_bits: object = UNSET
    last_coverage: object = UNSET
    last_weighed: object = UNSET

    def writes(self) -> tuple[str, ...]:
        return tuple(f for f in PATCH_FIELDS if getattr(self, f) is not UNSET)


@dataclasses.dataclass(frozen=True, slots=True)
class PriorRenderState:
    """What the previous turn left behind, and what _compose will read."""
    broad_options: tuple[str, ...] = ()
    last_bits: float = 0.0
    last_coverage: float = 0.0
    last_weighed: bool = False


def apply_patch(prior: PriorRenderState, patch: QuestionPatch) -> PriorRenderState:
    """prior + partial patch -> effective render state. Pure."""
    if not isinstance(patch, QuestionPatch):
        raise TypeError(f"expected QuestionPatch, got {type(patch).__name__}")
    values = {}
    for field in PATCH_FIELDS:
        got = getattr(patch, field)
        values[field] = getattr(prior, field) if got is UNSET else got
    if not isinstance(values["broad_options"], tuple):
        values["broad_options"] = tuple(values["broad_options"])
    return PriorRenderState(**values)


@dataclasses.dataclass(frozen=True, slots=True)
class QuestionSnapshot:
    asked: tuple[str, ...] = ()
    dry_streak: int = 0
    dry_others: int = 0
    uncertain_streak: int = 0
    prior: PriorRenderState = dataclasses.field(default_factory=PriorRenderState)

    @property
    def other_asked_count(self) -> int:
        # Derived, not carried: both legacy call sites use asked.count("other")
        # and a stored copy would be a second source of truth.
        return self.asked.count("other")


@dataclasses.dataclass(frozen=True, slots=True)
class QuestionPolicy:
    ask_policy: str
    ask_fallback_after: int
    answerability_after: int
    pool_give_up_after: int
    pool_depth: int
    overgeneral_cats: int
    question_utility: bool
    question_dry_cost: float


@dataclasses.dataclass(frozen=True, slots=True)
class FacetStat:
    attribute: str
    bits_with_missing: float
    bits_skip_missing: float
    coverage: float
    answerability: float


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateStats:
    window_size: int
    facets: tuple[FacetStat, ...] = ()
    overgeneral: bool = False
    options: tuple[str, ...] = ()


class QuestionReason(str, Enum):
    FIRST_TWO_OTHER = "FIRST_TWO_OTHER"
    EASIER_AFTER_UNCERTAIN = "EASIER_AFTER_UNCERTAIN"
    GIVE_UP_AFTER_DRY = "GIVE_UP_AFTER_DRY"
    POOL_ATTRIBUTE_SELECTED = "POOL_ATTRIBUTE_SELECTED"
    NO_DISCRIMINATING_FACET = "NO_DISCRIMINATING_FACET"
    PROBE_CYCLE = "PROBE_CYCLE"
    FALLBACK_OTHER = "FALLBACK_OTHER"


class QuestionModifier(str, Enum):
    # A modifier, not a competing primary reason: structured rendering can be a
    # consequence of inherited state rather than of the branch that fired.
    STRUCTURED_CLARIFICATION_DUE = "STRUCTURED_CLARIFICATION_DUE"


@dataclasses.dataclass(frozen=True, slots=True)
class QuestionDecision:
    attribute: str
    selection_mode: str
    render_mode: str
    effective_render_state: PriorRenderState
    selected_bits: float                 # diagnostics for THIS selection only
    selected_coverage: float
    selected_utility: float
    primary_reason: QuestionReason
    modifiers: tuple[QuestionModifier, ...]
    patch: QuestionPatch

    @property
    def effective_options(self) -> tuple[str, ...]:
        return self.effective_render_state.broad_options


def _easiest_unasked(asked, answerability, vocab) -> str:
    """Most answerable facet not yet asked. Reproduces _easiest_unasked."""
    seen = set(asked)
    options = [(score, name) for name, score in answerability.items()
               if name not in seen and name in vocab]
    return max(options)[1] if options else ""


def _select_from_pool(snapshot: QuestionSnapshot, policy: QuestionPolicy,
                      stats: CandidateStats) -> tuple[str, float, float, float, bool]:
    """Reproduces _pool_attribute: (attribute, bits, coverage, utility, weighed).

    `util > best_util` is STRICTLY greater, so equal utility keeps the first
    attribute in ATTR_VOCAB order -- which is why `stats.facets` must preserve
    that order rather than being sorted or built from a set.
    """
    weigh = snapshot.dry_streak >= policy.answerability_after
    best, best_bits, best_util = "other", 0.0, 0.0
    for facet in stats.facets:
        if facet.attribute in snapshot.asked:
            continue
        if policy.question_utility:
            bits = facet.bits_skip_missing
            util = (bits * facet.coverage * facet.answerability
                    - policy.question_dry_cost * (1.0 - facet.coverage * facet.answerability))
        else:
            bits = facet.bits_with_missing
            util = bits * facet.answerability if weigh else bits
        if util > best_util:
            best, best_bits, best_util = facet.attribute, bits, util
    # _pool_attribute always writes last_coverage for the WINNER; when the
    # winner is "other" that is 0.0, because ATTR_VOCAB has no such key.
    chosen = next((f for f in stats.facets if f.attribute == best), None)
    coverage = chosen.coverage if chosen is not None else 0.0
    return best, best_bits, coverage, best_util, weigh


def decide_question(snapshot: QuestionSnapshot, policy: QuestionPolicy,
                    stats: CandidateStats, *, answerability, vocab,
                    probe_order) -> QuestionDecision:
    """Pure. Reproduces _pick_attribute branch for branch.

    Receives no Agent, no session dict, no catalog and no lazy index; the
    vocabularies arrive as arguments so this module keeps importing nothing
    from the package.
    """
    def finish(attribute: str, selection_mode: str, patch: QuestionPatch,
               reason: QuestionReason, bits: float = 0.0, coverage: float = 0.0,
               utility: float = 0.0) -> QuestionDecision:
        effective = apply_patch(snapshot.prior, patch)
        # _compose renders the structured message on len(options) >= 2 -- NOT
        # on `overgeneral`. A single distinguishing option renders open, and
        # stale options from an earlier turn render structured on a branch that
        # wrote nothing.
        structured = len(effective.broad_options) >= 2
        return QuestionDecision(
            attribute=attribute, selection_mode=selection_mode,
            render_mode="structured" if structured else "open",
            effective_render_state=effective, selected_bits=bits,
            selected_coverage=coverage, selected_utility=utility,
            primary_reason=reason,
            modifiers=(QuestionModifier.STRUCTURED_CLARIFICATION_DUE,) if structured else (),
            patch=patch)

    pol = policy.ask_policy
    if pol in ("pool", "other_then_pool"):
        if pol == "other_then_pool" and snapshot.other_asked_count < 2:
            return finish("other", "first_two_other", QuestionPatch(),
                          QuestionReason.FIRST_TWO_OTHER)
        # broad_options is written HERE, before the uncertain branch is tested.
        patch_options = QuestionPatch(broad_options=tuple(stats.options))
        if snapshot.uncertain_streak >= policy.answerability_after:
            easy = _easiest_unasked(snapshot.asked, answerability, vocab)
            if easy:
                return finish(easy, "easier",
                              QuestionPatch(broad_options=tuple(stats.options),
                                            last_bits=0.0),
                              QuestionReason.EASIER_AFTER_UNCERTAIN)
        if not stats.overgeneral and snapshot.dry_streak >= policy.pool_give_up_after:
            return finish("other", "give_up", patch_options,
                          QuestionReason.GIVE_UP_AFTER_DRY)
        attribute, bits, coverage, utility, weighed = _select_from_pool(
            snapshot, policy, stats)
        patch = QuestionPatch(broad_options=tuple(stats.options), last_bits=bits,
                              last_coverage=coverage, last_weighed=weighed)
        if bits >= 0.2:
            return finish(attribute, "pool_selection", patch,
                          QuestionReason.POOL_ATTRIBUTE_SELECTED, bits, coverage, utility)
        return finish("other", "pool_selection", patch,
                      QuestionReason.NO_DISCRIMINATING_FACET, bits, coverage, utility)

    if pol == "other" and policy.ask_fallback_after and \
            snapshot.dry_others >= policy.ask_fallback_after:
        nxt = next((a for a in probe_order[:-1] if a not in snapshot.asked), "other")
        return finish(nxt, "cycle", QuestionPatch(), QuestionReason.PROBE_CYCLE)
    if pol == "probe_cycle":
        nxt = next((a for a in probe_order if a not in snapshot.asked), "other")
        return finish(nxt, "cycle", QuestionPatch(), QuestionReason.PROBE_CYCLE)
    if pol == "other_then_cycle":
        if snapshot.other_asked_count < 2:
            return finish("other", "first_two_other", QuestionPatch(),
                          QuestionReason.FIRST_TWO_OTHER)
        nxt = next((a for a in probe_order if a not in snapshot.asked), "other")
        return finish(nxt, "cycle", QuestionPatch(), QuestionReason.PROBE_CYCLE)
    return finish("other", "fallback", QuestionPatch(), QuestionReason.FALLBACK_OTHER)
