from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SIEVE_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # `validation_alias` (not `alias`) is what reads an env var that does not carry the prefix;
    # `alias` would also rename the constructor argument, and `populate_by_name=True` keeps the
    # Python name working in tests (`Settings(database_url=...)`).
    database_url: str = Field(
        default="postgresql+psycopg://sieve:sieve@localhost:5432/sieve",
        validation_alias="DATABASE_URL",
    )
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    judge_model: str = "claude-haiku-4-5"
    judge_enabled: bool = True
    judge_max_calls_per_scan: int = 50
    daily_judge_budget_usd: float = 2.00
    daily_scans_per_ip: int = 20
    model_id: str = "fixture"
    model_revision: str | None = None
    model_cache: str = "~/.cache/sieve"
    use_onnx: bool = True
    allow_remote_mcp: bool = False
    remote_timeout_seconds: int = 10
    max_tools_per_scan: int = 500
    scan_ttl_days: int = 7
    trust_proxy: bool = False
    ip_hash_salt: str = "change-me-per-deployment"  # PRD §13.2: SHA-256(salt + client_ip)
    log_level: str = "INFO"
    snyk_max_invocations: int = 25
    web_origin: str = Field(default="http://localhost:3000", validation_alias="WEB_ORIGIN")
    # Task 11.2: the worker's verdict-insert batching (`sieve.worker.scan_job._VerdictBatch`) is
    # bounded by size (25 rows) AND time, so a small scan still fills the results page and the
    # sieve animation per tool (PRD §14.4) instead of waiting for a 25-row batch that a short
    # scan may never reach. This is the only place the interval is defined; the worker reads it,
    # never a literal.
    verdict_flush_ms: int = 250


settings = Settings()
