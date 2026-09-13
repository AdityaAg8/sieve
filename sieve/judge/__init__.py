"""Stage-2 LLM judge (PRD §12).

Nothing is re-exported here on purpose. `sieve.judge.client` imports `anthropic`, which is
the optional `judge` extra; a package-level re-export would make every importer of
`sieve.judge.schema` (L3's ablation runner, L5's pipeline tests) pay for that dependency.
Import the module you need: `sieve.judge.schema`, `.render`, `.client`, `.cost`, `.record`,
`.locate`.
"""
