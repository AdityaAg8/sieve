"""What a failure is allowed to say once it becomes data (security review 11.1, finding F5).

A worker failure does not stay in the process. `scans.error` is returned by `GET /scans/{id}`
and streamed on `scan.failed`, and `jobs.last_error` is a stored column; both are written from
an exception's own message. That message is not Sieve's text: everything inside an analysed
tool definition is attacker-controlled (SC-7), and the libraries in this path quote their input
back -- a Pydantic `ValidationError` prints the value it rejected, and a driver error prints
the statement and its parameters. So an exception message is a *writer* in SC-5's sense, and it
gets the same two rules every other writer gets:

* **redacted** -- a credential that reached an exception must not reach a row or the wire; and
* **bounded** -- the string is stored and streamed, so its length is the caller's to choose,
  not the exception's.

Truncation happens after redaction on purpose: cutting first could split a credential across
the boundary and leave a recognisable prefix behind that the patterns no longer match.
"""

from __future__ import annotations

from sieve.redact import redact

#: Long enough for a real diagnosis, short enough that a 5 MB `ValidationError` is not a row.
MAX_ERROR_CHARS = 500

#: Appended when something was cut, so a truncated message is never mistaken for a whole one.
ELLIPSIS = "…"


def safe_error(exc: BaseException) -> str:
    """`TypeName: message`, redacted (SC-5) and capped at `MAX_ERROR_CHARS`."""
    return safe_detail(f"{type(exc).__name__}: {exc}")


def safe_detail(detail: str) -> str:
    """The same treatment for a message that was assembled rather than raised."""
    redacted, _kinds = redact(detail)
    if len(redacted) <= MAX_ERROR_CHARS:
        return redacted
    return redacted[: MAX_ERROR_CHARS - len(ELLIPSIS)] + ELLIPSIS


__all__ = ["ELLIPSIS", "MAX_ERROR_CHARS", "safe_detail", "safe_error"]
