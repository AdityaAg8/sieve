"""The worker's view of `sieve/errors.py`, kept as a name so its call sites do not move.

`safe_error` and `safe_detail` were written here, for `scans.error` and `jobs.last_error`. Fix
round 2 found a third writer with the same problem -- `sieve/logging.py` puts a formatted
traceback into every ERROR record, and `DropToolContent` only rewrites the content keys taken
from `extra`, never `exc`. The fix is one call, but `sieve.logging` cannot import
`sieve.worker`: the API, the CLI, the MCP server and `bench` all log and none of them run jobs.
So the implementation moved to the neutral `sieve/errors.py` and this module re-exports it.

Nothing else changed: the reasoning for why an exception message is a writer at all, and why
truncation happens after redaction, is in that module's docstring.
"""

from __future__ import annotations

from sieve.errors import ELLIPSIS, MAX_ERROR_CHARS, safe_detail, safe_error

__all__ = ["ELLIPSIS", "MAX_ERROR_CHARS", "safe_detail", "safe_error"]
