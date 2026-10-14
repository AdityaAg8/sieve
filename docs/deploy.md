# Deployment runbook

Not yet deployed. This document describes the configs and the commands to use; none of it has
been run against a real Fly or Vercel account, and no secret value appears in this repository.
Run the commands below yourself (or from a CI job with its own credentials). Placeholders are
`<angle-bracket>` text.

Three deployables, three configs:

| Deployable | Config | App |
| --- | --- | --- |
| API + queue worker | `fly.toml` | one Fly app, two process groups (`api`, `worker`) |
| Sandbox MCP-fetch worker | `fly.sandbox.toml` | a second, separate Fly app |
| Next.js frontend (`web/`) | `web/vercel.json` | one Vercel project, root directory `web` |

## Prerequisites

- `flyctl` installed and `fly auth login` completed, with an organization selected.
- `vercel` CLI installed and `vercel login` completed (or the GitHub-integration flow, below).
- A Fly Postgres cluster is required before the first `api`/`worker` deploy (see below);
  `fly.sandbox.toml`'s app also needs `DATABASE_URL` attached, since `sieve.worker.remote`
  writes its findings back through the same queue tables as `sieve.worker.main`.

## First-time setup: Fly apps and Postgres

```bash
fly apps create sieve-api      # or whatever name you put in fly.toml's `app` key
fly apps create sieve-sandbox  # or whatever name you put in fly.sandbox.toml's `app` key

fly postgres create --name sieve-db --vm-size shared-cpu-1x --volume-size 10
fly postgres attach --app sieve-api sieve-db      # injects DATABASE_URL as a secret on sieve-api
fly postgres attach --app sieve-sandbox sieve-db  # same cluster, same secret, on the sandbox app
```

`fly postgres attach` sets `DATABASE_URL` as a Fly secret directly, it is never written to
either `fly.toml` or `fly.sandbox.toml`; `tests/deploy/test_fly_config.py` asserts neither file
sets it.

## Secrets

Every value below is a placeholder. Set them once per app before the first deploy, and again
whenever you rotate one (see "Secret rotation").

```bash
fly secrets set --app sieve-api \
  ANTHROPIC_API_KEY=<your-anthropic-api-key> \
  SNYK_TOKEN=<your-snyk-token> \
  SIEVE_IP_HASH_SALT=<a-long-random-per-deployment-salt>

fly secrets set --app sieve-sandbox \
  SNYK_TOKEN=<your-snyk-token> \
  SIEVE_IP_HASH_SALT=<the-same-salt-as-sieve-api>
```

`ANTHROPIC_API_KEY` is not needed on `sieve-sandbox` (the judge only runs from the api/worker
app's pipeline calls); it is listed here only so rotation instructions have one place to live.

## Deploying the API + worker app

```bash
# Fixture model (no real artefact yet, or you deliberately want the fixture classifier):
fly deploy --config fly.toml --image-label <git-sha-or-version>

# Real, pinned model artefact -- the build args flow into Dockerfile.api's `artefact` stage,
# which downloads it with huggingface_hub.snapshot_download and bakes it into the image, so the
# running container makes zero network calls to load the model at cold start:
fly deploy --config fly.toml \
  --image-label <git-sha-or-version> \
  --build-arg SIEVE_MODEL_ID=<you>/sieve-triage-deberta-v3-base \
  --build-arg SIEVE_MODEL_REVISION=<sha>
```

`--image-label` tags the built image with something a person can recognise later (a git commit
hash or a version string) instead of Fly's default `deployment-<id>` tag. That makes
"Rollback" below a matter of reading a label rather than hunting through opaque deployment ids.

`fly.toml`'s `[deploy] release_command` runs `uv run python -m sieve.db.migrate` (the same
`alembic upgrade head` wrapper the seed and the test suite use)
against the new release before it takes traffic. First deploy only, once migrations have run:

```bash
fly ssh console --app sieve-api -C "uv run python -m sieve.db.seed"
```

The seed is idempotent: it inserts the fixed-id sample scan
(`00000000-0000-0000-0000-000000005eed`) the landing page's "Try the sample" control points at,
replacing it in place on every run.

Check readiness once the release is up:

```bash
curl https://sieve-api.fly.dev/api/v1/readyz
# {"ok": true, "db": "ok", "model": "<model_id>@<revision-or-fixture>"}
```

`GET /api/v1/readyz` checks both the database (`SELECT 1`) and the model load
(`sieve/api/routes/health.py`); `fly.toml`'s `[[http_service.checks]]` polls the same route
every 15s so a broken release never takes traffic.

The retention purge (`sieve/worker/purge.py`, `SIEVE_SCAN_TTL_DAYS`) is a job the `worker`
process group re-enqueues after every run, it starts itself once the worker is live; there is
no separate deploy step for it.

## The sandbox worker (remote MCP fetches)

`fly.sandbox.toml` deploys `sieve.worker.remote`, the only process anywhere permitted to fetch
an untrusted MCP server, gated by `SIEVE_ALLOW_REMOTE_MCP=true` AND
`SIEVE_SANDBOXED=1` set together on this app alone. See the long comment block at the top of
`fly.sandbox.toml` for exactly which `docker-compose.yml` sandbox restrictions (`read_only`
rootfs, `cap_drop: [ALL]`, `security_opt: no-new-privileges`, `tmpfs: ["/tmp"]`) Fly Machines
can and cannot express; the short version is that Fly Machines are already unprivileged
Firecracker microVMs, which is a different isolation boundary, not a substitute for the
container-level controls compose expresses, and that the SSRF guard
(`sieve/worker/ssrf.py`, `sieve/worker/ssrf_client.py`) is doing the real work on this path
either way.

```bash
fly deploy --config fly.sandbox.toml --image-label <git-sha-or-version>
fly scale count sandbox=0 --app sieve-sandbox   # resting state: zero Machines running
```

**`sieve.worker.remote` only polls Postgres, it has no HTTP listener.** Fly's proxy-managed
`auto_stop_machines`/`auto_start_machines` require a service attached to real inbound traffic
(Fly's docs say worker machines without an `[http_service]` are not managed by the proxy for
autostop), so true traffic-driven,
on-demand wake cannot be wired for this app, adding a fake listener just to get the proxy
config would either be dishonest or would open an unneeded public port on the one app that
fetches untrusted servers. Scale it up manually around a batch of remote-MCP jobs instead:

```bash
fly scale count sandbox=1 --app sieve-sandbox   # before a batch that needs remote MCP fetches
# ... run the batch (enqueue jobs against the same Postgres cluster) ...
fly scale count sandbox=0 --app sieve-sandbox   # back to the resting, zero-cost state
```

**A `remote_url` scan posted while `sandbox=0` waits; it does not fail.** Both apps poll the same
`jobs` table, but they claim different **kinds**: `POST /scans` enqueues `scan.remote` for a
`remote_url` source and plain `scan` for everything else, `sieve.worker.main` claims
`("scan", "purge")`, and `sieve.worker.remote` claims `("scan.remote",)`. A claim filters on the
kind inside the same `FOR UPDATE SKIP LOCKED` statement, so the `api`/`worker` app cannot take a
job it would only refuse. Before this routing existed, the plain worker won the race, raised
"remote scanning disabled", requeued, and finally marked the job `dead`, which, with this
resting state, made the remote feature fail by construction rather than queue.

## Rollback

**There is no `fly releases rollback` command in current flyctl.** Fly's own Rollback Guide is
explicit that rolling back means redeploying a previous release's *image*, not invoking a
dedicated rollback verb. Roll either app back independently, in two steps:

```bash
# 1. See what you've shipped -- lists past releases with the exact image tag each one used.
fly releases --app sieve-api --image
fly releases --app sieve-sandbox --image

# 2. Redeploy the image from the release before things went sideways. If deploys were tagged
#    with --image-label (see "Deploying the API + worker app" above), use that readable tag;
#    otherwise use the auto-generated `deployment-<id>` tag `fly releases --image` printed.
fly deploy --image registry.fly.io/sieve-api:<git-sha-or-version> --app sieve-api
fly deploy --image registry.fly.io/sieve-sandbox:<git-sha-or-version> --app sieve-sandbox
```

Redeploying an old image restores that release's *code and image*; it does **not** roll back
the database. If the release you are rolling back past ran a migration that is not
backward-compatible, restoring that release's behaviour also means restoring a matching
Postgres snapshot, Fly Postgres snapshots are automatic and daily by default
(`fly postgres db list` / your cluster's backup settings); consult those before rolling back
across a migration boundary, not after. Rolling back restores the VM image, not the database.

## Secret rotation

```bash
fly secrets set --app sieve-api ANTHROPIC_API_KEY=<new-value>
fly secrets set --app sieve-api SNYK_TOKEN=<new-value>
fly secrets set --app sieve-sandbox SNYK_TOKEN=<new-value>
```

Each `fly secrets set` triggers a new release on that app automatically; no separate restart
step is needed.

`SIEVE_IP_HASH_SALT` is different: it must be set identically on `sieve-api` and
`sieve-sandbox` (both hash the same client IPs against `SIEVE_DAILY_SCANS_PER_IP`), and rotating
it is a **forward-only** change, `sieve/config.py`'s own comment notes the hash is
`SHA-256(salt + client_ip)`, a fast digest, not a KDF, so a rotation does not re-hash history:
today's rate-limit ledger keyed on the old salt simply ages out under `SIEVE_SCAN_TTL_DAYS`
rather than being reversible or re-derivable. Rotate it deliberately, not reflexively:

```bash
fly secrets set --app sieve-api SIEVE_IP_HASH_SALT=<new-long-random-value>
fly secrets set --app sieve-sandbox SIEVE_IP_HASH_SALT=<the-same-new-value>
```

`DATABASE_URL` is rotated by Fly Postgres, not by hand: `fly postgres users` /
`fly postgres attach --app <app> <cluster>` again after a password change re-injects it.

## Vercel (`web/`)

The Next.js app lives in `web/`, not the repository root, and `web/vercel.json` is the config
file, Vercel reads `vercel.json` from the project's configured **Root Directory**, not from
the repository root, once that setting points at a subdirectory (Vercel CLI source,
`packages/cli/src/commands/deploy/index.ts`: it re-reads local config from
`join(cwd, rootDirectory)`). The Root Directory itself is a **Project Setting**, not a
`vercel.json` field, there is no `rootDirectory` key `vercel.json` accepts; set it once when
the project is created:

- Dashboard: Project -> Settings -> General -> Root Directory -> `web`.
- Or non-interactively when linking: `vercel link --cwd web` from the repository root, or via
  the GitHub integration's project-creation flow (point it at this repo, set Root Directory to
  `web` in the same step).

Environment variables, set via the dashboard (Project -> Settings -> Environment Variables) or
`vercel env add <NAME> <environment>`, not via `vercel.json`, and not secrets, so plain
values are fine to type at the prompt:

| Variable | Where it is read | Value |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | `web/src/lib/config.ts` (`apiBase()`) | empty string, for same-origin (the deployed setup proxies `/api/*` to the Fly api app, or set the api app's public URL if it does not) |
| `API_INTERNAL_URL` | `web/src/lib/config.ts` (`serverApiBase()`) | `https://sieve-api.fly.dev`, the address a Vercel server component reaches the API on |
| `NEXT_PUBLIC_DEMO_SCAN_ID` | `web/src/lib/config.ts` (`demoScanId()`) | `00000000-0000-0000-0000-000000005eed`, the id `sieve.db.seed` inserts |

**Naming note:** `web/src/lib/config.ts` reads `NEXT_PUBLIC_API_BASE` (browser) and
`API_INTERNAL_URL` (server). No code in `web/` reads `NEXT_PUBLIC_API_URL`, so do not set that
name.

```bash
vercel deploy --prod   # from web/, once linked, or via the GitHub integration on push to main
```

## What has not been verified

- Not yet deployed: `fly deploy`, `fly postgres create`, `fly secrets set`, and `vercel deploy`
  above are documented, not executed.
- `docker build` was not available when this was written; `Dockerfile.api`'s new `artefact` stage
  was checked by careful reading (arg plumbing, the fixture no-op branch, the
  `snapshot_download(..., cache_dir=...)` call matching `sieve/model/infer.py`'s runtime call
  exactly) rather than by an actual build. Build it once Docker is available:
  `docker build -f Dockerfile.api --build-arg SIEVE_MODEL_ID=fixture -t sieve-api:check .`
  should complete without any network access; a non-fixture `SIEVE_MODEL_ID` will need one.
