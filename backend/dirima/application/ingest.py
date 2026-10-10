"""Transactional, idempotent GenericAdapter ingestion."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from dirima import models as m
from dirima.policy.core import TERMINAL, transition
from dirima.schemas import AgentCompletionReceipt, EventEnvelope
from dirima.security.redaction import redact


def audit(
    db: Session,
    workspace_id: UUID,
    actor: str,
    action: str,
    target_type: str,
    target_id: UUID | None,
    detail: dict[str, Any],
) -> None:
    db.add(
        m.AuditLog(
            workspace_id=workspace_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=redact(detail),
        )
    )


def enqueue(db: Session, workspace_id: UUID, job_type: str, payload: dict[str, Any]) -> None:
    db.add(m.Job(workspace_id=workspace_id, job_type=job_type, payload=payload))


def identity_key(event: EventEnvelope) -> str:
    """Prefer a source event ID when the adapter provides one."""
    return (
        f"event:{event.source.external_event_id}"
        if event.source.external_event_id
        else f"key:{event.idempotency_key}"
    )


def existing_event(db: Session, event: EventEnvelope) -> m.RunEvent | None:
    return db.scalar(
        select(m.RunEvent).where(
            or_(
                m.RunEvent.event_id == event.event_id,
                (m.RunEvent.source_adapter == event.source.adapter)
                & (m.RunEvent.external_run_id == event.source.external_run_id)
                & (m.RunEvent.idempotency_key == identity_key(event)),
            )
        )
    )


def store_artifact(
    db: Session, run: m.Run, *, name: str, mime_type: str, content: bytes
) -> m.Artifact:
    digest = hashlib.sha256(content).hexdigest()
    root = Path(os.environ.get("DIRIMA_ARTIFACT_DIR", "data/artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / digest
    if not target.exists():
        temp = root / f".{digest}.{m.uid()}.tmp"
        temp.write_bytes(content)
        os.replace(temp, target)
    artifact = m.Artifact(
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        task_id=run.task_id,
        sha256=digest,
        mime_type=mime_type,
        size_bytes=len(content),
        original_name=Path(name).name[:255],
        storage_key=digest,
        created_by_run_id=run.id,
    )
    db.add(artifact)
    db.flush()
    return artifact


def ingest_event(db: Session, event: EventEnvelope, workspace_id: UUID) -> dict[str, Any]:
    if event.workspace_id != workspace_id:
        raise ValueError("Event workspace does not match authenticated workspace")
    duplicate = existing_event(db, event)
    if duplicate:
        if duplicate.workspace_id != workspace_id:
            raise ValueError("Event identity belongs to another workspace")
        result = {"event_id": str(duplicate.event_id), "duplicate": True}
        if duplicate.payload.get("artifact_id"):
            result["artifact_id"] = duplicate.payload["artifact_id"]
        return result
    task = db.scalar(select(m.Task).where(m.Task.id == event.task_id).with_for_update())
    if not task or task.workspace_id != workspace_id or task.project_id != event.project_id:
        raise ValueError("Task does not belong to project and workspace")
    run = db.get(m.Run, event.run_id)
    if event.type == "run.created":
        if run:
            raise ValueError("Run already exists")
        data = event.payload
        previous_id = (
            UUID(str(data["supersedes_run_id"])) if data.get("supersedes_run_id") else None
        )
        if previous_id:
            previous = db.get(m.Run, previous_id)
            if not previous or previous.task_id != task.id or previous.status not in TERMINAL:
                raise ValueError("Superseded Run must be terminal and belong to the same Task")
            findings = db.scalars(
                select(m.Finding).where(
                    m.Finding.run_id == previous_id, m.Finding.status == "ACTIVE"
                )
            ).all()
            for finding in findings:
                finding.status = "SUPERSEDED"
                for case in db.scalars(
                    select(m.DecisionCase).where(
                        m.DecisionCase.finding_id == finding.id,
                        m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
                    )
                ).all():
                    case.status, case.resolved_at = "SUPERSEDED", m.now()
                    audit(
                        db,
                        workspace_id,
                        "adapter",
                        "decision_case.supersede",
                        "decision_case",
                        case.id,
                        {"supersedes_run_id": str(previous_id)},
                    )
                for notice in db.scalars(
                    select(m.AttentionItem).where(
                        m.AttentionItem.task_id == task.id,
                        m.AttentionItem.kind == f"FINDING_ATTENTION:{finding.id}",
                        m.AttentionItem.status == "OPEN",
                    )
                ).all():
                    notice.status = "CLOSED"
            for notice in db.scalars(
                select(m.AttentionItem).where(
                    m.AttentionItem.task_id == task.id,
                    m.AttentionItem.kind.in_(
                        [f"RUN_FAILED:{previous_id}", f"RUN_CANCELLED:{previous_id}"]
                    ),
                    m.AttentionItem.status == "OPEN",
                )
            ).all():
                notice.status = "CLOSED"
        task.status = "RUNNING"
        run = m.Run(
            id=event.run_id,
            workspace_id=workspace_id,
            project_id=event.project_id,
            task_id=event.task_id,
            runtime_type=event.source.runtime,
            agent_name=str(data.get("agent_name", event.source.adapter)),
            model_provider=str(data.get("model_provider", "unknown")),
            model_name=str(data.get("model_name", "unknown")),
            model_family=str(data.get("model_family", "unknown")),
            prompt_lineage=str(data.get("prompt_lineage", "unknown")),
            external_run_id=event.source.external_run_id,
            status="CREATED",
            supersedes_run_id=previous_id,
        )
        db.add(run)
        audit(
            db,
            workspace_id,
            "adapter",
            "run.import",
            "run",
            run.id,
            {"adapter": event.source.adapter},
        )
    elif (
        not run
        or run.workspace_id != workspace_id
        or run.task_id != event.task_id
        or run.external_run_id != event.source.external_run_id
    ):
        raise ValueError("Run identity does not match event")
    assert run is not None
    receipt = (
        AgentCompletionReceipt.model_validate(event.payload["receipt"])
        if "receipt" in event.payload and event.type in {"run.completed", "run.failed"}
        else None
    )
    if event.type in {"run.started", "run.completed", "run.failed", "run.status_changed"}:
        status = {
            "run.started": "RUNNING",
            "run.completed": "SUCCEEDED",
            "run.failed": "FAILED",
        }.get(event.type)
        if receipt:
            status = {"succeeded": "SUCCEEDED", "failed": "FAILED", "cancelled": "CANCELLED"}[
                receipt.status
            ]
        status = status or event.payload.get("status")
        if status:
            if run.status in TERMINAL and status == run.status:
                pass  # A late event is appendable without changing terminal state.
            else:
                transition(run.status, status)
                run.status = status
                if status == "RUNNING" and run.started_at is None:
                    run.started_at = event.occurred_at
                if status in TERMINAL:
                    run.ended_at = event.occurred_at
    if receipt:
        previous_receipt = db.scalar(select(m.AgentReceipt).where(m.AgentReceipt.run_id == run.id))
        normalized = receipt.model_dump(mode="json")
        if previous_receipt and previous_receipt.content != normalized:
            raise ValueError("Correct a submitted receipt with a superseding Run")
        if not previous_receipt:
            db.add(m.AgentReceipt(workspace_id=workspace_id, run_id=run.id, content=normalized))
    stored_payload = dict(event.payload)
    artifact = None
    if event.type == "artifact.created" and "content_base64" in event.payload:
        try:
            artifact_bytes = base64.b64decode(event.payload["content_base64"], validate=True)
        except Exception as exc:
            raise ValueError("Invalid artifact base64") from exc
        artifact = store_artifact(
            db,
            run,
            name=str(event.payload.get("name", "artifact")),
            mime_type=str(event.payload.get("mime_type", "application/octet-stream")),
            content=artifact_bytes,
        )
        stored_payload.pop("content_base64")
        stored_payload.update(
            artifact_id=str(artifact.id), sha256=artifact.sha256, size_bytes=artifact.size_bytes
        )
    db.add(
        m.RunEvent(
            event_id=event.event_id,
            workspace_id=workspace_id,
            project_id=event.project_id,
            task_id=event.task_id,
            run_id=event.run_id,
            schema_version=event.schema_version,
            source_adapter=event.source.adapter,
            source_runtime=event.source.runtime,
            external_run_id=event.source.external_run_id,
            external_event_id=event.source.external_event_id,
            type=event.type,
            occurred_at=event.occurred_at,
            external_sequence=event.external_sequence,
            idempotency_key=identity_key(event),
            payload=stored_payload,
        )
    )
    enqueue(db, workspace_id, "reduce_task", {"task_id": str(task.id)})
    db.flush()
    result = {"event_id": str(event.event_id), "duplicate": False}
    if artifact:
        result["artifact_id"] = str(artifact.id)
    return result
