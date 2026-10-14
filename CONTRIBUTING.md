# Contributing

## Setup

You need [`uv`](https://docs.astral.sh/uv/) (Python 3.12), Node.js 22 and npm, and GNU make.
Docker is only needed for `make up`.

```sh
make setup                # uv sync --all-extras --group dev, then cd web && npm ci
cp .env.example .env      # fill in the keys you have; the tests need none
```

`make bench` and one test also run the Cisco YARA scanner, which is a separate tool:

```sh
uv tool install cisco-ai-mcp-scanner==4.8.0
```

## Tests and lint

```sh
make test     # pytest plus web vitest
make lint     # ruff, ruff format --check, mypy, then the web lint, token, contrast and tsc checks
make e2e      # Playwright against the mocked production build
```

The Postgres-backed tests need `DATABASE_URL` and skip without it:

```sh
export DATABASE_URL=postgresql+psycopg://sieve:sieve@127.0.0.1:5432/sieve
```

To run one area while you work:

```sh
uv run pytest -q tests/docs tests/cli
```

## Schemas

The web app's typed client comes from the API's OpenAPI schema. After changing an endpoint:

```sh
make schemas  # rewrites schemas/openapi.json and web/src/lib/api-types.ts
```

## Running it

```sh
make serve                # the API, reloading on change
cd web && npm run dev     # the web app on http://localhost:3000
make up                   # the whole stack in Docker, with a seeded sample scan
sieve triage tools.json   # the CLI
```

`make help` lists every target with one line each.

## Windows

`make` works from Git Bash. Install it with `winget install ezwinports.make` and add its `bin`
directory to `PATH`. From PowerShell, call a login Git Bash shell instead of `make.exe`:

```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc 'cd /d/path/to/sieve && make lint'
```

## Conventions

- Pass `encoding="utf-8"` to every `read_text`, `write_text` and `open`, and `newline="\n"` to
  anything CI diffs.
- Never edit `bench/results/` by hand. `make bench` regenerates it.
