"""Fly and Vercel deployment configuration (Task 11.4).

Configs only: nothing in this repository or this test suite runs `fly deploy` or `vercel`.
`fly.toml` (the `api`/`worker` app) and `fly.sandbox.toml` (the sandbox worker, the ONLY
process anywhere allowed to fetch an untrusted MCP server) are asserted against
docker-compose.yml's env gates -- PRD SC-1 requires `SIEVE_ALLOW_REMOTE_MCP=true` AND
`SIEVE_SANDBOXED=1` together, on the sandbox app only -- and against secrets never landing in
a file this repository commits.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FLY_TOML = REPO / "fly.toml"
FLY_SANDBOX_TOML = REPO / "fly.sandbox.toml"
DEPLOY_DOC = REPO / "docs" / "deploy.md"
VERCEL_JSON = REPO / "web" / "vercel.json"
DEPLOY_FILES = (FLY_TOML, FLY_SANDBOX_TOML, DEPLOY_DOC, VERCEL_JSON)

#: Secrets belong to `fly secrets set`, never to a committed `[env]` table (PRD SC-1's
#: discipline applied to credentials too: a committed file cannot leak what it never holds).
SECRET_ENV_KEYS = {"ANTHROPIC_API_KEY", "DATABASE_URL", "SNYK_TOKEN", "SIEVE_IP_HASH_SALT"}

#: The shape a live credential would take. A real Anthropic key, a Postgres URL whose password
#: is not the dev-only literal "sieve" docker-compose.yml also uses, or a Snyk token (a UUID)
#: actually assigned to `SNYK_TOKEN` rather than named in prose.
SECRET_SHAPED = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}"
    r"|postgresql\+psycopg://[^:/@]+:(?!sieve\b)[^@/]{4,}@"
    r"|SNYK_TOKEN\s*[=:]\s*[\"']?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: `SIEVE_IP_HASH_SALT` assigned to something other than a `<placeholder>` or the dev-only
#: default `.env.example` ships (`change-me-per-deployment`) would be a real, deployment-specific
#: secret value landing in a committed file.
IP_HASH_SALT_ASSIGNMENT = re.compile(r"SIEVE_IP_HASH_SALT\s*[=:]\s*[\"']?([^\"'\s,]+)")
_ALLOWED_IP_HASH_SALT_VALUES = {"change-me-per-deployment"}


def _looks_like_a_placeholder(value: str) -> bool:
    value = value.rstrip(">\"')")
    return value.startswith("<") or value in _ALLOWED_IP_HASH_SALT_VALUES


def _load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _services(config: dict[str, Any]) -> list[dict[str, Any]]:
    http = config.get("http_service")
    plain = config.get("services") or []
    plain = plain if isinstance(plain, list) else [plain]
    return ([http] if http else []) + plain


def test_fly_toml_declares_the_api_and_worker_process_groups() -> None:
    config = _load(FLY_TOML)
    assert config["processes"].keys() == {"api", "worker"}, config["processes"]


def test_fly_toml_sizes_the_vm_at_2_vcpu_2gb() -> None:
    config = _load(FLY_TOML)
    vms = config["vm"] if isinstance(config["vm"], list) else [config["vm"]]
    assert vms, "fly.toml declares no [[vm]] block"
    for vm in vms:
        assert vm.get("cpus") == 2, vm
        assert str(vm.get("memory", "")).lower() == "2gb", vm


def test_fly_toml_health_checks_the_readyz_route() -> None:
    config = _load(FLY_TOML)
    checks = config["http_service"]["checks"]
    checks = checks if isinstance(checks, list) else [checks]
    paths = {c["path"] for c in checks}
    assert "/api/v1/readyz" in paths, paths


def test_the_api_app_serves_only_the_api_process_over_http() -> None:
    """`worker` never gets a public listener -- it matches docker-compose.yml's `worker`."""
    config = _load(FLY_TOML)
    assert config["http_service"]["processes"] == ["api"]


def test_sandbox_app_flips_both_remote_mcp_gates_and_the_api_app_does_not() -> None:
    sandbox_env = _load(FLY_SANDBOX_TOML).get("env", {})
    api_env = _load(FLY_TOML).get("env", {})

    assert sandbox_env.get("SIEVE_ALLOW_REMOTE_MCP") == "true"
    assert sandbox_env.get("SIEVE_SANDBOXED") == "1"

    # Both gates pinned the opposite way on the api/worker app -- not merely unset, exactly
    # like docker-compose.yml pins `"false"`/`"0"` rather than leaving them to inherit.
    assert api_env.get("SIEVE_ALLOW_REMOTE_MCP") == "false"
    assert api_env.get("SIEVE_SANDBOXED") == "0"


def test_sandbox_app_has_no_standing_service_and_documents_scale_to_zero() -> None:
    """`sieve.worker.remote` only polls Postgres; it has no HTTP listener (grep confirmed no

    `uvicorn`/`aiohttp`/socket bind anywhere under sieve/worker/). A `[[services]]` block here
    would either be fake (no real listener behind it) or open a needless public port on the
    one app that fetches untrusted servers, so scale-to-zero for this app is a documented,
    manual operator action instead of a Fly proxy feature -- and this asserts it stays that way
    and is written down, rather than asserting a proxy config this app cannot honestly have.
    """
    config = _load(FLY_SANDBOX_TOML)
    assert not _services(config), "the sandbox worker has no listener; it should have no service"
    text = FLY_SANDBOX_TOML.read_text(encoding="utf-8")
    assert "scale count sandbox=0" in text, "must document the scale-to-zero resting state"
    assert "scale-to-zero" in text.lower()


def test_the_two_fly_apps_are_actually_different_apps() -> None:
    assert _load(FLY_TOML)["app"] != _load(FLY_SANDBOX_TOML)["app"]


def test_no_secret_value_appears_in_any_committed_deploy_file() -> None:
    for path in DEPLOY_FILES:
        text = path.read_text(encoding="utf-8")
        assert not SECRET_SHAPED.search(text), f"{path} looks like it embeds a real secret"

        for value in IP_HASH_SALT_ASSIGNMENT.findall(text):
            assert _looks_like_a_placeholder(value), (
                f"{path} assigns SIEVE_IP_HASH_SALT a concrete-looking value: {value!r}"
            )

    for path in (FLY_TOML, FLY_SANDBOX_TOML):
        env = _load(path).get("env", {})
        leaked = SECRET_ENV_KEYS & env.keys()
        assert not leaked, f"{path} sets {leaked} directly instead of via `fly secrets set`"


def test_deploy_doc_shows_rollback_and_secret_rotation_with_placeholders_only() -> None:
    """`fly releases rollback` does not exist in current flyctl (Fly's own Rollback Guide:

    rolling back means redeploying a previous release's image, via `fly releases --image` to
    find it and `fly deploy --image` to redeploy it). The doc may still name the nonexistent
    command in prose to warn readers off it, so this only guards against it appearing as a
    runnable command line, and otherwise asserts the real commands are the ones documented.
    """
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert not any(line.strip().startswith("fly releases rollback") for line in lines), (
        "fly releases rollback is not a real flyctl command; see Fly's Rollback Guide"
    )
    assert "fly releases --app" in text and "--image" in text
    assert "fly deploy --image " in text
    assert "fly secrets set" in text
    for key in SECRET_ENV_KEYS:
        assert key in text, f"docs/deploy.md never mentions rotating {key}"
