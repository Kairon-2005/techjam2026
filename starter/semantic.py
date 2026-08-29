"""Phase 7A-R1 arm A2: the semantic cascade, as its own module.

WHY IT LIVES HERE. Candidate ordering is retrieval's domain (Phase 5B,
notes/25). An ONNX session, a tokenizer, a product serialization and a rank
fusion are not dialogue concerns, and the first draft put all four on
DialogueMixin. This module owns them and RetrievalMixin calls it.

Imports only `starter.context` (for the pure fusion kernel), so the import
graph stays acyclic: context imports nothing from the package.

THE TOPOLOGY, frozen in notes/44 revision 3 and unchanged by revision 4:

    A0(full candidate population)  ->  rerank of a COPY of the prefix
                                   ->  unchanged A0 tail

A2 runs AFTER `_rotate` and never replaces `ranked`. Every failure path returns
the A0 ordering unchanged, with a REASON -- ten distinct ones, because
"something went wrong" and "the model was never installed" call for different
responses, and collapsing them would let a shard where the model never ran be
reported as a quality result.
"""
from __future__ import annotations

from pathlib import Path

from starter import context as _context

SEMANTIC_MODES = ("off", "on")
QUERY_CHARS = 200
ONNX_RELATIVE = ("onnx", "model_qint8_arm64.onnx")

# The ten outcomes. Ordered as the checks run.
REASON_MODE_OFF = "mode_off"
REASON_LAMBDA_ZERO = "lambda_zero"
REASON_PREFIX_TOO_SHORT = "prefix_too_short"
REASON_INELIGIBLE = "ineligible"
REASON_EMPTY_QUERY = "empty_query"
REASON_MODEL_ABSENT = "model_absent"
REASON_LOAD_FAILURE = "load_failure"
REASON_INFERENCE_FAILURE = "inference_failure"
REASON_BAD_PERMUTATION = "bad_permutation"
REASON_RERANKED = "reranked"

REASONS = (REASON_MODE_OFF, REASON_LAMBDA_ZERO, REASON_PREFIX_TOO_SHORT,
           REASON_INELIGIBLE, REASON_EMPTY_QUERY, REASON_MODEL_ABSENT,
           REASON_LOAD_FAILURE, REASON_INFERENCE_FAILURE,
           REASON_BAD_PERMUTATION, REASON_RERANKED)

# A shard where any of these fired did not measure the semantic arm. The
# production path fails open to A0, but an EXPERIMENT that silently fell back
# would report A0's quality under A2's name.
#
# `model_absent` IS INVALIDATING. It was first classified as "a configuration
# fact", which is true and beside the point: an A2 shard whose model directory
# was missing measured A0 on every turn, and a configuration fact that produces
# a quality number under the wrong arm's name is exactly what this set exists to
# catch. The distinction that matters downstream is not blame -- it is whether
# the shard measured what it claims. It did not.
INVALIDATING_REASONS = frozenset({REASON_MODEL_ABSENT, REASON_LOAD_FAILURE,
                                  REASON_INFERENCE_FAILURE,
                                  REASON_BAD_PERMUTATION})

# Turns on which the MODEL ITSELF RAN, or was asked to. `inference_failure`
# means the session existed, the batch was fed and the forward pass raised --
# the model was invoked and failed. `load_failure` is the opposite: the session
# was never constructed, so nothing was invoked. Counting a load failure as an
# invocation would inflate the denominator of every per-invocation cost or
# activation figure with turns on which no inference happened.
INVOKED_REASONS = frozenset({REASON_INFERENCE_FAILURE, REASON_BAD_PERMUTATION,
                             REASON_RERANKED})

# Legitimate non-invocations. The cascade decided, correctly and by design, not
# to call the model. These are DESCRIPTIVE and never invalidate a shard:
# `ineligible` is the frozen robustness gate doing its job, `empty_query` is a
# turn with no accepted evidence to ask about, and `prefix_too_short` is a
# window of fewer than two items, where a permutation cannot exist.
#
# `lambda_zero` and `mode_off` are also legitimate: lambda = 0 is a real
# `sup-train` OUTCOME meaning the semantic signal failed to beat A0, reported as
# such rather than retried -- and notes/44 Step 0 then eliminates A2 as a no-op.
# A legitimate result and a disqualifying one are not in tension; the arm ran,
# and what it found was nothing.
LEGITIMATE_UNINVOKED_REASONS = frozenset({REASON_MODE_OFF, REASON_LAMBDA_ZERO,
                                          REASON_PREFIX_TOO_SHORT,
                                          REASON_INELIGIBLE, REASON_EMPTY_QUERY})

assert set(REASONS) == (INVALIDATING_REASONS | INVOKED_REASONS
                        | LEGITIMATE_UNINVOKED_REASONS), "a reason is unclassified"
assert not (INVALIDATING_REASONS & LEGITIMATE_UNINVOKED_REASONS)
assert not (INVOKED_REASONS & LEGITIMATE_UNINVOKED_REASONS)
assert REASON_LOAD_FAILURE not in INVOKED_REASONS


def is_invalidating(reason: str) -> bool:
    """Did this turn fail to measure the semantic arm?"""
    return reason in INVALIDATING_REASONS


def is_invoked(reason: str) -> bool:
    """Was the model asked to run on this turn?"""
    return reason in INVOKED_REASONS


def mode_of(cfg) -> str:
    mode = str(cfg.get("semantic_rerank_mode", "off"))
    return mode if mode in SEMANTIC_MODES else "off"


def effective_k(k, top_k: int, n: int) -> int:
    """min(semantic_rerank_k, top_k, len(ordered)), never negative.

    The `top_k` term preserves the returned SET for a caller passing a smaller
    top_k: reordering the first 10 could promote a rank-7 item into a returned
    five and change what the caller sees.
    """
    return max(0, min(int(k), int(top_k), int(n)))


def eligible(state: dict, uncredible=frozenset()) -> bool:
    """The frozen robustness gate. Product logic, never searched.

    `uncredible` is DialogueMixin's contested/pre-override value set, passed in
    rather than reached for: a value the session has contested is exactly the
    case where A0's structured handling is doing work this relevance model has
    not been validated to replicate.
    """
    if str(state.get("route") or "") not in ("browsing", "mixed"):
        return False
    if int(state.get("last_override_turn") or 0):
        return False
    if uncredible:
        return False
    hard = 0
    for slot in state.get("slots") or ():
        if slot.active and slot.polarity < 0:
            return False                       # active negative
        if not slot.soft_ok:
            return False                       # abandoned / suppressed
        if slot.usable and slot.hardness == "hard":
            hard += 1
    return hard <= 1


def build_query(state: dict, uncredible=frozenset(),
                max_chars: int = QUERY_CHARS) -> str:
    """One canonical query. No variants, and none selectable later.

    Order: category, use-case evidence, other positive constraints, accepted
    evidence terms. Excludes negatives -- this MS MARCO relevance model has not
    been validated to enforce hard negative constraints, so they stay with A0's
    structured logic -- along with suppressed values, CONTESTED values from
    `uncredible`, raw message text and profile tags.
    """
    blocked = {str(v).casefold() for v in (uncredible or ())}
    parts: list[str] = []
    if state.get("category"):
        parts.append(str(state["category"]))
    slots = [s for s in (state.get("slots") or ())
             if s.usable and s.soft_ok and str(s.value).casefold() not in blocked]
    ordered = sorted(slots, key=lambda s: (s.source_turn, str(s.value)))
    parts += [str(s.value) for s in ordered if s.attribute == "use_case"]
    parts += [str(s.value) for s in ordered if s.attribute != "use_case"]
    parts += [str(t) for t in (state.get("terms") or ())]

    seen: set[str] = set()
    kept: list[str] = []
    for raw in parts:
        piece = " ".join(str(raw).split()).casefold()
        if not piece or piece in seen or piece in blocked:
            continue
        seen.add(piece)
        kept.append(piece)
    query = " ".join(kept)
    if len(query) <= max_chars:
        return query
    cut = query[:max_chars]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def product_text(cat, asin: str) -> str:
    """title -> full category path -> features/details -> description.

    Each field EXACTLY ONCE. `cat.text` is not used: it is
    " ".join(title, categories, features, details, store, description), so
    building from it would duplicate three fields and include `store`, which
    the pre-registration excludes. The duplication also matters at
    max_length=256 -- repeated title and category tokens would push the
    description out of the window entirely.
    """
    title = getattr(cat, "sem_title", {}).get(asin, "")
    desc = getattr(cat, "sem_desc", {}).get(asin, "")
    pieces = [title, cat.cats.get(asin, ""), cat.feat.get(asin, ""), desc]
    return " ".join(". ".join(p for p in pieces if p).split())


class Scorer:
    """ONNX session + tokenizer, loaded once per model directory."""

    def __init__(self, tokenizer, session, input_names) -> None:
        self.tokenizer = tokenizer
        self.session = session
        self.input_names = input_names

    @classmethod
    def load(cls, model_dir: str, max_length: int) -> "Scorer":
        import numpy as np                                   # noqa: F401
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root = Path(model_dir)
        tok = Tokenizer.from_file(str(root / "tokenizer.json"))
        tok.enable_truncation(max_length=int(max_length), strategy="only_second")
        tok.enable_padding()
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(str(root.joinpath(*ONNX_RELATIVE)),
                                    sess_options=opts,
                                    providers=["CPUExecutionProvider"])
        return cls(tok, sess, {i.name for i in sess.get_inputs()})

    def order(self, query: str, asins, texts) -> list[str]:
        """asins re-ordered by descending relevance. Native PAIR encoding.

        No literal " [SEP] ": the tokenizer builds the special-token layout for
        its own family, and hard-coding one separator mis-tokenizes for every
        family that uses a different contract.
        """
        import numpy as np
        encs = self.tokenizer.encode_batch(list(zip([query] * len(asins), texts)))
        feed = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encs],
                                           dtype=np.int64)}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encs],
                                              dtype=np.int64)
        feed = {k: v for k, v in feed.items() if k in self.input_names}
        logits = self.session.run(None, feed)[0]
        scored = [(float(logits[i][0]), i, a) for i, a in enumerate(asins)]
        # Descending score, then original index: total and stable.
        return [a for _, _, a in sorted(scored, key=lambda x: (-x[0], x[1]))]


def reorder(ordered, *, cat, cfg, state, top_k, uncredible=frozenset(),
            scorer_for, score_order=None) -> tuple[list[str], str, int]:
    """(result, reason, effective_k). `ordered` is never mutated.

    Every failure returns the A0 ordering unchanged, so the customer-visible
    result is byte-exact A0 whenever anything at all goes wrong.
    """
    a0 = list(ordered)
    if mode_of(cfg) != "on":
        return a0, REASON_MODE_OFF, 0
    k = effective_k(cfg.get("semantic_rerank_k", 0), top_k, len(a0))
    if float(cfg.get("semantic_lambda", 0.0)) == 0.0:
        return a0, REASON_LAMBDA_ZERO, k
    if k < 2:
        return a0, REASON_PREFIX_TOO_SHORT, k
    if not eligible(state, uncredible):
        return a0, REASON_INELIGIBLE, k
    query = build_query(state, uncredible)
    if not query:
        return a0, REASON_EMPTY_QUERY, k

    prefix, tail = a0[:k], a0[k:]
    if score_order is None:
        directory = str(cfg.get("semantic_model_dir") or "")
        if not directory or not Path(directory).is_dir():
            return a0, REASON_MODEL_ABSENT, k
        try:
            scorer = scorer_for(directory, int(cfg.get("semantic_max_length", 256)))
        except Exception:
            return a0, REASON_LOAD_FAILURE, k
        texts = [product_text(cat, a) for a in prefix]
        try:
            semantic = list(scorer.order(query, list(prefix), texts))
        except Exception:
            return a0, REASON_INFERENCE_FAILURE, k
    else:
        try:
            semantic = list(score_order(query, list(prefix)))
        except Exception:
            return a0, REASON_INFERENCE_FAILURE, k

    if not _context.is_permutation(semantic, prefix):
        # A scorer that dropped or invented an asin must never reach the
        # customer. Counted, not silently tolerated.
        return a0, REASON_BAD_PERMUTATION, k
    lam = float(cfg["semantic_lambda"])
    return _context.rrf_fuse(prefix, semantic, lam) + tail, REASON_RERANKED, k
