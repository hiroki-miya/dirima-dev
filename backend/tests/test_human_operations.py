import uuid

import pytest
from sqlalchemy import func, select

from dirima import models as m
from dirima.reduction.pipeline import reduce_task
from dirima.workers.queue import work_once

from .helpers import WORKSPACE, add_run, event

pytestmark = pytest.mark.usefixtures("clean")


def test_correction_preserves_original_and_supersedes_obsolete_case(db, client, project_task):
    project, task = project_task
    add_run(
        client, project, task, "Claude", "claude", [{"subject": "Lock", "claim": "PostgreSQL lock"}]
    )
    add_run(client, project, task, "Codex", "gpt", [{"subject": "Lock", "claim": "Redis lock"}])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    old = db.scalar(select(m.Finding).where(m.Finding.claim == "PostgreSQL lock"))
    case = client.get("/api/v1/decision-cases").json()[0]
    result = client.post(
        f"/api/v1/findings/{old.id}/correct",
        json={
            "claim": "Redis lock",
            "actor": "human",
            "rationale": "Corrected after reviewing the original",
        },
    )
    assert result.status_code == 201, result.text
    db.expire_all()
    assert old.claim == "PostgreSQL lock" and old.status == "SUPERSEDED"
    assert str(old.superseded_by) == result.json()["id"]
    assert client.get(f"/api/v1/decision-cases/{case['id']}").json()["status"] == "SUPERSEDED"
    assert client.get("/api/v1/decision-cases").json() == []
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.FindingEvidence)
            .where(m.FindingEvidence.finding_id == old.id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.FindingEvidence)
            .where(m.FindingEvidence.finding_id == uuid.UUID(result.json()["id"]))
        )
        == 2
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.AuditLog)
            .where(m.AuditLog.action == "finding.correct")
        )
        == 1
    )


def test_risk_override_reopens_auto_resolution(db, client, project_task):
    project, task = project_task
    client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": "PostgreSQL lock", "type": "HARD"},
    )
    add_run(
        client, project, task, "Claude", "claude", [{"subject": "Lock", "claim": "PostgreSQL lock"}]
    )
    add_run(client, project, task, "Codex", "gpt", [{"subject": "Lock", "claim": "Redis lock"}])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"
    finding = db.scalar(select(m.Finding))
    response = client.post(
        f"/api/v1/findings/{finding.id}/risk-override",
        json={"level": "HIGH", "actor": "reviewer", "rationale": "Additional impact discovered"},
    )
    assert response.status_code == 200
    db.expire_all()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    assert client.get("/api/v1/decision-cases").json()[0]["risk_level"] == "HIGH"


def test_superseding_failed_run_preserves_history_and_current_outcome(db, client, project_task):
    project, task = project_task
    original, corrected = str(uuid.uuid4()), str(uuid.uuid4())
    for run, status, payload in [
        (original, "failed", {}),
        (corrected, "succeeded", {"supersedes_run_id": original}),
    ]:
        receipt = {
            "schema_version": "1.0",
            "status": status,
            "summary": "Run result",
            "reported_findings": [{"subject": "Review", "claim": "First observation"}]
            if run == original
            else [],
        }
        for kind, data in [
            ("run.created", payload),
            ("run.started", {}),
            ("run.completed", {"receipt": receipt}),
        ]:
            response = client.post(
                "/api/v1/events", json=event(project, task, run, run, kind, data)
            )
            assert response.status_code == 200, response.text
        reduce_task(db, uuid.UUID(task["id"]))
        db.commit()
    db.expire_all()
    assert db.get(m.Run, uuid.UUID(original)).status == "FAILED"
    assert db.get(m.Run, uuid.UUID(corrected)).supersedes_run_id == uuid.UUID(original)
    assert db.scalar(select(m.Finding.status)) == "SUPERSEDED"
    receipt = client.get(f"/api/v1/tasks/{task['id']}/receipt").json()["content"]
    assert receipt["outcome"] == "completed"
    assert len(receipt["runs_involved"]) == 2 and len(receipt["current_runs"]) == 1
    assert receipt["unresolved_items"] == []


def test_blocking_question_reaches_inbox_and_can_be_decided(db, client, project_task):
    project, task = project_task
    run = str(uuid.uuid4())
    for kind, payload in [
        ("run.created", {}),
        ("run.started", {}),
        (
            "run.completed",
            {
                "receipt": {
                    "schema_version": "1.0",
                    "status": "succeeded",
                    "summary": "Blocked",
                    "unresolved": ["blocked on human clarification"],
                }
            },
        ),
    ]:
        client.post("/api/v1/events", json=event(project, task, run, run, kind, payload))
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    case = client.get("/api/v1/decision-cases").json()[0]
    assert case["attention_class"] == "BLOCKING"
    detail = client.get(f"/api/v1/decision-cases/{case['id']}").json()
    response = client.post(
        f"/api/v1/decision-cases/{case['id']}/resolve",
        json={
            "selected_option_id": detail["options"][0]["id"],
            "actor": "human",
            "rationale": "Clarified the required direction",
        },
    )
    assert response.status_code == 200
    assert client.get("/api/v1/decision-cases").json() == []
    assert client.get("/api/v1/attention").json() == []


def test_new_findings_supersede_stale_decision_options(db, client, project_task):
    project, task = project_task
    for name, claim in [("Claude", "PostgreSQL lock"), ("Codex", "Redis lock")]:
        add_run(client, project, task, name, name, [{"subject": "Lock", "claim": claim}])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    old = client.get("/api/v1/decision-cases").json()[0]
    old_detail = client.get(f"/api/v1/decision-cases/{old['id']}").json()
    add_run(client, project, task, "Other", "other", [{"subject": "Lock", "claim": "Another lock"}])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    new = client.get("/api/v1/decision-cases").json()[0]
    assert new["id"] != old["id"]
    assert len(client.get(f"/api/v1/decision-cases/{new['id']}").json()["options"]) == 3
    assert (
        client.post(
            f"/api/v1/decision-cases/{old['id']}/resolve",
            json={
                "selected_option_id": old_detail["options"][0]["id"],
                "actor": "human",
                "rationale": "Stale approval",
            },
        ).status_code
        == 409
    )


def test_malformed_job_can_exhaust_retries_without_secondary_failure(db, client, project_task):
    job = m.Job(workspace_id=uuid.UUID(WORKSPACE), job_type="unknown", payload={})
    db.add(job)
    db.commit()
    for attempt in range(4):
        assert work_once("test-worker")
        db.expire_all()
        if attempt < 3:
            job.available_at = m.now()
            db.commit()
    assert db.get(m.Job, job.id).status == "DEAD"
    assert db.scalar(select(m.AttentionItem.task_id)) is None
