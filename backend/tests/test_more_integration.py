"""Distinct failure paths and resolution grounds on real PostgreSQL."""

import base64
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from dirima import models as m
from dirima.providers.structured import ModelResult
from dirima.reduction.pipeline import reduce_task
from dirima.workers.queue import work_once

from .helpers import WORKSPACE, add_run, event

pytestmark = pytest.mark.usefixtures("clean")


def test_failed_job_retries_then_escalates(db, project_task):
    _, task = project_task
    job = m.Job(
        workspace_id=uuid.UUID(WORKSPACE), job_type="unknown", payload={"task_id": task["id"]}
    )
    db.add(job)
    db.commit()
    for attempt in range(4):
        assert work_once("retry-test")
        db.expire_all()
        assert db.get(m.Job, job.id).attempts == attempt + 1
        if attempt < 3:
            assert db.get(m.Job, job.id).status == "FAILED"
            db.get(m.Job, job.id).available_at = m.now()
            db.commit()
    assert db.get(m.Job, job.id).status == "DEAD"
    assert db.scalar(select(m.AttentionItem.kind)) == "REDUCTION_FAILED"


def test_artifact_integrity_and_filename_isolation(db, client, project_task, monkeypatch, tmp_path):
    project, task = project_task
    run_id, external = add_run(client, project, task, "Agent", "gpt", [])
    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    payload = {
        "name": "../../private/secret.txt",
        "mime_type": "text/plain",
        "content_base64": base64.b64encode(b"hello artifact").decode(),
    }
    response = client.post(
        "/api/v1/events", json=event(project, task, run_id, external, "artifact.created", payload)
    )
    assert response.status_code == 200
    artifact = db.scalar(select(m.Artifact))
    assert artifact.original_name == "secret.txt"
    assert artifact.sha256 == hashlib.sha256(b"hello artifact").hexdigest()
    assert (tmp_path / artifact.storage_key).read_bytes() == b"hello artifact"
    preview = client.get(f"/api/v1/artifacts/{artifact.id}/content")
    assert preview.status_code == 200
    assert preview.json()["content"] == "hello artifact"
    (tmp_path / artifact.storage_key).write_bytes(b"tampered")
    assert client.get(f"/api/v1/artifacts/{artifact.id}/content").status_code == 409
    invalid = client.post(
        "/api/v1/events",
        json=event(
            project, task, run_id, external, "artifact.created", {"content_base64": "not base64!"}
        ),
    )
    assert invalid.status_code == 400
    assert db.scalar(select(func.count()).select_from(m.Artifact)) == 1


def test_hard_constraint_uniquely_resolves_low_risk_conflict(db, client, project_task):
    project, task = project_task
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/constraints",
            json={"statement": "PostgreSQL lock", "type": "HARD"},
        ).status_code
        == 201
    )
    add_run(
        client, project, task, "Claude", "claude", [{"subject": "Lock", "claim": "PostgreSQL lock"}]
    )
    add_run(client, project, task, "Codex", "gpt", [{"subject": "Lock", "claim": "Redis lock"}])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"
    assert db.scalar(select(m.Resolution.method)) == "AUTHORITATIVE_CONSTRAINT"
    assert db.scalar(select(func.count()).select_from(m.DecisionCase)) == 0


def test_deterministic_test_rejects_one_conflicting_option(db, client, project_task):
    project, task = project_task
    first, first_external = add_run(
        client, project, task, "Claude", "claude", [{"subject": "Lock", "claim": "PostgreSQL lock"}]
    )
    second, second_external = add_run(
        client, project, task, "Codex", "gpt", [{"subject": "Lock", "claim": "Redis lock"}]
    )
    for run, external, status, code in [
        (first, first_external, "PASSED", 0),
        (second, second_external, "FAILED", 1),
    ]:
        assert (
            client.post(
                "/api/v1/events",
                json=event(
                    project,
                    task,
                    run,
                    external,
                    "verification.reported",
                    {
                        "subject": "Lock",
                        "method": "pytest",
                        "command_result": {
                            "command": ["pytest"],
                            "exit_code": code,
                            "stdout": status,
                            "stderr": "",
                            "observer": "generic",
                        },
                    },
                ),
            ).status_code
            == 200
        )
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"
    assert db.scalar(select(m.Resolution.method)) == "DETERMINISTIC_TEST"


def test_receipt_risk_and_reported_verification_remain_distinct(db, client, project_task):
    project, task = project_task
    run_id, external = str(uuid.uuid4()), str(uuid.uuid4())
    receipt = {
        "schema_version": "1.0",
        "status": "succeeded",
        "summary": "Done",
        "artifacts": [],
        "reported_findings": [],
        "reported_verifications": [{"method": "pytest", "status": "PASSED"}],
        "reported_risks": [{"description": "privacy data exposure"}],
        "unresolved": ["blocked on review"],
    }
    for kind, payload in [
        ("run.created", {}),
        ("run.started", {}),
        ("run.completed", {"receipt": receipt}),
    ]:
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run_id, external, kind, payload)
            ).status_code
            == 200
        )
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Verification.status)) == "INCONCLUSIVE"
    assert db.scalar(select(m.Risk.level)) == "HIGH"
    assert db.scalar(select(m.AttentionItem.classification)) == "BLOCKING"
    assert db.scalar(select(m.DecisionCase.status)) == "AWAITING_HUMAN"


def test_receipt_waits_for_new_run_to_finish(db, client, project_task):
    project, task = project_task
    add_run(client, project, task, "First", "first", [])
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    path = f"/api/v1/tasks/{task['id']}/receipt"
    assert client.get(path).status_code == 200

    run_id, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started"):
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run_id, external, kind)
            ).status_code
            == 200
        )
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert client.get(path).status_code == 404

    receipt = {
        "schema_version": "1.0",
        "status": "succeeded",
        "summary": "Second run done",
        "artifacts": [],
        "reported_findings": [],
        "reported_verifications": [],
        "reported_risks": [],
        "unresolved": [],
    }
    assert (
        client.post(
            "/api/v1/events",
            json=event(project, task, run_id, external, "run.completed", {"receipt": receipt}),
        ).status_code
        == 200
    )
    reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    response = client.get(path)
    assert response.status_code == 200
    assert len(response.json()["content"]["runs_involved"]) == 2


def test_semantic_cluster_preserves_two_original_findings(db, client, project_task):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Claude",
        "claude",
        [{"subject": "Lock", "claim": "Use PostgreSQL advisory lock"}],
    )
    add_run(
        client,
        project,
        task,
        "Codex",
        "gpt",
        [{"subject": "Lock", "claim": "Use a PG advisory lock"}],
    )

    class EquivalentProvider:
        provider = "fake"

        def generate_structured(self, **kwargs):
            findings = kwargs["input_data"]["findings"]
            return ModelResult(
                {
                    "equivalent": True,
                    "reason": "same mechanism",
                    "finding_ids": [f["id"] for f in findings],
                },
                "fake",
                "semantic",
                {},
            )

    reduce_task(db, uuid.UUID(task["id"]), provider=EquivalentProvider())
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 2
    assert db.scalar(select(func.count()).select_from(m.FindingCluster)) == 1
    assert db.scalar(select(func.count()).select_from(m.Conflict)) == 0
    assert db.scalar(select(m.ModelCall.purpose)) == "cluster"


def test_late_failed_verification_reopens_cluster(db, client, project_task):
    project, task = project_task
    claim = {"subject": "Lock", "claim": "PostgreSQL advisory lock"}
    run, external = add_run(client, project, task, "Claude", "claude", [claim])
    add_run(client, project, task, "Codex", "gpt", [claim])
    for status, code in [("PASSED", 0), ("FAILED", 1)]:
        assert (
            client.post(
                "/api/v1/events",
                json=event(
                    project,
                    task,
                    run,
                    external,
                    "verification.reported",
                    {
                        "subject": "Lock",
                        "method": "pytest",
                        "command_result": {
                            "command": ["pytest"],
                            "exit_code": code,
                            "stdout": status,
                            "stderr": "",
                            "observer": "generic",
                        },
                    },
                ),
            ).status_code
            == 200
        )
        reduce_task(db, uuid.UUID(task["id"]))
        db.commit()
        assert db.scalar(select(m.FindingCluster.status)) == (
            "AUTO_RESOLVED" if status == "PASSED" else "OPEN"
        )
    assert client.get(f"/api/v1/tasks/{task['id']}/receipt").json()["content"]["unresolved_items"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.AuditLog)
            .where(m.AuditLog.action == "cluster.reopen")
        )
        == 1
    )


def test_late_transcript_is_extracted_without_duplicate_findings(db, client, project_task):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started", "run.completed"):
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run, external, kind)
            ).status_code
            == 200
        )

    class Provider:
        provider = "test"
        extractions = 0

        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == "extractor":
                self.extractions += 1
                data = {
                    "findings": [
                        {"subject": f"Finding {i}", "claim": f"Observation {i}"}
                        for i in range(len(kwargs["input_data"]["events"]))
                    ],
                    "risks": [],
                    "unresolved": [],
                    "constraint_violations": [],
                }
            else:
                data = {
                    "conflict": False,
                    "reason": "independent observations",
                    "finding_ids": [],
                    "conflict_type": "NONE",
                }
            return ModelResult(data, "test", "configured", {})

    provider = Provider()
    for text in ("first transcript", "late transcript"):
        client.post(
            "/api/v1/events",
            json=event(project, task, run, external, "message.completed", {"text": text}),
        )
        reduce_task(db, uuid.UUID(task["id"]), provider)
        db.commit()
        reduce_task(db, uuid.UUID(task["id"]), provider)
        db.commit()
    assert provider.extractions == 2
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 2


def test_failed_extraction_job_is_not_completed(db, client, project_task):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started", "run.completed"):
        client.post("/api/v1/events", json=event(project, task, run, external, kind))
    client.post(
        "/api/v1/events",
        json=event(
            project, task, run, external, "message.completed", {"text": "Requires extraction"}
        ),
    )
    assert work_once("no-model-worker")
    db.expire_all()
    assert db.scalar(select(m.Job.status).order_by(m.Job.created_at)) == "DEAD"
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "FAILED"
