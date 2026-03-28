# vaner.ai Evaluation Framework

## A/B Test: Context Injection vs Baseline

`run_ab_test.py` runs 20 developer queries in two conditions and scores with Claude as judge.

### Run it

```bash
cd ~/repos/Vaner
apps/supervisor/.venv/bin/python eval/run_ab_test.py 2>&1 | tee eval/run_ab_test.log
```

### Results history

| Date | Model | WITH wins | Score WITH | Score WITHOUT | Delta | Verdict |
|---|---|---|---|---|---|---|
| 2026-03-28 | devstral:14B (RTX 5090) | 18/20 (90%) | 4.05 | 1.90 | +2.15 | **GO ✅** |

### Go/No-Go thresholds

| Metric | GO | CONDITIONAL | NO-GO |
|---|---|---|---|
| WITH injection win rate | ≥40% | 25–40% | <25% |
| Avg score delta | positive | ±0 | negative |
| Direct answer lift | any positive | — | negative |

### What the test measures

- **Win rate**: which condition produces better responses per Claude judge
- **Quality score (1-5)**: correctness + specificity + completeness
- **Direct answer rate**: did the model answer without needing tool calls?
- **Tool call reduction**: how many round-trips did injection eliminate?
- **Latency**: end-to-end response time per condition

### Interpreting results

The 2026-03-28 baseline (devstral 14B) showed:
- Without injection: model cannot answer codebase questions directly (0/20 direct answers, avg score 1.9)
- With injection: model answers directly 80% of the time (avg score 4.05)
- Core thesis **validated** — predictive context preparation is the critical enabler

Expected improvements when DGX Spark comes online:
- Better artifact quality → higher base scores
- Better model reasoning → fewer losses on complex queries
- Larger context window → richer artifact injection
