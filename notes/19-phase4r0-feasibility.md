# Phase 4R0 — dense feasibility spike

Time-boxed. Result: **route 2 (deterministic local dense representation) is
feasible; route 1 (compact local sentence encoder) is not available.**

## What is actually installed

Probed rather than assumed:

```
numpy scipy sklearn torch transformers sentence_transformers
onnxruntime faiss gensim        -- all ModuleNotFoundError
~/.cache/huggingface  ~/.cache/torch  ~/.cache/sentence_transformers  -- absent
```

The interpreter is bare CPython 3.14 with the standard library. There is no
model cache to reuse, and `pip install` fails with
`externally-managed-environment`: adding numpy would require a virtualenv,
which changes how every leased experiment and every score lock runs. Against a
project whose stated premise is stdlib-only with a zero-network main path, and
a submission rule warning that "organizer policy may disable network access",
that is not a trade worth making for one phase.

**Route 1 is rejected on availability, not on preference.** No large model is
downloaded and no live API is introduced.

## Route 2: Reflective Random Indexing → sign signatures

Pure stdlib, deterministic from a fixed seed.

1. Each vocabulary term gets a sparse ternary **index vector** (6 non-zeros in
   `DIM` dimensions), seeded per term so it is reproducible.
2. **Pass 1** — document vectors as idf-weighted sums of their terms' index
   vectors.
3. **Reflection** — term *context* vectors as sums of the vectors of documents
   they appear in.
4. **Pass 2** — document vectors rebuilt from term **context** vectors, then
   thresholded at zero into a `DIM`-bit signature.
5. Query: encode the same way, rank by Hamming distance via `int.bit_count()`.

The reflection is the whole point. **A random projection of TF-IDF would
preserve lexical inner products and reproduce BM25's neighbours** — a lexical
hash wearing a vector costume, which is precisely what this phase was told not
to ship under the name "semantic". Reflection makes the representation
co-occurrence-based: two products sharing no terms can be close if their terms
co-occur elsewhere in the catalog.

## Measured, DIM = 64, 50k products

| | measured | gate |
|---|---|---|
| build, dense passes | ~137 s (one-off, lazy) | — |
| resident artefacts | **39.4 MB** | — |
| peak build memory | **55.4 MB** | < 160 MB ✓ |
| query p50 / p95 | **5.1 / 5.5 ms** | < 100 ms ✓ |
| signature store | 0.44 MB for 50 000 docs | — |
| network at scoring time | **none** | none ✓ |

The first implementation used lists of floats and peaked at **239 MB**, over
the gate. Backing the vectors with `array('f')` brought it to 55 MB.

## The decisive test: independent route, or lexical hash?

BM25 Top-100 versus dense Top-100 on 60 public queries:

| | |
|---|---|
| mean overlap | **0.020** |
| median overlap | 0.010 |
| max overlap | 0.100 |
| target found by BM25 | 20/60 |
| target found **only** by dense | **1/60** |

**It is a genuinely independent route.** A 2% overlap is not a renamed BM25
list, and it is the evidence required before calling this a distinct
candidate-generation path.

**It is independent but thin.** Dense surfaces a different 98% of the catalog
and that different set contains the target once in sixty. Independence is a
necessary condition for Pillar I, not a sufficient one for a score gain, and
R1's arms will measure whether it pays. This is recorded now so a weak result
later reads as predicted rather than disappointing.

## Chosen configuration

`DIM = 32` for the R1 implementation, halving both build time and memory
against the DIM=64 spike, with signatures still discriminating 50 000
documents at 32 bits. Built lazily and only when the browsing dense source is
enabled, so the default path pays nothing.
