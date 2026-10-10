"""Ingest -> normalize -> extract -> cluster -> conflict -> verify -> resolve -> risk -> attention -> case -> receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from dirima import models as m
from dirima.application.ingest import audit
from dirima.policy.core import SENSITIVE, TERMINAL, attention, auto_resolvable, classify_risk
from dirima.providers.structured import (
    HttpStructuredProvider,
    StructuredModelProvider,
    call_model,
    call_model_retry,
)
from dirima.schemas import (
    ClusterClassification,
    CommandResult,
    ConflictClassification,
    ExtractedFinding,
    Extraction,
    ReceiptSummary,
    ResolutionAdvice,
    RiskAssessment,
)
from dirima.security.redaction import redact

EXTRACTION_SCHEMA = Extraction.model_json_schema()
CONFLICT_SCHEMA = ConflictClassification.model_json_schema()
CLUSTER_SCHEMA = ClusterClassification.model_json_schema()
RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()


def claim_key(value: str) -> str:
    """Claims and constraints can contain case/whitespace-sensitive literal values."""
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def conflict_preserved(db: Session, conflict: m.Conflict) -> bool:
    if conflict.status in {"OPEN", "AUTO_RESOLVED", "HUMAN_RESOLVED"}:
        return True
    return bool(
        db.scalar(
            select(m.DecisionCase.id)
            .where(m.DecisionCase.conflict_id == conflict.id, m.DecisionCase.status == "DISMISSED")
            .limit(1)
        )
    )


def alert(
    db: Session,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID | None,
    kind: str,
    title: str,
    detail: str = "",
    classification: str = "ATTENTION",
) -> None:
    existing = db.scalar(
        select(m.AttentionItem).where(
            m.AttentionItem.workspace_id == workspace_id,
            m.AttentionItem.task_id == task_id,
            m.AttentionItem.kind == kind,
            m.AttentionItem.title == title[:250],
            m.AttentionItem.status == "OPEN",
        )
    )
    if existing:
        existing.title, existing.detail, existing.classification = (
            title[:250],
            redact(detail),
            classification,
        )
    else:
        db.add(
            m.AttentionItem(
                workspace_id=workspace_id,
                task_id=task_id,
                kind=kind,
                classification=classification,
                title=title[:250],
                detail=redact(detail),
            )
        )


def provider_from_env() -> StructuredModelProvider | None:
    if os.environ.get("DIRIMA_MODEL_BASE_URL") and os.environ.get("DIRIMA_MODEL_API_KEY"):
        return HttpStructuredProvider()
    return None


def close_model_notice(db: Session, task: m.Task, title: str) -> list[str]:
    ids = []
    for notice in db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.task_id == task.id,
            m.AttentionItem.title == title,
            m.AttentionItem.status == "OPEN",
        )
    ).all():
        notice.status = "CLOSED"
        ids.append(str(notice.id))
    return ids


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def active_run_ids(db: Session, task_id: uuid.UUID) -> list[uuid.UUID]:
    runs = db.scalars(select(m.Run).where(m.Run.task_id == task_id)).all()
    superseded = {r.supersedes_run_id for r in runs if r.supersedes_run_id}
    return [r.id for r in runs if r.id not in superseded]


def extraction_chunks(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep every source character; repeat constraints and overlap fragment boundaries."""
    limit = max(2048, int(os.environ.get("DIRIMA_EXTRACTION_CHARS", "24000")))
    fragments: list[tuple[str, Any]] = []
    for collection in ("events", "artifacts"):
        for item in input_data[collection]:
            serialized = json.dumps(item, ensure_ascii=False)
            if len(serialized) <= limit:
                fragments.append((collection, item))
                continue
            is_artifact = collection == "artifacts"
            content = item["content"] if is_artifact else serialized
            stride = limit - 256
            starts = list(range(0, len(content), stride))
            for part, start in enumerate(starts, 1):
                fragment = {k: v for k, v in item.items() if k != "content"} if is_artifact else {}
                fragment.update(
                    content=content[start : start + limit],
                    part=part,
                    parts=len(starts),
                    format="text" if is_artifact else "json_fragment",
                )
                fragments.append((collection, fragment))
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] = {
        "events": [],
        "artifacts": [],
        "constraints": input_data["constraints"],
    }
    size = 0
    for collection, fragment in fragments:
        fragment_size = len(json.dumps(fragment, ensure_ascii=False))
        if size and size + fragment_size > limit:
            chunks.append(current)
            current = {"events": [], "artifacts": [], "constraints": input_data["constraints"]}
            size = 0
        current[collection].append(fragment)
        size += fragment_size
    if size:
        chunks.append(current)
    return chunks


def _extract(db: Session, task: m.Task, provider: StructuredModelProvider | None) -> bool:
    complete = True
    runs = db.scalars(select(m.Run).where(m.Run.id.in_(active_run_ids(db, task.id)))).all()
    for run in runs:
        receipt = db.scalar(select(m.AgentReceipt).where(m.AgentReceipt.run_id == run.id))
        receipt_output = Extraction(findings=[], risks=[], unresolved=[], constraint_violations=[])
        if receipt:
            raw = {
                "findings": receipt.content.get("reported_findings", []),
                "risks": [
                    str(x.get("description", x)) if isinstance(x, dict) else str(x)
                    for x in receipt.content.get("reported_risks", [])
                ],
                "unresolved": receipt.content.get("unresolved", []),
                "constraint_violations": [],
            }
            # Agent-provided structured data is validated but remains an assertion.
            try:
                receipt_output = Extraction.model_validate(raw)
            except Exception as exc:
                complete = False
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_OUTPUT_INVALID",
                    "Agent receipt has invalid extraction fields",
                    type(exc).__name__,
                )
                continue
        if run.status not in TERMINAL:
            continue
        artifacts = db.scalars(
            select(m.Artifact).where(m.Artifact.created_by_run_id == run.id)
        ).all()
        events = db.scalars(
            select(m.RunEvent).where(
                m.RunEvent.run_id == run.id,
                m.RunEvent.type.in_(
                    [
                        "message.completed",
                        "tool.called",
                        "tool.completed",
                        "tool.failed",
                        "run.status_changed",
                        "run.failed",
                        "run.completed",
                        "run.started",
                    ]
                ),
            )
        ).all()
        constraints = db.scalars(
            select(m.Constraint).where(
                m.Constraint.project_id == task.project_id, m.Constraint.status == "ACTIVE"
            )
        ).all()
        input_data = {
            "events": [
                {
                    "id": str(e.event_id),
                    "type": e.type,
                    "payload": redact({k: v for k, v in e.payload.items() if k != "receipt"}),
                }
                for e in events
                if any(k != "receipt" for k in e.payload)
            ],
            "artifacts": [],
            "constraints": [
                {"id": str(c.id), "statement": c.statement, "type": c.type} for c in constraints
            ],
        }
        integrity_ok = True
        for artifact in artifacts:
            path = (
                Path(os.environ.get("DIRIMA_ARTIFACT_DIR", "data/artifacts")) / artifact.storage_key
            )
            try:
                if artifact.storage_key != artifact.sha256:
                    raise ValueError("Invalid artifact storage key")
                content = path.read_bytes()
                if (
                    len(content) != artifact.size_bytes
                    or hashlib.sha256(content).hexdigest() != artifact.sha256
                ):
                    raise ValueError("Artifact digest or size mismatch")
                if not (
                    artifact.mime_type.split(";", 1)[0].strip().startswith("text/")
                    or artifact.mime_type.split(";", 1)[0].strip()
                    in {"application/json", "application/xml", "application/javascript"}
                ):
                    continue
                input_data["artifacts"].append(
                    {
                        "id": str(artifact.id),
                        "sha256": artifact.sha256,
                        "content": redact(content.decode("utf-8", errors="replace")),
                    }
                )
            except (OSError, ValueError) as exc:
                integrity_ok = False
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    f"ARTIFACT_INTEGRITY_ERROR:{artifact.id.hex}",
                    "Artifact integrity verification failed",
                    type(exc).__name__,
                    "BLOCKING",
                )
        if not integrity_ok:
            complete = False
            continue
        for artifact in artifacts:
            for notice in db.scalars(
                select(m.AttentionItem).where(
                    m.AttentionItem.task_id == task.id,
                    m.AttentionItem.kind == f"ARTIFACT_INTEGRITY_ERROR:{artifact.id.hex}",
                    m.AttentionItem.status == "OPEN",
                )
            ).all():
                notice.status = "CLOSED"
        source_hash = fingerprint({"sources": input_data, "receipt": receipt_output.model_dump()})
        if run.extraction_hash == source_hash:
            continue
        extracted = receipt_output
        if input_data["events"] or input_data["artifacts"]:
            if not provider:
                complete = False
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_PROVIDER_ERROR",
                    "Extractor provider is not configured",
                    "Transcript extraction needs a configured structured model",
                )
                continue
            if isinstance(provider, HttpStructuredProvider) and not os.environ.get(
                "DIRIMA_MODEL_EXTRACTOR"
            ):
                complete = False
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_PROVIDER_ERROR",
                    "Extractor model is not configured",
                )
                continue
            outputs: list[Extraction] = []
            chunks = extraction_chunks(input_data)
            for chunk in chunks:
                for attempt in range(3):
                    try:
                        result = call_model(
                            db,
                            task.workspace_id,
                            provider,
                            purpose="extractor",
                            input_data=chunk,
                            output_schema=EXTRACTION_SCHEMA,
                        )
                        outputs.append(Extraction.model_validate(result))
                        break
                    except Exception as exc:
                        if attempt == 2:
                            provider_error = isinstance(exc, (httpx.HTTPError, KeyError))
                            alert(
                                db,
                                task.workspace_id,
                                task.id,
                                "MODEL_PROVIDER_ERROR"
                                if provider_error
                                else "MODEL_OUTPUT_INVALID",
                                "Finding extraction failed",
                                type(exc).__name__,
                            )
                        elif isinstance(exc, httpx.HTTPError):
                            time.sleep(2**attempt)
                else:
                    break
            if len(outputs) != len(chunks):
                complete = False
                continue
            extracted = Extraction(
                findings=receipt_output.findings
                + [f for output in outputs for f in output.findings],
                risks=list(
                    dict.fromkeys(
                        receipt_output.risks + [r for output in outputs for r in output.risks]
                    )
                ),
                unresolved=list(
                    dict.fromkeys(
                        receipt_output.unresolved
                        + [u for output in outputs for u in output.unresolved]
                    )
                ),
                constraint_violations=list(
                    dict.fromkeys(v for output in outputs for v in output.constraint_violations)
                ),
            )
        assert extracted is not None
        all_findings = (
            extracted.findings
            + [
                ExtractedFinding(
                    kind="RISK", subject="Reported risk", claim=risk, severity="MEDIUM"
                )
                for risk in extracted.risks
            ]
            + [
                ExtractedFinding(
                    kind="CONSTRAINT_VIOLATION",
                    subject="Constraint violation",
                    claim=violation,
                    severity="HIGH",
                )
                for violation in extracted.constraint_violations
            ]
        )
        for item in all_findings:
            existing = db.scalar(
                select(m.Finding)
                .where(
                    m.Finding.run_id == run.id,
                    m.Finding.subject == item.subject,
                    m.Finding.claim == item.claim,
                )
                .order_by((m.Finding.status == "ACTIVE").desc())
            )
            if existing and existing.status != "ACTIVE":
                continue
            level, _, _ = classify_risk(item.claim, item.severity)
            if existing:
                finding = existing
                if RANK[level] > RANK[finding.severity]:
                    previous_level = finding.severity
                    finding.severity = level
                    audit(
                        db,
                        task.workspace_id,
                        "dirima",
                        "finding.severity_raise",
                        "finding",
                        finding.id,
                        {"previous": previous_level, "current": level, "source_hash": source_hash},
                    )
            else:
                finding = m.Finding(
                    workspace_id=task.workspace_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    run_id=run.id,
                    kind=item.kind,
                    subject=item.subject,
                    claim=item.claim,
                    severity=level,
                )
                db.add(finding)
                db.flush()
                evidence = m.Evidence(
                    workspace_id=task.workspace_id,
                    type="AGENT_ASSERTION",
                    source_ref=f"run:{run.id}",
                    content=redact(item.claim),
                    run_id=run.id,
                )
                db.add(evidence)
                db.flush()
                db.add(m.FindingEvidence(finding_id=finding.id, evidence_id=evidence.id))
            for ref in item.evidence_refs:
                try:
                    reference_id = uuid.UUID(ref)
                except ValueError:
                    continue
                linked_artifact = db.get(m.Artifact, reference_id)
                if (
                    linked_artifact
                    and linked_artifact.workspace_id == task.workspace_id
                    and linked_artifact.task_id == task.id
                ):
                    if db.scalar(
                        select(m.FindingEvidence.evidence_id)
                        .join(m.Evidence, m.FindingEvidence.evidence_id == m.Evidence.id)
                        .where(
                            m.FindingEvidence.finding_id == finding.id,
                            m.Evidence.artifact_id == linked_artifact.id,
                        )
                    ):
                        continue
                    linked = m.Evidence(
                        workspace_id=task.workspace_id,
                        type="ARTIFACT",
                        source_ref=f"artifact:{linked_artifact.id}",
                        content=f"SHA-256 {linked_artifact.sha256}",
                        artifact_id=linked_artifact.id,
                        run_id=run.id,
                    )
                    db.add(linked)
                    db.flush()
                    db.add(m.FindingEvidence(finding_id=finding.id, evidence_id=linked.id))
            if item.kind == "CONSTRAINT_VIOLATION":
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "CONSTRAINT_VIOLATION",
                    "Possible hard constraint violation",
                    item.claim,
                    "HUMAN_REQUIRED",
                )
        if extracted.unresolved:
            alert(
                db,
                task.workspace_id,
                task.id,
                f"AGENT_UNRESOLVED:{run.id}",
                "Agent reported unresolved questions",
                "; ".join(extracted.unresolved),
                "BLOCKING"
                if any("block" in value.lower() for value in extracted.unresolved)
                else "ATTENTION",
            )
        else:
            for notice in db.scalars(
                select(m.AttentionItem).where(
                    m.AttentionItem.task_id == task.id,
                    m.AttentionItem.kind == f"AGENT_UNRESOLVED:{run.id}",
                    m.AttentionItem.status == "OPEN",
                )
            ).all():
                notice.status = "CLOSED"
                case = (
                    db.get(m.DecisionCase, notice.decision_case_id)
                    if notice.decision_case_id
                    else None
                )
                if case and case.status in {"OPEN", "AWAITING_HUMAN"}:
                    case.status, case.resolved_at = "SUPERSEDED", m.now()
                    audit(
                        db,
                        task.workspace_id,
                        "dirima",
                        "decision_case.supersede",
                        "decision_case",
                        case.id,
                        {"reason": "Underlying unresolved question no longer reported"},
                    )
        run.extraction_hash = source_hash
    if complete:
        for notice in db.scalars(
            select(m.AttentionItem).where(
                m.AttentionItem.task_id == task.id,
                m.AttentionItem.kind.in_(["MODEL_OUTPUT_INVALID", "MODEL_PROVIDER_ERROR"]),
                m.AttentionItem.title.in_(
                    [
                        "Finding extraction failed",
                        "Extractor provider is not configured",
                        "Extractor model is not configured",
                        "Agent receipt has invalid extraction fields",
                    ]
                ),
                m.AttentionItem.status == "OPEN",
            )
        ).all():
            notice.status = "CLOSED"
    return complete


def _cluster(db: Session, task: m.Task, provider: StructuredModelProvider | None) -> bool:
    complete = True
    findings = db.scalars(
        select(m.Finding).where(m.Finding.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all()
    for finding in findings:
        if finding.cluster_id:
            continue
        topic, claim = key(finding.subject), claim_key(finding.claim)
        candidates = db.scalars(
            select(m.FindingCluster).where(
                m.FindingCluster.task_id == task.id, m.FindingCluster.topic_key == topic
            )
        ).all()
        cluster = next((c for c in candidates if c.claim_key == claim), None)
        semantic_failed = False
        if (
            cluster is None
            and provider
            and (
                not isinstance(provider, HttpStructuredProvider)
                or os.environ.get("DIRIMA_MODEL_CLUSTER")
            )
        ):
            for candidate in candidates:
                representative = db.scalar(
                    select(m.Finding).where(m.Finding.cluster_id == candidate.id).limit(1)
                )
                if not representative:
                    continue
                try:
                    result = call_model_retry(
                        db,
                        task.workspace_id,
                        provider,
                        purpose="cluster",
                        input_data={
                            "findings": [
                                {"id": str(f.id), "subject": f.subject, "claim": f.claim}
                                for f in (representative, finding)
                            ]
                        },
                        output_schema=CLUSTER_SCHEMA,
                        system_prompt="Decide if these two claims mean the same thing, preserving both originals.",
                    )
                    classified = ClusterClassification.model_validate(result)
                    if classified.equivalent and set(classified.finding_ids) == {
                        representative.id,
                        finding.id,
                    }:
                        cluster = candidate
                        break
                except Exception as exc:
                    complete = False
                    semantic_failed = True
                    alert(
                        db,
                        task.workspace_id,
                        task.id,
                        "MODEL_OUTPUT_INVALID",
                        "Semantic clustering failed",
                        type(exc).__name__,
                    )
                    break
        if semantic_failed:
            continue
        if not cluster:
            cluster = m.FindingCluster(
                workspace_id=task.workspace_id, task_id=task.id, topic_key=topic, claim_key=claim
            )
            db.add(cluster)
            db.flush()
        finding.cluster_id = cluster.id
        db.flush()
    if complete:
        close_model_notice(db, task, "Semantic clustering failed")
    return complete


def _conflicts(db: Session, task: m.Task, provider: StructuredModelProvider | None) -> bool:
    complete = True
    findings = db.scalars(
        select(m.Finding).where(m.Finding.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all()
    groups: dict[str, list[m.Finding]] = defaultdict(list)
    for finding in findings:
        if finding.cluster_id is None:
            complete = False
            continue
        groups[key(finding.subject)].append(finding)
    active_ids = {str(f.id) for f in findings}
    by_id = {str(f.id): f for f in findings}
    seen = set()
    for conflict in db.scalars(
        select(m.Conflict)
        .where(m.Conflict.task_id == task.id, m.Conflict.status.in_(["OPEN", "AUTO_RESOLVED"]))
        .order_by(m.Conflict.created_at.desc(), m.Conflict.id.desc())
    ).all():
        identity = (conflict.topic_key, tuple(sorted(conflict.finding_ids)))
        changed_group = conflict.topic_key in groups and set(conflict.finding_ids) != {
            str(f.id) for f in groups[conflict.topic_key]
        }
        if (
            not set(conflict.finding_ids) <= active_ids
            or (
                all(by_id[x].cluster_id for x in conflict.finding_ids if x in by_id)
                and len({by_id[x].cluster_id for x in conflict.finding_ids if x in by_id}) < 2
            )
            or changed_group
            or identity in seen
        ):
            supersede_conflict(db, task, conflict)
        else:
            seen.add(identity)
    db.flush()
    for topic, items in groups.items():
        claims = {f.cluster_id for f in items}
        if len(claims) < 2:
            continue
        existing = db.scalar(
            select(m.Conflict).where(
                m.Conflict.task_id == task.id,
                m.Conflict.topic_key == topic,
                m.Conflict.status.in_(["OPEN", "AUTO_RESOLVED"]),
            )
        )
        if existing:
            if set(existing.finding_ids) == {str(f.id) for f in items}:
                continue
            supersede_conflict(db, task, existing)
            db.flush()
        identities = {str(f.id) for f in items}
        previous = db.scalars(
            select(m.Conflict).where(m.Conflict.task_id == task.id, m.Conflict.topic_key == topic)
        ).all()
        if any(set(c.finding_ids) == identities and conflict_preserved(db, c) for c in previous):
            continue
        level = max(
            (f.severity for f in items),
            key=lambda x: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[x],
        )
        db.add(
            m.Conflict(
                workspace_id=task.workspace_id,
                task_id=task.id,
                topic_key=topic,
                finding_ids=[str(f.id) for f in items],
                severity=level,
            )
        )
    # Cross-topic semantic comparisons are optional when no provider is configured.
    if (
        provider
        and len(groups) > 1
        and (
            not isinstance(provider, HttpStructuredProvider)
            or os.environ.get("DIRIMA_MODEL_CONFLICT_DETECTOR")
        )
    ):
        values = list(groups.values())
        for left_index in range(len(values)):
            for right_index in range(left_index + 1, len(values)):
                left_items = {f.cluster_id: f for f in values[left_index]}
                right_items = {f.cluster_id: f for f in values[right_index]}
                for left in left_items.values():
                    for right in right_items.values():
                        pair_key = "semantic:" + ":".join(sorted((str(left.id), str(right.id))))
                        prior_pairs = db.scalars(
                            select(m.Conflict).where(
                                m.Conflict.task_id == task.id, m.Conflict.topic_key == pair_key
                            )
                        ).all()
                        if any(conflict_preserved(db, c) for c in prior_pairs):
                            continue
                        try:
                            result = call_model_retry(
                                db,
                                task.workspace_id,
                                provider,
                                purpose="conflict_detector",
                                input_data={
                                    "findings": [
                                        {"id": str(f.id), "subject": f.subject, "claim": f.claim}
                                        for f in (left, right)
                                    ]
                                },
                                output_schema=CONFLICT_SCHEMA,
                                system_prompt="Identify only genuine mutually exclusive claims or recommendations.",
                            )
                            classified = ConflictClassification.model_validate(result)
                            if classified.conflict and set(classified.finding_ids) == {
                                left.id,
                                right.id,
                            }:
                                db.add(
                                    m.Conflict(
                                        workspace_id=task.workspace_id,
                                        task_id=task.id,
                                        topic_key=pair_key,
                                        finding_ids=[str(left.id), str(right.id)],
                                        severity="MEDIUM",
                                        conflict_type=classified.conflict_type,
                                        reason=classified.reason,
                                    )
                                )
                        except Exception as exc:
                            complete = False
                            alert(
                                db,
                                task.workspace_id,
                                task.id,
                                "MODEL_OUTPUT_INVALID",
                                "Semantic conflict classification failed",
                                type(exc).__name__,
                            )
    if complete:
        close_model_notice(db, task, "Semantic conflict classification failed")
    return complete


def supersede_conflict(db: Session, task: m.Task, conflict: m.Conflict) -> None:
    conflict.status = "INVALIDATED"
    audit(
        db,
        task.workspace_id,
        "dirima",
        "conflict.invalidate",
        "conflict",
        conflict.id,
        {"reason": "Underlying findings changed"},
    )
    for case in db.scalars(
        select(m.DecisionCase).where(
            m.DecisionCase.conflict_id == conflict.id,
            m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
        )
    ).all():
        case.status, case.resolved_at = "SUPERSEDED", m.now()
        audit(
            db,
            task.workspace_id,
            "dirima",
            "decision_case.supersede",
            "decision_case",
            case.id,
            {"reason": "Underlying findings changed"},
        )
        for notice in db.scalars(
            select(m.AttentionItem).where(
                m.AttentionItem.decision_case_id == case.id, m.AttentionItem.status == "OPEN"
            )
        ).all():
            notice.status = "CLOSED"


def _verify(db: Session, task: m.Task) -> None:
    for receipt in db.scalars(
        select(m.AgentReceipt)
        .join(m.Run, m.AgentReceipt.run_id == m.Run.id)
        .where(m.Run.task_id == task.id)
        .where(m.Run.id.in_(active_run_ids(db, task.id)))
    ).all():
        run = db.get(m.Run, receipt.run_id)
        assert run is not None
        for index, reported in enumerate(receipt.content.get("reported_verifications", [])):
            ref = f"receipt:{run.id}:verification:{index}"
            if db.scalar(select(m.Evidence.id).where(m.Evidence.source_ref == ref)):
                continue
            evidence = m.Evidence(
                workspace_id=task.workspace_id,
                type="AGENT_ASSERTION",
                source_ref=ref,
                content=redact(json.dumps(reported, ensure_ascii=False)),
                run_id=run.id,
            )
            db.add(evidence)
            db.flush()
            method = (
                str(reported.get("method", "agent reported"))
                if isinstance(reported, dict)
                else "agent reported"
            )
            db.add(
                m.Verification(
                    workspace_id=task.workspace_id,
                    task_id=task.id,
                    run_id=run.id,
                    method=method[:100],
                    status="INCONCLUSIVE",
                    evidence_refs=[str(evidence.id)],
                    performed_by=run.agent_name,
                    performed_at=run.ended_at or m.now(),
                )
            )
    events = db.scalars(
        select(m.RunEvent).where(
            m.RunEvent.task_id == task.id,
            m.RunEvent.type == "verification.reported",
            m.RunEvent.run_id.in_(active_run_ids(db, task.id)),
        )
    ).all()
    for event in events:
        source_run = db.get(m.Run, event.run_id)
        if (
            source_run is None
            or source_run.status not in TERMINAL
            or source_run.extraction_hash is None
        ):
            # Preserve the event and classify it only once its possible targets are extracted.
            continue
        ref = f"event:{event.event_id}"
        if db.scalar(select(m.Evidence.id).where(m.Evidence.source_ref == ref)):
            continue
        payload = event.payload
        method = str(payload.get("method", "reported test"))[:100]
        finding = None
        if payload.get("finding_id"):
            try:
                candidate = db.get(m.Finding, uuid.UUID(str(payload["finding_id"])))
                if (
                    candidate
                    and candidate.task_id == task.id
                    and candidate.workspace_id == task.workspace_id
                    and candidate.status == "ACTIVE"
                ):
                    finding = candidate
            except (ValueError, TypeError):
                pass
        elif payload.get("subject"):
            candidates = db.scalars(
                select(m.Finding).where(
                    m.Finding.run_id == event.run_id,
                    m.Finding.subject == payload["subject"],
                    m.Finding.status == "ACTIVE",
                )
            ).all()
            if len(candidates) == 1:
                finding = candidates[0]
        status = "INCONCLUSIVE"
        try:
            observed = CommandResult.model_validate(payload.get("command_result"))
            valid_target = finding is not None or (
                not payload.get("finding_id")
                and not payload.get("subject")
                and payload.get("scope") == "task"
            )
            if observed.observer == event.source_adapter and valid_target:
                status = "PASSED" if observed.exit_code == 0 else "FAILED"
        except ValueError:
            pass
        evidence = m.Evidence(
            workspace_id=task.workspace_id,
            type="TEST_RESULT" if status != "INCONCLUSIVE" else "AGENT_ASSERTION",
            source_ref=ref,
            content=redact(json.dumps(payload, ensure_ascii=False)),
            run_id=event.run_id,
        )
        db.add(evidence)
        db.flush()
        if finding:
            db.add(m.FindingEvidence(finding_id=finding.id, evidence_id=evidence.id))
        db.add(
            m.Verification(
                workspace_id=task.workspace_id,
                task_id=task.id,
                finding_id=finding.id if finding else None,
                run_id=event.run_id,
                method=method[:100],
                status=status,
                evidence_refs=[str(evidence.id)],
                performed_by=str(payload.get("performed_by", "adapter"))[:100],
                performed_at=event.occurred_at,
            )
        )


def finding_risk(db: Session, finding: m.Finding) -> tuple[str, bool, str]:
    level, irreversible, reason = classify_risk(finding.claim, finding.severity)
    risk = db.scalar(select(m.Risk).where(m.Risk.finding_id == finding.id))
    if risk:
        level = max(level, risk.level, key=RANK.__getitem__)
        irreversible = irreversible or risk.irreversible
        reason = risk.reason
    return level, irreversible, reason


def _risk(db: Session, task: m.Task, provider: StructuredModelProvider | None) -> bool:
    complete = True
    for finding in db.scalars(
        select(m.Finding).where(m.Finding.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all():
        previous = db.scalar(select(m.Risk).where(m.Risk.finding_id == finding.id))
        cached = bool(
            previous and not previous.reason.startswith("Risk classification unavailable")
        )
        level, irreversible, reason = (
            finding_risk(db, finding) if cached else classify_risk(finding.claim, finding.severity)
        )
        if not cached and provider and os.environ.get("DIRIMA_MODEL_RISK_CLASSIFIER"):
            try:
                result = call_model_retry(
                    db,
                    task.workspace_id,
                    provider,
                    purpose="risk_classifier",
                    input_data={
                        "claim": finding.claim,
                        "deterministic_minimum": level,
                        "irreversible_minimum": irreversible,
                    },
                    output_schema=RiskAssessment.model_json_schema(),
                    system_prompt="Classify potential impact conservatively. You may raise risk; you cannot lower the deterministic minimum.",
                )
                assessment = RiskAssessment.model_validate(result)
                level = max(level, assessment.level, key=RANK.__getitem__)
                irreversible = irreversible or assessment.irreversible
                if irreversible:
                    level = max(level, "HIGH", key=RANK.__getitem__)
                reason = assessment.reason
            except Exception as exc:
                complete = False
                level, reason = (
                    max(level, "HIGH", key=RANK.__getitem__),
                    "Risk classification unavailable; human review required",
                )
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_PROVIDER_ERROR"
                    if isinstance(exc, httpx.HTTPError)
                    else "MODEL_OUTPUT_INVALID",
                    "Risk classification failed",
                    type(exc).__name__,
                )
        if previous:
            previous.level, previous.irreversible, previous.reason = level, irreversible, reason
        else:
            db.add(
                m.Risk(
                    workspace_id=task.workspace_id,
                    task_id=task.id,
                    finding_id=finding.id,
                    level=level,
                    irreversible=irreversible,
                    reason=reason,
                )
            )
        if level in {"HIGH", "CRITICAL"} or irreversible:
            cluster = db.get(m.FindingCluster, finding.cluster_id) if finding.cluster_id else None
            if cluster and cluster.status == "AUTO_RESOLVED":
                cluster.status = "OPEN"
                audit(
                    db,
                    task.workspace_id,
                    "dirima",
                    "cluster.reopen",
                    "finding_cluster",
                    cluster.id,
                    {"reason": "Elevated risk requires a human"},
                )
            for conflict in db.scalars(
                select(m.Conflict).where(
                    m.Conflict.task_id == task.id, m.Conflict.status == "AUTO_RESOLVED"
                )
            ).all():
                if str(finding.id) in conflict.finding_ids:
                    current_open = db.scalar(
                        select(m.Conflict.id).where(
                            m.Conflict.task_id == task.id,
                            m.Conflict.topic_key == conflict.topic_key,
                            m.Conflict.status == "OPEN",
                        )
                    )
                    conflict.status, conflict.resolution_id = (
                        "INVALIDATED" if current_open else "OPEN",
                        None,
                    )
                    audit(
                        db,
                        task.workspace_id,
                        "dirima",
                        "conflict.reopen",
                        "conflict",
                        conflict.id,
                        {"reason": "Elevated risk requires a human"},
                    )
    if complete:
        close_model_notice(db, task, "Risk classification failed")
    return complete


def _attention(db: Session, task: m.Task) -> None:
    for finding in db.scalars(
        select(m.Finding).where(m.Finding.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all():
        cluster = db.get(m.FindingCluster, finding.cluster_id) if finding.cluster_id else None
        checks = db.scalars(
            select(m.Verification).where(
                m.Verification.task_id == task.id,
                (m.Verification.run_id.is_(None))
                | m.Verification.run_id.in_(active_run_ids(db, task.id)),
                (m.Verification.finding_id.is_(None)) | (m.Verification.finding_id == finding.id),
            )
        ).all()
        level, irreversible, _ = finding_risk(db, finding)
        failed = any(v.status in {"FAILED", "ERROR"} for v in checks)
        classification = attention(
            risk=level,
            irreversible=irreversible,
            conflict=False,
            failed=failed,
            verified=not failed
            and (
                bool(cluster and cluster.status == "AUTO_RESOLVED")
                or any(v.status == "PASSED" for v in checks)
            ),
        )
        kind = f"FINDING_ATTENTION:{finding.id}"
        if classification == "ATTENTION":
            alert(
                db,
                task.workspace_id,
                task.id,
                kind,
                f"Verification needs attention: {finding.subject}",
                "Verification failed" if failed else "Finding is not independently verified",
            )
        else:
            for notice in db.scalars(
                select(m.AttentionItem).where(
                    m.AttentionItem.task_id == task.id,
                    m.AttentionItem.kind == kind,
                    m.AttentionItem.status == "OPEN",
                )
            ).all():
                notice.status = "CLOSED"
    for run in db.scalars(
        select(m.Run).where(
            m.Run.id.in_(active_run_ids(db, task.id)), m.Run.status.in_(["FAILED", "CANCELLED"])
        )
    ).all():
        alert(
            db,
            task.workspace_id,
            task.id,
            f"RUN_{run.status}:{run.id}",
            f"Run {run.status.lower()}: {run.agent_name}",
            f"Run {run.id} did not succeed",
        )


def _resolve(db: Session, task: m.Task, provider: StructuredModelProvider | None) -> bool:
    complete = True
    previous_auto = {}
    for conflict in db.scalars(
        select(m.Conflict).where(
            m.Conflict.task_id == task.id, m.Conflict.status == "AUTO_RESOLVED"
        )
    ).all():
        previous_auto[conflict.id] = conflict.resolution_id
        conflict.status = "OPEN"
    db.flush()
    for cluster in db.scalars(
        select(m.FindingCluster).where(m.FindingCluster.task_id == task.id)
    ).all():
        members = db.scalars(
            select(m.Finding).where(
                m.Finding.cluster_id == cluster.id, m.Finding.status == "ACTIVE"
            )
        ).all()
        if not members:
            cluster.status = "OPEN"
            continue
        families = {
            r.model_family
            for r in db.scalars(
                select(m.Run).where(m.Run.id.in_([f.run_id for f in members]))
            ).all()
            if r.model_family != "unknown"
        }
        checks = db.scalars(
            select(m.Verification).where(
                m.Verification.task_id == task.id,
                (m.Verification.run_id.is_(None))
                | m.Verification.run_id.in_(active_run_ids(db, task.id)),
                (m.Verification.finding_id.is_(None))
                | m.Verification.finding_id.in_([f.id for f in members]),
            )
        ).all()
        contested = any(
            set(c.finding_ids) & {str(f.id) for f in members}
            for c in db.scalars(
                select(m.Conflict).where(m.Conflict.task_id == task.id, m.Conflict.status == "OPEN")
            ).all()
        )
        risk = max(
            (finding_risk(db, f)[0] for f in members),
            default="LOW",
            key=lambda x: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[x],
        )
        eligible = not contested and auto_resolvable(
            risk=risk,
            irreversible=any(finding_risk(db, f)[1] for f in members),
            sensitive=any(SENSITIVE.search(f.claim) for f in members),
            passed=any(v.status == "PASSED" and v.finding_id is not None for v in checks),
            independent_families=len(families),
            inconclusive=any(
                v.status in {"INCONCLUSIVE", "PENDING", "FAILED", "ERROR"} for v in checks
            ),
        )
        if eligible and cluster.status != "AUTO_RESOLVED":
            cluster.status = "AUTO_RESOLVED"
            audit(
                db,
                task.workspace_id,
                "dirima",
                "cluster.auto_resolve",
                "finding_cluster",
                cluster.id,
                {"finding_ids": [str(f.id) for f in members]},
            )
        elif not eligible and cluster.status == "AUTO_RESOLVED":
            cluster.status = "OPEN"
            audit(
                db,
                task.workspace_id,
                "dirima",
                "cluster.reopen",
                "finding_cluster",
                cluster.id,
                {"reason": "New or conflicting evidence requires review"},
            )
    for conflict in db.scalars(
        select(m.Conflict).where(m.Conflict.task_id == task.id, m.Conflict.status == "OPEN")
    ).all():
        findings = db.scalars(
            select(m.Finding).where(m.Finding.id.in_([uuid.UUID(x) for x in conflict.finding_ids]))
        ).all()
        verifications = db.scalars(
            select(m.Verification).where(
                m.Verification.task_id == task.id,
                (m.Verification.run_id.is_(None))
                | m.Verification.run_id.in_(active_run_ids(db, task.id)),
                (m.Verification.finding_id.is_(None))
                | m.Verification.finding_id.in_([f.id for f in findings]),
            )
        ).all()
        passed_verifications = [
            v for v in verifications if v.status == "PASSED" and v.finding_id is not None
        ]
        failed_ids = {v.finding_id for v in verifications if v.status == "FAILED" and v.finding_id}
        winning = [
            f
            for f in findings
            if any(v.finding_id == f.id for v in passed_verifications) and f.id not in failed_ids
        ]
        constraints = db.scalars(
            select(m.Constraint).where(
                m.Constraint.project_id == task.project_id,
                m.Constraint.status == "ACTIVE",
                m.Constraint.type == "HARD",
            )
        ).all()
        authoritative = [f for f in findings if any(f.claim == c.statement for c in constraints)]
        selected = authoritative[0] if len(authoritative) == 1 else None
        if not authoritative:
            selected = (
                winning[0] if len(winning) == 1 and len(failed_ids) == len(findings) - 1 else None
            )
        if not selected:
            continue
        advice = None
        if provider and os.environ.get("DIRIMA_MODEL_RESOLVER"):
            try:
                result = call_model_retry(
                    db,
                    task.workspace_id,
                    provider,
                    purpose="resolver",
                    input_data={
                        "findings": [{"id": str(f.id), "claim": f.claim} for f in findings],
                        "deterministic_candidate": str(selected.id),
                        "verifications": [
                            {"finding_id": str(v.finding_id), "status": v.status}
                            for v in verifications
                        ],
                    },
                    output_schema=ResolutionAdvice.model_json_schema(),
                    system_prompt="Review the deterministic candidate. Require a human when requirements or evidence are ambiguous. A model recommendation never authorizes resolution.",
                )
                advice = ResolutionAdvice.model_validate(result)
                if advice.requires_human or advice.selected_finding_id != selected.id:
                    continue
            except Exception as exc:
                complete = False
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_PROVIDER_ERROR"
                    if isinstance(exc, httpx.HTTPError)
                    else "MODEL_OUTPUT_INVALID",
                    "Resolver assessment failed",
                    type(exc).__name__,
                )
                continue
        if any(
            v.status == "PASSED" and v.finding_id not in {None, selected.id} for v in verifications
        ):
            continue
        risk = max(
            (finding_risk(db, f)[0] for f in findings),
            key=lambda x: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[x],
        )
        if not auto_resolvable(
            risk=risk,
            irreversible=any(finding_risk(db, f)[1] for f in findings),
            sensitive=any(SENSITIVE.search(f.claim) for f in findings),
            passed=bool(passed_verifications),
            independent_families=0,
            authoritative=selected in authoritative,
            contrary_failed=bool(failed_ids),
            inconclusive=any(
                v.status in {"INCONCLUSIVE", "PENDING", "ERROR"}
                or (v.status == "FAILED" and v.finding_id in {None, selected.id})
                for v in verifications
            ),
        ):
            continue
        old = (
            db.get(m.Resolution, previous_auto.get(conflict.id))
            if previous_auto.get(conflict.id)
            else None
        )
        if old and old.selected_finding_id == selected.id:
            conflict.status = "AUTO_RESOLVED"
            continue
        resolution = m.Resolution(
            workspace_id=task.workspace_id,
            conflict_id=conflict.id,
            method="AUTHORITATIVE_CONSTRAINT"
            if selected in authoritative
            else "DETERMINISTIC_TEST",
            rationale="Supported by hard constraint or test evidence; contrary candidate failed",
            selected_finding_id=selected.id,
            evidence_refs=[str(v.id) for v in passed_verifications],
        )
        db.add(resolution)
        if advice:
            resolution.rationale += "; model review: " + advice.rationale
        db.flush()
        conflict.status, conflict.resolution_id = "AUTO_RESOLVED", resolution.id
        audit(
            db,
            task.workspace_id,
            "dirima",
            "conflict.auto_resolve",
            "conflict",
            conflict.id,
            {"resolution_id": str(resolution.id)},
        )
    for conflict in db.scalars(select(m.Conflict).where(m.Conflict.task_id == task.id)).all():
        if conflict.id in previous_auto and conflict.status == "OPEN":
            conflict.resolution_id = None
            audit(
                db,
                task.workspace_id,
                "dirima",
                "conflict.reopen",
                "conflict",
                conflict.id,
                {
                    "previous_resolution_id": str(previous_auto[conflict.id]),
                    "reason": "Current constraints or evidence no longer support automatic resolution",
                },
            )
        if conflict.status == "AUTO_RESOLVED":
            for case in db.scalars(
                select(m.DecisionCase).where(
                    m.DecisionCase.conflict_id == conflict.id,
                    m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
                )
            ).all():
                case.status, case.resolved_at = "SUPERSEDED", m.now()
                audit(
                    db,
                    task.workspace_id,
                    "dirima",
                    "decision_case.supersede",
                    "decision_case",
                    case.id,
                    {
                        "reason": "Conflict automatically resolved",
                        "resolution_id": str(conflict.resolution_id),
                    },
                )
                for notice in db.scalars(
                    select(m.AttentionItem).where(
                        m.AttentionItem.decision_case_id == case.id,
                        m.AttentionItem.status == "OPEN",
                    )
                ).all():
                    notice.status = "CLOSED"
    if complete:
        close_model_notice(db, task, "Resolver assessment failed")
    return complete


def _cases(db: Session, task: m.Task) -> None:
    findings = db.scalars(
        select(m.Finding).where(m.Finding.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all()
    for case in db.scalars(
        select(m.DecisionCase).where(
            m.DecisionCase.task_id == task.id,
            m.DecisionCase.conflict_id.is_(None),
            m.DecisionCase.finding_id.is_not(None),
            m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN"]),
        )
    ).all():
        original = db.get(m.Finding, case.finding_id)
        members = [
            f
            for f in findings
            if f.id == case.finding_id
            or (original and original.cluster_id and f.cluster_id == original.cluster_id)
        ]
        if not any(
            attention(
                risk=finding_risk(db, f)[0],
                irreversible=finding_risk(db, f)[1],
                conflict=False,
                failed=False,
            )
            == "HUMAN_REQUIRED"
            for f in members
        ):
            case.status, case.resolved_at = "SUPERSEDED", m.now()
            audit(
                db,
                task.workspace_id,
                "dirima",
                "decision_case.supersede",
                "decision_case",
                case.id,
                {"reason": "Current risk no longer requires a human decision"},
            )
            for notice in db.scalars(
                select(m.AttentionItem).where(
                    m.AttentionItem.decision_case_id == case.id, m.AttentionItem.status == "OPEN"
                )
            ).all():
                notice.status = "CLOSED"
    open_conflicts = db.scalars(
        select(m.Conflict).where(m.Conflict.task_id == task.id, m.Conflict.status == "OPEN")
    ).all()
    for conflict in open_conflicts:
        risk = max(
            [conflict.severity]
            + [finding_risk(db, f)[0] for f in findings if str(f.id) in conflict.finding_ids],
            key=RANK.__getitem__,
        )
        existing_case = db.scalar(
            select(m.DecisionCase).where(
                m.DecisionCase.conflict_id == conflict.id,
                m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN", "RESOLVED"]),
            )
        )
        if existing_case:
            if existing_case.status in {"OPEN", "AWAITING_HUMAN"}:
                existing_case.risk_level = max(existing_case.risk_level, risk, key=RANK.__getitem__)
            continue
        risk = max(
            [conflict.severity]
            + [finding_risk(db, f)[0] for f in findings if str(f.id) in conflict.finding_ids],
            key=RANK.__getitem__,
        )
        case = m.DecisionCase(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            conflict_id=conflict.id,
            title=f"Conflicting findings: {next((f.subject for f in findings if str(f.id) in conflict.finding_ids), conflict.topic_key)}"[
                :250
            ],
            question="Which supported outcome should be adopted?",
            reason_human_required="Evidence does not uniquely resolve the conflict",
            risk_level=risk,
            reversibility="unknown",
            attention_class="HUMAN_REQUIRED",
        )
        db.add(case)
        db.flush()
        for f in findings:
            if str(f.id) in conflict.finding_ids:
                evidence_ids = db.scalars(
                    select(m.FindingEvidence.evidence_id).where(
                        m.FindingEvidence.finding_id == f.id
                    )
                ).all()
                db.add(
                    m.DecisionOption(
                        workspace_id=task.workspace_id,
                        decision_case_id=case.id,
                        label=f.claim[:200],
                        description=f.claim,
                        supporting_evidence=[str(e) for e in evidence_ids],
                        opposing_evidence=[
                            str(e)
                            for other in findings
                            if str(other.id) in conflict.finding_ids
                            and other.cluster_id != f.cluster_id
                            for e in db.scalars(
                                select(m.FindingEvidence.evidence_id).where(
                                    m.FindingEvidence.finding_id == other.id
                                )
                            ).all()
                        ],
                        downstream_impact=f"Task {task.title}",
                        reversibility="unknown",
                    )
                )
    for finding in findings:
        if any(str(finding.id) in conflict.finding_ids for conflict in open_conflicts):
            continue
        level, irreversible, reason = finding_risk(db, finding)
        if (
            attention(risk=level, irreversible=irreversible, conflict=False, failed=False)
            != "HUMAN_REQUIRED"
        ):
            continue
        previous_case = db.scalar(
            select(m.DecisionCase)
            .where(
                m.DecisionCase.task_id == task.id,
                m.DecisionCase.conflict_id.is_(None),
                m.DecisionCase.status.in_(["OPEN", "AWAITING_HUMAN", "RESOLVED", "DISMISSED"]),
                m.DecisionCase.finding_id.in_(
                    [
                        f.id
                        for f in findings
                        if f.id == finding.id
                        or (finding.cluster_id is not None and f.cluster_id == finding.cluster_id)
                    ]
                ),
            )
            .order_by(m.DecisionCase.created_at.desc())
        )
        if previous_case:
            if previous_case.status in {"OPEN", "AWAITING_HUMAN"}:
                previous_case.risk_level = max(
                    previous_case.risk_level, level, key=RANK.__getitem__
                )
                previous_case.reversibility = (
                    "irreversible" if irreversible else previous_case.reversibility
                )
                continue
            if RANK[level] <= RANK[previous_case.risk_level]:
                continue
        case = m.DecisionCase(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            finding_id=finding.id,
            title=f"Risk decision: {finding.subject}"[:250],
            question=f"Proceed with the reported action: {finding.claim}?",
            reason_human_required=reason,
            risk_level=level,
            reversibility="irreversible" if irreversible else "unknown",
            attention_class="HUMAN_REQUIRED",
        )
        db.add(case)
        db.flush()
        for label in ("Proceed", "Do not proceed"):
            evidence_ids = db.scalars(
                select(m.FindingEvidence.evidence_id).where(
                    m.FindingEvidence.finding_id == finding.id
                )
            ).all()
            db.add(
                m.DecisionOption(
                    workspace_id=task.workspace_id,
                    decision_case_id=case.id,
                    label=label,
                    description=label,
                    supporting_evidence=[str(e) for e in evidence_ids],
                    opposing_evidence=[],
                    downstream_impact=f"Task {task.title}",
                    reversibility=case.reversibility,
                )
            )

    for notice in db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.task_id == task.id,
            m.AttentionItem.status == "OPEN",
            m.AttentionItem.classification == "BLOCKING",
            m.AttentionItem.kind.like("AGENT_UNRESOLVED:%"),
        )
    ).all():
        previous = (
            db.get(m.DecisionCase, notice.decision_case_id) if notice.decision_case_id else None
        )
        if previous and previous.status in {"OPEN", "AWAITING_HUMAN"}:
            continue
        case = m.DecisionCase(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            task_id=task.id,
            title=f"Blocked task: {task.title}"[:250],
            question=notice.detail,
            reason_human_required="Downstream work requires human clarification",
            risk_level="MEDIUM",
            reversibility="unknown",
            attention_class="BLOCKING",
        )
        db.add(case)
        db.flush()
        notice.decision_case_id = case.id
        for label in ("Provide clarification", "Stop this work"):
            db.add(
                m.DecisionOption(
                    workspace_id=task.workspace_id,
                    decision_case_id=case.id,
                    label=label,
                    description="Record your direction and rationale",
                    supporting_evidence=[],
                    opposing_evidence=[],
                    downstream_impact=f"Task {task.title}",
                    reversibility="unknown",
                )
            )


def _receipt(
    db: Session, task: m.Task, reduction_complete: bool, provider: StructuredModelProvider | None
) -> bool:
    runs = db.scalars(select(m.Run).where(m.Run.task_id == task.id)).all()
    active = active_run_ids(db, task.id)
    current_runs = [r for r in runs if r.id in active]
    if not current_runs or not all(r.status in TERMINAL for r in current_runs):
        task.status = "RUNNING" if runs else "OPEN"
        return reduction_complete
    findings = db.scalars(select(m.Finding).where(m.Finding.task_id == task.id)).all()
    verifications = db.scalars(
        select(m.Verification).where(m.Verification.task_id == task.id)
    ).all()
    conflicts = db.scalars(select(m.Conflict).where(m.Conflict.task_id == task.id)).all()
    cases = db.scalars(select(m.DecisionCase).where(m.DecisionCase.task_id == task.id)).all()
    artifacts = db.scalars(select(m.Artifact).where(m.Artifact.task_id == task.id)).all()
    decisions = (
        db.scalars(
            select(m.HumanDecision).where(
                m.HumanDecision.decision_case_id.in_([c.id for c in cases])
            )
        ).all()
        if cases
        else []
    )
    risks = db.scalars(
        select(m.Risk)
        .join(m.Finding, m.Risk.finding_id == m.Finding.id)
        .where(m.Risk.task_id == task.id, m.Finding.status == "ACTIVE")
    ).all()
    notices = db.scalars(
        select(m.AttentionItem).where(
            m.AttentionItem.task_id == task.id, m.AttentionItem.status == "OPEN"
        )
    ).all()
    content: dict[str, Any] = {
        "policy_version": "audit-3",
        "generated_at": m.now().isoformat(),
        "reduction_status": "SUCCEEDED" if reduction_complete else "FAILED",
        "outcome": "completed" if all(r.status == "SUCCEEDED" for r in current_runs) else "failed",
        "artifacts": [
            {"id": str(a.id), "name": a.original_name, "sha256": a.sha256} for a in artifacts
        ],
        "verification": [
            {"method": v.method, "status": v.status, "evidence_refs": v.evidence_refs}
            for v in verifications
        ],
        "resolved_conflicts": [
            str(c.id) for c in conflicts if c.status in {"AUTO_RESOLVED", "HUMAN_RESOLVED"}
        ],
        "human_decisions": [str(d.id) for d in decisions],
        "residual_risks": [
            {"level": r.level, "reason": r.reason} for r in risks if r.level in {"HIGH", "CRITICAL"}
        ],
        "unresolved_items": [str(c.id) for c in conflicts if c.status == "OPEN"]
        + [str(c.id) for c in cases if c.status in {"OPEN", "AWAITING_HUMAN"}]
        + [str(n.id) for n in notices],
        "runs_involved": [str(r.id) for r in runs],
        "current_runs": [str(r.id) for r in current_runs],
        "agent_reports": [
            {
                "run_id": str(r.id),
                "agent_name": r.agent_name,
                "summary": redact(agent_receipt.content.get("summary", "")[:500]),
                "self_reported": True,
            }
            for r in current_runs
            if (
                agent_receipt := db.scalar(
                    select(m.AgentReceipt).where(m.AgentReceipt.run_id == r.id)
                )
            )
        ],
        "finding_count": len(findings),
    }
    receipt = db.scalar(select(m.CompletionReceipt).where(m.CompletionReceipt.task_id == task.id))
    if provider and os.environ.get("DIRIMA_MODEL_RECEIPT_GENERATOR"):
        source_hash = fingerprint({k: v for k, v in content.items() if k != "generated_at"})
        if receipt and receipt.content.get("narrative_hash") == source_hash:
            content["summary"] = receipt.content.get("summary", "")
            content["narrative_hash"] = source_hash
        else:
            try:
                result = call_model_retry(
                    db,
                    task.workspace_id,
                    provider,
                    purpose="receipt_generator",
                    input_data=content,
                    output_schema=ReceiptSummary.model_json_schema(),
                    system_prompt="Summarize only these recorded facts for a human. Preserve unresolved items and verification failures. Do not assert any additional success.",
                )
                summary = ReceiptSummary.model_validate(result)
                content["summary"] = redact(summary.summary)
                content["narrative_hash"] = source_hash
                closed = close_model_notice(db, task, "Receipt generation failed")
                content["unresolved_items"] = [
                    item for item in content["unresolved_items"] if item not in closed
                ]
            except Exception as exc:
                reduction_complete = False
                content["reduction_status"] = "FAILED"
                task.status = "NEEDS_ATTENTION"
                alert(
                    db,
                    task.workspace_id,
                    task.id,
                    "MODEL_PROVIDER_ERROR"
                    if isinstance(exc, httpx.HTTPError)
                    else "MODEL_OUTPUT_INVALID",
                    "Receipt generation failed",
                    type(exc).__name__,
                )
                db.flush()
                content["unresolved_items"] += [
                    str(n.id)
                    for n in db.scalars(
                        select(m.AttentionItem).where(
                            m.AttentionItem.task_id == task.id,
                            m.AttentionItem.title == "Receipt generation failed",
                            m.AttentionItem.status == "OPEN",
                        )
                    ).all()
                ]
    task.status = (
        "NEEDS_ATTENTION"
        if content["unresolved_items"] or not reduction_complete
        else "COMPLETED"
        if content["outcome"] == "completed"
        else "FAILED"
    )
    if receipt:
        receipt.content = content
    else:
        db.add(
            m.CompletionReceipt(workspace_id=task.workspace_id, task_id=task.id, content=content)
        )
    return reduction_complete


def reduce_task(
    db: Session, task_id: uuid.UUID, provider: StructuredModelProvider | None = None
) -> bool:
    task = db.scalar(select(m.Task).where(m.Task.id == task_id).with_for_update())
    if not task:
        raise ValueError("Task not found")
    provider = provider if provider is not None else provider_from_env()
    unavailable = provider is None and any(
        os.environ.get(f"DIRIMA_MODEL_{role}")
        for role in (
            "EXTRACTOR",
            "CLUSTER",
            "CONFLICT_DETECTOR",
            "RESOLVER",
            "RISK_CLASSIFIER",
            "RECEIPT_GENERATOR",
        )
    )
    if unavailable:
        alert(
            db,
            task.workspace_id,
            task.id,
            "MODEL_PROVIDER_ERROR",
            "Configured model endpoint is unavailable",
            "Set the endpoint and its API key",
        )
    else:
        close_model_notice(db, task, "Configured model endpoint is unavailable")
    reduction_complete = _extract(db, task, provider) and not unavailable
    db.flush()
    reduction_complete = _cluster(db, task, provider) and reduction_complete
    db.flush()
    reduction_complete = _conflicts(db, task, provider) and reduction_complete
    db.flush()
    _verify(db, task)
    db.flush()
    if reduction_complete:
        reduction_complete = _resolve(db, task, provider)
    else:
        for conflict in db.scalars(
            select(m.Conflict).where(
                m.Conflict.task_id == task.id, m.Conflict.status == "AUTO_RESOLVED"
            )
        ).all():
            previous_resolution = conflict.resolution_id
            conflict.status, conflict.resolution_id = "OPEN", None
            audit(
                db,
                task.workspace_id,
                "dirima",
                "conflict.reopen",
                "conflict",
                conflict.id,
                {
                    "previous_resolution_id": str(previous_resolution),
                    "reason": "New source reduction is incomplete",
                },
            )
        for cluster in db.scalars(
            select(m.FindingCluster).where(
                m.FindingCluster.task_id == task.id, m.FindingCluster.status == "AUTO_RESOLVED"
            )
        ).all():
            cluster.status = "OPEN"
            audit(
                db,
                task.workspace_id,
                "dirima",
                "cluster.reopen",
                "finding_cluster",
                cluster.id,
                {"reason": "New source reduction is incomplete"},
            )
    db.flush()
    reduction_complete = _risk(db, task, provider) and reduction_complete
    db.flush()
    _attention(db, task)
    db.flush()
    _cases(db, task)
    db.flush()
    reduction_complete = reduction_complete and not bool(
        db.scalar(
            select(m.AttentionItem.id)
            .where(
                m.AttentionItem.task_id == task.id,
                m.AttentionItem.kind.in_(["MODEL_OUTPUT_INVALID", "MODEL_PROVIDER_ERROR"]),
                m.AttentionItem.status == "OPEN",
                m.AttentionItem.title != "Receipt generation failed",
            )
            .limit(1)
        )
    )
    return _receipt(db, task, reduction_complete, provider)
