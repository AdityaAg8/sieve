"""What a failure is allowed to say once it becomes data (security review 11.1, finding F5).

A failure does not stay in the process. `scans.error` is returned by `GET /scans/{id}` and
streamed on `scan.failed`, `jobs.last_error` is a stored column, and a formatted traceback goes
out at ERROR into whatever aggregator the deployment runs. All three are written from an
exception's own message, and that message is not Sieve's text: everything inside an analysed
tool definition is attacker-controlled (SC-7), and the libraries on these paths quote their input
back -- a Pydantic `ValidationError` prints the value it rejected, and a driver error prints the
statement and its parameters. So an exception message is a *writer* in SC-5's sense, and it gets
the same two rules every other writer gets:

* **redacted** -- a credential that reached an exception must not reach a row, the wire, or a
  log line; and
* **bounded** -- the string is stored, streamed and logged, so its length is the caller's to
  choose, not the exception's.

Truncation happens after redaction on purpose: cutting first could split a credential across the
boundary and leave a recognisable prefix behind that the patterns no longer match.

This module is deliberately *neutral*. It lived in `sieve/worker/errors.py`, which is why
`sieve/logging.py` -- the one place SC-5's log rule is enforced -- did not use it: importing the
worker from the logging module would have inverted the dependency for every surface that logs
and never runs a job (fix round 2, I4). `sieve.worker.errors` now re-exports these names, so the
worker's call sites read exactly as they did.
"""

from __future__ import annotations

from sieve.redact import redact

#: Long enough for a real diagnosis, short enough that a 5 MB `ValidationError` is not a row.
MAX_ERROR_CHARS = 500

#: The bound for a *log* line rather than a row. A traceback is the one caller whose useful part
#: is at the **end** -- the exception type and message follow every frame -- so `MAX_ERROR_CHARS`
#: cuts exactly the diagnosis it was sized to preserve: measured, a seven-frame traceback reaches
#: 500 characters somewhere in the middle of the frame list. A log line is not a database column
#: and does not need a column's bound; it still needs *a* bound, because the string being
#: truncated is attacker-influenced (SC-7) and nothing else limits it.
MAX_TRACEBACK_CHARS = 8_000

#: Appended when something was cut, so a truncated message is never mistaken for a whole one.
ELLIPSIS = "…"


def safe_error(exc: BaseException) -> str:
    """`TypeName: message`, redacted (SC-5) and capped at `MAX_ERROR_CHARS`."""
    return safe_detail(f"{type(exc).__name__}: {exc}")


def safe_detail(detail: str, *, limit: int = MAX_ERROR_CHARS) -> str:
    """The same treatment for a message that was assembled rather than raised.

    `limit` exists for one caller -- `sieve/logging.py`'s traceback -- and defaults to the row
    bound every other caller has always had. Redaction is the part that is not negotiable and
    is identical either way.
    """
    redacted, _kinds = redact(detail)
    if len(redacted) <= limit:
        return redacted
    return redacted[: limit - len(ELLIPSIS)] + ELLIPSIS


__all__ = [
    "ELLIPSIS",
    "MAX_ERROR_CHARS",
    "MAX_TRACEBACK_CHARS",
    "safe_detail",
    "safe_error",
]
