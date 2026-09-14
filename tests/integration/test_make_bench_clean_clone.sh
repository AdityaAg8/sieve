#!/usr/bin/env bash
#
# Reproduce the committed ablation rows from a clean clone (PRD M6a).
#
# `bench/results/` is never edited by hand, which is a claim about where those numbers came
# from. This script is what makes the claim checkable: clone the repository into a temporary
# directory, with **no `.env` and no `ANTHROPIC_API_KEY`**, regenerate the four rows that need
# neither an API key nor a judge cassette, and require the regenerated rows to be byte-identical
# to the ones the clone was carrying.
#
# The comparison is against the **clone's** copy, snapshotted before the run, not against the
# working tree: the claim is about committed bytes, and an uncommitted edit beside this script
# must not be able to satisfy it.
#
# Rows 1-4 are the classifier-only and baseline-replay rows: the Cisco YARA baseline, TF-IDF, and
# the two encoder serialisations. `SIEVE_JUDGE_ENABLED=false` is belt-and-braces -- none of the
# four names a judge -- and `bench.ablations.run` calls `require_offline()` itself.
#
# Exit codes, and they are the interface the weekly workflow reads:
#
#   0  the four rows reproduced byte-for-byte
#   1  they did not, or the run failed
#   3  a prerequisite artefact is not in the clone, so **nothing was measured**
#
# 3 is not a pass. A trained artefact is a runbook step (`make train-all`, then `make bench`), so
# until one is committed this script reports "artefact missing" and the weekly workflow reports a
# skip. Reporting green for a reproduction that never ran is the one outcome this file exists to
# prevent.
#
# Usage:  bash tests/integration/test_make_bench_clean_clone.sh
set -euo pipefail

ROWS="1,2,3,4"
SEEDS="${SEEDS:-0,1,2}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

workdir="$(mktemp -d)"
clone="$workdir/sieve"
committed="$workdir/committed-ablations.json"
trap 'rm -rf "$workdir"' EXIT

echo "== cloning $repo into $clone"
git clone --quiet --no-hardlinks "$repo" "$clone"

# A clone carries no ignored file, so there is no `.env` in it -- assert that rather than assume
# it, because "the key came from somewhere unexpected" is precisely the failure mode here.
if [ -e "$clone/.env" ]; then
  echo "FAIL: the clone has a .env; this run would not be clean" >&2
  exit 1
fi

missing=()
require() { [ -e "$clone/$1" ] || missing+=("$1"); }
require "bench/results/ablations.json"
require "bench/data/labelled.jsonl"
require "bench/results/baseline_raw.json"
require "bench/results/minimal_pairs.json"
for seed in ${SEEDS//,/ }; do
  require "models/deberta-v3-base-s$seed"
  require "models/deberta-v3-base-desc-s$seed"
done

if [ ${#missing[@]} -ne 0 ]; then
  echo "SKIP: artefact missing -- nothing was reproduced. The clean clone has no:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "Train and publish them first (see docs/handoffs/11.3.md)." >&2
  exit 3
fi

cp "$clone/bench/results/ablations.json" "$committed"

echo "== regenerating rows $ROWS with no key and the judge off"
(
  cd "$clone"
  unset ANTHROPIC_API_KEY
  SIEVE_JUDGE_ENABLED=false uv run python -m bench.ablations.run \
    --matrix bench/ablations.yaml --rows "$ROWS" --seeds "$SEEDS"
)

echo "== comparing rows $ROWS against the committed file"
# Run inside the clone's environment: the step above already built it, and the comparison itself
# needs nothing but the standard library.
cd "$clone"
COMMITTED="$committed" REGENERATED="$clone/bench/results/ablations.json" ROWS="$ROWS" \
  uv run python - <<'PY'
"""Byte-compare the selected rows, and say which key differs when they do not match."""
import json
import os
import sys

wanted = {int(x) for x in os.environ["ROWS"].split(",")}


def rows(path: str) -> dict[int, str]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    found = {}
    for row in payload["rows"]:
        if int(row["row"]) in wanted:
            found[int(row["row"])] = json.dumps(row, indent=2, sort_keys=True)
    return found


committed, regenerated = rows(os.environ["COMMITTED"]), rows(os.environ["REGENERATED"])

missing = sorted(wanted - set(committed))
if missing:
    print(f"SKIP: the committed file has no row(s) {missing}; nothing to compare", file=sys.stderr)
    raise SystemExit(3)

bad = [n for n in sorted(wanted) if committed[n] != regenerated.get(n)]
if bad:
    for n in bad:
        print(f"--- row {n}: committed", file=sys.stderr)
        print(committed[n], file=sys.stderr)
        print(f"+++ row {n}: regenerated", file=sys.stderr)
        print(regenerated.get(n, "<absent>"), file=sys.stderr)
    raise SystemExit(1)

print(f"OK: rows {sorted(wanted)} reproduced byte-for-byte from a clean clone")
PY
