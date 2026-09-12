ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/usr/bin/bash.exe
endif

.PHONY: setup lint test schemas up down serve baseline train bench eval-final readme corpus-stats

setup:
	uv sync --all-extras --group dev
	cd web && npm ci

lint:
	uv run ruff check sieve bench tests
	uv run ruff format --check sieve bench tests
	uv run mypy
	cd web && npm run lint && npx tsc --noEmit && npm run lint:tokens && npm run lint:contrast

test:
	uv run pytest -q -m "not slow and not docker and not holdout" --cov=sieve --cov-report=term-missing
	cd web && npm test

schemas:
	uv run python -m sieve.api.export_schemas
	cd web && npx openapi-typescript ../schemas/openapi.json -o src/lib/api-types.ts

up:
	docker compose up -d --build
	uv run python -m sieve.db.seed

down:
	docker compose down -v

serve:
	uv run uvicorn sieve.api.main:app --reload

baseline:
	uv run python -m bench.baseline.run

train:
	uv run python -m sieve.model.train --config bench/train.yaml --profile deberta

bench:
	uv run python -m bench.ablations.run --matrix bench/ablations.yaml
	uv run python -m bench.analysis.errors
	uv run python -m bench.demo

eval-final:
	SIEVE_FINAL=1 uv run python -m bench.eval_final

readme:
	uv run python docs/scripts/readme_numbers.py

corpus-stats:
	uv run python -m bench.stats
