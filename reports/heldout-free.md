# Arena Intelligence evaluation report

Recorded matches: **48**. Configuration labels identify the backend and reasoning mode.

Forecast truth is the resource node with the most opposing bots within Manhattan distance 2 at cutoff +300 ticks. Positive ties choose the lowest node ID; zero occupancy is `none`.

Coverage includes every scheduled planning opportunity, including missing, rejected, superseded, expired, and unavailable forecasts. Accuracy and unnormalised multiclass Brier score use only covered forecasts; read them alongside coverage.

| Configuration | Matches | W / D / L | Mean score | Mean margin | Coverage | Accuracy | Brier | Known cost USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frequency/scripted | 24 | 4 / 8 / 12 | 179.0 | -67.5 | 144 / 144 | 0.444 | 0.860 | 0.0000 |
| mock/scripted | 24 | 20 / 0 / 4 | 237.9 | 118.3 | 144 / 144 | 0.889 | 0.493 | 0.0000 |

## Coverage, latency, and uncertainty

### frequency/scripted

- Forecast statuses: scored=144.
- Accuracy Wilson 95% interval: [0.366, 0.526] over 144 covered forecasts. This descriptive interval treats forecasts as independent; repeated conditions and within-match correlation limit that interpretation.
- Inference cycles: 144; mean / p95 latency: 0.000 / 0.000 seconds. Native tick timing is measured separately by `arena-sim --benchmark`.
- Evidence errors: 0; stale results: 0 (0.0% of returned assessments).
- Recorded failure fallbacks: 0 accepted, 0 rejected. Fallbacks are not forecasts or assessment rejection errors.
- Unresolved API cost reservations: 0. Reported costs are logged settled amounts; unresolved requests are not confirmed zero-cost calls.
- Measurement kind: offline.

### mock/scripted

- Forecast statuses: scored=144.
- Accuracy Wilson 95% interval: [0.827, 0.930] over 144 covered forecasts. This descriptive interval treats forecasts as independent; repeated conditions and within-match correlation limit that interpretation.
- Inference cycles: 144; mean / p95 latency: 0.001 / 0.001 seconds. Native tick timing is measured separately by `arena-sim --benchmark`.
- Evidence errors: 0; stale results: 0 (0.0% of returned assessments).
- Recorded failure fallbacks: 0 accepted, 0 rejected. Fallbacks are not forecasts or assessment rejection errors.
- Unresolved API cost reservations: 0. Reported costs are logged settled amounts; unresolved requests are not confirmed zero-cost calls.
- Measurement kind: offline.

Offline scripted/mock/replay runs verify integration and scoring. They do not establish an improvement from LLM reasoning or adversarial collaboration.

## Conditions and records

| Configuration | Seed | Opponent policy | Mirrored | Result | Scores (team 0 : team 1) |
|---|---:|---|---|---|---:|
| frequency/scripted | 101 | nearest | False | draw | 179 : 179 |
| mock/scripted | 101 | nearest | False | win | 347 : 179 |
| frequency/scripted | 101 | nearest | True | draw | 179 : 179 |
| mock/scripted | 101 | nearest | True | win | 346 : 179 |
| frequency/scripted | 101 | holder | False | loss | 179 : 346 |
| mock/scripted | 101 | holder | False | win | 11 : 9 |
| frequency/scripted | 101 | holder | True | loss | 179 : 347 |
| mock/scripted | 101 | holder | True | loss | 9 : 11 |
| frequency/scripted | 101 | switch | False | loss | 179 : 259 |
| mock/scripted | 101 | switch | False | win | 357 : 169 |
| frequency/scripted | 101 | switch | True | win | 179 : 169 |
| mock/scripted | 101 | switch | True | win | 356 : 169 |
| frequency/scripted | 211 | nearest | False | draw | 179 : 179 |
| mock/scripted | 211 | nearest | False | win | 347 : 179 |
| frequency/scripted | 211 | nearest | True | draw | 179 : 179 |
| mock/scripted | 211 | nearest | True | win | 346 : 179 |
| frequency/scripted | 211 | holder | False | loss | 179 : 346 |
| mock/scripted | 211 | holder | False | win | 12 : 10 |
| frequency/scripted | 211 | holder | True | loss | 179 : 347 |
| mock/scripted | 211 | holder | True | loss | 10 : 12 |
| frequency/scripted | 211 | switch | False | loss | 179 : 259 |
| mock/scripted | 211 | switch | False | win | 357 : 169 |
| frequency/scripted | 211 | switch | True | win | 179 : 169 |
| mock/scripted | 211 | switch | True | win | 356 : 169 |
| frequency/scripted | 307 | nearest | False | draw | 179 : 179 |
| mock/scripted | 307 | nearest | False | win | 347 : 179 |
| frequency/scripted | 307 | nearest | True | draw | 179 : 179 |
| mock/scripted | 307 | nearest | True | win | 346 : 179 |
| frequency/scripted | 307 | holder | False | loss | 179 : 346 |
| mock/scripted | 307 | holder | False | win | 12 : 11 |
| frequency/scripted | 307 | holder | True | loss | 179 : 347 |
| mock/scripted | 307 | holder | True | loss | 11 : 12 |
| frequency/scripted | 307 | switch | False | loss | 179 : 259 |
| mock/scripted | 307 | switch | False | win | 357 : 169 |
| frequency/scripted | 307 | switch | True | win | 179 : 169 |
| mock/scripted | 307 | switch | True | win | 356 : 169 |
| frequency/scripted | 401 | nearest | False | draw | 179 : 179 |
| mock/scripted | 401 | nearest | False | win | 347 : 179 |
| frequency/scripted | 401 | nearest | True | draw | 179 : 179 |
| mock/scripted | 401 | nearest | True | win | 346 : 179 |
| frequency/scripted | 401 | holder | False | loss | 179 : 346 |
| mock/scripted | 401 | holder | False | win | 11 : 9 |
| frequency/scripted | 401 | holder | True | loss | 179 : 347 |
| mock/scripted | 401 | holder | True | loss | 9 : 11 |
| frequency/scripted | 401 | switch | False | loss | 179 : 259 |
| mock/scripted | 401 | switch | False | win | 357 : 169 |
| frequency/scripted | 401 | switch | True | win | 179 : 169 |
| mock/scripted | 401 | switch | True | win | 356 : 169 |

Results describe the listed seeds and policies. Compare configurations on matching conditions and equal token/spending ceilings before making a reasoning-performance claim.
