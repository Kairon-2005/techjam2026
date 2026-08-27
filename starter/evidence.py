"""Evidence: what the customer said, and how much of it to believe.

Message parsing, typed slots, polarity and hardness. This module is the leaf
of the package: it imports nothing else from `starter`, so it can be reasoned
about -- and tested -- without a catalog, a session or an agent.

Host capabilities required: NONE. Every function here is pure.
"""
from __future__ import annotations

import dataclasses
import re

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
WS_RE = re.compile(r"\s+")

BASE_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}
# Boilerplate emitted by the simulated customer; carries zero information.
CHROME_STOP = {
    "dont", "don", "have", "additional", "preference", "not", "quite", "right",
    "yet", "ask", "about", "one", "specific", "attribute", "those", "options",
    "judgment", "use", "actually", "ignore", "earlier", "what", "need",
    "key", "requirement", "still", "exploring", "matters", "prefer",
}

NOISE_REPLIES = (
    "i don't have an additional preference",
    "i don't have a preference",
    "those options are not quite right",
)
OVERRIDE_MARK = "ignore my earlier preference"

PROBE_ORDER = ["material", "color", "style", "feature", "use_case",
               "category", "brand", "size", "budget", "other"]

def _norm(text: str) -> str:
    return WS_RE.sub(" ", text).strip().lower()
# --------------------------------------------------------------------------
# Message parsing: turn simulated-customer prose into a structured state.
# --------------------------------------------------------------------------
LOOKING = "i'm looking for "
EXPLORING = ", but i'm still exploring."
KEY_REQ = ". a key requirement is: "
MATTERS = "for that, what matters is: "
MATERIAL_RE = re.compile(r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric)\b", re.I)
COLOR_RE = re.compile(r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange)\b", re.I)

NEED_IS = "what i need is: "

# Fallback extraction for paraphrased customer language (template-independent).
CUE_RE = re.compile(
    r"(?:must have|essential(?: for me)? is|requirements?\s*:|important\s*:"
    r"|what i (?:truly )?need is\s*:?|now i want\s*:?|new requirement\s*:?"
    r"|what i care about is|(?:things? that really )?matters? to me (?:would be|is|are)"
    r")\s*(.+)", re.I)
TRAILER_RE = re.compile(
    r",?\s*(?:hope you can help|if that helps|to be honest|i am easy|you know)\.?\s*$", re.I)
NOISE_RE = re.compile(
    r"(?:\bno\b|\bnot\b|don'?t|couldn'?t|could not)[^.]{0,50}\bpreference\b"
    r"|your judgment|do not mind|don'?t mind|i'?m easy|i am easy|you pick"
    r"|any \w+ is fine|up to you|not quite|wrong direction|not really working", re.I)
# What a customer turn actually did. Distinguishing these is what stops an
# uninformative reply from being treated as product evidence: "Hmm, hard to say"
# used to contribute hmm/hard/say to the BM25 query.
class Outcome:
    INFORMATIVE = "informative"
    OVERRIDE = "override"
    NO_PREFERENCE = "no_preference"
    UNCERTAIN = "uncertain"
    REFUSAL = "refusal"
    REQUEST_MORE = "request_more_options"
    CORRECTION = "correction"


MORE_RE = re.compile(
    r"(?:show|give|see|got)\b[^.?!]{0,20}\bmore\b|more options|other options|anything else"
    r"|something else|different options|next\s+(?:few|batch|set)", re.I)
UNCERTAIN_RE = re.compile(
    r"not sure|hard to say|no idea|don'?t know|do not know|can'?t say|cannot say"
    r"|what do you think|you tell me|hmm+\b|maybe\?*$", re.I)
REFUSAL_RE = re.compile(
    r"rather not say|prefer not to|won'?t say|none of your|skip (?:that|this)", re.I)
CORRECTION_RE = re.compile(r"not quite|wrong direction|not really working|that'?s not", re.I)


# Common product qualities that carry real intent but appear in no template.
FEATURE_LEX = re.compile(
    r"\b(waterproof|water[- ]resistant|breathable|lightweight|light\s?weight|insulated"
    r"|adjustable|machine washable|non[- ]slip|slip[- ]resistant|padded|quick[- ]dry"
    r"|wrinkle[- ]free|stretchy|durable|warm|cushioned|arch support)\b", re.I)
NEGATION_RE = re.compile(
    r"\b(?:not|no|nothing|never|avoid|without|don'?t want|rather not|too"
    r"|steer clear of|stay away from|skip|pass on|rule out|allergic to"
    r"|can'?t stand|dislike|not a fan of)\b", re.I)
# Restrictive positives that LOOK like negations. "Nothing but leather" means
# only leather -- reading it as a rejection inverts the constraint and actively
# demotes the right product.
RESTRICTIVE_RE = re.compile(
    r"\b(?:nothing|none)\s+(?:but|other than|except|besides)\b"
    r"|\band nothing else\b|\bnot only\b|\bonly\b", re.I)
# Exceptives, which share every word after the first with the restrictives:
# "ANYTHING but leather" rejects leather, "NOTHING but leather" requires it.
# One pattern covering both read "anything except polyester" as a positive --
# an inversion, and the worse of the two failures, because it turns a
# rejection into a requirement for the very thing the customer refused.
EXCEPTIVE_RE = re.compile(
    r"\b(?:anything|everything|something|any)\s+(?:but|other than|except|besides)\b"
    r"|\bother than\b|\bexcept\b|\bapart from\b|\baside from\b", re.I)
# Hedges. "I'm not sure whether leather matters" contains a negation but
# states no constraint: reading it as a rejection manufactures evidence out of
# an admission of ignorance, and does so with the polarity inverted.
HEDGE_RE = re.compile(
    r"\b(?:not sure|unsure|don'?t know|do not know|no idea|hard to say"
    r"|can'?t say|cannot say|not certain|either way|no strong feelings"
    r"|no preference|whatever)\b", re.I)
# Materials/qualities worth catching as rejections even though they are not in
# the positive vocabularies.
NEGATIVE_LEX = re.compile(r"\b(synthetic|plastic|itchy|scratchy|bulky|sheer|see[- ]through)\b", re.I)


def open_world_evidence(message: str) -> list[tuple[str, str, int]]:
    """Pull (attribute, value, polarity) out of free-form phrasing.

    The template parser is high precision and low recall: anything it cannot
    parse yields no evidence at all, so "Leather would be ideal" and "Mostly
    for hiking" were discarded exactly like "Hmm, hard to say". That is safe
    against filler but silently drops real constraints, which is the paraphrase
    risk on the private set. This runs only after the templates find nothing.
    """
    raw = (message or "").strip()
    if not raw:
        return []
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    vocab = list(ATTR_VOCAB.items()) + [("feature", FEATURE_LEX), ("material", NEGATIVE_LEX)]
    for attribute, pattern in vocab:
        for match in pattern.finditer(raw):
            value = _norm(match.group(0))
            if (attribute, value) in seen:
                continue
            seen.add((attribute, value))
            # Negation within the ~28 characters before the value flips
            # polarity: "nothing too formal" is a constraint, just an inverted
            # one. Three families have to be told apart, and they overlap:
            #   "nothing BUT leather"   -> restrictive positive (only leather)
            #   "anything BUT leather"  -> exceptive negative   (not leather)
            #   "not sure about leather"-> hedge, no constraint at all
            window = raw[max(0, match.start() - 28):match.start()]
            if HEDGE_RE.search(window):
                continue
            if RESTRICTIVE_RE.search(window):       # checked first: "nothing
                negated = False                     # other than X" is both
            elif EXCEPTIVE_RE.search(window):
                negated = True
            else:
                negated = bool(NEGATION_RE.search(window))
            found.append((attribute, value, -1 if negated else 1))
    return found


# Requirement language versus preference language. hardness was previously
# assigned from the TURN INDEX -- turn 1 hard, everything later soft -- which
# says nothing about what the customer actually asked for, and made the field
# unusable as a filter gate (a requirement stated on turn 3 was "soft", an
# idle turn-1 aside was "hard").
HARD_RE = re.compile(
    r"\b(?:must|needs? to be|has to be|have to be|requirement|required|require"
    r"|essential|non-?negotiable|deal-?breaker|can'?t do without|cannot do without"
    r"|strictly|definitely|absolutely|it has to|i need)\b", re.I)
SOFT_RE = re.compile(
    r"\b(?:prefer|preferably|ideal|ideally|would like|would love|would be nice"
    r"|would help|leaning|lean towards|maybe|perhaps|nice to have|if possible"
    r"|open to|sort of|kind of|rather|might|probably|somewhat|i guess"
    r"|something like|on the fence)\b", re.I)


def hardness_of(message: str, default: str = "soft") -> str:
    """Requirement or preference, read from the wording rather than the turn.

    `default` is the verdict when the message commits to neither: evidence the
    template parser understood is a stated constraint and defaults hard, while
    open-world extraction from free text defaults soft.
    """
    low = message or ""
    if HARD_RE.search(low):
        return "hard"
    if SOFT_RE.search(low):
        return "soft"
    return default


def relaxation_order(slots) -> list:
    """Constraints in give-up-first order, per the Phase 2B rescue contract:

        soft  ->  low-confidence hard  ->  older hard  ->  latest explicit hard

    The last thing surrendered is the most recent thing the customer stated as
    a requirement, which is also the thing they would notice being ignored.
    This is the single ordering both the rescue lane and (in Phase 2B) the
    safe hard-filter relaxation read, so the two cannot disagree.
    """
    return sorted([sl for sl in slots if sl.active],
                  key=lambda sl: (sl.hardness == "hard", sl.confidence, sl.source_turn))


def classify_reply(message: str, parsed_phrases: list[str],
                   parsed_category: str | None = None,
                   open_world: bool = True) -> str:
    """Label one customer turn. Evidence is merged only for INFORMATIVE/OVERRIDE.

    Order matters: an override is an override even though it also carries a new
    constraint, and a request for more options is not a preference statement.
    Anything unrecognised with no parsable constraint falls through to
    UNCERTAIN, so unknown phrasing can never silently become query terms.
    """
    low = (message or "").lower()
    if is_override(message):
        return Outcome.OVERRIDE
    evidence = bool(parsed_phrases) or bool(parsed_category)
    if MORE_RE.search(low) and not evidence:
        return Outcome.REQUEST_MORE
    if any(n in low for n in NOISE_REPLIES):
        return Outcome.NO_PREFERENCE
    if evidence:
        # A stated category is evidence even with no constraint attached:
        # "I'm looking for running shoes, but I'm still exploring."
        return Outcome.INFORMATIVE
    if NOISE_RE.search(low) and not CUE_RE.search(low):
        return Outcome.NO_PREFERENCE
    if CORRECTION_RE.search(low):
        return Outcome.CORRECTION
    if REFUSAL_RE.search(low):
        return Outcome.REFUSAL
    if open_world and open_world_evidence(message):
        return Outcome.INFORMATIVE
    return Outcome.UNCERTAIN


@dataclasses.dataclass
class SlotValue:
    """One piece of stated evidence, with everything needed to judge it later."""
    attribute: str
    value: str
    polarity: int = 1                 # +1 wants it, -1 rejects it
    hardness: str = "hard"            # hard | soft
    confidence: float = 1.0
    source_turn: int = 0
    provenance: tuple[str, ...] = ()  # query terms this slot contributed
    active: bool = True
    soft_ok: bool = True              # may this slot be soft-rescued?
    catalog_support: int = -1         # -1 unknown; else verbatim matches in pool
    contradiction: str = ""           # "" | "contested" | "unsupported"

    @property
    def usable(self) -> bool:
        return self.active and self.polarity > 0


# Slot taxonomy. The names come from the API contract's ALLOWED_ATTRIBUTES, not
# from the simulator, so the same buckets apply to any customer phrasing.
SLOT_RES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("budget", re.compile(r"budget|price|\$\s*\d|\bunder\s+\d|<=\s*\d|\bcheap|\bafford", re.I)),
    ("material", MATERIAL_RE),
    ("color", re.compile(r"\bcolou?r\b|" + COLOR_RE.pattern, re.I)),
    ("size", re.compile(r"\bsize|sizing|width|\bwide\b|narrow|\bfits?\b|\blength\b", re.I)),
    ("style", re.compile(r"department|\bstyle|\bfit\b|sleeve|\bneck|collar|pattern", re.I)),
    ("use_case", re.compile(r"hiking|running|gym|winter|summer|outdoor|\bwork\b|travel|casual", re.I)),
    ("brand", re.compile(r"\bbrand\b|\bmade by\b", re.I)),
)


# Catalog-readable attribute vocabularies. Used to measure how much a given
# question would actually split the current candidate pool.
# How likely a shopper can answer a question about this attribute at all.
# Someone who just said "I don't know" can still say what they'll use it for;
# they probably still cannot tell you a fabric composition.
ANSWERABILITY: dict[str, float] = {
    "use_case": 1.00, "category": 0.90, "style": 0.80, "color": 0.75,
    "budget": 0.55, "brand": 0.50, "feature": 0.45, "material": 0.40, "size": 0.35,
}

ATTR_VOCAB: dict[str, "re.Pattern[str]"] = {
    "material": MATERIAL_RE,
    "color": COLOR_RE,
    "style": re.compile(r"\b(slim|regular|relaxed|loose|oversized|crew neck|v-?neck|"
                        r"long sleeve|short sleeve|sleeveless|hooded|zip|button)\b", re.I),
    "use_case": re.compile(r"\b(hiking|running|gym|workout|yoga|winter|summer|outdoor|"
                           r"work|travel|casual|formal|sleep|swim)\b", re.I),
    "size": re.compile(r"\b(x-?small|small|medium|large|x-?large|xx-?large|petite|plus)\b", re.I),
}


def _distinguishing(labels: list[str]) -> list[str]:
    """Drop the words every option shares -- those carry no choice for the user.

    "women clothing dresses casual" / "women clothing dresses work" becomes
    "casual" / "work".
    """
    split = [l.split() for l in labels]
    if len(split) < 2:
        return labels
    common = set(split[0])
    for words in split[1:]:
        common &= set(words)
    trimmed = [" ".join(w for w in words if w not in common) or " ".join(words[-2:])
               for words in split]
    return trimmed if len(set(trimmed)) == len(trimmed) else labels


# Attributes a shopper can only hold one value of at a time. Two "feature"
# constraints coexist happily ("rubber sole" AND "imported"); two materials
# usually mean the later one replaced the earlier.
SINGLE_VALUED = frozenset({"material", "color", "size", "budget", "brand", "category"})


def slot_of(phrase: str) -> str:
    """Bucket a constraint phrase into one attribute slot ("feature" = default)."""
    for name, pattern in SLOT_RES:
        if pattern.search(phrase):
            return name
    return "feature"


# An override needs a replacement, not just the word "forget". The first fix
# for the missed "forget shoes slippers entirely" used a bare \bforget\b, which
# also fires on "Don't forget it needs pockets" and "I forgot to mention black"
# -- those ADD a requirement rather than replacing one.
OVERRIDE_RE = re.compile(
    r"ignore my earlier|disregard my earlier|forget what i said"
    r"|forget my (?:earlier|previous)|scratch that|change of plans"
    r"|changed my mind|start over|no longer (?:want|need|looking)"
    r"|not looking for\b[^.]{0,40}\banymore", re.I)
# "forget X" / "instead of X": an override only when something replaces X.
ABANDON_RE = re.compile(
    r"\b(?:forget|drop|skip)\s+(?:about\s+)?(?P<span>[^.,;!?]{2,60})"
    r"|\b(?:instead of|rather than)\s+(?P<span2>[^.,;!?]{2,60})", re.I)
# Deliberately excludes a bare "instead": in "made of leather instead of
# synthetic" the same word would satisfy both the abandon cue and the
# replacement cue, turning a single constraint into a false override.
REPLACEMENT_RE = re.compile(
    r"what i (?:really |truly )?need is|i (?:now )?want|i'?d (?:like|prefer)"
    r"|now i'?m|looking for|let'?s (?:go|try)|new requirement", re.I)
# Phrases containing "forget" that are never an intent override.
NOT_OVERRIDE_RE = re.compile(
    r"don'?t forget|do not forget|forgot to mention|forget-?me-?not|always forget"
    r"|never forget|didn'?t forget", re.I)


def is_override(message: str) -> bool:
    """Did this turn REPLACE an earlier intent (vs. add to it)?"""
    low = (message or "").lower()
    if NOT_OVERRIDE_RE.search(low):
        return False
    if OVERRIDE_MARK in low or OVERRIDE_RE.search(low):
        return True
    return bool(ABANDON_RE.search(low) and REPLACEMENT_RE.search(low))


def abandoned_span(message: str) -> str:
    """The thing the customer explicitly told us to drop, if they named one."""
    low = (message or "").lower()
    if NOT_OVERRIDE_RE.search(low):
        return ""
    match = ABANDON_RE.search(low)
    if not match:
        return ""
    span = (match.group("span") or match.group("span2") or "").strip()
    span = re.split(r"\s+[-\u2013\u2014]\s+", span)[0]       # drop a trailing aside
    span = re.sub(r"\b(entirely|completely|altogether|for now|please)\b", " ", span, flags=re.I)
    return WS_RE.sub(" ", span).strip(" -")


def _fallback_phrases(raw: str) -> list[str]:
    """Cue-based constraint extraction when no known template matches."""
    match = CUE_RE.search(raw)
    if not match:
        return []
    body = TRAILER_RE.sub("", match.group(1).strip()).strip().rstrip(".!?")
    parts = [TRAILER_RE.sub("", p).strip().rstrip(".!?") for p in body.split(";")]
    out = [p for p in parts if len(p) >= 3]
    if len(out) > 1 and len(body) >= 3:
        out.append(body)
    return out


def parse_message(message: str) -> tuple[str | None, list[str]]:
    """Return (stated_category, [constraint phrases]) for one customer turn."""
    raw = (message or "").strip()
    low = raw.lower()
    if low.startswith(LOOKING):
        rest = raw[len(LOOKING):]
        rest_low = low[len(LOOKING):]
        if rest_low.endswith(EXPLORING):
            return rest[: -len(EXPLORING)].strip(), []
        idx = rest_low.find(KEY_REQ)
        if idx >= 0:
            category = rest[:idx].strip()
            value = rest[idx + len(KEY_REQ):].strip().rstrip(".")
            return category, [value] if value else []
        head, sep, tail = rest.partition(". ")
        if sep:
            return head.strip(), [tail.strip().rstrip(".")] if tail.strip() else []
        return rest.strip().rstrip("."), []
    if low.startswith(MATTERS):
        body = raw[len(MATTERS):].strip().rstrip(".")
        parts = [p.strip() for p in body.split("; ") if p.strip()]
        # keep the whole body too: a constraint may itself contain "; "
        if len(parts) > 1:
            parts.append(body)
        return None, parts
    idx = low.find(NEED_IS)
    if idx >= 0:
        value = raw[idx + len(NEED_IS):].strip().rstrip(".")
        return None, [value] if value else []
    return None, _fallback_phrases(raw)


def terms_of(text: str, stop: frozenset | set) -> list[str]:
    """Query terms from free text, minus the stopword set the host supplies.

    Extracted from `Agent._terms` so both mixins can reach it without either
    of them owning it. The host keeps a thin `_terms` wrapper: the stopword
    set is per-agent configuration, so it stays a host capability rather than
    module state.
    """
    return [t.lower() for t in TOKEN_RE.findall(text)
            if len(t) > 1 and t.lower() not in stop]
