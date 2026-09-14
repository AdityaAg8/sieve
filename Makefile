ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/usr/bin/bash.exe
endif

.PHONY: setup lint test e2e schemas up down serve baseline train train-all bench eval-final readme corpus-stats

# `train`/`train-all` variables: PROFILE names a `bench/train.yaml` profile; SEED and FOLD are
# the CLI's own `--seed`/`--fold`. MODEL_OUT follows the "models/<profile>-s<seed>/fold<fold>"
# layout `bench/ablations/run.py` and `sieve/model/select.py` already assume ("models/<profile>-s*").
PROFILE ?= deberta
SEED ?= 0
FOLD ?= 0
MODEL_OUT ?= models/$(PROFILE)-s$(SEED)/fold$(FOLD)

# `bench` variables: MODEL_DIR is the calibrated (`--fold all`) artefact `bench.analysis.errors`
# reads thresholds from; FOLD_DIRS are the five per-fold artefacts its `--dirs` pools OOF logits
# from. BENCH_CORPUS matches the CLIs' own `--corpus` default.
MODEL_DIR ?= models/$(PROFILE)-s$(SEED)
FOLD_DIRS ?= $(MODEL_DIR)/fold0 $(MODEL_DIR)/fold1 $(MODEL_DIR)/fold2 $(MODEL_DIR)/fold3 $(MODEL_DIR)/fold4
BENCH_CORPUS ?= bench/data/labelled.jsonl

# `bench/latency.py` variables: LATENCY_BASE_URL is the running deployment `make serve` (or a
# real deployment) answers on; the script's own defaults name everything else (Task 11.2).
LATENCY_BASE_URL ?= http://127.0.0.1:8000

setup:
	uv sync --all-extras --group dev
	cd web && npm ci

lint:
	uv run ruff check sieve bench tests
	uv run ruff format --check sieve bench tests
	uv run mypy
	cd web && npm run lint && npx tsc --noEmit && npm run lint:tokens && npm run lint:contrast
	# The MSW guard needs something built to look at: `npm run build` runs it from `postbuild`,
	# and `lint:bundle` again so a failure names itself rather than hiding in the build output.
	cd web && npm run build && npm run lint:bundle

test:
	uv run pytest -q -m "not slow and not docker and not holdout" --cov=sieve --cov-report=term-missing
	cd web && npm test

# Both Playwright projects: `mocked` against MSW, `integration` against the real API. Playwright
# starts everything it needs, including the API stack -- `E2E_STACK=compose` brings it up with
# `docker compose`, and the default `local` runs uvicorn and the worker from `uv` against an
# already-running Postgres (`E2E_DATABASE_URL` names it). See web/scripts/e2e-stack.mjs.
e2e:
	cd web && npm run e2e

schemas:
	uv run python -m sieve.api.export_schemas
	cd web && npx openapi-typescript ../schemas/openapi.json -o src/lib/api-types.ts

up:
	docker compose up -d --build
	uv run python -m sieve.db.seed

down:
	docker compose down -v

serve:
	uv run python -m sieve.api --reload

baseline:
	uv run python -m bench.baseline.run

train:
	uv run python -m sieve.model.train --config bench/train.yaml --profile $(PROFILE) --fold $(FOLD) --seed $(SEED) --out $(MODEL_OUT)

train-all:
	for f in 0 1 2 3 4; do \
		uv run python -m sieve.model.train --config bench/train.yaml --profile $(PROFILE) --fold $$f --seed $(SEED) --out models/$(PROFILE)-s$(SEED)/fold$$f; \
	done

bench:
	uv run python -m bench.ablations.run --matrix bench/ablations.yaml
	uv run python -m bench.analysis.errors --dirs $(FOLD_DIRS) --corpus $(BENCH_CORPUS) --model-dir $(MODEL_DIR)
	uv run python -m bench.analysis.judge_agreement
	uv run python -m bench.demo
	# Task 11.2: p50/p95/p99 for POST /api/v1/triage against whatever's already running at
	# LATENCY_BASE_URL (`make serve` in another terminal, or a real deployment) -- 20
	# concurrent, PRD M6a: never edit bench/results/latency.json by hand.
	uv run python -m bench.latency --base-url $(LATENCY_BASE_URL)

eval-final:
	@test -n "$(REASON)" || (echo "make eval-final REASON=\"why the holdout is being opened\"" && exit 2)
	SIEVE_FINAL=1 uv run python -m bench.eval_final --reason "$(REASON)"

readme:
	uv run python docs/scripts/readme_numbers.py

corpus-stats:
	uv run python -m bench.stats
