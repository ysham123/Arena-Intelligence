# Matched observation forecast benchmark

Forecasts are conditional on their proposed assignments, which are not applied in this matched-input benchmark. The three current scripted opponent policies and overlapping bots make opponent trajectories independent of friendly assignments; a native regression test checks this invariance. Labels use the recorded opponent trajectories. Score and W/D/L belong to the separate closed-loop study.

All configurations receive identical permitted histories at the six planning cutoffs. Coverage includes failed, late, invalid, and missing outputs. Accuracy and multiclass Brier use covered forecasts; read them alongside coverage.

| Configuration | Covered / opportunities | Accuracy | Brier | Mean latency s | Known cost USD |
|---|---:|---:|---:|---:|---:|
| frequency | 144 / 144 | 0.5139 | 0.5387 | 0.0008 | 0.0000 |
| multi | 144 / 144 | 0.8333 | 0.4300 | 11.5867 | 5.3450 |
| single | 144 / 144 | 0.8264 | 0.4381 | 5.5974 | 2.4779 |

Native acceptance and score/WDL are not measured here. Free scripted runs exercise this benchmark and do not establish an LLM reasoning gain. Latencies include the recorded concurrent load; repeated forecasts within a reference are correlated.
