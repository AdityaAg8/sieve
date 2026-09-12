"""Stage-1 model pipeline: data, baselines, training, calibration, export, inference.

Every number this package produces comes from pooled out-of-fold predictions over
`split == "dev"` records. Nothing here reads a `holdout` or `eval_only` row (PRD §11.6).
"""
