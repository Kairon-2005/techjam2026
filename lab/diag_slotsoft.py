"""Why does slot_soft cost score on override_category?

Time-boxed investigation. Finds sessions whose target rank differs between
slot_soft on/off, then dumps, for the final turn of each: the target's and the
displacing competitor's per-feature score decomposition, which phrases were
"dead" (zero verbatim support in the pool), each slot's source turn, the
f_slot values, override state, and what the credibility gate would have
blocked.

Usage:  python3 -m lab.diag_slotsoft [scenario] [seed] [max_sessions]
"""
from __future__ import annotations

import sys

import evaluator.local_evaluator as E
import starter.agent as A
from lab import scenarios as S

TOKEN_RE = A.TOKEN_RE


def decompose(agent: A.Agent, state: dict, asin: str, cands, cfg) -> dict:
    """Recompute one candidate's features the same way _rerank does."""
    phrases = [A._norm(p) for p in state["phrases"]]
    phrases = [p for p in phrases if len(p) >= 3]
    terms = state["terms"][: cfg["term_cap"]]
    idfs = {t: agent.cat.idf(t) for t in terms}
    idf_total = sum(idfs.values()) or 1.0
    blob = agent.cat.text.get(asin, "")
    feat_blob = agent.cat.feat.get(asin, "")
    vals = agent.cat.vals.get(asin, ())
    pool = [agent.cat.text.get(a, "") for a, _ in cands]
    blocked = agent._uncredible(state)
    dead_idx = [i for i, ph in enumerate(phrases)
                if ";" not in ph and len(ph) <= 80
                and not any(ph in b for b in pool)]
    ptoks = []
    for ph in phrases:
        pairs = [(t, agent.cat.idf(t)) for t in agent._terms(ph)]
        kept = [(t, w) for t, w in pairs if w >= cfg["soft_min_idf"]]
        ptoks.append(kept or pairs)
    f_slot = 0.0
    if dead_idx:
        acc = 0.0
        for i in dead_idx:
            tot = sum(w for _, w in ptoks[i]) or 1.0
            acc += sum(w for t, w in ptoks[i] if t in blob) / tot
        f_slot = acc / len(dead_idx)
    out = {
        "f_phrase": sum(1 for p in phrases if p in blob) / (len(phrases) or 1),
        "f_exact": sum(1 for p in phrases if p in vals) / (len(phrases) or 1),
        "f_field": sum(1 for p in phrases if p in feat_blob) / (len(phrases) or 1),
        "f_idf": sum(w for t, w in idfs.items() if t in blob) / idf_total,
        "f_pop": agent.cat.popularity(asin, cfg["pop_mode"]),
        "f_slot": f_slot,
    }
    out["contrib"] = {
        "phrase": cfg["w_phrase"] * out["f_phrase"], "exact": cfg["w_exact"] * out["f_exact"],
        "field": cfg["w_field"] * out["f_field"], "idf": cfg["w_idf"] * out["f_idf"],
        "pop": cfg["w_pop"] * out["f_pop"], "slot_soft": cfg["slot_soft"] * out["f_slot"],
    }
    out["total"] = sum(out["contrib"].values())
    out["dead_phrases"] = [phrases[i] for i in dead_idx]
    out["blocked_by_gate"] = sorted(blocked)
    return out


def main(scenario: str = "override_category", seed: int = 7, limit: int = 3) -> None:
    samples, ids, cats, prods = S.load()
    scen = S.BY_NAME[scenario]
    ranks: dict[str, dict] = {}
    for label, cfg in (("on", {}), ("off", {"slot_soft": 0.0})):
        res = S.run(scen, cfg, samples, ids, cats, prods, seed=seed)
        for sess in res["sessions"]:
            ranks.setdefault(sess["sample_id"], {})[label] = sess["best_rank"]
    diff = [sid for sid, r in ranks.items()
            if r.get("on") != r.get("off")]
    print(f"{scenario} seed={seed}: {len(diff)} of {len(ranks)} sessions change rank\n")
    worse = [sid for sid in diff
             if (ranks[sid]["on"] or 99) > (ranks[sid]["off"] or 99)]
    better = [sid for sid in diff
              if (ranks[sid]["on"] or 99) < (ranks[sid]["off"] or 99)]
    print(f"  slot_soft HURTS {len(worse)}   HELPS {len(better)}")
    print(f"  hurt sessions: {worse[:12]}\n")

    by_id = {s["sample_id"]: s for s in samples}
    for sid in worse[:limit]:
        sample = by_id[sid]
        target = str(sample["ground_truth"]["parent_asin"])
        print("=" * 78)
        print(f"{sid}  rank on={ranks[sid]['on']}  off={ranks[sid]['off']}  "
              f"scenario_type={sample['scenario_type']}")
        agent = A.Agent(S.CATALOG)
        state = None
        # replay the session with slot_soft ON, capturing the final state
        orig = E.materialize_hidden_fields
        card, beh = scen.mutate(sample, *orig(sample, prods), prods,
                                S._rng_for(scen.name, sample, seed)) if scen.mutate else orig(sample, prods)
        eff = {**sample, "intent_card": card, "behavior": beh}
        disclosed, bu = set(), False
        applied = sample["scenario_type"] != "intent_override"
        msg = E.initial_message(eff, E.coarse_category(cats.get(target, [])), disclosed)
        agent.reset(sid, sample["user_profile"])
        for turn in range(1, 11):
            out = agent.respond(sid, msg, turn, 10)
            recs = [r["parent_asin"] for r in out["recommendations"]]
            state = agent._sessions[sid]
            if applied and target in recs:
                break
            if turn == 10:
                break
            ov = eff.get("behavior", {}).get("override") or {}
            if not applied and turn + 1 == int(ov.get("turn", 3)):
                applied = True
                disclosed.add(str(ov.get("new_value", "")))
                msg = str(ov.get("message", ""))
            else:
                msg, bu = E.customer_reply(eff, out.get("ask_attribute"), disclosed, bu)
        cfg = agent._route_cfg(state)
        cands = agent._retrieve(state["terms"], max(10, cfg["candidates"]))
        ranked = agent._rerank(cands, state)
        competitor = ranked[0]
        print(f"  slots: " + ", ".join(
            f"[t{sl.source_turn} {sl.attribute} {'ACTIVE' if sl.usable else sl.contradiction}] "
            f"{sl.value[:34]!r}" for sl in state["slots"][:6]))
        print(f"  last_override_turn={state.get('last_override_turn')}  "
              f"category={state.get('category')!r}")
        for who, asin in (("TARGET    ", target), ("COMPETITOR", competitor)):
            d = decompose(agent, state, asin, cands, cfg)
            print(f"  {who} {asin} total={d['total']:.4f}  "
                  + "  ".join(f"{k}={v:+.3f}" for k, v in d["contrib"].items()))
        d = decompose(agent, state, target, cands, cfg)
        print(f"  dead phrases ({len(d['dead_phrases'])}): {[p[:38] for p in d['dead_phrases']]}")
        print(f"  gate would block: {[p[:38] for p in d['blocked_by_gate']]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "override_category",
         int(sys.argv[2]) if len(sys.argv) > 2 else 7,
         int(sys.argv[3]) if len(sys.argv) > 3 else 3)
