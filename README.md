# Sieve

Sieve is a triage layer for MCP tool-poisoning scanner findings. It does not scan and it does not
detect: it takes the findings a scanner already produced, decides which of them a human should
actually look at, and says why.

<!-- numbers:start -->

**M1 · `FPR_pipeline` on the real subset:** —

**M2 · False-positive reduction vs the scanner baseline, real subset:** —

**Holdout poisoned recall:** —

**M13 · `real_poisoned` recall:** —

*These four numbers are written by `make readme` from `bench/results/`, never by hand. They stay
`—` until `make bench` has produced the ablation matrix and `make eval-final REASON="..."` has
opened the sealed holdout once (PRD §11.6). No model has been trained yet, so `make readme`
currently refuses to run and says which file is missing.*

<!-- numbers:end -->

<!-- human: rewrite - a 10-second GIF of the sieve belongs here (PRD §17.1). Nothing has been
recorded yet; `web/screenshots/` holds 24 stills from L6 and no capture exists. Do not describe
a video that does not exist. -->

## What it does, in 30 seconds

An MCP server describes its tools to your agent in free text. That text is instructions the model
will follow, so a tool description can tell the agent to read `~/.ssh/id_rsa` and paste it into the
next call. Pattern scanners flag this, and they also flag every honest tool that says "You MUST
call `resolve-library-id` first" — the one public audit of the ecosystem [R1] found most of what a
scanner flagged was of that second kind.

Sieve is the layer after the scanner. Stage 1 is a fine-tuned encoder that classifies each tool
definition as `benign`, `imperative_benign` (directive but legitimate: the false-positive class) or
`poisoned`. Anything it is confident about is auto-dismissed or auto-confirmed; only the band in
between reaches Stage 2, an LLM judge that has to quote the span it objects to. The output is a
verdict per tool with evidence, and everything a scanner flagged that Sieve dismisses states which
class it was dismissed as.

- Three-class taxonomy and the rule that separates the middle class:
  [ADR-001](docs/adr/0001-three-class-taxonomy.md).
- The rest of the architecture decisions: [`docs/adr/`](docs/adr/README.md).

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
make schemas # regenerate schemas/openapi.json and the web's typed client
```

`make up` seeds a pre-computed sample scan at the fixed id
`00000000-0000-0000-0000-000000005eed`, so the UI has content before you scan anything.

On Windows, `make` works from Git Bash directly. From PowerShell, invoke a login Git Bash shell
instead of calling `make.exe` directly (a local PATH quirk between PowerShell and nvm-for-windows;
see `lessons.md`):

```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc 'cd /d/Projects/Sieve && make lint'
```

### CLI

```bash
uv run sieve triage fixtures/tools/sample-27.json --offline --format json
uv run sieve triage - --from cisco-yara --format json < fixtures/scanner/cisco-yara-sample-27.json
uv run sieve triage --config fixtures/mcp-config/sample.mcp.json --static --format sarif -o sieve.sarif
uv run sieve version
```

`--from cisco-yara|sarif` ingests someone else's report, which carries tool *names* and findings
but no description or schema, so Stage 1 sees only the name; a SARIF-only run cannot see a tool
the scanner never flagged. `--config PATH --static` parses the config and never starts the server
it names, so it always reports zero tools (SC-1 forbids spawning an untrusted MCP server outside
the sandboxed worker). `--offline` installs a socket guard and refuses to open any connection.
`--fail-on confirmed|review|any|never` sets the exit code; the tiers are read as a widening scale
and `sieve/cli/app.py::_resolve_exit_code` documents that reading.

### GitHub Action

```yaml
- uses: ./action                  # or aditya/sieve-action@v1 once published
  with:
    config: .mcp.json
    fail-on: confirmed
    upload-sarif: "true"
```

The composite action in [`action/`](action/action.yml) installs the CLI, runs it in `--static`
mode, uploads SARIF to Code Scanning and posts a PR comment. It installs `sieve` from PyPI, where
Sieve is **not published yet**, so `.github/workflows/self-scan.yml` fails at that step today
(L7 hand-off).

### MCP server

Sieve is itself an MCP server: three tools that take tool definitions as data and return verdicts.
It never starts, installs or connects to another MCP server. Setup, the `claude mcp add` command
and a verified stdio transcript are in [docs/mcp-server.md](docs/mcp-server.md).

```bash
uv run python -m sieve.mcp_server --transport stdio
```

### Web

```bash
cd web && npm run dev     # http://localhost:3000
```

## Reproducing the numbers

```bash
make corpus-stats                       # corpus gate: provenance, dedup, splits, leakage probe
make train FOLD=0                       # one fold; make train-all does five (GPU)
make bench                              # every published metric, offline, from committed artefacts
make eval-final REASON="v0.1.0 release" # opens the sealed holdout once, appends to test_opened.log
make readme                             # rewrites the block at the top of this file
```

`make bench` runs with no network and no API key: it replays `bench/results/judge_outputs.jsonl`
and re-reads the committed prediction artefacts, so every metric is recomputed rather than
remembered (PRD M6a). `bench/results/` is never edited by hand. Every decision in the project is
made on pooled out-of-fold predictions over the development split; the holdout and the
`real_poisoned` slice are opened exactly once, by `bench/eval_final.py` under `SIEVE_FINAL=1`.

## Benchmark

<!-- human: rewrite - the benchmark table is generated by `make bench` into
bench/results/ablations.json and rendered on the /benchmark route. It is not transcribed here
until the numbers exist; today bench/results/ holds only demo.json. -->

Nothing has been trained yet, so there is no benchmark table in this file. The matrix that will
fill it is the fourteen rows of `bench/ablations.yaml` (PRD §11.5), measured against these two
targets and reported whatever they come out as:

- **M1 target (PRD §4.3):** a pipeline false-positive rate of at most 20% on the real subset, at a
  holdout poisoned recall of at least 0.95 (point) and 0.90 (lower bound).
- **M2 target (PRD §4.3):** at least a 40% relative reduction against the scanner baseline, at
  equal or higher recall.

## Limitations

- **Sieve is not a scanner.** It cannot find a tool nobody flagged unless you hand it the whole
  `tools/list`. It writes no detection rules and no YARA signatures, by design (PRD §4.2).
- **The corpus is small and partly synthetic.** Synthetic mutations are minimal pairs of real
  tools; they are labelled blind and reported separately from the real subset, and every headline
  number above is the real-subset one for that reason.
- **One public audit.** The problem statement rests on a single 2026 audit of 33 servers [R1],
  quoted with its confidence interval and named as the only one that exists.
- **The judge's self-defence suite is unmeasured.** The two-sided injection gate (PRD §12.5) is
  written and wired, but no `ANTHROPIC_API_KEY` has ever been used here, so every case skips with
  the recording command as its reason (L4 hand-off). A skipped gate is not a passing gate.
- **The scanner baseline is one scanner.** `cisco_yara` is captured for real; `cisco_llm` is
  fixture-only and `snyk_agent_scan` is illustrative, hard-capped at 25 invocations (PRD SC-2).
- **Remote scanning is off by default** and only ever runs inside the sandboxed worker.

## Links

- Spec: [`docs/superpowers/specs/2026-09-11-sieve-prd-v2.md`](docs/superpowers/specs/2026-09-11-sieve-prd-v2.md)
- Architecture decisions: [`docs/adr/`](docs/adr/README.md)
- Datasheet: [`bench/datasheet.md`](bench/datasheet.md)
- MCP server: [`docs/mcp-server.md`](docs/mcp-server.md)
- Dataset, model and write-up URLs: none yet. The web front end reads them from
  `NEXT_PUBLIC_DATASET_URL`, `NEXT_PUBLIC_MODEL_URL`, `NEXT_PUBLIC_WRITEUP_URL` and
  `NEXT_PUBLIC_REPO_URL` and renders "not published yet" while they are unset.

**[R1]** AppSec Santa, *MCP Server Security Audit 2026*:
<https://appsecsanta.com/research/mcp-server-security-audit-2026>

## Licence and status

Pre-release. Nothing here is published to PyPI, the Hugging Face Hub or a dataset host yet, and no
number in this repository has been measured on a trained model.
