#!/usr/bin/env bash
#
# Reproduce the committed ablation rows from a clean clone (PRD M6a).
#
# `bench/results/` is never edited by hand, which is a claim about where those numbers came
# from. This script is what makes the claim checkable: clone the repository into a temporary
# directory, with **no `.env`, no `ANTHROPIC_API_KEY` and no cached results**, regenerate the
# four rows that need neither an API key nor a judge cassette, and require them byte-identical
# to the ones the clone was carrying.
#
# The comparison is against the **clone's** copy, snapshotted before the run, not against the
# working tree: the claim is about committed bytes, and an uncommitted edit beside this script
# must not be able to satisfy it.
#
# ## Artefacts are supplied, never committed
#
# `models/` is git-ignored (a few hundred megabytes of weights do not belong in git), so a
# clone can never *carry* a trained artefact. It is handed one instead:
#
#   SIEVE_ARTEFACTS_DIR   a directory laid out like `models/` -- one subdirectory per artefact
#                         (`deberta-v3-base-s0`, ...). Copied into the clone and made read-only
#                         there, so the reproduction cannot write back into the source.
#
# The weekly workflow fills that variable by fetching the artefacts named in
# `bench/artefacts.json` at their pinned revisions and verifying every listed sha256
# (`python -m bench.artefacts --dest ...`). Locally, point it at your own `models/`.
#
# Unset or empty, this script exits 3. It never trains anything and never invents a number.
#
# ## Exit codes -- the interface the weekly workflow reads
#
#   0  the four rows reproduced byte-for-byte
#   1  they did not
#   3  a prerequisite is not available, so **nothing was measured**
#
# Any other non-zero code is a failure of a step rather than a verdict: `set -euo pipefail`
# means `git clone`, `cp`, `uv sync` and `bench.ablations.run` propagate their own status
# unchanged (uv's 2, a signal's 128+n, and so on). The weekly workflow treats **every code
# except 0 and 3 as an error**, so an unforeseen failure is never mistaken for a verdict.
#
# 3 is not a pass. Reporting green for a reproduction that never ran is the one outcome this
# file exists to prevent.
#
# Usage:
#   bash tests/integration/test_make_bench_clean_clone.sh                    # skips, exit 3
#   SIEVE_ARTEFACTS_DIR=models bash tests/integration/test_make_bench_clean_clone.sh
set -euo pipefail

ROWS="1,2,3,4"
SEEDS="${SEEDS:-0,1,2}"
ARTEFACTS="${SIEVE_ARTEFACTS_DIR:-}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

workdir="$(mktemp -d)"
clone="$workdir/sieve"
committed="$workdir/committed-ablations.json"
# The copied artefacts are made read-only; directories have to be writable again before the
# temporary tree can be removed.
cleanup() { chmod -R u+w "$workdir" 2>/dev/null || true; rm -rf "$workdir"; }
trap cleanup EXIT

skip() {
  echo "SKIP: $1" >&2
  shift
  printf '  - %s\n' "$@" >&2
  echo "Nothing was reproduced. See docs/RUNBOOK.md, section 2." >&2
  exit 3
}

if [ -z "$ARTEFACTS" ] || [ -z "$(ls -A "$ARTEFACTS" 2>/dev/null)" ]; then
  skip "artefact missing -- SIEVE_ARTEFACTS_DIR is unset or empty" \
    "set it to a directory laid out like models/, or" \
    "fill in bench/artefacts.json and run: uv run python -m bench.artefacts --dest <dir>"
fi

echo "== cloning $repo into $clone"
git clone --quiet --no-hardlinks "$repo" "$clone"

# A clone carries no ignored file, so there is no `.env` in it -- assert that rather than assume
# it, because "the key came from somewhere unexpected" is precisely the failure mode here.
if [ -e "$clone/.env" ]; then
  echo "FAIL: the clone has a .env; this run would not be clean" >&2
  exit 1
fi

echo "== supplying artefacts from $ARTEFACTS"
mkdir -p "$clone/models"
cp -R "$ARTEFACTS"/. "$clone/models/"
# Read-only inside the clone: the reproduction reads artefacts, and a run that wrote to one
# would be reading back something it had produced.
find "$clone/models" -type f -exec chmod a-w {} +

missing=()
require() { [ -e "$clone/$1" ] || missing+=("$1"); }
require "bench/results/ablations.json"
require "bench/data/labelled.jsonl"
require "bench/results/baseline_raw.json"
require "bench/results/minimal_pairs.json"
# Row 4 is `models/{primary}-s*` (training plan H5): the winner selection.json names, or the
# declared default before one exists. Row 3 is single-seed (D9), so only its s0 is required.
primary=deberta-v3-base
if [ -f "$clone/bench/results/selection.json" ]; then
  primary=$(sed -n 's/.*"winner": *"\([^"]*\)".*/\1/p' "$clone/bench/results/selection.json")
fi
for seed in ${SEEDS//,/ }; do
  require "models/$primary-s$seed"
done
require "models/deberta-v3-base-desc-s0"
if [ ${#missing[@]} -ne 0 ]; then
  skip "artefact missing -- the prepared clone has no:" "${missing[@]}"
fi

cp "$clone/bench/results/ablations.json" "$committed"

# A clone's `uv sync` installs base + dev only, and row 2 imports `sieve.model.tfidf`, which
# imports scikit-learn. Both extras, in the clone, so this script and the weekly workflow agree
# about what the reproduction runs against.
echo "== syncing the clone's environment"
(cd "$clone" && uv sync --extra model --extra bench --group dev)

echo "== regenerating rows $ROWS with no key and the judge off"
(
  cd "$clone"
  unset ANTHROPIC_API_KEY
  SIEVE_JUDGE_ENABLED=false uv run python -m bench.ablations.run \
    --matrix bench/ablations.yaml --rows "$ROWS" --seeds "$SEEDS"
)

echo "== comparing rows $ROWS against the committed file"
# `bench.reproduce` rather than an inline heredoc: the comparison is the half of this gate that
# can be wrong quietly, so it is an importable module with its own tests
# (tests/bench/test_reproduce.py). Run from the clone, whose environment is already built.
cd "$clone"
uv run python -m bench.reproduce \
  --committed "$committed" \
  --regenerated "$clone/bench/results/ablations.json" \
  --rows "$ROWS"
