"""Single source of truth for every wire and corpus type. Changes here are contracts PRs."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

ScannerName = Literal["cisco_yara", "cisco_llm", "snyk_agent_scan", "sarif_in", "static_json"]
Severity = Literal["low", "medium", "high", "critical"]
EvidenceField = Literal[
    "name", "title", "description", "inputSchema", "outputSchema", "annotations"
]
Verdict = Literal["confirmed", "needs_review", "dismissed"]
Stage = Literal["classifier", "judge", "judge_failed", "judge_budget_exhausted"]
SubLabel = Literal[
    "exfiltration",
    "instruction_override",
    "tool_shadowing",
    "privilege_escalation",
    "concealment",
    "secret_solicitation",
    "command_execution",
]
Label = Literal["benign", "imperative_benign", "poisoned"]
LABELS: tuple[Label, Label, Label] = ("benign", "imperative_benign", "poisoned")
Difficulty = Literal["easy", "medium", "hard"]
SourceType = Literal["tools_json", "mcp_config", "scanner_report", "remote_url"]
ScanStatus = Literal["queued", "running", "complete", "failed"]
CorpusSource = Literal["public_repo", "synthetic_mutation", "wild_fp", "real_poisoned"]
CaptureMethod = Literal[
    "static_py", "static_ts", "sandbox_tools_list", "authored", "published_artifact"
]
SchemaFidelity = Literal["exact", "approx"]
Concealment = Literal["none", "html_comment", "zero_width", "bidi", "whitespace"]
Split = Literal["dev", "holdout", "eval_only"]


class ToolDefinition(BaseModel):
    """An MCP tool object. Extra spec fields (icons, execution, _meta) are preserved."""

    model_config = ConfigDict(extra="allow")

    name: str
    title: str | None = None
    description: str | None = None
    inputSchema: dict[str, Any]  # noqa: N815 (MCP wire name)
    outputSchema: dict[str, Any] | None = None  # noqa: N815
    annotations: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not TOOL_NAME_RE.match(v):
            raise ValueError("tool name must match [A-Za-z0-9_.-]{1,128}")
        return v

    @field_validator("inputSchema")
    @classmethod
    def _schema_is_object(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "type" in v and v["type"] != "object":
            raise ValueError("inputSchema must be a JSON Schema of type 'object'")
        return v


class ToolRecord(BaseModel):
    """A tool plus the context Sieve treats as features."""

    id: str
    server_name: str
    tool: ToolDefinition
    sibling_tool_names: list[str] = Field(default_factory=list)
    server_instructions: str | None = None


class ScannerFinding(BaseModel):
    scanner: ScannerName
    scanner_version: str
    rule_id: str | None
    severity: Severity | None
    message: str | None
    raw: dict[str, Any]


class EvidenceSpan(BaseModel):
    field: EvidenceField
    path: str | None  # JSON pointer inside the field, e.g. "/properties/path/description"
    quote: str
    start: int | None  # None when the quote could not be located
    end: int | None


class TriageResult(BaseModel):
    verdict: Verdict
    stage: Stage
    predicted_class: Label  # the classifier's argmax; the reason a dismissal is a dismissal
    class_probabilities: dict[Label, float]  # calibrated, sums to 1
    poisoned_probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    sub_labels: list[SubLabel]
    owasp: list[str]
    evidence_spans: list[EvidenceSpan]
    reasoning: str | None
    remediation: str | None
    scanner_findings: list[ScannerFinding]
    model_version: str
    judge_model: str | None
    latency_ms: int

    @model_validator(mode="after")
    def _probabilities_are_consistent(self) -> TriageResult:
        if set(self.class_probabilities) != set(LABELS):
            raise ValueError(f"class_probabilities keys must be exactly {LABELS}")
        total = sum(self.class_probabilities.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"class_probabilities must sum to 1, got {total}")
        if abs(self.class_probabilities["poisoned"] - self.poisoned_probability) > 1e-6:
            raise ValueError("poisoned_probability must equal class_probabilities['poisoned']")
        return self


class ScanCreate(BaseModel):
    source_type: SourceType
    tools_json: dict[str, Any] | None = None
    mcp_config: dict[str, Any] | None = None
    scanner_report: dict[str, Any] | None = None
    remote_url: str | None = None
    scanner: ScannerName | None = None


class ScanSummary(BaseModel):
    id: str
    status: ScanStatus
    source_type: SourceType
    tool_count: int
    scanner_flagged: int
    confirmed: int
    needs_review: int
    dismissed: int
    judge_calls: int
    judge_cost_usd: float
    #: "usage" when the figure is the API's own token counts, "estimate" for PRD §12.4's
    #: model (a judge that reports no usage), null when nothing was judged.
    judge_cost_source: str | None = None
    model_version: str | None
    judge_model: str | None
    created_at: datetime
    expires_at: datetime
    error: str | None = None


class Finding(BaseModel):
    id: str
    scan_id: str
    tool: ToolDefinition
    server_name: str | None
    sibling_tool_names: list[str]
    result: TriageResult


class FindingPage(BaseModel):
    items: list[Finding]
    total: int
    next_cursor: str | None


class FeedbackCreate(BaseModel):
    agrees: bool
    note: str | None = Field(default=None, max_length=1000)
    website: str | None = None  # honeypot; must be empty


class ModelInfo(BaseModel):
    model_version: str
    t_low: float
    t_high: float
    temperature: float
    onnx: bool
    judge_model: str | None
    judge_enabled: bool


class BenchSummary(BaseModel):
    """Served verbatim from bench/results; the web never computes numbers."""

    dataset_version: str
    model_version: str
    headline: dict[str, Any]
    tables: dict[str, Any]
    generated_at: datetime
    provisional: bool = False


class CorpusRecord(BaseModel):
    id: str
    source: CorpusSource
    source_repo: str
    source_commit: str
    source_license: str
    capture_method: CaptureMethod
    schema_fidelity: SchemaFidelity
    server_name: str
    tool: ToolDefinition
    sibling_tool_names: list[str] = Field(default_factory=list)
    server_instructions: str | None = None
    label: Label
    sub_labels: list[SubLabel] = Field(default_factory=list)
    difficulty: Difficulty | None = None  # semantic tier (PRD §9.5), set at labelling time
    concealment: Concealment = "none"  # orthogonal to tier; crossed with it in reporting
    payload_fields: list[EvidenceField] = Field(default_factory=list)
    mutation_of: str | None = None
    generator_model: str | None = None  # "human", or model id for LLM, or None for real text
    label_rationale: str | None = None
    labeller: str
    labelled_at: datetime
    second_label: Label | None = None
    adjudicated: bool = False
    split: Split | None = None
    fold: int | None = Field(default=None, ge=0, le=4)  # 5-fold index; only for split == "dev"

    def to_tool_record(self) -> ToolRecord:
        return ToolRecord(
            id=self.id,
            server_name=self.server_name,
            tool=self.tool,
            sibling_tool_names=self.sibling_tool_names,
            server_instructions=self.server_instructions,
        )
