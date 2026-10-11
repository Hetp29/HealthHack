# Numerical tolerances for the fs-1.3 freeze retest

**Declared BEFORE any comparison was run.** Recorded here so the sequence is
auditable: tolerances are not adjusted after seeing results. Committed in the same
change as the results they govern.

| ID | Comparison | Tolerance | Justification |
|---|---|---|---|
| **T1** | Repeated inference on an identical request, same process | **0.0 — exact bitwise equality** | Same code path, same inputs, no RNG at inference. Any difference is a defect. |
| **T2** | Repeated inference across separate processes | **1e-9 mg/dL** | Allows only float formatting/serialisation noise. |
| **T3** | Episode, participant, horizon and exclusion COUNTS vs saved records | **0 — exact integer equality** | Counts are discrete; a difference means a different cohort. |
| **T4** | Reference-set MAE / RMSE / peak MAE vs `metrics.json`, recorded to 2 dp | **0.015 mg/dL** | 0.005 rounding at 2 dp + 0.010 for CPU BLAS reduction-order variation. |
| **T5** | Reference-set time-to-peak MAE vs saved, recorded to 2 dp | **0.015 min** | Same basis as T4. |
| **T6** | Reference-set iAUC MAE vs saved, recorded to 1 dp | **0.15 mg/dL·min** | 0.05 rounding at 1 dp + 0.10 on a ~1478 magnitude. |
| **T7** | Chosen Ridge alpha per horizon vs saved | **exact equality** | A discrete selected hyperparameter. |
| **T8** | Development-regime MAE vs a prior report **using the same fold construction** | **0.001 mg/dL** | Deterministic given identical folds; allows BLAS reduction order only. |
| **T9** | Trajectory/summary internal consistency inside one API response | **0.05 mg/dL** | The contract serialises glucose to 1 dp, so 0.05 is the representable floor. |
| **T10** | Latency vs the recorded fs-1.3 baseline (359 ms median) | **advisory, no gate** | Latency depends on machine load; a regression is investigated and reported, never used to fail the freeze, and never "fixed" by changing predictions. |

## Comparisons that are NOT made, and why

* **Across different fold constructions.** Earlier missions used
  `rolling_folds(n_folds=3)`, which yields **2** validation folds, while some
  reports quoted a 3-fold number. Absolute MAEs from different episode sets are
  **not interchangeable** and are not compared. Only runs sharing a fold
  construction are compared under T8.
* **`metrics.json` headline 20.55 against the shipped artifact's own predictions.**
  These are two different estimators — see the Phase 3 finding. The shipped
  artifact is fitted on 1,171 episodes with alpha 0.1/10/100/100; the recorded
  20.55 came from the evaluation harness's fit on the 938-episode training fold
  with alpha 100/100/100/100. Comparing them under T4 would be comparing two
  models, so T4 is applied only to the harness reproduction, and the shipped
  artifact's reference-set performance is reported as a separate, newly measured
  quantity.
