"""Build the A1 feature cache over the OPERATIVE sup-train split, and gate it.

WHAT THIS REFUSES TO DO. It refuses to run on any split that is not the one
`notes/40` registered. The corpus contains 1,000 sessions and the notes contain
two splits -- the operative stratified 800/200 and a SUPERSEDED global-hash
806/194 -- so "800 sessions" is not a thing to check afterwards in a report. It
is asserted here, at startup, against pre-registered per-scenario counts and
two pre-registered id hashes, before a single session runs.

WHAT IT CAPTURES. At each `_rerank` call, a deep-copied snapshot of the exact
input: the candidate list in retrieval order with its BM25 scores, the whole
session state, and a field-level fingerprint of every SlotValue. Not a
reference -- the live state keeps mutating for the rest of the session -- and
not a reconstruction from the trace afterwards. The features come from the same
call through `_rerank`'s own `collect` hook, so the cache and the live ordering
are one execution of one kernel.

WHAT IT PROVES BEFORE TRIAL 0. `lab/a1cache.replay_gate` re-derives every
turn's full candidate order twice, from the snapshot and from the cached
features, and requires A0's live order both times; then requires cached MRR to
equal A0's own MRR exactly. The manifest carries the cache hash, the split
hash, the agent commit and sha, and the catalog sha, so the number the search
optimises can be traced to the inputs that produced it.

    python3 -m lab.a1driver --limit 20 --out /tmp/probe   # smoke, unleased
    python3 -m lab.a1driver                               # the real build
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import starter.agent as A
from evaluator import local_evaluator as E
from lab import a1cache as CACHE
from lab import lease as L
from lab import record as R
from lab import split as SPLIT

CATALOG = Path("data/catalog.jsonl")
CACHE_NAME = "a1cache.jsonl"
MANIFEST_NAME = "a1cache.meta.json"
LAB = Path("lab")
# The build ledger, separate from lab/results.jsonl: a cache manifest is not a
# score row, and mixing the two would put a row with no score into every table
# that reads the score ledger.
BUILD_LOG = "lab/a1builds.jsonl"


def run_session(agent, sample: str | dict, capture: CACHE.Capture,
                catalog_ids, categories, products) -> dict:
    """One session, driven exactly as `evaluator.local_evaluator.evaluate` does.

    The loop is replicated rather than called because the cache needs a hook at
    each turn and `evaluate()` offers none. Every customer-side decision -- the
    opening message, the reply, the override turn, the boundary refusal, the
    stop-at-first-hit -- comes from the evaluator's own functions, so there is
    no second definition of how a customer behaves.

    The one deliberate difference: `session_id` is derived from the sample id
    rather than a uuid4, so a rebuild of this cache is reproducible. Nothing in
    the agent reads it beyond dictionary identity.
    """
    sample_id = str(sample["sample_id"])
    session_id = f"suptrain_{sample_id}"
    agent.reset(session_id, sample["user_profile"])
    target = str(sample["ground_truth"]["parent_asin"])
    card, behavior = E.materialize_hidden_fields(sample, products)
    effective = {**sample, "intent_card": card, "behavior": behavior}
    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    user_message = E.initial_message(
        effective, E.coarse_category(categories.get(target, [])), disclosed)
    hit_turn: int | None = None
    best_rank: int | None = None
    turns: list[int] = []
    rotated_turns: list[int] = []
    for turn in range(1, E.MAX_TURNS + 1):
        capture.key = (sample_id, turn)
        try:
            response = agent.respond(session_id, user_message, turn, E.TOP_K)
        finally:
            capture.key = None
        if not isinstance(response, dict) or not isinstance(response.get("message"), str):
            response = {"message": "", "ask_attribute": None, "recommendations": []}
        turns.append(turn)
        ranked = E.normalize_recommendations(response.get("recommendations"), catalog_ids)
        # `_rotate` runs AFTER `_rerank`, so the emitted Top-10 and the order
        # the cache records are the same list only while no turn asks for
        # alternatives. Measured rather than assumed: this is the whole reason
        # the cached objective and the evaluator's MRR can differ.
        snap = capture.snapshots.get((sample_id, turn))
        if snap is not None and ranked != list(snap.live_order)[:E.TOP_K]:
            rotated_turns.append(turn)
        if override_applied and target in ranked:
            best_rank = ranked.index(target) + 1
            hit_turn = turn
            break
        if turn == E.MAX_TURNS:
            break
        override = effective.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            user_message = str(override.get("message",
                                            "Actually, please ignore my earlier preference."))
        else:
            user_message, boundary_used = E.customer_reply(
                effective, response.get("ask_attribute"), disclosed, boundary_used)
    return {"sample_id": sample_id, "scenario": str(sample["scenario_type"]),
            "target": target, "turns": turns, "hit_turn": hit_turn,
            "best_rank": best_rank, "rotated_turns": rotated_turns,
            "reciprocal_rank": 0.0 if best_rank is None else 1.0 / best_rank}


def build(rows, catalog: Path = CATALOG, config: dict | None = None,
          progress_every: int = 50) -> dict:
    """Run every row, capture every turn, and assemble the cache sessions."""
    catalog_ids, categories, products = E.catalog_index(catalog)
    agent = A.Agent(str(catalog), config=dict(config or {}))
    capture = CACHE.Capture()
    outcomes: list[dict] = []
    started = time.time()
    with CACHE.capturing(agent, capture):
        for i, sample in enumerate(rows, 1):
            outcomes.append(run_session(agent, sample, capture,
                                        catalog_ids, categories, products))
            # The agent keeps every session it has ever seen in `_sessions`,
            # and 800 of them with full trace logs is memory this build has no
            # use for: the snapshots are the record.
            agent._sessions.clear()
            if progress_every and i % progress_every == 0:
                print(f"  {i}/{len(rows)} sessions, {time.time() - started:.0f}s",
                      flush=True)
    sessions: list[dict] = []
    for outcome in outcomes:
        turn_rows = []
        for turn in outcome["turns"]:
            key = (outcome["sample_id"], turn)
            row = capture.rows.get(key)
            if row is None:
                # A turn that never reached `_rerank` -- an empty candidate
                # pool, or rerank switched off. It is not a cached turn, and
                # inventing an empty one would put a session in the objective
                # with turns it never had.
                continue
            turn_rows.append({**row, "target": outcome["target"]})
        sessions.append({"sample_id": outcome["sample_id"],
                         "scenario": outcome["scenario"], "turns": turn_rows})
    return {"sessions": sessions, "capture": capture, "agent": agent,
            "outcomes": outcomes, "seconds": round(time.time() - started, 1)}


def evaluator_mrr(outcomes) -> float:
    """The evaluator's own MRR over these sessions -- POST-rotate, from the
    emitted recommendations. A diagnostic, not the gate: the cache records
    `_rerank`'s order and `_rotate` runs after it, so the two coincide only
    while no session asks for alternatives. Reported so that stops being an
    assumption."""
    return statistics.fmean([o["reciprocal_rank"] for o in outcomes]) if outcomes else 0.0


def rotation_diagnostic(outcomes, sessions, snapshots) -> dict:
    """How far `_rotate` moved the emitted order away from the cached one.

    The cached objective is defined over `_rerank`'s order (notes/40 section 6,
    "cached evaluator semantics"); the evaluator scores the order the customer
    was shown, which `_rotate` may have refreshed. The gap between the two is
    NOT a defect and NOT an error bar -- it is the size of the off-policy
    approximation the pre-registration accepted, and it belongs in the record
    with a number rather than a shrug.
    """
    rotated = [o for o in outcomes if o["rotated_turns"]]
    by_id = {s["sample_id"]: s for s in sessions}
    moved: list[dict] = []
    for outcome in outcomes:
        session = by_id.get(outcome["sample_id"])
        if session is None:
            continue
        cached_rr = 0.0
        for turn in session["turns"]:
            snap = snapshots.get((session["sample_id"], turn["turn"]))
            if snap is None:
                continue
            order = list(snap.live_order)
            rank = order.index(turn["target"]) + 1 if turn["target"] in order else None
            if rank is not None and rank <= E.TOP_K:
                cached_rr = 1.0 / rank
                break
        if cached_rr != outcome["reciprocal_rank"]:
            moved.append({"sample_id": outcome["sample_id"],
                          "scenario": outcome["scenario"],
                          "cached_rr": cached_rr,
                          "evaluator_rr": outcome["reciprocal_rank"]})
    by_scenario: dict[str, int] = {}
    for row in moved:
        by_scenario[row["scenario"]] = by_scenario.get(row["scenario"], 0) + 1
    return {
        "rotated_sessions": len(rotated),
        "rotated_turns": sum(len(o["rotated_turns"]) for o in outcomes),
        "sessions_whose_rr_moved": len(moved),
        "rr_moved_by_scenario": dict(sorted(by_scenario.items())),
        "rr_moved_examples": moved[:5],
    }


def gate(built, split: SPLIT.Split, cache_path: Path) -> dict:
    """Write the cache, run the replay gate, and assemble the manifest."""
    sessions = built["sessions"]
    written = CACHE.write(sessions, cache_path)
    if not written["ok"]:
        return {"ok": False, "stage": "schema", **written}
    result = CACHE.replay_gate(sessions, built["agent"], built["capture"].snapshots)
    fingerprints = R.git_state(dataset=str(SPLIT.DEV))
    manifest = {
        "phase": "7A-R1",
        "arm": "A1",
        "ok": bool(result["ok"]) and result["delta_mrr"] == 0.0
              and not built["capture"].duplicate_keys,
        "checked_sessions": result["checked_sessions"],
        "checked_turns": result["checked_turns"],
        "full_order_mismatches": result["mismatches"],
        "first_mismatch": result["first_mismatch"],
        "cached_default_mrr": result["cached_default_mrr"],
        "live_a0_mrr": result["live_a0_mrr"],
        "delta_mrr": result["delta_mrr"],
        "evaluator_mrr_diagnostic": evaluator_mrr(built["outcomes"]),
        "rotation_diagnostic": rotation_diagnostic(
            built["outcomes"], sessions, built["capture"].snapshots),
        "cache_sha256": written["sha256"],
        "cache_path": str(cache_path),
        "cache_sessions": written["sessions"],
        "cache_turns": written["turns"],
        "split": "sup-train",
        "split_train_hash": split.train_hash,
        "split_val_hash": split.val_hash,
        "split_train_n": len(split.train),
        "split_val_n": len(split.val),
        "agent_commit": fingerprints["agent_commit"],
        "agent_sha256": fingerprints["agent_sha256"],
        "agent_in_worktree": fingerprints["agent_in_worktree"],
        "code_dirty": fingerprints["code_dirty"],
        "catalog_sha256": fingerprints["catalog_sha256"],
        "dataset_sha256": fingerprints["dataset_sha256"],
        "duplicate_rerank_keys": [list(k) for k in built["capture"].duplicate_keys],
        "orphan_rerank_calls": built["capture"].orphan_calls,
        "build_seconds": built["seconds"],
    }
    return manifest


def report(manifest: dict) -> None:
    print("\n=== A1 default replay gate ===")
    for field in ("checked_sessions", "checked_turns", "full_order_mismatches",
                  "cached_default_mrr", "live_a0_mrr", "delta_mrr",
                  "evaluator_mrr_diagnostic", "cache_sha256", "split_train_hash",
                  "agent_commit", "agent_sha256", "catalog_sha256"):
        print(f"  {field:<26} {manifest.get(field)}")
    rot = manifest.get("rotation_diagnostic") or {}
    if rot:
        print(f"  {'rotated turns':<26} {rot.get('rotated_turns')}"
              f"  (sessions {rot.get('rotated_sessions')})")
        print(f"  {'sessions whose RR moved':<26} {rot.get('sessions_whose_rr_moved')}"
              f"  {rot.get('rr_moved_by_scenario')}")
    if manifest.get("first_mismatch"):
        print(f"  FIRST MISMATCH             {manifest['first_mismatch']}")
    print(f"  VERDICT                    {'PASS' if manifest['ok'] else 'STOP'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lab.a1driver")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke mode: first N sup-train rows, NEVER a build")
    ap.add_argument("--out", default="",
                    help="directory for the cache and manifest (default lab/)")
    ap.add_argument("--leased", action="store_true",
                    help="acquire the experiment lease and run the build inside "
                         "an isolated worktree, in its own interpreter")
    args = ap.parse_args(argv)
    if args.leased:
        return leased(args.limit)

    # HARD ASSERTIONS FIRST. Row count, per-scenario counts, both id hashes,
    # overlap, union, corpus namespace, and the superseded 806/194 tripwire.
    # A failure here raises SplitError and nothing runs.
    split, rows = SPLIT.operative()
    by_id = {str(r["sample_id"]): r for r in rows}
    train = [by_id[i] for i in split.train]
    print(f"sup-train {len(train)} rows, hash {split.train_hash}")
    print(f"sup-val   {len(split.val)} rows, hash {split.val_hash}")

    out = Path(args.out) if args.out else LAB
    cache_path, manifest_path = out / CACHE_NAME, out / MANIFEST_NAME
    if args.limit:
        train = train[: args.limit]
        print(f"SMOKE MODE: {len(train)} rows. This is not a cache build.")
    built = build(train)
    manifest = gate(built, split, cache_path)
    manifest["smoke"] = bool(args.limit)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    # Under a lease the manifest is JOURNALLED, not written to the ledger:
    # provenance is not established until the lease has verified, after the
    # run, that nothing moved. Without a lease nothing is journalled, which is
    # what makes a smoke run visibly not a build.
    journal = L.journal_path()
    if journal is not None:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema_version": 2, "tag": "p7a-r1-a1cache",
                                 "scenario": "supplementary_dev",
                                 "config": {}, "seeds": [],
                                 **manifest}) + "\n")
    report(manifest)
    return 0 if manifest["ok"] else 1


def leased(limit: int = 0) -> int:
    """Build the cache under the experiment lease, in an isolated worktree.

    The cache and its manifest are written to the ORIGIN's lab/, not to the
    worktree: the worktree is deleted when the lease exits, and a 345 MB
    artefact that vanishes with its own provenance is not an artefact.
    """
    origin = Path.cwd().resolve()
    argv = ["--out", str(origin / LAB)]
    if limit:
        argv += ["--limit", str(int(limit))]
    script = ("import sys\n"
              "from lab import a1driver as D\n"
              f"sys.exit(D.main({argv!r}))\n")
    with L.lease("p7a-r1-a1cache", log=BUILD_LOG) as held:
        held.run(script, expected_cells=1)
    print(f"lease {held.verdict} {held.broke}")
    return 0 if held.verdict == "valid" else 1


if __name__ == "__main__":
    sys.exit(main())
