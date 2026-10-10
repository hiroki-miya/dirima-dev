"""Models can request review, but cannot bypass deterministic authority."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from dirima import models as m
from dirima.providers.structured import ModelResult
from dirima.reduction.pipeline import reduce_task

from .helpers import add_run, event

pytestmark = pytest.mark.usefixtures("clean")


class AdvisoryProvider:
    provider = "test"

    def __init__(self, *, veto=False, elevate=False):
        self.veto = veto
        self.elevate = elevate

    def generate_structured(self, **kwargs):
        purpose, data = kwargs["purpose"], kwargs["input_data"]
        if purpose == "cluster":
            result = {
                "equivalent": False,
                "reason": "different choices",
                "finding_ids": [f["id"] for f in data["findings"]],
            }
        elif purpose == "resolver":
            result = {
                "selected_finding_id": data["deterministic_candidate"],
                "requires_human": self.veto,
                "rationale": "Review deterministic evidence",
            }
        elif purpose == "risk_classifier":
            result = {
                "level": "HIGH" if self.elevate else "LOW",
                "irreversible": False,
                "reason": "Independent model risk advice",
            }
        elif purpose == "receipt_generator":
            result = {"summary": "Recorded results require the indicated review."}
        else:
            result = {
                "conflict": False,
                "reason": "no conflict",
                "finding_ids": [],
                "conflict_type": "NONE",
            }
        return ModelResult(result, "test", "configured-role", {"total_tokens": 10}, 0)


def setup_conflict(client, project_task):
    project, task = project_task
    client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": "PostgreSQL lock", "type": "HARD"},
    )
    add_run(
        client, project, task, "Claude", "claude", [{"subject": "Lock", "claim": "PostgreSQL lock"}]
    )
    add_run(client, project, task, "Codex", "gpt", [{"subject": "Lock", "claim": "Redis lock"}])
    return uuid.UUID(task["id"])


def test_all_configured_roles_preserve_recorded_facts(db, client, project_task, monkeypatch):
    task_id = setup_conflict(client, project_task)
    for role in ("RESOLVER", "RISK_CLASSIFIER", "RECEIPT_GENERATOR"):
        monkeypatch.setenv(f"DIRIMA_MODEL_{role}", "configured")
    assert reduce_task(db, task_id, AdvisoryProvider())
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"
    content = db.scalar(select(m.CompletionReceipt.content))
    assert content["resolved_conflicts"] and content["summary"]
    purposes = set(db.scalars(select(m.ModelCall.purpose)).all())
    assert {"resolver", "risk_classifier", "receipt_generator"} <= purposes
    calls = db.scalar(
        select(func.count())
        .select_from(m.ModelCall)
        .where(m.ModelCall.purpose == "receipt_generator")
    )
    assert reduce_task(db, task_id, AdvisoryProvider())
    db.commit()
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.ModelCall)
            .where(m.ModelCall.purpose == "receipt_generator")
        )
        == calls
    )


def test_model_veto_prevents_deterministic_auto_resolution(db, client, project_task, monkeypatch):
    task_id = setup_conflict(client, project_task)
    monkeypatch.setenv("DIRIMA_MODEL_RESOLVER", "configured")
    assert reduce_task(db, task_id, AdvisoryProvider(veto=True))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    assert db.scalar(select(m.DecisionCase.status)) == "AWAITING_HUMAN"


def test_model_risk_escalation_requires_human(db, client, project_task, monkeypatch):
    task_id = setup_conflict(client, project_task)
    monkeypatch.setenv("DIRIMA_MODEL_RISK_CLASSIFIER", "configured")
    assert reduce_task(db, task_id, AdvisoryProvider(elevate=True))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    assert db.scalar(select(m.DecisionCase.risk_level)) == "HIGH"
    assert db.scalar(select(m.Risk.level)) == "HIGH"


def test_configured_missing_endpoint_does_not_silently_resolve(
    db, client, project_task, monkeypatch
):
    task_id = setup_conflict(client, project_task)
    monkeypatch.setenv("DIRIMA_MODEL_RESOLVER", "configured")
    monkeypatch.delenv("DIRIMA_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("DIRIMA_MODEL_API_KEY", raising=False)
    assert not reduce_task(db, task_id)
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "FAILED"
    assert reduce_task(db, task_id, AdvisoryProvider())
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"


def test_model_cannot_lower_destructive_risk(db, client, project_task, monkeypatch):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Migration", "claim": "production DB destructive migration"}],
    )
    monkeypatch.setenv("DIRIMA_MODEL_RISK_CLASSIFIER", "configured")
    assert reduce_task(db, uuid.UUID(task["id"]), AdvisoryProvider())
    db.commit()
    assert db.scalar(select(m.Risk.level)) == "CRITICAL"
    assert db.scalar(select(m.Risk.irreversible)) is True


def test_provider_timeout_retries_with_backoff_and_provenance(
    db, client, project_task, monkeypatch
):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started", "run.completed"):
        client.post("/api/v1/events", json=event(project, task, run, external, kind))
    client.post(
        "/api/v1/events",
        json=event(project, task, run, external, "message.completed", {"text": "observation"}),
    )
    delays = []
    monkeypatch.setattr("dirima.reduction.pipeline.time.sleep", delays.append)

    class TimeoutProvider:
        provider = "test"
        calls = 0

        def generate_structured(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise httpx.ReadTimeout("provider timed out")
            return ModelResult(
                {"findings": [], "risks": [], "unresolved": [], "constraint_violations": []},
                "test",
                "configured",
                {},
            )

    assert reduce_task(db, uuid.UUID(task["id"]), TimeoutProvider())
    db.commit()
    assert delays == [1, 2]
    calls = db.scalars(select(m.ModelCall).order_by(m.ModelCall.started_at)).all()
    assert [c.status for c in calls] == ["FAILED", "FAILED", "SUCCEEDED"]
    assert all(c.ended_at and c.trace_id and c.input_hash for c in calls)


def test_failed_optional_roles_are_visible_and_can_recover(db, client, project_task, monkeypatch):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Migration", "claim": "production DB destructive migration"}],
    )
    for role in ("RISK_CLASSIFIER", "RECEIPT_GENERATOR"):
        monkeypatch.setenv(f"DIRIMA_MODEL_{role}", "configured")

    class InvalidProvider:
        provider = "test"

        def generate_structured(self, **kwargs):
            return "This looks fine to me."

    assert not reduce_task(db, uuid.UUID(task["id"]), InvalidProvider())
    db.commit()
    assert db.scalar(select(m.Risk.level)) == "CRITICAL"
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "FAILED"
    assert (
        db.scalar(
            select(func.count()).select_from(m.ModelCall).where(m.ModelCall.status == "FAILED")
        )
        == 6
    )
    assert reduce_task(db, uuid.UUID(task["id"]), AdvisoryProvider())
    db.commit()
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "SUCCEEDED"
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.AttentionItem)
            .where(m.AttentionItem.status == "OPEN", m.AttentionItem.kind == "MODEL_OUTPUT_INVALID")
        )
        == 0
    )
