from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dirima.adapters.durevi import DurableCandidate, eligible
from dirima.policy.core import attention, auto_resolvable, classify_risk, transition
from dirima.schemas import EventEnvelope
from dirima.security.redaction import redact


def test_transitions():
    transition("CREATED", "RUNNING")
    transition("RUNNING", "SUCCEEDED")
    with pytest.raises(ValueError):
        transition("SUCCEEDED", "RUNNING")


def test_risk_and_resolution():
    risk, irreversible, _ = classify_risk("production DB destructive migration")
    assert risk == "CRITICAL" and irreversible
    assert not auto_resolvable(
        risk=risk, irreversible=True, sensitive=True, passed=True, independent_families=10
    )
    assert auto_resolvable(
        risk="LOW", irreversible=False, sensitive=False, passed=True, independent_families=2
    )
    assert not auto_resolvable(
        risk="LOW", irreversible=False, sensitive=False, passed=False, independent_families=2
    )
    assert (
        attention(risk="LOW", irreversible=False, conflict=False, failed=False, verified=True)
        == "NO_ACTION"
    )
    assert (
        attention(risk="HIGH", irreversible=False, conflict=False, failed=False) == "HUMAN_REQUIRED"
    )


def test_redaction():
    assert redact({"api_key": "secret-value", "text": "API_KEY=secret-value Bearer abc123"}) == {
        "api_key": "[REDACTED]",
        "text": "API_KEY=[REDACTED] Bearer [REDACTED]",
    }


def test_deterministic_risk_floor_preserves_higher_reported_severity():
    assert classify_risk("Security review", "CRITICAL")[0] == "CRITICAL"
    assert classify_risk("Security review", "LOW")[0] == "HIGH"
    with pytest.raises(ValueError):
        classify_risk("Security review", "INVALID")


def test_event_requires_timezone_and_known_type():
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "project_id": str(uuid4()),
        "task_id": str(uuid4()),
        "run_id": str(uuid4()),
        "source": {"adapter": "generic", "runtime": "cli", "external_run_id": "x"},
        "type": "run.created",
        "occurred_at": datetime.now().isoformat(),
        "idempotency_key": "k",
        "payload": {},
    }
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(event)
    event["occurred_at"] += "+00:00"
    event["type"] = "token.delta"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(event)


def test_durevi_candidate_boundary():
    base = {
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "content": "Use PostgreSQL",
        "provenance_ref": "decision:123",
        "confirmed_by": "human",
    }
    assert eligible(DurableCandidate(kind="FINAL_HUMAN_DECISION", **base))
    assert not eligible(DurableCandidate(kind="RAW_TRANSCRIPT", **base))
