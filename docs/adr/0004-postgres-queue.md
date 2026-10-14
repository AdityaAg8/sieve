# ADR-004: A Postgres `SKIP LOCKED` queue instead of Redis

## Status

Accepted, 2026-10-14.

## Context

A scan is a background job: the API accepts tools, returns a scan id, and a worker triages them
while the browser watches over Server-Sent Events. That needs a queue with at-least-once delivery,
visibility timeouts, retries and a dead-letter state.

The default answer is Redis plus RQ or Celery. It works, it is well understood, and it costs a
second stateful service in every environment: local Compose, CI, and the deployment. Sieve already
runs PostgreSQL for scans, verdicts, feedback, rate-limit counters and the spend ledger, and the
expected throughput is a handful of jobs per minute from a portfolio deployment, not thousands per
second.

## Decision

Use a Postgres table as the queue, claimed with `SELECT ... FOR UPDATE SKIP LOCKED` in a single
statement (about 200 lines in `sieve/worker/queue.py`). Heartbeats extend a visibility deadline;
a job whose worker dies is reaped after the deadline and re-claimed with `attempts` incremented;
three attempts with exponential backoff, then `dead`. The purge job re-enqueues itself and is
bootstrapped once, so the chain has a beginning.

Rejected: Redis + RQ (a second service for a load that does not need it), and an in-process
`asyncio` queue (loses every job when the process restarts, and cannot be shared with the CLI or a
second worker).

## Consequences

- One stateful service. `make up` is Postgres, API, worker and web, and CI needs no Redis.
- A job claim is transactional with the rows it touches, so a verdict, its events and the spend
  charge commit or roll back together. That is why `spend.charge()` deliberately does not commit.
- The queue is polled rather than pushed, so a job waits up to one poll interval. Acceptable at
  this scale; visible in the scan's own event timeline if it ever is not.
- The revisit trigger is throughput beyond roughly 50 jobs per second.
  Nothing in the code assumes the trigger will never fire, but nothing anticipates it either.
- Two correctness traps came out of building it and are now tests, not comments:
  two concurrent claims never return the same job, and a reaped worker can no longer close or fail
  its old job (`tests/worker/test_queue.py`).
