#!/bin/bash
# Seven fresh processes per candidate. Identical workload, offline, no labels.
for m in ms-marco-TinyBERT-L2-v2 ms-marco-MiniLM-L2-v2 ms-marco-MiniLM-L6-v2; do
  for rep in 0 1 2 3 4 5 6; do
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
      ./.venv/bin/python r0_bench.py --model-dir "artifacts/$m" --rep "$rep" \
      >> r0_results.jsonl 2>>r0_errors.log || echo "{\"event\":\"abort\",\"model\":\"$m\",\"rep\":$rep}" >> r0_results.jsonl
  done
  echo "done: $m"
done
