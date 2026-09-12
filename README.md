# Sieve

Sieve is a triage layer for MCP tool-poisoning scanner findings — it does not scan or detect on its
own. See `docs/superpowers/specs/2026-09-11-sieve-prd-v2.md` for the full spec.

This README is intentionally short for now; Task L10 expands it with real usage docs once the
product exists end to end.

## Quickstart (Windows and Unix)

**Prerequisites**

- [`uv`](https://docs.astral.sh/uv/) (Python 3.12 project/dependency manager)
- Node.js 22 LTS and npm
- Docker Desktop (for `make up`; not required for `make lint` / `make test`)
- GNU make — on Windows: `winget install ezwinports.make`, then add its `bin` directory to `PATH`

**Commands**

```bash
make setup   # uv sync --all-extras --group dev, plus `cd web && npm ci`
make test    # Python (pytest) + web (vitest) test suites
make lint    # ruff, mypy, and the web lint/type/token/contrast checks
make up      # docker compose up -d --build, then seeds the dev database
make serve   # run the API locally with --reload (no Docker)
```

On Windows, `make` works from Git Bash directly. From PowerShell, invoke a login Git Bash shell
instead of calling `make.exe` directly (a local PATH quirk between PowerShell and nvm-for-windows;
see `lessons.md`):

```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc 'cd /d/Projects/Sieve && make lint'
```
