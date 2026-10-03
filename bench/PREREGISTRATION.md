# Sieve v1: pre-registration

Committed before the GPU phase (training plan H7, §13). `bench/report_final.py` refuses to
render `holdout.json` unless this file hashes to `bench/PREREGISTRATION.sha256`, and it writes
that hash into the report. Changing any line below after the hash is committed means a new
version, a new hash and a new `test_opened.log` line (PRD §11.6).

## 1. Headline metrics

Every number is computed per seed at that seed's own thresholds, then reported as mean ± std
(ddof = 1) with the per-seed values beside it (§6.6). Every interval is a server-cluster
bootstrap; MCPTox uses template × server clusters; undefined replicates are dropped and counted.

- Poisoned recall at `T_low` on **holdout-synthetic**, with its cluster-bootstrap 95% interval.
- Stage-1 FPR at `T_low` on the **real** test-ID rows: on the triage slice (YARA-flagged), on all
  real negatives, per source and prevalence-weighted (`wild_fp` natural share 3.5%).
- On test-ID, "2-class macro-F1 (benign, imperative)"; never a 3-class figure.
- AUROC and AUPRC, poisoned vs rest.
- MCPTox (`real_poisoned`, M13) recall at `T_low`, Stage 1 and pipeline, per template and risk
  class; the Docker and MCPTox-clean FPR at `T_low`. Reported, never gated.
- Routing rate (M3): the share of rows in [`T_low`, `T_high`). The LLM judge is deferred to v1.1
  (decision D8): pipeline figures are Stage-1 only.

## 2. The one primary comparison

**Ablation row 4 vs row 1: the FP reduction vs YARA on the triage slice** (M2), as a relative
reduction with a cluster-bootstrap interval and a negatives-only McNemar test (cluster-adjusted
beside the exact one), reported only alongside pipeline recall at equal-or-higher recall. Every
other comparison is secondary and carries a **Holm** correction across the family of
comparisons reported.

## 3. Release gate (§6.5, PRD §11.4)

Poisoned recall on the holdout-synthetic positives **≥ 0.95**, with a server-cluster-bootstrap
lower bound **≥ 0.90**, at the published model's `T_low`. `T_low` is the highest threshold whose
pooled-OOF recall lower bound (2.5% quantile) is ≥ 0.98; `T_high` is the lowest threshold with
poisoned precision ≥ 0.95 and ≥ 5 predicted positives. Both come from the artefact, never from
code. MCPTox recall is reported beside the gate and never gates.

## 4. Selection rule

`sieve/model/select.py` `RULE`, on pooled out-of-fold predictions, three seeds per architecture:
the architecture with the higher mean pooled OOF macro-F1 across seeds wins; ties inside 0.005
break toward the lower p95 latency; a remaining tie breaks toward DeBERTa-v3. There is no
override flag. No sealed row informs any selection, threshold or label.

## 5. The published artefact

The **median-seed `FOLD=all` model** of the selected architecture (the seed whose pooled-OOF
macro-F1 is the median of the three), trained on the whole dev set for the median best epoch of
its five folds. It is the `--headline` of `bench/report_final.py`.

## 6. Cluster keys

Server name everywhere (the mutation host's server for a synthetic row); `template|server` for
MCPTox; the hashed server name for MCPTox clean, so a clean tool and its poisoned twins share a
cluster.

## 7. Gates that stop the run

- **Probe gate** (`make gate`, E4): source leakage per stratum (imperative, benign), surface,
  length (synthetic leak, and the real class gap widened by at most 0.02) and transfer, each
  AUROC ≤ 0.70 on dev rows, pooled OOF, grouped by server. One tightening pass, then stop and ask.
- **dev-50 gate** (F3): Cohen's κ between the owner's blind labels on 50 seeded real dev rows and
  the ensemble ≥ 0.70, else stop and ask. Those 50 human labels replace the ensemble's.

## 8. Report-only

**Test-ID labels are report-only.** The owner's blind labels on the 104 real holdout rows are
gold for the test-ID audit and never change a training label, a threshold or a model choice.
val-OOD is report-only as well.
