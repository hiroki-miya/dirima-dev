"""Authenticated REST API; startup never creates schema."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dirima import models as m
from dirima.application.ingest import audit, enqueue, existing_event, ingest_event
from dirima.db import engine, session_scope
from dirima.infrastructure.logging import configure
from dirima.policy.core import TERMINAL, classify_risk
from dirima.reduction.pipeline import reduce_task
from dirima.schemas import (
    ConstraintCreate,
    ConstraintSupersede,
    DecisionInput,
    DismissInput,
    EventEnvelope,
    FindingCorrection,
    GoalCreate,
    ProjectCreate,
    RiskOverride,
    TaskCreate,
)
from dirima.security.redaction import redact

logger = configure("api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_security()
    with Session(bind=engine()) as db:
        workspace = configured_workspace()
        if not db.get(m.Workspace, workspace):
            db.add(m.Workspace(id=workspace, name="Default workspace"))
            db.flush()
        snapshot = configuration()
        previous = db.scalar(
            select(m.AuditLog)
            .where(
                m.AuditLog.workspace_id == workspace, m.AuditLog.action == "configuration.change"
            )
            .order_by(m.AuditLog.created_at.desc())
            .limit(1)
        )
        if not previous or previous.detail != snapshot:
            audit(db, workspace, "system", "configuration.change", "configuration", None, snapshot)
        db.commit()
    yield


app = FastAPI(title="Dirima API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {k: v for k, v in error.items() if k not in {"input", "ctx"}} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": redact(jsonable_encoder(errors))})


@app.middleware("http")
async def request_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "request completed",
        extra={
            "service": "api",
            "trace_id": trace_id,
            "workspace_id": os.environ.get("DIRIMA_WORKSPACE_ID"),
            "event": "http.request",
            "status": response.status_code,
        },
    )
    return response


def configured_workspace() -> uuid.UUID:
    return uuid.UUID(os.environ["DIRIMA_WORKSPACE_ID"])


def configuration() -> dict[str, Any]:
    return {
        "auth_mode": os.environ.get("DIRIMA_AUTH_MODE", "token"),
        "provider": os.environ.get("DIRIMA_MODEL_PROVIDER", "compatible"),
        "endpoint_configured": bool(
            os.environ.get("DIRIMA_MODEL_BASE_URL") and os.environ.get("DIRIMA_MODEL_API_KEY")
        ),
        "roles": {
            role: os.environ.get(f"DIRIMA_MODEL_{role.upper()}") or None
            for role in (
                "extractor",
                "cluster",
                "conflict_detector",
                "resolver",
                "risk_classifier",
                "receipt_generator",
            )
        },
        "adapters": ["generic"],
        "durevi_enabled": False,
    }


def auth(
    authorization: str | None = Header(None), x_workspace_id: str | None = Header(None)
) -> uuid.UUID:
    workspace_id = configured_workspace()
    if x_workspace_id:
        try:
            if uuid.UUID(x_workspace_id) != workspace_id:
                raise ValueError("Different workspace")
        except ValueError:
            raise HTTPException(403, "Workspace access denied") from None
    mode = os.environ.get("DIRIMA_AUTH_MODE", "token")
    if mode == "token":
        token = os.environ.get("DIRIMA_TOKEN", "")
        if not token:
            raise HTTPException(503, "Authentication token is not configured")
        if not authorization or not hmac.compare_digest(
            authorization.encode(), f"Bearer {token}".encode()
        ):
            raise HTTPException(
                401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"}
            )
    elif mode != "none":
        raise HTTPException(503, "Invalid authentication mode")
    return workspace_id


@app.get("/api/v1/configuration")
def get_configuration(workspace: uuid.UUID = Depends(auth)) -> dict[str, Any]:
    return cast(dict[str, Any], redact(configuration()))


def record(obj: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        redact(jsonable_encoder({c.name: getattr(obj, c.name) for c in obj.__table__.columns})),
    )


def owned(db: Session, cls: type, id: uuid.UUID, workspace: uuid.UUID) -> Any:
    row = db.get(cls, id)
    if not row or row.workspace_id != workspace:
        raise HTTPException(404, "Not found")
    return row


def locked_case(db: Session, id: uuid.UUID, workspace: uuid.UUID) -> m.DecisionCase:
    case = owned(db, m.DecisionCase, id, workspace)
    db.scalar(select(m.Task).where(m.Task.id == case.task_id).with_for_update())
    return cast(
        m.DecisionCase,
        db.scalar(
            select(m.DecisionCase)
            .where(m.DecisionCase.id == id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ),
    )


def check_security() -> None:
    mode = os.environ.get("DIRIMA_AUTH_MODE", "token")
    host = os.environ.get("DIRIMA_HOST", "127.0.0.1")
    if mode == "none" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Unauthenticated non-local bind is forbidden")
    if mode == "token" and not os.environ.get("DIRIMA_TOKEN"):
        raise RuntimeError("DIRIMA_TOKEN is required")
    configured_workspace()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(db: Session = Depends(session_scope)) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc


@app.get("/workerz")
def workerz(db: Session = Depends(session_scope)) -> dict[str, Any]:
    recent = db.scalar(
        select(m.WorkerHeartbeat.worker_id)
        .where(m.WorkerHeartbeat.seen_at > m.now() - timedelta(seconds=30))
        .limit(1)
    )
    if not recent:
        raise HTTPException(503, "Worker heartbeat missing")
    return {"status": "ready"}


@app.post("/api/v1/projects", status_code=201)
def create_project(
    body: ProjectCreate, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    if not db.get(m.Workspace, workspace):
        db.add(m.Workspace(id=workspace, name="Default workspace"))
        db.flush()
    project = m.Project(workspace_id=workspace, name=body.name, description=body.description)
    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Project name already exists") from None
    return record(project)


@app.get("/api/v1/projects")
def list_projects(
    workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    return [
        record(x)
        for x in db.scalars(select(m.Project).where(m.Project.workspace_id == workspace)).all()
    ]


@app.get("/api/v1/projects/{id}")
def get_project(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    return record(owned(db, m.Project, id, workspace))


@app.post("/api/v1/projects/{id}/constraints", status_code=201)
def create_constraint(
    id: uuid.UUID,
    body: ConstraintCreate,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    owned(db, m.Project, id, workspace)
    task_ids = db.scalars(
        select(m.Task.id).where(m.Task.project_id == id).order_by(m.Task.id).with_for_update()
    ).all()
    row = m.Constraint(
        workspace_id=workspace,
        project_id=id,
        statement=body.statement,
        type=body.type,
        scope=body.scope,
        source=body.source,
    )
    db.add(row)
    db.flush()
    db.add(
        m.Evidence(
            workspace_id=workspace,
            type="USER_CONSTRAINT",
            source_ref=f"constraint:{row.id}",
            content=body.statement,
        )
    )
    audit(
        db,
        workspace,
        "human",
        "constraint.create",
        "constraint",
        row.id,
        {"type": row.type, "statement": row.statement},
    )
    for task_id in task_ids:
        enqueue(db, workspace, "reduce_task", {"task_id": str(task_id)})
    db.commit()
    return record(row)


@app.get("/api/v1/projects/{id}/constraints")
def list_constraints(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    owned(db, m.Project, id, workspace)
    return [
        record(x)
        for x in db.scalars(
            select(m.Constraint).where(
                m.Constraint.workspace_id == workspace, m.Constraint.project_id == id
            )
        ).all()
    ]


@app.post("/api/v1/constraints/{id}/supersede", status_code=201)
def supersede_constraint(
    id: uuid.UUID,
    body: ConstraintSupersede,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    previous = db.scalar(
        select(m.Constraint)
        .where(m.Constraint.id == id, m.Constraint.workspace_id == workspace)
        .with_for_update()
    )
    if previous is None:
        raise HTTPException(404, "Constraint not found")
    if previous.status != "ACTIVE":
        raise HTTPException(409, "Constraint is already superseded")
    task_ids = db.scalars(
        select(m.Task.id)
        .where(m.Task.project_id == previous.project_id)
        .order_by(m.Task.id)
        .with_for_update()
    ).all()
    new = m.Constraint(
        workspace_id=workspace,
        project_id=previous.project_id,
        statement=body.statement,
        type=previous.type,
        scope=previous.scope,
        source=body.actor,
    )
    db.add(new)
    db.flush()
    previous.status, previous.superseded_by = "SUPERSEDED", new.id
    db.add(
        m.Evidence(
            workspace_id=workspace,
            type="USER_CONSTRAINT",
            source_ref=f"constraint:{new.id}",
            content=body.statement,
        )
    )
    audit(
        db,
        workspace,
        body.actor,
        "constraint.supersede",
        "constraint",
        id,
        {"superseded_by": str(new.id)},
    )
    for task_id in task_ids:
        enqueue(db, workspace, "reduce_task", {"task_id": str(task_id)})
    db.commit()
    return record(new)


@app.post("/api/v1/projects/{id}/goals", status_code=201)
def create_goal(
    id: uuid.UUID,
    body: GoalCreate,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    owned(db, m.Project, id, workspace)
    goal = m.Goal(
        workspace_id=workspace, project_id=id, title=body.title, description=body.description
    )
    db.add(goal)
    db.commit()
    return record(goal)


@app.get("/api/v1/projects/{id}/goals")
def list_goals(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    owned(db, m.Project, id, workspace)
    return [
        record(x)
        for x in db.scalars(
            select(m.Goal).where(m.Goal.project_id == id, m.Goal.workspace_id == workspace)
        ).all()
    ]


@app.get("/api/v1/projects/{id}/tasks")
def list_project_tasks(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    owned(db, m.Project, id, workspace)
    return [
        record(x)
        for x in db.scalars(
            select(m.Task).where(m.Task.project_id == id, m.Task.workspace_id == workspace)
        ).all()
    ]


@app.post("/api/v1/tasks", status_code=201)
def create_task(
    body: TaskCreate, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    owned(db, m.Project, body.project_id, workspace)
    if body.goal_id:
        goal = owned(db, m.Goal, body.goal_id, workspace)
        if goal.project_id != body.project_id:
            raise HTTPException(400, "Goal belongs to another project")
    task = m.Task(
        workspace_id=workspace,
        project_id=body.project_id,
        goal_id=body.goal_id,
        title=body.title,
        description=body.description,
    )
    db.add(task)
    db.commit()
    return record(task)


@app.get("/api/v1/tasks/{id}")
def get_task(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    task = owned(db, m.Task, id, workspace)
    result = record(task)
    result["runs"] = [record(x) for x in db.scalars(select(m.Run).where(m.Run.task_id == id)).all()]
    result["findings"] = [
        record(x) for x in db.scalars(select(m.Finding).where(m.Finding.task_id == id)).all()
    ]
    return result


@app.get("/api/v1/runs/{id}")
def get_run(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    run = owned(db, m.Run, id, workspace)
    result = record(run)
    result["events"] = [
        record(x)
        for x in db.scalars(
            select(m.RunEvent).where(m.RunEvent.run_id == id).order_by(m.RunEvent.occurred_at)
        ).all()
    ]
    result["artifacts"] = [
        record(x)
        for x in db.scalars(select(m.Artifact).where(m.Artifact.created_by_run_id == id)).all()
    ]
    return result


@app.post("/api/v1/findings/{id}/correct", status_code=201)
def correct_finding(
    id: uuid.UUID,
    body: FindingCorrection,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    previous = owned(db, m.Finding, id, workspace)
    db.scalar(select(m.Task).where(m.Task.id == previous.task_id).with_for_update())
    db.refresh(previous)
    if previous.status != "ACTIVE":
        raise HTTPException(409, "Finding has already been corrected")
    corrected = m.Finding(
        workspace_id=workspace,
        project_id=previous.project_id,
        task_id=previous.task_id,
        run_id=previous.run_id,
        kind=previous.kind,
        subject=body.subject or previous.subject,
        claim=body.claim,
        severity=previous.severity,
    )
    db.add(corrected)
    db.flush()
    correction_evidence = m.Evidence(
        workspace_id=workspace,
        type="USER_CONSTRAINT",
        source_ref=f"human-correction:{corrected.id}",
        content=redact(
            f"Human correction by {body.actor}: {body.claim}. Rationale: {body.rationale}"
        ),
        run_id=previous.run_id,
    )
    db.add(correction_evidence)
    db.flush()
    db.add(m.FindingEvidence(finding_id=corrected.id, evidence_id=correction_evidence.id))
    previous.status, previous.superseded_by = "SUPERSEDED", corrected.id
    for evidence_id in db.scalars(
        select(m.FindingEvidence.evidence_id).where(m.FindingEvidence.finding_id == id)
    ).all():
        db.add(m.FindingEvidence(finding_id=corrected.id, evidence_id=evidence_id))
    for case in db.scalars(
        select(m.DecisionCase).where(
            m.DecisionCase.finding_id == id, m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"])
        )
    ).all():
        case.status, case.resolved_at = "SUPERSEDED", m.now()
        audit(
            db,
            workspace,
            body.actor,
            "decision_case.supersede",
            "decision_case",
            case.id,
            {"corrected_finding": str(corrected.id)},
        )
    for notice in db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.task_id == previous.task_id,
            m.AttentionItem.kind == f"FINDING_ATTENTION:{id}",
            m.AttentionItem.status == "OPEN",
        )
    ).all():
        notice.status = "CLOSED"
    audit(
        db,
        workspace,
        body.actor,
        "finding.correct",
        "finding",
        id,
        {"superseded_by": str(corrected.id), "rationale": body.rationale},
    )
    db.flush()
    reduce_task(db, previous.task_id)
    db.commit()
    return record(corrected)


@app.post("/api/v1/findings/{id}/risk-override")
def override_risk(
    id: uuid.UUID,
    body: RiskOverride,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    finding = owned(db, m.Finding, id, workspace)
    db.scalar(select(m.Task).where(m.Task.id == finding.task_id).with_for_update())
    minimum, irreversible, _ = classify_risk(finding.claim, finding.severity)
    level = max(
        minimum, body.level, key={"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}.__getitem__
    )
    row = db.scalar(select(m.Risk).where(m.Risk.finding_id == id))
    before = row.level if row else minimum
    if row is None:
        row = m.Risk(
            workspace_id=workspace,
            task_id=finding.task_id,
            finding_id=id,
            level=level,
            reason=body.rationale,
            irreversible=irreversible,
        )
        db.add(row)
    else:
        row.level, row.reason, row.irreversible = (
            level,
            body.rationale,
            irreversible or row.irreversible,
        )
    audit(
        db,
        workspace,
        body.actor,
        "risk.override",
        "finding",
        id,
        {
            "before": before,
            "requested": body.level,
            "effective": level,
            "rationale": body.rationale,
        },
    )
    db.flush()
    reduce_task(db, finding.task_id)
    db.commit()
    return record(row)


@app.get("/api/v1/artifacts/{id}/content")
def artifact_content(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    artifact = owned(db, m.Artifact, id, workspace)
    media_type = artifact.mime_type.split(";", 1)[0].strip()
    if not (
        media_type.startswith("text/")
        or media_type in {"application/json", "application/xml", "application/javascript"}
    ):
        raise HTTPException(415, "Text preview is unavailable for this artifact type")
    if artifact.storage_key != artifact.sha256:
        raise HTTPException(409, "Artifact storage identity is invalid")
    path = Path(os.environ.get("DIRIMA_ARTIFACT_DIR", "data/artifacts")) / artifact.sha256
    if not path.is_file():
        raise HTTPException(404, "Artifact content is unavailable")
    digest = hashlib.sha256()
    preview = bytearray()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            if len(preview) < 100000:
                preview.extend(chunk[: 100000 - len(preview)])
    if digest.hexdigest() != artifact.sha256:
        raise HTTPException(409, "Artifact checksum does not match")
    return {
        "id": str(id),
        "content": redact(bytes(preview).decode("utf-8", errors="replace")),
        "truncated": path.stat().st_size > len(preview),
    }


@app.post("/api/v1/events")
async def post_event(
    request: Request, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    try:
        event = EventEnvelope.model_validate(await request.json())
        result = ingest_event(db, event, workspace)
        db.commit()
        logger.info(
            "event ingested",
            extra={
                "service": "api",
                "trace_id": request.state.trace_id,
                "workspace_id": str(workspace),
                "project_id": str(event.project_id),
                "task_id": str(event.task_id),
                "run_id": str(event.run_id),
                "event": "event.duplicate" if result["duplicate"] else "event.ingested",
            },
        )
        return result
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(
            400, redact(exc.errors(include_input=False, include_context=False))
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, redact(str(exc))) from exc
    except IntegrityError as exc:
        db.rollback()
        # Concurrent duplicate inserts are normal; unrelated constraint failures are not.
        duplicate = existing_event(db, event)
        if duplicate:
            if duplicate.workspace_id != workspace:
                raise HTTPException(403, "Event identity belongs to another workspace") from exc
            result = {"event_id": str(duplicate.event_id), "duplicate": True}
            if duplicate.payload.get("artifact_id"):
                result["artifact_id"] = duplicate.payload["artifact_id"]
            return result
        raise HTTPException(409, "Database constraint rejected event") from exc


@app.get("/api/v1/decision-cases")
def list_cases(
    workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    cases = db.scalars(
        select(m.DecisionCase).where(
            m.DecisionCase.workspace_id == workspace,
            m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
        )
    ).all()
    order = {"BLOCKING": 0, "HUMAN_REQUIRED": 1}
    result = []
    for case in sorted(
        cases,
        key=lambda c: (
            order.get(c.attention_class, 2),
            -{"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[c.risk_level],
            c.created_at,
        ),
    ):
        item = record(case)
        item["affected_task"] = owned(db, m.Task, case.task_id, workspace).title
        options = db.scalars(
            select(m.DecisionOption).where(m.DecisionOption.decision_case_id == case.id)
        ).all()
        item["option_labels"] = [o.label for o in options]
        item["evidence_summary"] = (
            f"{len({e for o in options for e in o.supporting_evidence})} referenced evidence items"
        )
        result.append(item)
    return result


@app.get("/api/v1/decision-cases/{id}")
def get_case(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    case = owned(db, m.DecisionCase, id, workspace)
    result = record(case)
    result["options"] = [
        record(x)
        for x in db.scalars(
            select(m.DecisionOption).where(m.DecisionOption.decision_case_id == id)
        ).all()
    ]
    result["conflict"] = record(db.get(m.Conflict, case.conflict_id)) if case.conflict_id else None
    findings = db.scalars(select(m.Finding).where(m.Finding.task_id == case.task_id)).all()
    result["findings"] = []
    for finding in findings:
        f = record(finding)
        evidence_ids = db.scalars(
            select(m.FindingEvidence.evidence_id).where(m.FindingEvidence.finding_id == finding.id)
        ).all()
        f["evidence"] = [record(owned(db, m.Evidence, e, workspace)) for e in evidence_ids]
        result["findings"].append(f)
    result["affected_task"] = record(owned(db, m.Task, case.task_id, workspace))
    return result


@app.post("/api/v1/decision-cases/{id}/resolve")
def resolve_case(
    id: uuid.UUID,
    body: DecisionInput,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    case = locked_case(db, id, workspace)
    if case.status not in {"OPEN", "AWAITING_HUMAN"}:
        raise HTTPException(409, "Case is already closed")
    option = owned(db, m.DecisionOption, body.selected_option_id, workspace)
    if option.decision_case_id != id:
        raise HTTPException(400, "Option belongs to another case")
    if case.risk_level in {"HIGH", "CRITICAL"} and not body.rationale.strip():
        raise HTTPException(400, "Rationale is required for high risk")
    decision = m.HumanDecision(
        workspace_id=workspace,
        decision_case_id=id,
        selected_option_id=option.id,
        actor=body.actor,
        rationale=body.rationale,
    )
    db.add(decision)
    case.status, case.resolved_at = "RESOLVED", m.now()
    if case.conflict_id:
        conflict = owned(db, m.Conflict, case.conflict_id, workspace)
        resolution = m.Resolution(
            workspace_id=workspace,
            conflict_id=conflict.id,
            method="HUMAN",
            rationale=body.rationale or "Human selected an option",
            evidence_refs=[],
        )
        db.add(resolution)
        db.flush()
        conflict.status, conflict.resolution_id = "HUMAN_RESOLVED", resolution.id
    db.flush()
    for notice in db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.decision_case_id == id, m.AttentionItem.status == "OPEN"
        )
    ).all():
        notice.status = "CLOSED"
    audit(
        db,
        workspace,
        body.actor,
        "decision.resolve",
        "decision_case",
        id,
        {"decision_id": str(decision.id), "option_id": str(option.id), "rationale": body.rationale},
    )
    reduce_task(db, case.task_id)
    db.commit()
    return record(decision)


@app.post("/api/v1/decision-cases/{id}/dismiss")
def dismiss_case(
    id: uuid.UUID,
    body: DismissInput,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    case = locked_case(db, id, workspace)
    if case.status not in {"OPEN", "AWAITING_HUMAN"}:
        raise HTTPException(409, "Case is already closed")
    case.status, case.resolved_at = "DISMISSED", m.now()
    if case.conflict_id:
        owned(db, m.Conflict, case.conflict_id, workspace).status = "INVALIDATED"
    for notice in db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.decision_case_id == id, m.AttentionItem.status == "OPEN"
        )
    ).all():
        notice.status = "CLOSED"
    audit(
        db,
        workspace,
        body.actor,
        "decision_case.dismiss",
        "decision_case",
        id,
        {"rationale": body.rationale},
    )
    reduce_task(db, case.task_id)
    db.commit()
    return record(case)


@app.post("/api/v1/decision-cases/{id}/investigate")
def investigate_case(
    id: uuid.UUID,
    body: DismissInput,
    workspace: uuid.UUID = Depends(auth),
    db: Session = Depends(session_scope),
) -> dict[str, Any]:
    case = locked_case(db, id, workspace)
    if case.status not in {"OPEN", "AWAITING_HUMAN"}:
        raise HTTPException(409, "Case is already closed")
    case.status = "AWAITING_HUMAN"
    audit(
        db,
        workspace,
        body.actor,
        "decision_case.investigate",
        "decision_case",
        id,
        {"rationale": body.rationale},
    )
    db.commit()
    return record(case)


@app.get("/api/v1/attention")
def get_attention(
    workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> list[dict[str, Any]]:
    return [
        record(x)
        for x in db.scalars(
            select(m.AttentionItem).where(
                m.AttentionItem.workspace_id == workspace, m.AttentionItem.status == "OPEN"
            )
        ).all()
    ]


@app.get("/api/v1/tasks/{id}/receipt")
def get_receipt(
    id: uuid.UUID, workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    owned(db, m.Task, id, workspace)
    receipt = db.scalar(select(m.CompletionReceipt).where(m.CompletionReceipt.task_id == id))
    runs = db.scalars(select(m.Run).where(m.Run.task_id == id)).all()
    if not receipt or not runs or any(run.status not in TERMINAL for run in runs):
        raise HTTPException(404, "Receipt not available")
    latest = db.scalar(select(func.max(m.RunEvent.created_at)).where(m.RunEvent.task_id == id))
    generated = receipt.content.get("generated_at")
    pending = (
        db.scalar(
            select(m.Job.id)
            .where(
                m.Job.payload["task_id"].as_string() == str(id),
                m.Job.created_at > datetime.fromisoformat(generated),
                m.Job.status.in_(["READY", "RUNNING", "FAILED", "DEAD"]),
            )
            .limit(1)
        )
        if generated
        else True
    )
    if (
        receipt.content.get("policy_version") != "audit-3"
        or pending
        or not generated
        or (latest and datetime.fromisoformat(generated) < latest)
    ):
        raise HTTPException(404, "Receipt is being refreshed")
    return record(receipt)


@app.get("/api/v1/metrics")
def metrics(
    workspace: uuid.UUID = Depends(auth), db: Session = Depends(session_scope)
) -> dict[str, Any]:
    def count(cls: Any, *filters: Any) -> int:
        return (
            db.scalar(
                select(func.count()).select_from(cls).where(cls.workspace_id == workspace, *filters)
            )
            or 0
        )

    events = count(m.RunEvent)
    cases = count(m.DecisionCase)
    attention_items = count(m.AttentionItem)
    event_rows = db.scalars(select(m.RunEvent).where(m.RunEvent.workspace_id == workspace)).all()

    def numeric(value: Any) -> int:
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else 0

    input_tokens = sum(
        numeric(e.payload.get("usage", {}).get("input_tokens", 0))
        if isinstance(e.payload.get("usage"), dict)
        else 0
        for e in event_rows
    )
    output_tokens = sum(
        numeric(e.payload.get("usage", {}).get("output_tokens", 0))
        if isinstance(e.payload.get("usage"), dict)
        and e.payload["usage"].get("output_tokens") is not None
        else max(1, (len(str(e.payload)) + numeric(e.payload.get("size_bytes", 0))) // 4)
        for e in event_rows
    )
    open_cases = db.scalars(
        select(m.DecisionCase).where(
            m.DecisionCase.workspace_id == workspace,
            m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
        )
    ).all()
    open_attention = db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.workspace_id == workspace, m.AttentionItem.status == "OPEN"
        )
    ).all()
    visible_tokens = sum(
        max(1, len(c.title + c.question + c.reason_human_required) // 4) for c in open_cases
    ) + sum(max(1, len(a.title + a.detail) // 4) for a in open_attention)
    reduction_seconds = (
        db.scalar(
            select(func.coalesce(func.sum(m.Job.duration_seconds), 0)).where(
                m.Job.workspace_id == workspace, m.Job.status == "COMPLETED"
            )
        )
        or 0
    )
    return {
        "dirima_events_total": events,
        "dirima_runs_total": count(m.Run),
        "dirima_jobs_failed_total": count(m.Job, m.Job.status.in_(["FAILED", "DEAD"])),
        "dirima_model_calls_total": count(m.ModelCall),
        "dirima_decision_cases_open": count(
            m.DecisionCase, m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"])
        ),
        "dirima_auto_resolutions_total": count(m.Resolution, m.Resolution.method != "HUMAN")
        + count(m.FindingCluster, m.FindingCluster.status == "AUTO_RESOLVED"),
        "dirima_human_decisions_total": count(m.HumanDecision),
        "dirima_reduction_duration_seconds": float(reduction_seconds),
        "dirima_human_visible_tokens": visible_tokens,
        "dirima_agent_input_tokens": input_tokens,
        "dirima_agent_output_tokens": output_tokens,
        "cognitive_compression_ratio": round(output_tokens / visible_tokens, 2)
        if visible_tokens
        else None,
        "event_count": events,
        "finding_count": count(m.Finding),
        "decision_cases": cases,
        "attention_items": attention_items,
        "completed_tasks": count(m.Task, m.Task.status == "COMPLETED"),
    }
