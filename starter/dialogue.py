"""Dialogue: session state, and what to say next.

Override handling and span-targeted erasure, credibility of older evidence,
starvation detection, candidate-aware question utility, and the customer-facing
copy. Everything here reads or writes the per-session state dict.

HOST CAPABILITIES REQUIRED (reached through `self`, never imported):
    self.cfg            resolved configuration
    self.cat            catalog and its indexes
    self.stop           stopword set
    self._terms()       tokenisation
    self._route_cfg()   per-route configuration      [RetrievalMixin]
    self._category_on() whether the category plane contributes [RetrievalMixin]

The last two are calls INTO RetrievalMixin. They are listed rather than
imported: the two mixins must not import each other, and the domain
dependency between them is real and is documented, not dissolved. See
notes/25-phase5b-design.md -- an acyclic IMPORT graph is not an acyclic
DOMAIN graph, and this split does not claim otherwise.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from starter import context as _context
from starter.catalog import _clean
from starter.evidence import (
    ABANDON_RE, ANSWERABILITY, ATTR_VOCAB, NOISE_REPLIES, OVERRIDE_MARK,
    PROBE_ORDER, SINGLE_VALUED, TOKEN_RE, _distinguishing, _norm,
    abandoned_span, is_override, parse_message, slot_of,
)


class DialogueMixin:
    """Mixed into Agent. Not instantiable alone -- see host capabilities above."""

    @staticmethod
    def _route(first_message: str, phrases: list[str] | None = None,
               category: str | None = None) -> str:
        """Classify the opening turn.

        Unknown phrasing used to fall through to "override", which is a claim
        the message never made -- 100% of vague openings ("Show me something
        good") were labelled as intent overrides. Unknown now degrades to
        "mixed", and the label is inferred from what the message actually
        carried rather than from which template it failed to match.
        """
        low = (first_message or "").lower()
        if "a key requirement is" in low:
            return "buying"
        if "still exploring" in low:
            return "browsing"
        if is_override(first_message):
            return "override"
        if phrases:
            return "buying"        # a requirement was stated, whatever the wording
        if category:
            return "browsing"      # a category but no requirement yet
        return "mixed"             # under-specified: neither is known
    @staticmethod
    def _retarget(state: dict) -> str:
        """Routes are not fixed at turn 1: browsing converges into buying.

        Once a shopper has committed to real constraints they are no longer
        browsing, so an exploratory route must be able to firm up.
        """
        route = state.get("route") or "mixed"
        if route in ("mixed", "browsing"):
            # Any constraint the shopper volunteers is a commitment signal.
            # Gating on hardness=="hard" would only ever count turn-1 openings,
            # so a browser who later states requirements could never firm up.
            if any(slot.usable for slot in state["slots"]):
                return "buying"
            if route == "mixed" and state.get("category"):
                return "browsing"
        return route

    @staticmethod
    def _entropy_of(counts: dict[str, int]) -> float:
        """Shared kernel. _facet_pass buckets the same values in one walk with
        coverage, and both must agree to the last bit -- so there is one
        expression, not two that look alike. Insertion order is identical
        because both walk the same window in the same direction, which is what
        makes the float summation order identical too."""
        total = sum(counts.values())
        if total < 2 or len(counts) < 2:
            return 0.0
        return -sum((c / total) * math.log2(c / total) for c in counts.values() if c)

    def _suppress_abandoned(self, state: dict, message: str, cfg: dict) -> None:
        """Bar soft-rescue for exactly what the customer named as abandoned.

        Blanket blocking of every pre-pivot slot fixes the category pivot but
        costs paraphrase robustness, because most old evidence is still valid.
        The customer told us which part is dead -- suppress only that.
        """
        if not cfg.get("suppress_abandoned"):
            return          # the switch must disable the whole mechanism, not
                            # just the rerank blocklist: without this the "off"
                            # arm of the ablation still deactivated slots.
        span = abandoned_span(message)
        if not span:
            return
        span_tokens = set(self._terms(span))
        if not span_tokens:
            return
        for slot in state["slots"]:
            if not slot.usable or not slot.soft_ok:
                continue
            tokens = set(self._terms(slot.value))
            if tokens and len(tokens & span_tokens) / len(tokens) >= 0.5:
                slot.soft_ok = False
                slot.contradiction = "abandoned"
                if cfg.get("abandoned_policy") == "deactivate":
                    slot.active = False

    def _release_abandoned_category(self, state: dict, message: str, cfg: dict) -> None:
        """Stop pinning the shelf the customer just walked away from.

        Before Phase 2B a stale category contributed a handful of weak query
        terms and the ranker could out-vote it. With a category data plane it
        selects the candidate pool, so "forget dresses" while state["category"]
        still says dresses holds the customer on the shelf they just left --
        and the rescue lane would be carrying the entire rest of the catalog.

        Dropping to None is deliberate: no category constraint is a weaker
        claim than the wrong one, and the next turn can re-establish it. Gated
        on dual_plane because on the legacy path the category still feeds
        query TERMS, and removing them there would move the frozen baseline.
        """
        if not self._category_on(cfg) or not state.get("category"):
            return
        span = abandoned_span(message)
        if not span:
            return
        span_tokens = set(self._terms(span))
        current = set(self._terms(state["category"]))
        if not span_tokens or not current:
            return
        # Measured against the SPAN, not the category: "forget dresses" names
        # one word of a three-word category and still abandons it, whereas
        # requiring a third of the category to be named let that through.
        if len(current & span_tokens) / len(span_tokens) < 0.5:
            return                      # they abandoned something else
        _, phrases = parse_message(message)
        for phrase in phrases:          # did they name a shelf to move to?
            if self.cat.category_index.shelves(phrase):
                state["category"] = phrase
                return
        state["category"] = None

    def _uncredible(self, state: dict) -> frozenset:
        """Constraint values that may still match exactly but must not be
        soft-matched: stated before an override, or contested by a newer value
        of a single-valued attribute.
        """
        pivot = state.get("last_override_turn", 0)
        blocked: set[str] = set()
        latest: dict[str, tuple[int, str]] = {}
        for slot in state["slots"]:
            if not slot.usable:
                continue
            if pivot and slot.source_turn <= pivot:
                blocked.add(slot.value)          # predates the customer's pivot
            if slot.attribute in SINGLE_VALUED:
                seen = latest.get(slot.attribute)
                if seen is None or slot.source_turn >= seen[0]:
                    if seen is not None and seen[1] != slot.value:
                        blocked.add(seen[1])     # superseded by a newer value
                    latest[slot.attribute] = (slot.source_turn, slot.value)
                elif seen[1] != slot.value:
                    blocked.add(slot.value)
        return frozenset(blocked)

    def _starved(self, state: dict, cfg: dict) -> bool:
        """Thin wrapper. The rule itself lives in context.decide_retrieval.

        Phase 6B1 relocated this decision into a pure function so it could be
        tested against a boundary grid and driven from telemetry. What is left
        here is an adapter, kept because tests and older call sites name it.

        There is deliberately ONE copy of the rule. The measurement commit
        held two side by side to compare them across 18,597 raw
        turn-comparisons (8,483.4 seed-normalised); keeping them after that
        would have been the same defect this project has already paid for
        twice -- the void suppress_abandoned switch and the inert
        route_overrides patch.
        """
        snapshot = _context.PreRetrievalSnapshot(
            route=str(state.get("route") or "mixed"),
            query_term_count=len(state.get("terms") or ()),
            active_slot_count=sum(1 for slot in state.get("slots") or () if slot.usable),
            dry_streak=int(state.get("dry_streak") or 0),
            rotate_pending=bool(state.get("rotate_pending")))
        return _context.decide_retrieval(snapshot, _context.policy_from(cfg)).starved

    def _overgeneral(self, pool: list[str], cfg: dict) -> tuple[bool, list[str]]:
        """Is the pool too broad to rank, rather than merely unranked?

        Truncating recommendations would be pure loss under this metric, so the
        cutoff drives the QUESTION, not the result list: we stop trying to rank
        an under-specified request and ask a structured one instead.
        """
        limit = int(cfg["overgeneral_cats"])
        if not limit or not pool:
            return False, []
        head = pool[: max(2, int(cfg["pool_depth"]))]
        counts: dict[str, int] = {}
        for asin in head:
            leaf = (self.cat.cats.get(asin, "").split(",")[-1] or "").strip()
            if leaf:
                counts[leaf] = counts.get(leaf, 0) + 1
        if len(counts) < limit:
            return False, []
        top = [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
        return True, _distinguishing(top)

    def _facet_coverage(self, window: list[str], attribute: str) -> float:
        """Share of the window carrying any value for `attribute`."""
        pattern = ATTR_VOCAB.get(attribute)
        if not pattern or not window:
            return 0.0
        seen = sum(1 for asin in window
                   if pattern.search(self.cat.text.get(asin, "")))
        return round(seen / len(window), 4)

    # ---- Phase 6B2-R2: staged question decision -------------------------

    def _question_window(self, pool: list[str], cfg: dict) -> list[str]:
        """ranked[:max(2, pool_depth)] -- the one expression _overgeneral and
        _pool_attribute both use. NOT ranked[:pool_depth]."""
        return pool[: max(2, int(cfg["pool_depth"]))]

    def _question_category(self, pool: list[str],
                           cfg: dict) -> "_context.QuestionCategorySummary":
        """Stage 3: one bounded pass over cat.cats. Never a lazy index."""
        broad, options = self._overgeneral(pool, cfg)
        return _context.QuestionCategorySummary(overgeneral=broad,
                                                options=tuple(options))

    def _facet_pass(self, window: list[str], attribute: str,
                    plan: "_context.FacetScanPlan") -> "_context.FacetSample":
        """One pass over the window for one facet: exactly what the plan asked.

        Entropy and coverage are the same walk. Both need
        `pattern.search(text)` for every candidate -- coverage counts the
        matches, entropy buckets their values -- so when the plan wants both,
        one loop produces both and the second search is not performed at all.
        Legacy calls _pool_entropy and then _facet_coverage, walking the window
        twice; this is where the utility-ON path gets CHEAPER than legacy
        rather than merely no dearer.
        """
        pattern = ATTR_VOCAB[attribute]
        counts: dict[str, int] = {}
        seen = 0
        for asin in window:
            match = pattern.search(self.cat.text.get(asin, ""))
            if match is not None:
                seen += 1
            elif plan.skip_missing:
                # Silence is not an answer the customer can give.
                continue
            value = match.group(1).lower() if match else ""
            counts[value] = counts.get(value, 0) + 1
        return _context.FacetSample(
            attribute=attribute, bits=self._entropy_of(counts),
            answerability=ANSWERABILITY.get(attribute, 0.5),
            coverage=(round(seen / len(window), 4) if window else 0.0)
                     if plan.with_coverage else 0.0,
            coverage_known=bool(plan.with_coverage))

    def _facet_samples(self, window: list[str],
                       plan: "_context.FacetScanPlan") -> tuple:
        """The host's half of stage 5: the plan's scans, in the plan's order."""
        return tuple(self._facet_pass(window, attribute, plan)
                     for attribute in plan.attributes)

    def _question_snapshot(self, state: dict) -> "_context.QuestionSnapshot":
        return _context.QuestionSnapshot(
            asked=tuple(state.get("asked") or ()),
            dry_streak=int(state.get("dry_streak") or 0),
            dry_others=int(state.get("dry_others") or 0),
            uncertain_streak=int(state.get("uncertain_streak") or 0),
            prior=_context.PriorRenderState(
                broad_options=tuple(state.get("broad_options") or ()),
                last_bits=float(state.get("last_bits") or 0.0),
                last_coverage=float(state.get("last_coverage") or 0.0),
                last_weighed=bool(state.get("last_weighed"))))

    @staticmethod
    def _question_policy(cfg: dict) -> "_context.QuestionPolicy":
        return _context.QuestionPolicy(
            ask_policy=cfg["ask_policy"],
            ask_fallback_after=int(cfg["ask_fallback_after"] or 0),
            answerability_after=int(cfg["answerability_after"]),
            pool_give_up_after=int(cfg["pool_give_up_after"]),
            pool_depth=int(cfg["pool_depth"]),
            overgeneral_cats=int(cfg["overgeneral_cats"]),
            question_utility=bool(cfg["question_utility"]),
            question_dry_cost=float(cfg["question_dry_cost"]))

    def _decide_question(self, state: dict, pool: list[str],
                         cfg: dict) -> "_context.QuestionDecision":
        """Drive the staged controller, scanning only between stages.

        The order of these five blocks IS the pre-registered scan topology
        (notes/33-phase6b2-r2-prereg.md). Every early return is a scan not
        performed: a first-two-`other` turn -- 45% of live turns -- reaches
        neither cat.cats nor cat.text, and an easier or give-up turn reaches
        cat.cats once and cat.text never.

        Nothing below is handed an Agent, a session dict, a catalog or a lazy
        index. The staged functions receive tuples of primitives; the scans
        happen HERE, in the host, in plain sight.
        """
        snapshot = self._question_snapshot(state)
        policy = self._question_policy(cfg)
        decision = _context.question_without_candidates(
            snapshot, policy, probe_order=PROBE_ORDER)
        if decision is not None:
            return decision

        category = self._question_category(pool, cfg)          # 1 cat.cats pass
        decision = _context.question_from_category(
            snapshot, policy, category, answerability=ANSWERABILITY,
            vocab=ATTR_VOCAB)
        if decision is not None:
            return decision

        plan = _context.facet_scan_plan(snapshot, policy, vocab=ATTR_VOCAB)
        window = self._question_window(pool, cfg)
        pick = _context.select_pool_attribute(
            snapshot, policy, self._facet_samples(window, plan))
        # With utility ON the winner's coverage came back in its own pass; with
        # it OFF legacy takes coverage for the winner ALONE, after selecting,
        # so exactly one more scan happens here and only here.
        coverage = (pick.coverage if pick.coverage_known
                    else self._facet_coverage(window, pick.attribute))
        return _context.question_from_pool(snapshot, policy, category, pick,
                                           coverage)

    @staticmethod
    def _apply_question_patch(state: dict, patch: "_context.QuestionPatch") -> None:
        """Write exactly the keys the patch marks as written -- no more.

        A patch that always wrote all four would look equivalent and would
        change the rendered message on later turns, because _compose reads
        broad_options and last_bits and three of ten branches write neither.
        """
        for field in _context.PATCH_FIELDS:
            value = getattr(patch, field)
            if value is _context.UNSET:
                continue
            state[field] = list(value) if field == "broad_options" else value

    def _pick_attribute(self, state: dict, pool: list[str] | None = None) -> str:
        """Thin adapter. The rule itself lives in the staged controller.

        Phase 6B2-R2 relocated this decision into staged pure functions so the
        scan topology could be tested per branch rather than described, and so
        the cost of each branch could be measured on its own. What is left here
        is an adapter, kept because tests and older call sites name it.

        There is deliberately ONE copy of the rule. The measurement commit held
        two side by side to compare them across 18,597 raw turn-comparisons
        (8,483.4 seed-normalised); keeping them after that would have been the
        same defect this project has already paid for three times -- the void suppress_abandoned switch, the inert
        route_overrides patch, and _starved's own duplicate before 6B1.

        BECAUSE this delegates, `question_context_mode="shadow"` now compares a
        value with itself. Its agreement telemetry is diagnostic from here on
        and is not evidence of anything. The valid evidence is the pre-adoption
        pre-adoption comparison at tag p6b2r2-shadow: 18,597 raw
        turn-comparisons, 8,483.4 seed-normalised, zero disagreements.
        """
        cfg = self._route_cfg(state)
        decision = self._decide_question(state, pool or [], cfg)
        self._apply_question_patch(state, decision.patch)
        return decision.attribute

    def _rebuild_terms(self, state: dict, message: str, cfg: dict) -> None:
        """Query terms come from accepted evidence, not from raw message tokens.

        The legacy path appended every non-stopword token of any message the
        noise filter let through, so "Hmm, hard to say really" contributed
        hmm/hard/say/really to the BM25 query. On the clean simulator the two
        paths agree exactly -- every message there is either a parsable template
        or already filtered -- so this is free on the public set and only bites
        when the customer says something the templates do not cover.
        """
        if not cfg["evidence_query"]:
            for term in self._terms(message):
                if term not in state["terms"]:
                    state["terms"].append(term)
            return
        terms: list[str] = []
        for term in self._terms(state["category"] or ""):
            if term not in terms:
                terms.append(term)
        for slot in state["slots"]:
            if not slot.usable:
                continue
            for term in slot.provenance:
                if term not in terms:
                    terms.append(term)
        state["terms"] = terms

    def _slot_override(self, state: dict, message: str) -> None:
        """Selective rewrite: drop only the slots the customer just replaced.

        "forget boots, I want running shoes" supersedes the use_case/category
        slot and leaves an unrelated colour constraint standing. Slots in a
        superseded attribute are deactivated along with the terms they
        contributed; every other slot survives.
        """
        new_category, new_phrases = parse_message(message)
        superseded = {slot_of(p) for p in new_phrases if len(_norm(p)) >= 3}
        if new_category:
            superseded.add("category")
            state["category"] = new_category
        if not superseded:
            return
        incoming = {_norm(p) for p in new_phrases}
        dropped = False
        for slot in state["slots"]:
            if not slot.usable or slot.value in incoming:
                continue                            # re-stated verbatim: not stale
            if slot.attribute in superseded:
                slot.active = False
                slot.contradiction = "superseded"
                dropped = True
        if not dropped:
            return
        # Slots are the source of truth: deactivating one removes the terms it
        # contributed, because the query is rebuilt from active evidence.
        state["phrases"] = [sl.value for sl in state["slots"] if sl.usable]
        survivors: set[str] = set(self._terms(state["category"] or ""))
        for slot in state["slots"]:
            if slot.usable:
                survivors.update(slot.provenance)
        state["terms"] = [t for t in state["terms"] if t in survivors]

    # ---- customer-facing copy ----------------------------------------
    # The evaluator only requires `message` to be a string; it drives the
    # simulator entirely from `ask_attribute`. So the wording is free, and
    # there is no reason for it to read like a form field.
    ASK_COPY = {
        "material": "what it should be made of",
        "color": "which colours work for you",
        "size": "what size you take",
        "style": "the cut or style you prefer",
        "feature": "any feature you can't do without",
        "use_case": "where you'll mostly be using it",
        "brand": "whether you lean towards a particular brand",
        "budget": "roughly what you'd like to spend",
        "category": "what kind of item you have in mind",
        "other": "anything else that matters to you",
    }

    def _compose(self, attribute: str, state: dict, ranked: list[str],
                 shown: list[str]) -> str:
        if not shown:
            return ("I haven't got a good match yet — could you tell me a bit more "
                    "about what you're after?")
        lead = ""
        top = self.cat.title.get(shown[0], "")
        # Prefer short attribute-like constraints; marketing prose lifted from a
        # description reads badly when quoted back at the customer.
        hits = [p for p in state["phrases"] if _norm(p) in self.cat.text.get(shown[0], "")]
        matched = [_clean(p, 44) for p in sorted(hits, key=len) if len(p) <= 50][:2]
        matched = [m for m in matched if m]
        if top:
            if matched:
                lead = (f"Top of the list right now is {top} — it matches "
                        f"{' and '.join(matched)}. ")
            else:
                lead = f"Top of the list right now is {top}. "
        options = state.get("broad_options") or []
        bits = state.get("last_bits", 0.0)
        if options and len(options) >= 2:
            listed = ", ".join(options[:-1]) + f" or {options[-1]}"
            return (lead + f"That still spans quite a range — I'm seeing {listed}. "
                    f"Which of those is closest to what you want?")
        if attribute in ATTR_VOCAB and bits:
            depth = min(len(ranked), max(2, int(self.cfg["pool_depth"])))
            ask = (f"The {depth} closest options still disagree most on "
                   f"{self.ASK_COPY.get(attribute, attribute)}, so that answer would "
                   f"narrow things down fastest — any preference?")
        else:
            ask = f"To sharpen this, could you tell me {self.ASK_COPY.get(attribute, attribute)}?"
        return lead + ask
    # ---- Phase 6C1: profile credibility (shadow only) --------------------

    def _profile_snapshot(self, state: dict, turn: int) -> "_context.ProfileSnapshot":
        """Session facts only. No candidate identity, and no target."""
        slots = state.get("slots") or ()
        blocked = self._uncredible(state)
        return _context.ProfileSnapshot(
            tags=_context.normalize_profile_tags(
                (state.get("profile") or {}).get("preference_tags")),
            stated_values=tuple(sorted({str(s.value).casefold() for s in slots
                                        if s.polarity > 0 and s.active})),
            negated_values=tuple(sorted({str(s.value).casefold() for s in slots
                                         if s.polarity < 0})),
            blocked_values=tuple(sorted({str(v).casefold() for v in blocked})),
            turn=int(turn))

    def _profile_decision(self, state: dict, window_asins: list[str], cfg: dict,
                          turn: int = 0) -> "_context.ProfileDecision":
        """Classify this turn's profile against the PRE-RERANK window.

        The host resolves asins to text here and hands the pure function texts
        alone. That is not a convenience: passing texts is what keeps candidate
        identity -- and therefore the ground-truth target -- structurally
        unable to reach the decision. D5 joins the target in the lab, after.
        """
        texts = [self.cat.text.get(asin, "") for asin in window_asins]
        return _context.classify_profile(
            self._profile_snapshot(state, turn),
            _context.ProfilePolicy(
                profile_max_coverage=float(cfg["profile_max_coverage"])),
            texts)

    @staticmethod
    def _profile_trace(decision: "_context.ProfileDecision", *,
                       first_recommendation_turn: bool,
                       window_asins: list[str] | None = None) -> dict:
        """Bounded telemetry for one turn. Raw counts, never a rate.

        The window asins are recorded ONLY on the first recommendation turn.
        The lab needs them to join the target and compute D5's margin, and D5
        is a first-turn gate -- recording them every turn would carry 30 asins
        per turn per session for rows no gate reads.
        """
        row = {
            "profile_session_verdict": decision.session_verdict.value,
            "profile_credible_tags": list(decision.credible_tags),
            "profile_tag_count": len(decision.tags),
            "profile_window_size": decision.window_size,
            "profile_first_recommendation_turn": bool(first_recommendation_turn),
            "profile_tags": [
                {"tag": v.tag, "category": v.category.value,
                 "match_count": v.match_count, "coverage": v.coverage}
                for v in decision.tags],
        }
        row.update({f"profile_cat_{k}": v for k, v in decision.counts().items()})
        if first_recommendation_turn:
            row["profile_window_asins"] = list(window_asins or ())
        return row

    # ---- Phase 7A-R1: the A2 semantic cascade ---------------------------
    #
    # A2 runs AFTER _rotate and reorders only a COPY of the final visible
    # window. `ranked` -- what the question controller, the ContextSnapshot and
    # _compose's pool logic read -- is never replaced. Running before _rotate
    # would make the semantic order an input to the rotation, so the two
    # mechanisms would interact and neither could be reasoned about alone.

    SEMANTIC_QUERY_CHARS = 200

    @staticmethod
    def _effective_semantic_k(k: int, top_k: int, n: int) -> int:
        """min(semantic_rerank_k, top_k, len(ordered)), never negative.

        The `top_k` term is what preserves the returned SET for a caller
        passing a smaller top_k: reordering the first 10 could promote a
        rank-7 item into a returned five and change what the caller sees.
        """
        return max(0, min(int(k), int(top_k), int(n)))

    @staticmethod
    def _semantic_eligible(state: dict) -> bool:
        """The robustness gate. Product logic, frozen, never searched.

        Buying, contradiction-sensitive and post-override traffic stays on A0:
        those are the slices where A0's structured handling of negatives and
        hard constraints is doing work a relevance model has not been validated
        to replicate.
        """
        if str(state.get("route") or "") not in ("browsing", "mixed"):
            return False
        if int(state.get("last_override_turn") or 0):
            return False
        hard = 0
        for slot in state.get("slots") or ():
            if slot.active and slot.polarity < 0:
                return False                      # active negative
            if not slot.soft_ok:
                return False                      # abandoned / suppressed
            if slot.usable and slot.hardness == "hard":
                hard += 1
        return hard <= 1

    def _semantic_query(self, state: dict) -> str:
        """One canonical query. No variants, and none selectable later.

        Order: category, use-case evidence, other positive constraints,
        accepted evidence terms. Excludes negatives (this MS MARCO relevance
        model has not been validated to enforce hard negative constraints, so
        they stay with A0's structured logic), suppressed values, raw message
        text and profile tags.
        """
        parts: list[str] = []
        if state.get("category"):
            parts.append(str(state["category"]))
        slots = [s for s in (state.get("slots") or ())
                 if s.usable and s.soft_ok]
        ordered = sorted(slots, key=lambda s: (s.source_turn, str(s.value)))
        parts += [str(s.value) for s in ordered if s.attribute == "use_case"]
        parts += [str(s.value) for s in ordered if s.attribute != "use_case"]
        parts += [str(t) for t in (state.get("terms") or ())]

        seen: set[str] = set()
        kept: list[str] = []
        for raw in parts:
            piece = " ".join(str(raw).split()).casefold()
            if not piece or piece in seen:
                continue
            seen.add(piece)
            kept.append(piece)
        query = " ".join(kept)
        if len(query) <= self.SEMANTIC_QUERY_CHARS:
            return query
        # Truncate at a word boundary so a half-token does not reach the
        # tokenizer as a different word.
        cut = query[: self.SEMANTIC_QUERY_CHARS]
        return cut[: cut.rfind(" ")] if " " in cut else cut

    def _semantic_product_text(self, asin: str) -> str:
        """Canonical, deterministic: title, full category path, features,
        description/details. Popularity and rating are EXCLUDED -- they are
        already A0 features, and feeding them here would double-count one
        signal and make the fusion weight uninterpretable.
        """
        cat = self.cat
        pieces = [cat.title.get(asin, ""), cat.cats.get(asin, "")]
        feat = getattr(cat, "feat", {}).get(asin, "")
        if feat:
            pieces.append(feat)
        text = cat.text.get(asin, "")
        if text:
            pieces.append(text)
        joined = ". ".join(p for p in pieces if p)
        return " ".join(joined.split())

    def _semantic_session(self, cfg: dict):
        """Load the ONNX session and tokenizer, or return None.

        Returns None rather than raising: an absent or unloadable model is a
        fallback to A0, not an error the customer sees.
        """
        directory = str(cfg.get("semantic_model_dir") or "")
        if not directory or not Path(directory).is_dir():
            return None
        cached = getattr(self, "_semantic_cache", None)
        if cached is not None and cached[0] == directory:
            return cached[1]
        import numpy as np                              # noqa: F401
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        tok.enable_truncation(max_length=int(cfg["semantic_max_length"]),
                              strategy="only_second")
        tok.enable_padding()
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(
            str(Path(directory) / "onnx" / "model_qint8_arm64.onnx"),
            sess_options=opts, providers=["CPUExecutionProvider"])
        loaded = (tok, sess, {i.name for i in sess.get_inputs()})
        self._semantic_cache = (directory, loaded)
        return loaded

    def _semantic_score_order(self, query: str, asins: list[str]) -> list[str]:
        """asins re-ordered by descending relevance. Native pair encoding."""
        import numpy as np
        loaded = self._semantic_session(self.cfg)
        if loaded is None:
            raise RuntimeError("no semantic session")
        tok, sess, names = loaded
        encs = tok.encode_batch([(query, self._semantic_product_text(a))
                                 for a in asins])
        feed = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encs],
                                           dtype=np.int64)}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encs],
                                              dtype=np.int64)
        logits = sess.run(None, {k: v for k, v in feed.items() if k in names})[0]
        scored = [(float(logits[i][0]), i, a) for i, a in enumerate(asins)]
        # Descending score, then original index: total and stable.
        return [a for _, _, a in sorted(scored, key=lambda x: (-x[0], x[1]))]

    def _semantic_reorder(self, ordered: list[str], state: dict,
                          cfg: dict, top_k: int) -> tuple[list[str], str]:
        """A COPY of `ordered`, prefix-reordered. Returns (result, reason).

        Every failure path returns `ordered` unchanged, so the customer-visible
        result is byte-exact A0 whenever anything at all goes wrong.
        """
        if self._semantic_mode(cfg) != "on":
            return ordered, "mode_off"
        lam = float(cfg["semantic_lambda"])
        k = self._effective_semantic_k(cfg["semantic_rerank_k"], top_k, len(ordered))
        if lam == 0.0:
            return ordered, "lambda_zero"
        if k < 2:
            return ordered, "prefix_too_short"
        if not self._semantic_eligible(state):
            return ordered, "ineligible"
        query = self._semantic_query(state)
        if not query:
            return ordered, "empty_query"
        prefix, tail = ordered[:k], ordered[k:]
        try:
            semantic = list(self._semantic_score_order(query, list(prefix)))
        except Exception:
            # Load or inference failure. A0 is always available.
            return ordered, "inference_failure"
        if not _context.is_permutation(semantic, prefix):
            # A scorer that dropped or invented an asin must never reach the
            # customer. Counted, not silently tolerated.
            return ordered, "bad_permutation"
        return _context.rrf_fuse(prefix, semantic, lam) + tail, "reranked"

    # ---- protocol ----------------------------------------------------
    @staticmethod
    def _blank_state(profile: dict | None = None) -> dict:
        return {
            "terms": [], "asked": [], "phrases": [], "category": None,
            "route": None, "profile": profile or {}, "dry_others": 0,
            # phrase -> terms it contributed, so slot erasure can drop them too
            "provenance": {}, "overrides": 0, "dry_streak": 0, "broad_options": [],
            "slots": [], "outcome": "", "wants_more": 0, "shown": [],
            "uncertain_streak": 0, "last_override_turn": 0, "rotate_pending": False,
            "route_history": [],
        }
