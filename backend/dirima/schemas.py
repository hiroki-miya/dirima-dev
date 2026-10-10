from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVENT_TYPES = Literal[
    "run.created",
    "run.started",
    "run.status_changed",
    "message.completed",
    "tool.called",
    "tool.completed",
    "tool.failed",
    "artifact.created",
    "verification.reported",
    "run.completed",
    "run.failed",
]


class Source(BaseModel):
    adapter: str = Field(min_length=1, max_length=100)
    runtime: str = Field(min_length=1, max_length=100)
    external_run_id: str = Field(min_length=1, max_length=200)
    external_event_id: str | None = Field(default=None, min_length=1, max_length=200)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    event_id: UUID
    workspace_id: UUID
    project_id: UUID
    task_id: UUID
    run_id: UUID
    source: Source
    type: EVENT_TYPES
    occurred_at: datetime
    external_sequence: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def bounded_payload(self) -> Self:
        if self.type == "run.created":
            for name, limit in (
                ("agent_name", 100),
                ("model_provider", 100),
                ("model_name", 100),
                ("model_family", 100),
                ("prompt_lineage", 200),
            ):
                if name in self.payload and (
                    not isinstance(self.payload[name], str)
                    or not 1 <= len(self.payload[name]) <= limit
                ):
                    raise ValueError(f"{name} requires a string of 1..{limit} characters")
        if self.type == "artifact.created" and (
            not isinstance(self.payload.get("mime_type", "application/octet-stream"), str)
            or len(self.payload.get("mime_type", "application/octet-stream")) > 150
        ):
            raise ValueError("mime_type exceeds storage bounds")
        return self

    @field_validator("occurred_at")
    @classmethod
    def tz_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at requires a timezone")
        return value


class AgentCompletionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    status: Literal["succeeded", "failed", "cancelled"]
    summary: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    reported_findings: list[dict[str, Any]] = Field(default_factory=list)
    reported_verifications: list[dict[str, Any]] = Field(default_factory=list)
    reported_risks: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: list[str] = Field(min_length=1)
    exit_code: int
    stdout: str
    stderr: str
    observer: str = Field(min_length=1, max_length=100)

    @field_validator("command")
    @classmethod
    def command_required(cls, value: list[str]) -> list[str]:
        if any(not part.strip() for part in value):
            raise ValueError("Command arguments must not be empty")
        return value


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ConstraintCreate(BaseModel):
    statement: str = Field(min_length=1)
    type: Literal["HARD", "SOFT"]
    scope: str = Field(default="project", min_length=1, max_length=200)
    source: str = Field(default="human", min_length=1, max_length=200)


class TaskCreate(BaseModel):
    project_id: UUID
    goal_id: UUID | None = None
    title: str = Field(min_length=1, max_length=250)
    description: str = ""


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    description: str = ""


class ConstraintSupersede(BaseModel):
    statement: str = Field(min_length=1)
    actor: str = Field(min_length=1, max_length=200)


class DecisionInput(BaseModel):
    selected_option_id: UUID
    actor: str = Field(min_length=1, max_length=200)
    rationale: str = ""


class DismissInput(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1)


class ExtractedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="OBSERVATION", min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=250)
    claim: str = Field(min_length=1)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    evidence_refs: list[str] = Field(default_factory=list)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[ExtractedFinding]
    risks: list[Annotated[str, Field(min_length=1)]]
    unresolved: list[Annotated[str, Field(min_length=1)]]
    constraint_violations: list[Annotated[str, Field(min_length=1)]]


class ConflictClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conflict: bool
    reason: str
    finding_ids: list[UUID]
    conflict_type: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def needs_reason(self) -> Self:
        if self.conflict and (not self.reason or len(self.finding_ids) < 2):
            raise ValueError("Conflict requires reason and two findings")
        return self


class ClusterClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equivalent: bool
    reason: str
    finding_ids: list[UUID]


class ResolutionAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_finding_id: UUID | None
    requires_human: bool
    rationale: str


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    irreversible: bool
    reason: str


class ReceiptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str


class FindingCorrection(BaseModel):
    claim: str = Field(min_length=1)
    subject: str | None = Field(default=None, min_length=1, max_length=250)
    actor: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1)


class RiskOverride(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    actor: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1)
