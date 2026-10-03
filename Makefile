ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/usr/bin/bash.exe
endif

.PHONY: help setup lint test e2e schemas up down serve baseline train train-all best-epoch calibrate bench eval-final readme corpus corpus-stats plan-evidence label-dev-audit label-test-id model-dir sweep gpu-bench probes tfidf-ood gate

.DEFAULT_GOAL := help

help: ## list every target with its one-line purpose
	@grep -hE '^[a-z][a-z0-9_-]*:.*## ' $(MAKEFILE_LIST) | sed -E 's/^([a-z0-9_-]+):[^#]*## (.*)$$/  \1	\2/' | sort
	@echo && echo 'Human steps (GPU, keys, deploys): docs/RUNBOOK.md'

# `train`/`train-all` variables: PROFILE names a `bench/train.yaml` profile; SEED and FOLD are
# the CLI's own `--seed`/`--fold`. MODEL_OUT follows the "models/<family>-s<seed>/fold<fold>"
# layout `bench/ablations.yaml` and `sieve/model/select.py` read ("models/<family>-s*").
PROFILE ?= deberta
SEED ?= 0
FOLD ?= 0

# FAMILY is the profile's artefact family (training plan C1): every speed variant of one recipe
# (`deberta`, `deberta-nockpt`) writes the same directory, the one the ablation rows name. Read
# from `bench/train.yaml` once, on first use, so `make help` never starts Python.
FAMILY ?= $(eval FAMILY := $(shell uv run python -m sieve.model.family $(PROFILE)))$(FAMILY)

# `bench` variables: MODEL_DIR is the calibrated (`--fold all`) artefact `bench.analysis.errors`
# reads thresholds from; FOLD_DIRS are the five per-fold artefacts its `--dirs` pools OOF logits
# from. BENCH_CORPUS matches the CLIs' own `--corpus` default.
MODEL_DIR ?= models/$(FAMILY)-s$(SEED)

# `--fold all` is not a sixth fold: it is the whole-dev artefact everything downstream reads
# thresholds and weights from, so it belongs at MODEL_DIR itself -- the path `make bench` passes
# as `--model-dir`, and the one `models/<family>-s*` names. A literal `fold$(FOLD)` would
# have put it in `models/<family>-s<seed>/foldall`, where nothing looks for it.
MODEL_OUT ?= $(if $(filter all,$(FOLD)),$(MODEL_DIR),$(MODEL_DIR)/fold$(FOLD))

# `--fold all` also refuses to run without an explicit epoch count (`TrainConfig`: there is no
# early-stopping slice to find one with, so it has to be the count CV selected). EPOCHS is empty
# for every other fold, where the profile's own value plus early stopping is correct.
EPOCHS ?=
TRAIN_EPOCHS = $(if $(EPOCHS),--epochs $(EPOCHS))
FOLD_DIRS ?= $(MODEL_DIR)/fold0 $(MODEL_DIR)/fold1 $(MODEL_DIR)/fold2 $(MODEL_DIR)/fold3 $(MODEL_DIR)/fold4
BENCH_CORPUS ?= bench/data/labelled.jsonl

# `bench/latency.py` variables: LATENCY_BASE_URL is the running deployment `make serve` (or a
# real deployment) answers on; the script's own defaults name everything else (Task 11.2).
LATENCY_BASE_URL ?= http://127.0.0.1:8000

setup:  ## uv sync --all-extras --group dev, then cd web && npm ci
	uv sync --all-extras --group dev
	cd web && npm ci

# `fixtures/` and `docs/scripts/` are checked too: both hold committed Python that generates
# committed artefacts (the mini corpus, the README's numbers), so "it is not in `sieve/`" is not
# a reason for it to drift. `mypy` keeps its configured scope -- `files = ["sieve"]` in
# pyproject.toml -- because `--strict` over a one-off generator is a different decision.
lint:  ## ruff, ruff format --check, mypy, then the web lint/tokens/contrast/tsc checks
	uv run ruff check sieve bench tests fixtures docs/scripts
	uv run ruff format --check sieve bench tests fixtures docs/scripts
	uv run mypy
	cd web && npm run lint && npx tsc --noEmit && npm run lint:tokens && npm run lint:contrast
	# The MSW guard needs something built to look at: `npm run build` runs it from `postbuild`,
	# and `lint:bundle` again so a failure names itself rather than hiding in the build output.
	cd web && npm run build && npm run lint:bundle

test:  ## pytest (Postgres suites need DATABASE_URL) + web vitest
	uv run pytest -q -m "not slow and not docker and not holdout" --cov=sieve --cov-report=term-missing
	cd web && npm test

# Both Playwright projects: `mocked` against MSW, `integration` against the real API. Playwright
# starts everything it needs, including the API stack -- `E2E_STACK=compose` brings it up with
# `docker compose`, and the default `local` runs uvicorn and the worker from `uv` against an
# already-running Postgres (`E2E_DATABASE_URL` names it). See web/scripts/e2e-stack.mjs.
e2e:  ## Playwright against the mocked production build
	cd web && npm run e2e

schemas:  ## regenerate schemas/openapi.json and the web's typed client
	uv run python -m sieve.api.export_schemas
	cd web && npx openapi-typescript ../schemas/openapi.json -o src/lib/api-types.ts

up:  ## docker compose up -d --build and seed the sample scan
	docker compose up -d --build
	uv run python -m sieve.db.seed

down:  ## docker compose down
	docker compose down -v

serve:  ## run the API locally (python -m sieve.api, --reload)
	uv run python -m sieve.api --reload

baseline:  ## Cisco YARA scanner on dev rows -> bench/results/baseline{,_raw}.json (record-id keyed)
	uv run python -m bench.baseline.run

# `tfidf-ood` (training plan C5): the repo's own TF-IDF (sieve/model/tfidf.py), fitted on dev, on
# val-OOD (report-only). test-OOD is scored by `eval-final` only.
tfidf-ood:  ## the repo's TF-IDF baseline, fitted on dev, on val-OOD -> bench/results/tfidf_ood.json
	uv run python -m bench.tfidf_ood --corpus $(BENCH_CORPUS)

train:  ## one FP32 training run: PROFILE= SEED= FOLD=<0-4|all>
	uv run python -m sieve.model.train --config bench/train.yaml --profile $(PROFILE) --fold $(FOLD) --seed $(SEED) --out $(MODEL_OUT) $(TRAIN_EPOCHS)

train-all:  ## every fold and seed (GPU)
	# `|| exit 1`: a loop's status is its last command's, so without it a failed fold scrolls
	# past, the next folds run, and `calibrate` pools whatever that fold left from before.
	for f in 0 1 2 3 4; do \
		uv run python -m sieve.model.train --config bench/train.yaml --profile $(PROFILE) --fold $$f --seed $(SEED) --out $(MODEL_DIR)/fold$$f || exit 1; \
	done

best-epoch:  ## the EPOCHS= for FOLD=all: the median best_epoch of MODEL_DIR's five folds
	uv run python -c "import json, statistics; e = [json.load(open(r'$(MODEL_DIR)/fold%d/metrics.json' % k, encoding='utf-8'))['best_epoch'] for k in range(5)]; print('best epochs', e, '-> EPOCHS =', int(statistics.median(e)))"

# `calibrate` passes --out explicitly: the CLI's own default is a sibling of the fold
# directories' parent, which under this layout nests (models/<family>-s0/<family>-s0).
model-dir:  ## print MODEL_DIR for PROFILE= SEED= (models/<family>-s<seed>)
	@echo $(MODEL_DIR)

# `probes` (training plan C4, §5.3): the four style-leakage probes on dev rows -- per-stratum
# source leakage (imperative, benign), surface, length-only, transfer. Exit 1 if any > 0.70.
probes:  ## the §5.3 leakage probes on dev rows -> bench/results/leakage_probe.json (exit 1 if any > 0.70)
	uv run python -m bench.analysis.probes --corpus $(BENCH_CORPUS)

# `gpu-bench` (training plan C3, §13 step 7): every speed variant timed on real batches, the
# pre-registered profile choice printed, residual non-determinism measured. Refuses on the CPU
# path and on a busy card (FORCE=1 overrides the latter only).
gpu-bench:  ## time every profile variant, print the §6.4 choice, measure non-determinism (GPU)
	uv run python -m bench.gpu_bench $(if $(FORCE),--force) $(if $(GPU_IDLE_MIB),--idle-mib $(GPU_IDLE_MIB))

# `sweep` (training plan C2): the whole per-seed recipe, resumable, refusing a busy GPU before
# every training step. FORCE=1 skips that refusal; GPU_IDLE_MIB moves its threshold.
SEEDS ?= 0 1 2
sweep:  ## PROFILE= SEEDS="0 1 2": per seed 5 folds -> best epoch -> FOLD=all -> calibrate (resumable)
	uv run python -m bench.sweep --profile $(PROFILE) --seeds "$(SEEDS)" $(if $(FORCE),--force) $(if $(GPU_IDLE_MIB),--idle-mib $(GPU_IDLE_MIB))

calibrate:  ## temperature, T_low and T_high from the five folds' pooled OOF logits, into MODEL_DIR
	uv run python -m sieve.model.calibrate --dirs $(FOLD_DIRS) --corpus $(BENCH_CORPUS) --out $(MODEL_DIR)

bench:  ## regenerate bench/results/* offline (latency, ablations, errors, judge agreement)
	# Task 11.2: first, so a server missing at LATENCY_BASE_URL (`make serve` in another
	# terminal, or a real deployment) fails fast -- before the expensive steps below, not
	# after. p50/p95/p99 for POST /api/v1/triage at 20 concurrent; PRD M6a: never edit
	# bench/results/latency.json by hand, and the script itself refuses to write one there
	# for the fixture/fake classifier.
	uv run python -m bench.latency --base-url $(LATENCY_BASE_URL)
	uv run python -m bench.ablations.run --matrix bench/ablations.yaml
	uv run python -m bench.analysis.errors --dirs $(FOLD_DIRS) --corpus $(BENCH_CORPUS) --model-dir $(MODEL_DIR)
	uv run python -m bench.analysis.judge_agreement
	uv run python -m bench.demo

eval-final:  ## the one-shot sealed evaluation, SIEVE_FINAL=1, REASON= required
	@test -n "$(REASON)" || (echo "make eval-final REASON=\"why the holdout is being opened\"" && exit 2)
	SIEVE_FINAL=1 uv run python -m bench.eval_final --reason "$(REASON)"

readme:  ## rewrite README numbers from bench/results/ (refuses without holdout.json)
	uv run python docs/scripts/readme_numbers.py

# `corpus`: real catalog rows -> mutations -> dedup -> grouped split -> stats. `constructed.jsonl`
# is rebuilt every run and merged into `corpus.jsonl` (the file the TUI and the vote label in
# place) by `bench.collect.merge_corpus` (training plan C6): content from the build, labels from
# the corpus, regenerated synthetic rows taken whole; a removed row or a label on changed text
# stops with a diff and leaves the corpus untouched. Synthetic v1 (training plan E1-E3): the
# re-plan `plan_v1.json` / `human_plan_v1.json` and the locally regenerated `payloads_v1.jsonl`
# (`python -m bench.generate.generate_local --all`, §13 step 2); v0 files are kept for history.
corpus:  ## build bench/data/labelled.jsonl (catalog import, mutations, dedup, split, stats)
	uv run python -m bench.collect.catalog --out bench/data/real.jsonl
	uv run python -m bench.generate.mutate --corpus bench/data/real.jsonl --plan bench/data/synthetic/human_plan_v1.json --generator human --id-prefix sb-hum- --out bench/data/synthetic/human.jsonl
	uv run python -m bench.generate.mutate --corpus bench/data/real.jsonl --plan bench/data/synthetic/plan_v1.json --generator file --payloads bench/data/synthetic/payloads_v1.jsonl --id-prefix sb-syn- --out bench/data/synthetic/model.jsonl
	cat bench/data/real.jsonl bench/data/synthetic/human.jsonl bench/data/synthetic/model.jsonl > bench/data/constructed.jsonl
	uv run python -m bench.collect.merge_corpus --constructed bench/data/constructed.jsonl --corpus bench/data/corpus.jsonl
	uv run python -m bench.split.dedup --corpus bench/data/corpus.jsonl --out bench/data/deduped.jsonl --twins bench/data/near_twins.json --split-map bench/data/split_map.json
	uv run python -m bench.split.split --corpus bench/data/deduped.jsonl --out bench/data/labelled.jsonl --split-map bench/data/split_map.json --near-twins bench/data/near_twins.json
	uv run python -m bench.stats --corpus bench/data/labelled.jsonl

# `gate` (training plan E4, §5.3): the pre-registered synthetic-style gate on dev rows, with one
# tightening pass (regenerate the failing stratum, rebuild the corpus) and then stop (exit 3).
# NO_REGENERATE=1 only checks (§13 step 5, after relabelling).
gate:  ## pre-registered style gate -> bench/results/gate.json (exit 3 = stop and ask)
	uv run python -m bench.gate $(if $(NO_REGENERATE),--check-only,)

corpus-stats:  ## corpus statistics report
	uv run python -m bench.stats

# `plan-evidence`: every measured number in SIEVE_TRAINING_PLAN.md (task A3). Fetches the pinned
# third-party clones into the gitignored bench/data/external/ first; CPU only, HF cache offline.
plan-evidence:  ## regenerate the training plan's measured numbers (bench/data/external/plan_evidence/)
	uv run python -m bench.collect.external
	CUDA_VISIBLE_DEVICES=-1 uv run python -m bench.analysis.plan_evidence all

# Blind human labelling (training plan F4, section 4.2). Both sessions hide the label, the id and
# the provenance, present rows in a seeded order, and save as you go (`w`); quit with `q`.
label-dev-audit:  ## blind-label the 50 seeded real dev rows of the dev-50 audit (~1.7 h)
	uv run python -m bench.label.tui bench/data/corpus.jsonl --blind --dev-audit

label-test-id:  ## blind-label the 104 real test-ID rows into bench/data/holdout_gold.jsonl (~3.5 h)
	uv run python -m bench.label.holdout_gold
