import base64
import json
import uuid

import pytest
from sqlalchemy import func, select

from dirima import models as m
from dirima.providers.structured import ModelResult
from dirima.reduction.pipeline import reduce_task

from .helpers import event

pytestmark = pytest.mark.usefixtures("clean")


def imported_artifact(client, project_task, content):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started", "run.completed"):
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run, external, kind)
            ).status_code
            == 200
        )
    envelope = event(
        project,
        task,
        run,
        external,
        "artifact.created",
        {
            "name": "report.json",
            "mime_type": "application/json",
            "content_base64": base64.b64encode(content).decode(),
        },
    )
    response = client.post("/api/v1/events", json=envelope)
    assert response.status_code == 200, response.text
    assert (
        client.post("/api/v1/events", json=envelope).json()["artifact_id"]
        == response.json()["artifact_id"]
    )
    return uuid.UUID(task["id"]), uuid.UUID(run), uuid.UUID(response.json()["artifact_id"])


class ArtifactProvider:
    provider = "test"

    def __init__(self, fail_tail=False):
        self.inputs = []
        self.fail_tail = fail_tail

    def generate_structured(self, **kwargs):
        data = kwargs["input_data"]
        if kwargs["purpose"] == "extractor":
            self.inputs.append(data)
            if self.fail_tail and "LAST_MARKER" in json.dumps(data):
                raise ValueError("Invalid tail output")
            findings = []
            for artifact in data["artifacts"]:
                for marker in ("FIRST_MARKER", "LAST_MARKER"):
                    if marker in artifact["content"]:
                        findings.append(
                            {
                                "subject": marker,
                                "claim": f"Observed {marker}",
                                "evidence_refs": [artifact["id"]],
                            }
                        )
            result = {
                "findings": findings,
                "risks": [],
                "unresolved": [],
                "constraint_violations": [],
            }
        else:
            result = {
                "conflict": False,
                "reason": "independent observations",
                "finding_ids": [],
                "conflict_type": "NONE",
            }
        return ModelResult(result, "test", "configured", {})


def test_full_artifact_is_chunked_without_db_blob_or_secret_leak(
    db, client, project_task, monkeypatch, tmp_path
):
    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("DIRIMA_EXTRACTION_CHARS", "2048")
    content = json.dumps(
        {
            "first": "FIRST_MARKER",
            "api_key": "secret-value",
            "padding": "x" * 110000,
            "last": "LAST_MARKER",
        }
    ).encode()
    task_id, run_id, artifact_id = imported_artifact(client, project_task, content)
    provider = ArtifactProvider()
    assert reduce_task(db, task_id, provider)
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.Evidence)
            .where(m.Evidence.artifact_id == artifact_id)
        )
        == 2
    )
    payload = db.scalar(select(m.RunEvent.payload).where(m.RunEvent.type == "artifact.created"))
    assert "content_base64" not in payload
    assert payload["artifact_id"] == str(artifact_id)
    assert payload["size_bytes"] == len(content)
    assert len(provider.inputs) > 50
    assert "secret-value" not in json.dumps(provider.inputs)
    calls = len(provider.inputs)
    assert reduce_task(db, task_id, provider)
    db.commit()
    assert len(provider.inputs) == calls
    assert db.get(m.Run, run_id).extraction_hash


def test_failed_tail_does_not_commit_partial_extraction(
    db, client, project_task, monkeypatch, tmp_path
):
    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("DIRIMA_EXTRACTION_CHARS", "2048")
    task_id, run_id, _ = imported_artifact(
        client, project_task, b"FIRST_MARKER" + b"x" * 5000 + b"LAST_MARKER"
    )
    assert not reduce_task(db, task_id, ArtifactProvider(fail_tail=True))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 0
    assert db.get(m.Run, run_id).extraction_hash is None
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "FAILED"
    assert reduce_task(db, task_id, ArtifactProvider())
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 2


def test_corrupt_artifact_blocks_extraction_and_can_recover(
    db, client, project_task, monkeypatch, tmp_path
):
    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    original = b'"FIRST_MARKER"'
    task_id, run_id, artifact_id = imported_artifact(client, project_task, original)
    artifact = db.get(m.Artifact, artifact_id)
    path = tmp_path / artifact.storage_key
    path.write_bytes(b"changed")
    provider = ArtifactProvider()
    assert not reduce_task(db, task_id, provider)
    db.commit()
    assert not provider.inputs
    assert db.get(m.Run, run_id).extraction_hash is None
    assert db.scalar(select(m.AttentionItem.kind)).startswith("ARTIFACT_INTEGRITY_ERROR:")
    path.write_bytes(original)
    assert reduce_task(db, task_id, provider)
    db.commit()
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.AttentionItem)
            .where(
                m.AttentionItem.kind.like("ARTIFACT_INTEGRITY_ERROR:%"),
                m.AttentionItem.status == "OPEN",
            )
        )
        == 0
    )
