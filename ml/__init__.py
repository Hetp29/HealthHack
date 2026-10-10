"""GlucoTwin machine-learning package (owner: Ruhaan).

Module map (the kickoff directive's naming; the master specification's canonical tree
lists the same responsibilities under slightly shorter names):

    config.py           frozen modelling constants -- horizons, grid, tolerances
    schema.py           canonical typed domain records, mirroring canonical_schema_v1.md
    devfixture.py       TEMPORARY labelled synthetic dev fixture (replaced by Het's layer)
    features.py         prediction-time feature extraction, strictly truncated at the meal
    targets.py          supervised target construction from post-meal CGM
    baselines.py        Model 0: glucose persistence
    train.py            CLI: fit Model 1 (generalized) + Model 2 (personalized), write artifact
    personalization.py  shrunk per-participant residual calibration
    inference.py        artifact -> 25-point forecast + derived summary metrics
    metrics.py          curve metrics and error metrics
    evaluate.py         CLI: leakage-safe chronological evaluation, write metrics.json
    explain.py          model-grounded feature attribution
"""

__all__ = ["config", "schema"]
