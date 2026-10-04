# Arena Intelligence evaluation report

Recorded matches: **48**. Configuration labels identify the backend and reasoning mode.

Forecast truth is the resource node with the most opposing bots within Manhattan distance 2 at cutoff +300 ticks. Positive ties choose the lowest node ID; zero occupancy is `none`.

Coverage includes every scheduled planning opportunity, including missing, rejected, superseded, expired, and unavailable forecasts. Accuracy and unnormalised multiclass Brier score use only covered forecasts; read them alongside coverage.

| Configuration | Matches | W / D / L | Mean score | Mean margin | Coverage | Accuracy | Brier | Known cost USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| multi/two | 24 | 21 / 0 / 3 | 288.3 | 125.1 | 144 / 144 | 0.701 | 0.509 | 5.2068 |
| single/single | 24 | 16 / 1 / 7 | 236.7 | 100.8 | 142 / 144 | 0.866 | 0.440 | 2.4922 |

## Coverage, latency, and uncertainty

### multi/two

- Forecast statuses: scored=144.
- Accuracy Wilson 95% interval: [0.622, 0.770] over 144 covered forecasts. This descriptive interval treats forecasts as independent; repeated conditions and within-match correlation limit that interpretation.
- Inference cycles: 144; mean / p95 latency: 11.673 / 14.054 seconds. Native tick timing is measured separately by `arena-sim --benchmark`.
- Evidence errors: 0; stale results: 0 (0.0% of returned assessments).
- Recorded failure fallbacks: 0 accepted, 0 rejected. Fallbacks are not forecasts or assessment rejection errors.
- Unresolved API cost reservations: 0. Reported costs are logged settled amounts; unresolved requests are not confirmed zero-cost calls.
- Measurement kind: live_api.

### single/single

- Forecast statuses: missing=2, scored=142.
- Accuracy Wilson 95% interval: [0.800, 0.913] over 142 covered forecasts. This descriptive interval treats forecasts as independent; repeated conditions and within-match correlation limit that interpretation.
- Inference cycles: 144; mean / p95 latency: 5.736 / 7.081 seconds. Native tick timing is measured separately by `arena-sim --benchmark`.
- Evidence errors: 0; stale results: 0 (0.0% of returned assessments).
- Recorded failure fallbacks: 2 accepted, 0 rejected. Fallbacks are not forecasts or assessment rejection errors.
- Unresolved API cost reservations: 0. Reported costs are logged settled amounts; unresolved requests are not confirmed zero-cost calls.
- Measurement kind: live_api.

## Conditions and records

| Configuration | Seed | Opponent policy | Mirrored | Result | Scores (team 0 : team 1) |
|---|---:|---|---|---|---:|
| single/single | 101 | nearest | False | win | 345 : 179 |
| multi/two | 101 | nearest | False | win | 339 : 179 |
| single/single | 101 | nearest | True | win | 343 : 179 |
| multi/two | 101 | nearest | True | win | 338 : 179 |
| single/single | 101 | holder | False | draw | 10 : 10 |
| multi/two | 101 | holder | False | win | 266 : 185 |
| single/single | 101 | holder | True | loss | 65 : 74 |
| multi/two | 101 | holder | True | win | 306 : 186 |
| single/single | 101 | switch | False | win | 266 : 169 |
| multi/two | 101 | switch | False | win | 262 : 169 |
| single/single | 101 | switch | True | win | 354 : 169 |
| multi/two | 101 | switch | True | win | 349 : 169 |
| single/single | 211 | nearest | False | win | 345 : 179 |
| multi/two | 211 | nearest | False | win | 341 : 179 |
| single/single | 211 | nearest | True | win | 344 : 179 |
| multi/two | 211 | nearest | True | win | 339 : 179 |
| single/single | 211 | holder | False | loss | 35 : 44 |
| multi/two | 211 | holder | False | loss | 40 : 55 |
| single/single | 211 | holder | True | loss | 35 : 45 |
| multi/two | 211 | holder | True | win | 308 : 187 |
| single/single | 211 | switch | False | win | 355 : 169 |
| multi/two | 211 | switch | False | win | 269 : 169 |
| single/single | 211 | switch | True | win | 354 : 169 |
| multi/two | 211 | switch | True | win | 351 : 169 |
| single/single | 307 | nearest | False | win | 344 : 179 |
| multi/two | 307 | nearest | False | win | 341 : 179 |
| single/single | 307 | nearest | True | win | 344 : 179 |
| multi/two | 307 | nearest | True | win | 338 : 179 |
| single/single | 307 | holder | False | loss | 35 : 45 |
| multi/two | 307 | holder | False | loss | 42 : 57 |
| single/single | 307 | holder | True | loss | 36 : 80 |
| multi/two | 307 | holder | True | loss | 63 : 88 |
| single/single | 307 | switch | False | win | 265 : 169 |
| multi/two | 307 | switch | False | win | 298 : 169 |
| single/single | 307 | switch | True | win | 273 : 169 |
| multi/two | 307 | switch | True | win | 351 : 169 |
| single/single | 401 | nearest | False | win | 345 : 179 |
| multi/two | 401 | nearest | False | win | 342 : 179 |
| single/single | 401 | nearest | True | win | 343 : 179 |
| multi/two | 401 | nearest | True | win | 339 : 179 |
| single/single | 401 | holder | False | loss | 82 : 96 |
| multi/two | 401 | holder | False | win | 339 : 186 |
| single/single | 401 | holder | True | loss | 55 : 83 |
| multi/two | 401 | holder | True | win | 306 : 188 |
| single/single | 401 | switch | False | win | 355 : 169 |
| multi/two | 401 | switch | False | win | 351 : 169 |
| single/single | 401 | switch | True | win | 353 : 169 |
| multi/two | 401 | switch | True | win | 301 : 169 |

Results describe the listed seeds and policies. Compare configurations on matching conditions and equal token/spending ceilings before making a reasoning-performance claim.
