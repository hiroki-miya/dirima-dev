"""Operational records. External agent text is data, never an instruction."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> uuid.UUID:
    return uuid.uuid4()


class Id:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)


class Stamp:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, nullable=False
    )


class Scoped:
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id"), nullable=False, index=True
    )


class Workspace(Base, Id, Stamp):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Project(Base, Id, Stamp, Scoped):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "name"),
    )


class Constraint(Base, Id, Stamp, Scoped):
    __tablename__ = "constraints"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), default="project", nullable=False)
    source: Mapped[str] = mapped_column(String(200), default="human", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("constraints.id"))
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "workspace_id"], ["projects.id", "projects.workspace_id"]
        ),
        CheckConstraint("type IN ('HARD','SOFT')"),
        CheckConstraint("status IN ('ACTIVE','SUPERSEDED')"),
    )


class Goal(Base, Id, Stamp, Scoped):
    __tablename__ = "goals"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "workspace_id"], ["projects.id", "projects.workspace_id"]
        ),
    )


class Task(Base, Id, Stamp, Scoped):
    __tablename__ = "tasks"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("goals.id"))
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','RUNNING','COMPLETED','FAILED','NEEDS_ATTENTION')",
            name="tasks_valid_status",
        ),
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["project_id", "workspace_id"], ["projects.id", "projects.workspace_id"]
        ),
    )


class Run(Base, Id, Stamp, Scoped):
    __tablename__ = "runs"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    runtime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    model_family: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    prompt_lineage: Mapped[str] = mapped_column(String(200), default="unknown", nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="CREATED", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    extraction_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "runtime_type", "external_run_id"),
        ForeignKeyConstraint(["task_id", "workspace_id"], ["tasks.id", "tasks.workspace_id"]),
        CheckConstraint(
            "status IN ('CREATED','RUNNING','WAITING','SUCCEEDED','FAILED','CANCELLED','UNKNOWN')"
        ),
    )


class RunEvent(Base, Id, Stamp, Scoped):
    __tablename__ = "run_events"
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    source_adapter: Mapped[str] = mapped_column(String(100), nullable=False)
    source_runtime: Mapped[str] = mapped_column(String(100), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_sequence: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(206), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "type IN ('run.created','run.started','run.status_changed','message.completed','tool.called','tool.completed','tool.failed','artifact.created','verification.reported','run.completed','run.failed')",
            name="run_events_valid_type",
        ),
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
        UniqueConstraint("source_adapter", "external_run_id", "idempotency_key"),
        UniqueConstraint("source_adapter", "external_run_id", "external_event_id"),
    )


class Artifact(Base, Id, Stamp, Scoped):
    __tablename__ = "artifacts"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by_run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("size_bytes >= 0"),
        CheckConstraint("storage_key = sha256", name="artifacts_storage_digest"),
    )


class AgentReceipt(Base, Id, Stamp, Scoped):
    __tablename__ = "agent_receipts"
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
    )


class Finding(Base, Id, Stamp, Scoped):
    __tablename__ = "findings"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(12), default="LOW", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("finding_clusters.id"))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("findings.id"))
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "workspace_id"], ["runs.id", "runs.workspace_id"]),
        CheckConstraint("status IN ('ACTIVE','SUPERSEDED','RETRACTED')"),
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH','CRITICAL')"),
    )


class FindingCluster(Base, Id, Stamp, Scoped):
    __tablename__ = "finding_clusters"
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    topic_key: Mapped[str] = mapped_column(String(300), nullable=False)
    claim_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','AUTO_RESOLVED')", name="finding_clusters_valid_status"),
        ForeignKeyConstraint(["task_id", "workspace_id"], ["tasks.id", "tasks.workspace_id"]),
        UniqueConstraint("task_id", "topic_key", "claim_key"),
    )


class Evidence(Base, Id, Stamp, Scoped):
    __tablename__ = "evidence"
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("artifacts.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    __table_args__ = (
        CheckConstraint(
            "type IN ('USER_CONSTRAINT','SOURCE_CODE','TEST_RESULT','RUNTIME_OBSERVATION','OFFICIAL_SPEC','ARTIFACT','AGENT_ASSERTION')"
        ),
    )


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("findings.id"), primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence.id"), primary_key=True
    )


class Verification(Base, Id, Stamp, Scoped):
    __tablename__ = "verifications"
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), nullable=False)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("findings.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    performed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','PASSED','FAILED','INCONCLUSIVE','ERROR')"),
    )


class Conflict(Base, Id, Stamp, Scoped):
    __tablename__ = "conflicts"
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(300), nullable=False)
    finding_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    conflict_type: Mapped[str] = mapped_column(
        String(100), default="INCOMPATIBLE_CLAIMS", nullable=False
    )
    reason: Mapped[str] = mapped_column(
        Text, default="Claims for the same subject differ", nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    resolution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("resolutions.id", use_alter=True, name="conflicts_resolution_id_fkey")
    )
    __table_args__ = (
        CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="conflicts_valid_severity"
        ),
        Index(
            "conflicts_one_open_topic",
            "task_id",
            "topic_key",
            unique=True,
            postgresql_where=status == "OPEN",
        ),
        CheckConstraint("status IN ('OPEN','AUTO_RESOLVED','HUMAN_RESOLVED','INVALIDATED')"),
    )


class Resolution(Base, Id, Stamp, Scoped):
    __tablename__ = "resolutions"
    conflict_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("conflicts.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    selected_finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("findings.id"))
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class Risk(Base, Id, Stamp, Scoped):
    __tablename__ = "risks"
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), nullable=False)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("findings.id"))
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    irreversible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (CheckConstraint("level IN ('LOW','MEDIUM','HIGH','CRITICAL')"),)


class DecisionCase(Base, Id, Stamp, Scoped):
    __tablename__ = "decision_cases"
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), nullable=False)
    conflict_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("conflicts.id"))
    finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("findings.id"))
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reason_human_required: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(12), nullable=False)
    reversibility: Mapped[str] = mapped_column(String(30), nullable=False)
    attention_class: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="AWAITING_HUMAN", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="decision_cases_valid_risk"
        ),
        CheckConstraint(
            "attention_class IN ('HUMAN_REQUIRED','BLOCKING')",
            name="decision_cases_valid_attention",
        ),
        CheckConstraint("status IN ('OPEN','AWAITING_HUMAN','RESOLVED','DISMISSED','SUPERSEDED')"),
    )


class DecisionOption(Base, Id, Scoped):
    __tablename__ = "decision_options"
    decision_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("decision_cases.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    opposing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    downstream_impact: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reversibility: Mapped[str] = mapped_column(String(30), nullable=False)


class HumanDecision(Base, Id, Scoped):
    __tablename__ = "human_decisions"
    decision_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("decision_cases.id"), nullable=False, unique=True
    )
    selected_option_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("decision_options.id"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, nullable=False
    )


class AttentionItem(Base, Id, Stamp, Scoped):
    __tablename__ = "attention_items"
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tasks.id"))
    decision_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("decision_cases.id")
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="OPEN", nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('OPEN','CLOSED')", name="attention_items_valid_status"),
        CheckConstraint("classification IN ('ATTENTION','HUMAN_REQUIRED','BLOCKING')"),
    )


class CompletionReceipt(Base, Id, Stamp, Scoped):
    __tablename__ = "completion_receipts"
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id"), nullable=False, unique=True
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AuditLog(Base, Id, Stamp, Scoped):
    __tablename__ = "audit_logs"
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ModelCall(Base, Id, Scoped):
    __tablename__ = "model_calls"
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    cost: Mapped[float | None] = mapped_column(Numeric(12, 6))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(100), nullable=False)


class Job(Base, Id, Stamp, Scoped):
    __tablename__ = "jobs"
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="READY", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, nullable=False
    )
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        CheckConstraint("status IN ('READY','RUNNING','COMPLETED','FAILED','DEAD')"),
        Index("ix_jobs_claim", "status", "available_at"),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
