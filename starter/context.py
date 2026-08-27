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
