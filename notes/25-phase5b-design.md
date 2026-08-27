# Phase 5B design — module split of `starter/agent.py`

**For review before any file is moved.** No code has been moved. Recorded at
`0ce1bfc`, 192 tests passing.

`starter/agent.py` is **2,466 lines**: `DEFAULTS` (221), catalog and indexes
(~600), evidence and parsing (~400), and `class Agent` (1,135 lines, 39
methods).

## The finding that decides the approach

Extracting dialogue and retrieval as plain function modules would create an
**import cycle**. The two groups call each other in both directions:

| direction | call |
|---|---|
| dialogue → retrieval | `_pick_attribute` → `_route_cfg` |
| dialogue → retrieval | `_release_abandoned_category` → `_category_on` |
| retrieval → dialogue | `_eligible_filters` → `_uncredible` |
| retrieval → dialogue | `_rerank` → `_uncredible` |
| retrieval → dialogue | `_rerank` → `_terms` |

Breaking that cycle means threading `self.cfg`, `self.cat` and session state
through new signatures — a large diff across every call site, on a refactor
whose acceptance gate is **bit-exactness**.

**Proposal: mixin classes, not function modules.** `DialogueMixin` and
`RetrievalMixin` resolve these five edges through `self` at runtime, so the two
modules import **nothing from each other**. The migration becomes pure code
movement: no call site changes, no signature changes, and bit-exactness holds
close to by construction rather than by luck.

The trade-off, stated plainly: mixins are a weaker boundary than modules with
explicit interfaces. They document the split without enforcing it — nothing
stops a future method reaching across. I judge that the right trade for a
refactor that must not move a single digit; a stricter boundary is a later
change with its own budget, and it should not be smuggled in under a
"cleanup" that is supposed to be behaviour-free.

## Modules, with measured sizes

| module | contents | lines |
|---|---|---|
| `starter/evidence.py` | `TOKEN_RE`/`WS_RE`/`_norm`, stopwords, `Outcome`, `SlotValue`, `ATTR_VOCAB`, `ANSWERABILITY`, `SLOT_RES`, `SINGLE_VALUED`, negation/restrictive/exceptive/hedge/hard/soft patterns, `open_world_evidence`, `hardness_of`, `relaxation_order`, `classify_reply`, `slot_of`, `is_override`, `abandoned_span`, `parse_message`, `_distinguishing` | ~430 |
| `starter/catalog.py` | `_Catalog`, `_CategoryIndex`, `_FacetCoverage`, `_FacetIndex`, `_DenseIndex`, `_catalog`, `clear_catalog_cache`, `_text`, `_flatten`, `_card4` | ~640 |
| `starter/dialogue.py` | `DialogueMixin`: 15 methods — state, override, suppression, starvation, question utility, composition | ~345 |
| `starter/retrieval.py` | `RetrievalMixin`: 16 methods — safe pool, three planes, dense source, RRF, funnel, rerank, rotation | ~520 |
| `starter/agent.py` | `DEFAULTS`, `PROFILES`, `_load_config`, `_resolve_catalog`, `class Agent(RetrievalMixin, DialogueMixin)` with `__init__`/`reset`/`respond`/`close`/context-manager, and the compatibility re-exports | ~480 |

## Dependency graph — acyclic

```
        evidence.py            (leaf: regex, Outcome, SlotValue, parsing)
         ^      ^
         |      |
   catalog.py   |              (imports ATTR_VOCAB, MATERIAL_RE, COLOR_RE)
      ^     ^   |
      |     |   |
retrieval.py  dialogue.py      (mixins; NO import between them)
      ^          ^
      |          |
        agent.py                (composes; owns DEFAULTS and the re-exports)
```

`evidence` imports nothing from the project. `catalog` needs the vocabularies,
which also lets `_FacetIndex.sources()` stop resolving them lazily — that hack
exists only because the index classes sit above the vocabularies in one file.
**I propose keeping it lazy anyway**, to hold the diff to pure movement.

## External contract — 20 names, all re-exported from `starter.agent`

Everything outside reaches the agent as `import starter.agent as A`. Measured
usage across `tests/`, `lab/`, `evaluator/`, `supplementary/`:

`Agent` (54) · `clear_catalog_cache` (35) · `SlotValue` (19) ·
`parse_message` (14) · `Outcome` (12) · `open_world_evidence` (8) ·
`DEFAULTS` (7) · `hardness_of` (5) · `classify_reply` (5) · `ATTR_VOCAB` (5) ·
`_catalog` (4) · `is_override` (3) · `relaxation_order` (2) ·
`abandoned_span` (2) · `_load_config` (2) · `slot_of` · `_norm` · `TOKEN_RE` ·
`SLOT_RES`

All keep working unchanged. `DEFAULTS` is referenced by **no** `Agent` method
(verified), so it stays in `agent.py` without creating a dependency.

## `score_default` / `showcase_dense` separation

`agent.py` gains an explicit, inert mapping:

```python
PROFILES = {
    "score_default":  {},                       # DEFAULTS as shipped; dense OFF
    "showcase_dense": {"dense_browsing": True,  # arm B, demo only
                       "dense_mixed": True,
                       "dense_fusion": "dense_only"},
}
```

Naming only — `DEFAULTS` is untouched, dense stays off, and no dense
algorithm, dimension, seed, RRF weight, category or profile weight is changed
in this phase.

## Migration order — five commits, suite green at each

1. **`evidence.py`** — the leaf. Nothing depends on it yet that does not
   already exist.
2. **`catalog.py`** — depends only on `evidence`.
3. **`dialogue.py`** — `DialogueMixin`.
4. **`retrieval.py`** — `RetrievalMixin`; the cycle edges become `self` calls.
5. **`agent.py`** — slim to composition + `PROFILES` + re-exports.

After each step: full suite including the frozen score lock. After step 5, and
only then, the leased bit-exact matrix.

## Verification

**Per commit:** 192 existing tests, score lock `0.932067 / 0.995 / 0.852556 /
2.060`, compat `0.928708`.

**New boundary tests:** `evidence` importable with no catalog present ·
`catalog` builds facets lazily and independently of `Agent` · route dispatch
selects the right plane per route · starvation bypass fires only on starved
turns · funnel quotas and determinism · override/slot erasure · the 20
re-exports all resolve from `starter.agent`.

**Final leased matrix**, bit-exact against `0ce1bfc`: clean + four official
slices, `vague_start`, `uncooperative`, `override_genuine`,
`override_category`, `contradiction`, `supplementary_dev`, compat anchor.
Plus cold-start and warm latency re-measured — **any regression stops the
phase and is attributed, not averaged away.**

## Risks

1. **Mixins document rather than enforce** the boundary. Accepted above.
2. **Import-time ordering.** `_FacetIndex.sources()` and `ATTR_VOCAB` are
   order-sensitive today; the split changes module init order. Mitigated by
   keeping `sources()` lazy and by step 1 making `evidence` a true leaf.
3. **Re-export drift.** A name silently not re-exported fails only in whatever
   imports it. Mitigated by an explicit test asserting all 20 resolve.
4. **`_terms` is unassigned** in the grouping above (3 lines, used by both
   mixins). Proposal: it belongs to `evidence` as a free function, with a thin
   `Agent._terms` retained for compatibility.

## Not in this phase

No change to dense, RRF, category, profile weights, retrieval ranking, slot
semantics, routes, question policy or supplementary data. No new dependency.
Phase 6A (`ContextSnapshot` / `ContextPolicy`) is designed only after 5B lands.
