"""Four pillars, on real public sessions, in a form you can screen-record.

WHAT THIS IS NOT. It is not a scripted walkthrough. Every session below is a
real public-set sample driven by the OFFICIAL evaluator's own customer
simulator, and the scenario variants reuse `lab/scenarios.py`'s hooks rather
than a second definition of how a customer behaves.

THE AGENT NEVER SEES THE TARGET. It receives the anonymized profile and the
customer's messages, exactly as it does under scoring. The hidden target and
whether it was found are known only to this harness, which is the outer
evaluator here, and are printed once at the end of each session -- the same
separation the real evaluator keeps.

    python3 -m demo                       # the four default scenarios
    python3 -m demo --only override       # one of them
    python3 -m demo --extra semantic      # + the A2-10 Top-10 permutation
    python3 -m demo --extra dense         # + dense candidates and RRF sources
    python3 -m demo --extra profile       # + profile credibility rejection
    python3 -m demo --list
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import starter.agent as A
from evaluator import local_evaluator as E

CATALOG = Path("data/catalog.jsonl")
PUBLIC = Path("data/public_set.jsonl")
RULE = "─" * 72
TOP_SHOWN = 3


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def title(text: str, sub: str = "") -> None:
    print(f"\n{RULE}\n  {text}")
    if sub:
        print(f"  {sub}")
    print(RULE)


def short(text: str, width: int = 62) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def slot_line(state: dict, cap: int = 26, most: int = 4) -> str:
    """Active evidence, then what the session has erased or suppressed.

    Values are bounded because the simulator discloses whole sentences as
    constraints -- a 200-character slot is real evidence and unreadable on a
    recording, so it is elided here and nowhere else.
    """
    live, dead = [], []
    for slot in state.get("slots") or ():
        sign = "+" if slot.polarity > 0 else "−"
        mark = f"{sign}{short(slot.value, cap)} ({slot.hardness[0]}{slot.source_turn}"
        mark += f", c={slot.confidence:g})" if slot.confidence != 1.0 else ")"
        (live if (slot.active and slot.soft_ok) else dead).append(mark)

    def join(items):
        head = "  ".join(items[:most])
        return head + (f"  +{len(items) - most} more" if len(items) > most else "")

    out = join(live) or "—"
    if dead:
        out += "\n               dropped: " + join(dead)
    return out


def show_turn(turn: int, message: str, state: dict, response: dict,
              trace: dict, cat, ms: float) -> None:
    print(f"\n  turn {turn}  ·  {ms:6.1f} ms")
    print(f"    customer   {short(message)}")
    mode = trace.get("decided_retrieval_mode", "?")
    reasons = ",".join(trace.get("decided_reasons") or []) or "—"
    print(f"    route      {str(trace.get('route', state.get('route'))):<12}"
          f" retrieval  {mode} ({reasons}, depth {trace.get('decided_depth')})")
    print(f"    evidence   {slot_line(state)}")
    asked = response.get("ask_attribute")
    why = trace.get("q_primary_reason", "—")
    render = trace.get("q_render_mode", "")
    print(f"    question   {str(asked or '—'):<12} {why}"
          f"{' / ' + render if render else ''}")
    print(f"    agent      {short(response.get('message', ''))}")
    recs = [r.get("parent_asin") if isinstance(r, dict) else r
            for r in (response.get("recommendations") or [])]
    for rank, asin in enumerate(recs[:TOP_SHOWN], 1):
        print(f"    top {rank}      {asin}  {short(cat.title.get(asin, ''), 48)}")
    if not recs:
        print("    top        — no recommendation this turn")


def show_result(hit_turn, best_rank, target: str, cat) -> None:
    print(f"\n    ── harness only, never visible to the agent ──")
    print(f"    target     {target}  {short(cat.title.get(target, ''), 46)}")
    if hit_turn is None:
        print("    RESULT     not surfaced in a scored Top-10 within 10 turns")
    else:
        print(f"    RESULT     found at turn {hit_turn}, rank {best_rank}"
              f"  ·  RR {1.0 / best_rank:.3f}")


# --------------------------------------------------------------------------
# driving one real session
# --------------------------------------------------------------------------
def drive(agent, sample: dict, ids, cats, prods, *, scenario=None,
          seed: int = 7, quiet: bool = False) -> dict:
    """One session through the evaluator's own loop, with the trace shown.

    `scenario` is an optional `lab.scenarios.Scenario` whose `init`/`reply`
    hooks replace the default customer -- the same objects the robustness
    matrix uses, so the demo cannot drift from what was measured.
    """
    rng = None
    if scenario is not None:
        from lab import scenarios as SC
        rng = SC._rng_for(scenario.name, sample, seed)
    sid = f"demo_{sample['sample_id']}"
    agent.reset(sid, sample["user_profile"])
    target = str(sample["ground_truth"]["parent_asin"])
    card, behavior = E.materialize_hidden_fields(sample, prods)
    if scenario is not None and scenario.mutate:
        card, behavior = scenario.mutate(sample, card, behavior, prods, rng)
    effective = {**sample, "intent_card": card, "behavior": behavior}
    disclosed: set[str] = set()
    boundary_used = False
    applied = sample["scenario_type"] != "intent_override"
    coarse = E.coarse_category(cats.get(target, []))
    message = None
    if scenario is not None and scenario.init:
        message = scenario.init(effective, coarse, disclosed, rng)
    if message is None:
        message = E.initial_message(effective, coarse, disclosed)
    hit_turn = best_rank = None
    for turn in range(1, E.MAX_TURNS + 1):
        t0 = time.perf_counter()
        response = agent.respond(sid, message, turn, E.TOP_K)
        ms = (time.perf_counter() - t0) * 1000
        state = agent._sessions[sid]
        trace = (state.get("trace_log") or [{}])[-1]
        if not quiet:
            show_turn(turn, message, state, response, trace, agent.cat, ms)
        ranked = E.normalize_recommendations(response.get("recommendations"), ids)
        if applied and target in ranked:
            best_rank, hit_turn = ranked.index(target) + 1, turn
            break
        if turn == E.MAX_TURNS:
            break
        override = (effective.get("behavior") or {}).get("override") or {}
        if not applied and turn + 1 == int(override.get("turn", 3)):
            applied = True
            if override.get("new_value"):
                disclosed.add(str(override["new_value"]))
            message = str(override.get("message", "Actually, ignore that."))
        else:
            reply = None
            if scenario is not None and scenario.reply:
                reply = scenario.reply(effective, response.get("ask_attribute"),
                                       disclosed, boundary_used, rng)
            if reply is None:
                reply = E.customer_reply(effective, response.get("ask_attribute"),
                                         disclosed, boundary_used)
            message, boundary_used = reply
    if not quiet:
        show_result(hit_turn, best_rank, target, agent.cat)
    return {"hit_turn": hit_turn, "best_rank": best_rank, "target": target,
            "session_id": sid}


# --------------------------------------------------------------------------
# the four default scenarios
# --------------------------------------------------------------------------
def pick(samples, scenario_type: str, index: int = 0) -> dict:
    rows = [s for s in samples if s["scenario_type"] == scenario_type]
    if not rows:
        raise SystemExit(f"no public sample with scenario_type={scenario_type!r}")
    return rows[index % len(rows)]


def pick_id(samples, sample_id: str, fallback_type: str) -> dict:
    """A named public session, so a demo shows the behaviour it claims to.

    Pinned by id rather than by position: these sessions were chosen because
    they exercise a specific path -- over-generality, a targeted question --
    and picking "the first browsing sample" would silently stop doing that the
    moment the corpus is reordered.
    """
    for row in samples:
        if str(row["sample_id"]) == sample_id:
            return row
    return pick(samples, fallback_type)


def demo_buying(ctx) -> None:
    title("1 · BUYING — hard constraints, precision track",
          "The customer states what they want. Watch `evidence` accumulate as "
          "typed slots and the retrieval mode stay on the precision route.")
    drive(ctx["agent"], pick(ctx["samples"], "buying"), *ctx["index"])


def demo_browsing(ctx) -> None:
    title("2 · BROWSING — over-generality and proactive clarification",
          "The opening names a category and no constraint. Watch the pool span "
          "many coarse categories (`overgeneral`), and the question controller "
          "stop asking the open 'anything else' and ask a POOL-DERIVED question "
          "instead -- `asked_targeted`, with the attribute chosen by how well it "
          "splits the live candidate pool.")
    drive(ctx["agent"], pick_id(ctx["samples"], "public_0012", "browsing"),
          *ctx["index"])


def demo_override(ctx) -> None:
    title("3 · INTENT OVERRIDE — what score_default actually does",
          "The customer replaces a preference mid-session. score_default's "
          "policy is `on_override='keep'`, and that is a MEASURED choice, not "
          "an oversight -- see below. A scored hit before the override turn "
          "counts for nothing, which is why this scenario is scored from the "
          "override turn onward.")
    sample = pick(ctx["samples"], "intent_override")
    drive(ctx["agent"], sample, *ctx["index"])

    print("\n  Why evidence is KEPT rather than erased")
    print("    This system SCORES candidates, it does not filter them, so an "
          "obsolete\n    constraint can only contribute a little wrong credit "
          "-- while forgetting\n    genuinely destroys evidence. Measured over "
          "5 seeds on the override\n    stress harness (lab/override_stress.py, "
          "NOTES.md):")
    print("      keep   0.9233   ← shipped")
    print("      slot   0.9140   targeted erasure of the superseded value")
    print("      erase  0.8458   drop all evidence on override")
    print("\n    Targeted erasure IS implemented and IS measured; it is a "
          "config key,\n    `on_override='slot'`, and it is not the default "
          "because it scored worse.")

    ids, cats, prods = ctx["index"]
    erasing = A.Agent(str(CATALOG), config={"trace": True, "on_override": "slot"})
    out = drive(erasing, sample, ids, cats, prods, quiet=True)
    state = erasing._sessions[out["session_id"]]
    print(f"\n    the same session under on_override='slot':")
    print(f"      evidence   {slot_line(state)}")
    print(f"      result     " + ("not surfaced" if out["hit_turn"] is None else
          f"found at turn {out['hit_turn']}, rank {out['best_rank']}"))


def demo_uncooperative(ctx) -> None:
    from lab import scenarios as SC
    title("4 · UNCOOPERATIVE / REQUEST MORE — recovery without evidence",
          "The customer stops answering and asks for alternatives. Watch "
          "starvation-aware widening in `retrieval`, and the pinned head with a "
          "refreshed tail after a request-more turn.")
    drive(ctx["agent"], pick(ctx["samples"], "buying", 3), *ctx["index"],
          scenario=SC.BY_NAME["uncooperative"])


# --------------------------------------------------------------------------
# optional showcases
# --------------------------------------------------------------------------
def demo_semantic(ctx) -> None:
    from starter import semantic as SEM
    title("5 · SHOWCASE — A2-10 semantic Top-10 permutation  (feature-off)",
          "Architecture demonstration and supplementary evidence ONLY. This "
          "arm has NO public result and is off in score_default.")
    if not Path(A.SEMANTIC_MODEL_DIR).is_dir():
        print(f"\n  artifact absent at {A.SEMANTIC_MODEL_DIR}; the cascade would "
              f"fall back byte-exactly to A0.")
        return
    try:
        import onnxruntime, tokenizers, numpy       # noqa: F401
    except ImportError:
        print("\n  onnxruntime/tokenizers/numpy are not installed. Install them "
              "with:\n    ./.venv/bin/pip install -r requirements-semantic.txt")
        return
    sample = pick(ctx["samples"], "browsing")
    ids, cats, prods = ctx["index"]
    a0 = A.Agent(str(CATALOG), config={"trace": True})
    a2 = A.Agent(str(CATALOG), config={"trace": True, **A.PROFILES["showcase_semantic"]})
    base = drive(a0, sample, ids, cats, prods, quiet=True)
    drive(a2, sample, ids, cats, prods, quiet=True)
    shown = 0
    for turn_a0, turn_a2 in zip(a0._sessions[base["session_id"]]["trace_log"],
                                a2._sessions[base["session_id"]]["trace_log"]):
        if turn_a2.get("semantic_reason") != SEM.REASON_RERANKED:
            continue
        shown += 1
        print(f"\n  turn {turn_a2.get('turn', shown)}  ·  the model ran "
              f"(effective_k = {turn_a2.get('semantic_effective_k')})")
        break
    if not shown:
        reasons = {t.get("semantic_reason") for t
                   in a2._sessions[base["session_id"]]["trace_log"]}
        print(f"\n  the eligibility gate did not open on this session: {sorted(r for r in reasons if r)}")
        print("  Buying, contradiction-sensitive and post-override traffic stays "
              "on A0 by design.")
        return
    left = _top10(a0, base["session_id"])
    right = _top10(a2, base["session_id"])
    print(f"\n    {'rank':<6}{'A0 (score_default)':<24}{'A2-10 (showcase)':<24}")
    for rank, (x, y) in enumerate(zip(left, right), 1):
        moved = "" if x == y else "  ←moved"
        print(f"    {rank:<6}{x:<24}{y:<24}{moved}")
    import collections
    print(f"\n    same multiset  {collections.Counter(left) == collections.Counter(right)}"
          f"   →  HR@10 and MTTC cannot move; only MRR can.")


def _top10(agent, session_id: str) -> list[str]:
    state = agent._sessions[session_id]
    return list(state.get("shown") or ())[:10]


def demo_dense(ctx) -> None:
    title("6 · SHOWCASE — dense candidate source and RRF fusion  (feature-off)",
          "Architecture demonstration ONLY. Not score_default: it improves "
          "Browsing MRR but drops Boundary MRR 1.000 → 0.870.")
    sample = pick(ctx["samples"], "browsing")
    agent = A.Agent(str(CATALOG),
                    config={"trace": True, "dense_browsing": True,
                            "dense_mixed": True, "dense_fusion": "rrf"})
    out = drive(agent, sample, *ctx["index"], quiet=True)
    for turn in agent._sessions[out["session_id"]]["trace_log"]:
        if turn.get("dense_returned") is None:
            continue
        print(f"\n  turn {turn.get('turn', '?')}  plane {turn.get('plane')}  "
              f"fusion {turn.get('fusion')}")
        print(f"    dense returned      {turn['dense_returned']}")
        print(f"    dense-only          {turn.get('dense_only')}   "
              f"(no lexical match at all)")
        print(f"    overlap with BM25   {turn.get('dense_overlap')}")
        print(f"    fused unique pool   {turn.get('fused_unique')}")
        break
    else:
        print("\n  the dense plane did not open on this session.")


def demo_profile(ctx) -> None:
    from starter import context as CTX
    title("7 · SHOWCASE — profile credibility  (ranking weight stays 0)",
          "The evaluator supplies an external long-term preference profile. The "
          "agent distills it into bounded profile evidence and judges each tag "
          "against the live candidate pool. Classification is RECORDED and never "
          "moves a rank: `w_profile` is 0.0 in score_default.")
    print("\n  How a tag is judged (starter/context.py):")
    print("    matches nothing in the window          → unsupported")
    print("    matches more than 50% of the window    → generic  (it separates nothing)")
    print("    matches some of it                     → specific_informative")
    print("    A session is credible only if at least one tag is specific_informative.")

    sample = pick_id(ctx["samples"], "public_0012", "browsing")
    ids, cats, prods = ctx["index"]

    # The window this session actually produces, so the constructed tag below
    # is informative ABOUT THIS POOL rather than informative in the abstract.
    probe = A.Agent(str(CATALOG),
                    config={"trace": True, "profile_context_mode": "shadow"})
    out = drive(probe, {**sample, "user_profile": {"preference_tags": ["x"]}},
                ids, cats, prods, quiet=True)
    window = next((t["profile_window_asins"] for t
                   in probe._sessions[out["session_id"]]["trace_log"]
                   if t.get("profile_window_asins")), [])
    target_words = {w for w in
                    str(prods[str(sample["ground_truth"]["parent_asin"])]
                        .get("title", "")).lower().split() if len(w) > 3}
    # BOTH cases are derived from this window's measured coverage rather than
    # guessed. A hand-picked "obviously generic" word turned out to match 10%
    # of the pool and was classified informative -- correctly, and it made the
    # demo's own caption false.
    vocabulary = sorted(target_words | {
        "trendy", "premium", "bestselling", "artisanal", "ergonomic",
        "waterproof", "titanium", "handbag", "sneaker", "dress", "women",
        "cotton", "leather", "black", "casual", "sleeve"})
    coverage = CTX.profile_coverage(vocabulary, window, probe.cat.text, 30)
    unsupported = sorted(t for t, c in coverage.items() if c == 0.0)
    generic = sorted(((c, t) for t, c in coverage.items() if c > 0.5), reverse=True)
    informative = sorted((c, t) for t, c in coverage.items() if 0.0 < c <= 0.5)
    rejected = unsupported[:2] + ([generic[0][1]] if generic else [])
    constructed = [informative[-1][1]] if informative else ["velvet"]

    # Captions describe the SHAPE of each case. The exact coverage each tag
    # gets is printed by the classifier below and is not restated here: the
    # selection pass and the classifier take their own windows, so quoting one
    # number in prose and printing another in the table would look like a bug.
    cases = [("rejected — nothing to act on", rejected,
              "two tags match no candidate in the window at all; the third "
              "matches most of it, so it separates nothing"),
             ("constructed informative — built from this session's own pool",
              constructed,
              "chosen because its coverage over the live window falls inside "
              "the informative band")]
    for label, tags, why in cases:
        agent = A.Agent(str(CATALOG),
                        config={"trace": True, "profile_context_mode": "shadow"})
        row = {**sample, "user_profile": {"preference_tags": list(tags)}}
        got = drive(agent, row, ids, cats, prods, quiet=True)
        trace = next((t for t in agent._sessions[got["session_id"]]["trace_log"]
                      if "profile_session_verdict" in t), {})
        print(f"\n  {label}")
        print(f"    tags               {tags}")
        print(f"    why               {why}")
        for tag in trace.get("profile_tags") or []:
            print(f"      {tag['tag']:<14} {tag['category']:<22} "
                  f"matches {tag['match_count']:<4} coverage {tag['coverage']}")
        print(f"    session verdict    {trace.get('profile_session_verdict')}")
        print(f"    credible tags      {trace.get('profile_credible_tags')}")

    print(f"\n    w_profile in score_default = {A.DEFAULTS['w_profile']}"
          f"  ·  w_profile_adaptive = {A.DEFAULTS['w_profile_adaptive']}"
          f"  ·  profile_context_mode = {A.DEFAULTS['profile_context_mode']!r}")
    print("    Phase 6C1 found no demonstrated target alignment, so the weight")
    print("    stays at zero. Current-session evidence always outranks the prior:")
    print("    a stated constraint enters the query, a profile tag never does.")
    print("    There is no cross-session memory -- the evaluation API provides no")
    print("    stable user identity, so none is invented.")


DEFAULT = {"buying": demo_buying, "browsing": demo_browsing,
           "override": demo_override, "uncooperative": demo_uncooperative}
EXTRA = {"semantic": demo_semantic, "dense": demo_dense, "profile": demo_profile}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="demo", description=__doc__.split("\n")[0])
    ap.add_argument("--only", action="append", default=[],
                    choices=sorted(DEFAULT) + sorted(EXTRA))
    ap.add_argument("--extra", action="append", default=[], choices=sorted(EXTRA))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--catalog", default=str(CATALOG))
    args = ap.parse_args(argv)
    if args.list:
        print("default:", ", ".join(sorted(DEFAULT)))
        print("extra:  ", ", ".join(sorted(EXTRA)))
        return 0
    if not CATALOG.exists():
        print(f"{CATALOG} is missing. See the README for the download step.",
              file=sys.stderr)
        return 1

    chosen = ([(n, {**DEFAULT, **EXTRA}[n]) for n in args.only] if args.only
              else [(n, DEFAULT[n]) for n in ("buying", "browsing", "override",
                                              "uncooperative")]
              + [(n, EXTRA[n]) for n in args.extra])
    print(f"  loading catalog and building the index ...", flush=True)
    t0 = time.perf_counter()
    samples = E.load_jsonl(PUBLIC)
    index = E.catalog_index(Path(args.catalog))
    agent = A.Agent(args.catalog, config={"trace": True})
    print(f"  ready in {time.perf_counter() - t0:.1f}s  ·  "
          f"score_default, standard library only, no network")
    ctx = {"agent": agent, "samples": samples, "index": index}
    for _, fn in chosen:
        fn(ctx)
    print(f"\n{RULE}\n  score_default reproduces 0.932067 on the full public set:")
    print("    python3 -m evaluator.local_evaluator --catalog data/catalog.jsonl "
          "--dataset data/public_set.jsonl")
    print(RULE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
