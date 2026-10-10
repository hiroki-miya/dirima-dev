import uuid

import pytest
from sqlalchemy import func, select

from dirima import models as m
from dirima.providers.structured import ModelResult
from dirima.reduction.pipeline import reduce_task

from .helpers import add_run, event

pytestmark = pytest.mark.usefixtures("clean")


def test_long_claims_do_not_merge_on_shared_prefix(db, client, project_task):
    project, task = project_task
    prefix = "shared context " * 40
    for name, ending in (("Claude", "Option A"), ("Codex", "Option B")):
        add_run(
            client, project, task, name, name, [{"subject": "Choice", "claim": prefix + ending}]
        )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.FindingCluster)) == 2
    assert db.scalar(select(m.Conflict.status)) == "OPEN"


def test_hard_constraint_requires_whole_claim_match(db, client, project_task):
    project, task = project_task
    prefix = "shared context " * 40
    client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": prefix + "Option C", "type": "HARD"},
    )
    for name, claim in (("Claude", prefix + "Option A"), ("Codex", "Option B")):
        add_run(client, project, task, name, name, [{"subject": "Choice", "claim": claim}])
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"


def test_constraint_replacement_rechecks_auto_resolution(db, client, project_task):
    project, task = project_task
    constraint = client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": "Option A", "type": "HARD"},
    ).json()
    for name, claim in (("Claude", "Option A"), ("Codex", "Option B")):
        add_run(client, project, task, name, name, [{"subject": "Choice", "claim": claim}])
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Resolution.selected_finding_id)) == db.scalar(
        select(m.Finding.id).where(m.Finding.claim == "Option A")
    )
    assert (
        client.post(
            f"/api/v1/constraints/{constraint['id']}/supersede",
            json={"statement": "Option B", "actor": "human"},
        ).status_code
        == 201
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    conflict = db.scalar(select(m.Conflict))
    assert db.get(m.Resolution, conflict.resolution_id).selected_finding_id == db.scalar(
        select(m.Finding.id).where(m.Finding.claim == "Option B")
    )


class SemanticProvider:
    provider = "test"

    def __init__(self, fail=False):
        self.fail, self.cluster_calls, self.pairs = fail, 0, []

    def generate_structured(self, **kwargs):
        data = kwargs["input_data"]
        if kwargs["purpose"] == "cluster":
            self.cluster_calls += 1
            if self.fail:
                raise ValueError("temporarily invalid")
            result = {
                "equivalent": False,
                "reason": "different",
                "finding_ids": [f["id"] for f in data["findings"]],
            }
        elif kwargs["purpose"] == "conflict_detector":
            self.pairs.append({f["claim"] for f in data["findings"]})
            result = {
                "conflict": False,
                "reason": "different",
                "finding_ids": [],
                "conflict_type": "NONE",
            }
        else:
            result = {
                "findings": [{"subject": "Transcript", "claim": "Runtime observation"}],
                "risks": [],
                "unresolved": [],
                "constraint_violations": [],
            }
        return ModelResult(result, "test", "fixture", {})


def test_semantic_clustering_retries_after_failure(db, client, project_task):
    project, task = project_task
    for name, claim in (("Claude", "Option A"), ("Codex", "Option B")):
        add_run(client, project, task, name, name, [{"subject": "Choice", "claim": claim}])
    assert not reduce_task(db, uuid.UUID(task["id"]), SemanticProvider(fail=True))
    db.commit()
    recovered = SemanticProvider()
    assert reduce_task(db, uuid.UUID(task["id"]), recovered)
    db.commit()
    assert recovered.cluster_calls >= 1


def test_cross_topic_detector_compares_every_distinct_claim(db, client, project_task):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [
            {"subject": "One", "claim": "A"},
            {"subject": "One", "claim": "B"},
            {"subject": "Two", "claim": "C"},
        ],
    )
    provider = SemanticProvider()
    assert reduce_task(db, uuid.UUID(task["id"]), provider)
    db.commit()
    assert {"B", "C"} in provider.pairs


def test_receipt_does_not_hide_late_transcript_or_tool_result(db, client, project_task):
    project, task = project_task
    run, external = add_run(
        client, project, task, "Agent", "gpt", [{"subject": "Receipt", "claim": "Original claim"}]
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    for kind in ("message.completed", "tool.failed"):
        client.post(
            "/api/v1/events",
            json=event(project, task, run, external, kind, {"text": "Runtime observation"}),
        )
    provider = SemanticProvider()
    assert reduce_task(db, uuid.UUID(task["id"]), provider)
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 2


def test_maximum_identity_keys_ingest_without_database_overflow(db, client, project_task):
    project, task = project_task
    run, external = add_run(client, project, task, "Agent", "gpt", [])
    for use_external in (False, True):
        envelope = event(
            project, task, run, external, "message.completed", {"text": "boundary"}, "k" * 200
        )
        if use_external:
            envelope["source"]["external_event_id"] = "e" * 200
        response = client.post("/api/v1/events", json=envelope)
        assert response.status_code == 200, response.text
        assert client.post("/api/v1/events", json=envelope).json()["duplicate"]


def test_malformed_usage_does_not_break_metrics(client, project_task):
    project, task = project_task
    run, external = add_run(client, project, task, "Agent", "gpt", [])
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "message.completed",
            {"usage": {"input_tokens": "unknown", "output_tokens": {}}, "size_bytes": []},
        ),
    )
    assert client.get("/api/v1/metrics").status_code == 200


def command(code=0, observer="generic"):
    return {
        "command": ["pytest", "tests/target.py"],
        "exit_code": code,
        "stdout": "captured",
        "stderr": "",
        "observer": observer,
    }


@pytest.mark.parametrize(
    "result",
    [
        None,
        command(True),
        command(observer="other"),
        {**command(), "command": []},
        {**command(), "stdout": {}},
    ],
)
def test_unobserved_or_invalid_command_remains_inconclusive(db, client, project_task, result):
    project, task = project_task
    run, external = add_run(
        client, project, task, "Agent", "gpt", [{"subject": "Choice", "claim": "A"}]
    )
    payload = {"subject": "Choice", "method": "latest update", "status": "PASSED", "exit_code": 0}
    if result is not None:
        payload["command_result"] = result
    client.post(
        "/api/v1/events", json=event(project, task, run, external, "verification.reported", payload)
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Verification.status)) == "INCONCLUSIVE"
    assert db.scalar(select(m.FindingCluster.status)) == "OPEN"


def test_ambiguous_subject_is_not_task_wide_pass(db, client, project_task):
    project, task = project_task
    run, external = add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "verification.reported",
            {"subject": "Choice", "command_result": command()},
        ),
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    check = db.scalar(select(m.Verification))
    assert check.status == "INCONCLUSIVE" and check.finding_id is None


def test_late_failure_reopens_auto_conflict_and_preserves_history(db, client, project_task):
    project, task = project_task
    client.post(
        f"/api/v1/projects/{project['id']}/constraints", json={"statement": "A", "type": "HARD"}
    )
    run, external = add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    first = db.scalar(select(m.Conflict))
    assert first.status == "AUTO_RESOLVED"
    selected = db.scalar(select(m.Finding.id).where(m.Finding.claim == "A"))
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "verification.reported",
            {"finding_id": str(selected), "command_result": command(1)},
        ),
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.get(m.Conflict, first.id).status == "OPEN"
    assert db.scalar(select(func.count()).select_from(m.Resolution)) == 1
    assert db.scalar(select(m.DecisionCase.status)) == "AWAITING_HUMAN"


def test_auto_resolution_retires_old_human_case(db, client, project_task):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    case_id = db.scalar(select(m.DecisionCase.id))
    client.post(
        f"/api/v1/projects/{project['id']}/constraints", json={"statement": "A", "type": "HARD"}
    )
    assert client.get(f"/api/v1/tasks/{task['id']}/receipt").status_code == 404
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.get(m.DecisionCase, case_id).status == "SUPERSEDED"
    assert not db.scalar(
        select(m.AttentionItem.id).where(
            m.AttentionItem.decision_case_id == case_id, m.AttentionItem.status == "OPEN"
        )
    )


def test_legal_subject_boundary_and_invalid_metadata(db, client, project_task):
    project, task = project_task
    subject = "x" * 250
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": subject, "claim": "A"}, {"subject": subject, "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert len(db.scalar(select(m.DecisionCase.title))) == 250
    invalid = event(
        project,
        task,
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        "run.created",
        {"agent_name": "x" * 101},
    )
    assert client.post("/api/v1/events", json=invalid).status_code == 400
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/constraints",
            json={"statement": "A", "type": "HARD", "scope": "x" * 201},
        ).status_code
        == 422
    )


def test_generic_and_dirima_tokens_are_redacted():
    from dirima.security.redaction import redact

    assert "sensitive-value" not in str(
        redact(
            {
                "DIRIMA_TOKEN": "sensitive-value",
                "text": 'TOKEN="sensitive-value" DIRIMA_TOKEN=sensitive-value',
            }
        )
    )


@pytest.mark.parametrize(
    "name", ["POSTGRES_PASSWORD", "DIRIMA_DB_PASSWORD", "DIRIMA_MODEL_API_KEY"]
)
def test_prefixed_credentials_are_redacted(name):
    from dirima.security.redaction import redact

    for value in [f"{name}=sensitive-value", f'"{name}": "sensitive-value"']:
        assert "sensitive-value" not in redact(value)


def test_concurrent_constraint_replacements_have_one_successor(db, client, project_task):
    from concurrent.futures import ThreadPoolExecutor

    project, _ = project_task
    previous = client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": "Original", "type": "HARD"},
    ).json()

    def replace(value):
        return client.post(
            f"/api/v1/constraints/{previous['id']}/supersede",
            json={"statement": value, "actor": "human"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(replace, ["A", "B"])) == [201, 409]
    assert (
        db.scalar(
            select(func.count()).select_from(m.Constraint).where(m.Constraint.status == "ACTIVE")
        )
        == 1
    )


def test_receipt_cannot_bypass_binary_artifact_integrity(
    db, client, project_task, monkeypatch, tmp_path
):
    import base64

    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    project, task = project_task
    run, external = add_run(
        client, project, task, "Agent", "gpt", [{"subject": "Choice", "claim": "A"}]
    )
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "artifact.created",
            {
                "content_base64": base64.b64encode(b"original").decode(),
                "mime_type": "application/octet-stream",
            },
        ),
    )
    artifact = db.scalar(select(m.Artifact))
    (tmp_path / artifact.storage_key).write_bytes(b"corrupted")
    assert not reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.CompletionReceipt.content))["reduction_status"] == "FAILED"


def test_explicit_target_tracks_active_correction_from_review_run(db, client, project_task):
    project, task = project_task
    add_run(client, project, task, "Agent", "gpt", [{"subject": "Choice", "claim": "A"}])
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    original = db.scalar(select(m.Finding.id))
    response = client.post(
        f"/api/v1/findings/{original}/correct",
        json={"claim": "B", "actor": "human", "rationale": "Correction"},
    )
    assert response.status_code == 201
    corrected = response.json()["id"]
    run, external = add_run(client, project, task, "Reviewer", "claude", [])
    for target in [str(original), corrected]:
        client.post(
            "/api/v1/events",
            json=event(
                project,
                task,
                run,
                external,
                "verification.reported",
                {"finding_id": target, "command_result": command()},
            ),
        )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    checks = db.scalars(select(m.Verification).order_by(m.Verification.performed_at)).all()
    assert checks[0].status == "INCONCLUSIVE" and checks[0].finding_id is None
    assert checks[1].status == "PASSED" and str(checks[1].finding_id) == corrected


def test_long_high_risk_case_is_stable_on_reduction(db, client, project_task):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "x" * 250, "claim": "Approve production deployment", "severity": "HIGH"}],
    )
    for _ in range(2):
        assert reduce_task(db, uuid.UUID(task["id"]))
        db.commit()
    assert db.scalar(select(func.count()).select_from(m.DecisionCase)) == 1


def test_reclustering_invalidates_obsolete_conflict_and_case(db, client, project_task):
    class EquivalentProvider(SemanticProvider):
        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == "cluster":
                return ModelResult(
                    {
                        "equivalent": True,
                        "reason": "same",
                        "finding_ids": [f["id"] for f in kwargs["input_data"]["findings"]],
                    },
                    "test",
                    "fixture",
                    {},
                )
            return super().generate_structured(**kwargs)

    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [
            {"subject": "Lock", "claim": "PostgreSQL advisory locking"},
            {"subject": "Lock", "claim": "Use database advisory locks"},
        ],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    for finding in db.scalars(select(m.Finding)).all():
        finding.cluster_id = None
    for cluster in db.scalars(select(m.FindingCluster)).all():
        cluster.topic_key = "legacy:" + str(cluster.id)
    db.commit()
    assert reduce_task(db, uuid.UUID(task["id"]), EquivalentProvider())
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "INVALIDATED"
    assert db.scalar(select(m.DecisionCase.status)) == "SUPERSEDED"
    assert not db.scalar(
        select(m.AttentionItem.id).where(
            m.AttentionItem.status == "OPEN", m.AttentionItem.decision_case_id.is_not(None)
        )
    )


@pytest.mark.parametrize("uncertain", ["INCONCLUSIVE", "PENDING", "ERROR"])
def test_uncertain_opponent_blocks_authoritative_resolution(db, client, project_task, uncertain):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    opponent = db.scalar(select(m.Finding).where(m.Finding.claim == "B"))
    db.add(
        m.Verification(
            workspace_id=opponent.workspace_id,
            task_id=opponent.task_id,
            finding_id=opponent.id,
            run_id=opponent.run_id,
            method="pytest",
            status=uncertain,
            evidence_refs=[],
            performed_by="fixture",
            performed_at=m.now(),
        )
    )
    db.commit()
    client.post(
        f"/api/v1/projects/{project['id']}/constraints", json={"statement": "A", "type": "HARD"}
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"


def test_model_output_bounds_are_validated_before_storage():
    from pydantic import ValidationError

    from dirima.schemas import ConflictClassification, Extraction

    for field in ["risks", "constraint_violations"]:
        payload = {"findings": [], "risks": [], "unresolved": [], "constraint_violations": []}
        payload[field] = [""]
        with pytest.raises(ValidationError):
            Extraction.model_validate(payload)
    with pytest.raises(ValidationError):
        ConflictClassification.model_validate(
            {"conflict": False, "reason": "", "finding_ids": [], "conflict_type": "x" * 101}
        )


def test_verification_before_completion_is_classified_after_extraction(db, client, project_task):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind, payload in [
        ("run.created", {}),
        ("run.started", {}),
        ("verification.reported", {"subject": "Choice", "command_result": command()}),
    ]:
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run, external, kind, payload)
            ).status_code
            == 200
        )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Verification)) == 0
    assert (
        client.post(
            "/api/v1/events",
            json=event(
                project,
                task,
                run,
                external,
                "run.completed",
                {
                    "receipt": {
                        "schema_version": "1.0",
                        "status": "succeeded",
                        "summary": "done",
                        "reported_findings": [{"subject": "Choice", "claim": "A"}],
                    }
                },
            ),
        ).status_code
        == 200
    )
    for _ in range(2):
        assert reduce_task(db, uuid.UUID(task["id"]))
        db.commit()
    check = db.scalar(select(m.Verification))
    assert check.status == "PASSED" and check.finding_id is not None
    assert db.scalar(select(func.count()).select_from(m.Verification)) == 1
    assert (
        db.scalar(
            select(func.count()).select_from(m.Evidence).where(m.Evidence.type == "TEST_RESULT")
        )
        == 1
    )


def test_sequence_bounds_are_rejected_at_ingress(client, project_task):
    project, task = project_task
    run, external = add_run(client, project, task, "Agent", "gpt", [])
    for sequence in [-1, 2**31, True]:
        payload = event(project, task, run, external, "message.completed", {})
        payload["external_sequence"] = sequence
        assert client.post("/api/v1/events", json=payload).status_code == 400


def test_redaction_preserves_usage_counters():
    from dirima.security.redaction import redact

    assert redact({"input_tokens": 10, "output_tokens": 20}) == {
        "input_tokens": 10,
        "output_tokens": 20,
    }


def test_completion_payload_without_receipt_is_extracted(db, client, project_task):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind, payload in [
        ("run.created", {}),
        ("run.started", {}),
        ("run.completed", {"summary": "Runtime observation"}),
    ]:
        assert (
            client.post(
                "/api/v1/events", json=event(project, task, run, external, kind, payload)
            ).status_code
            == 200
        )
    assert reduce_task(db, uuid.UUID(task["id"]), SemanticProvider())
    db.commit()
    assert db.scalar(select(m.Finding.claim)) == "Runtime observation"


def test_duplicate_project_names_return_conflict_under_concurrency(db, client, project_task):
    from concurrent.futures import ThreadPoolExecutor

    name = "same-name-" + str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda _: client.post("/api/v1/projects", json={"name": name}).status_code, range(2)
            )
        )
    assert sorted(statuses) == [201, 409]
    assert db.scalar(select(func.count()).select_from(m.Project).where(m.Project.name == name)) == 1


@pytest.mark.parametrize("purpose", ["cluster", "conflict_detector"])
def test_semantic_result_with_wrong_references_is_visible_and_retried(
    db, client, project_task, purpose
):
    class WrongReferences(SemanticProvider):
        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == purpose:
                self.cluster_calls += 1
                result = {
                    "reason": "positive",
                    "finding_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                }
                if purpose == "cluster":
                    result["equivalent"] = True
                else:
                    result.update(conflict=True, conflict_type="RECOMMENDATION")
                return ModelResult(result, "test", "fixture", {})
            return super().generate_structured(**kwargs)

    project, task = project_task
    second_topic = "Choice" if purpose == "cluster" else "Other"
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": second_topic, "claim": "B"}],
    )
    provider = WrongReferences()
    assert not reduce_task(db, uuid.UUID(task["id"]), provider)
    db.commit()
    assert provider.cluster_calls == 3
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.ModelCall)
            .where(m.ModelCall.purpose == purpose, m.ModelCall.status == "FAILED")
        )
        == 3
    )
    assert db.scalar(
        select(m.AttentionItem.id).where(
            m.AttentionItem.kind == "MODEL_OUTPUT_INVALID", m.AttentionItem.status == "OPEN"
        )
    )


@pytest.mark.parametrize(
    "value",
    [
        '"first\'sensitive-tail"',
        "'first\"sensitive-tail'",
        '"first\\"sensitive-tail"',
        "'first\\'sensitive-tail'",
    ],
)
def test_quoted_credentials_do_not_leak_after_other_or_escaped_quotes(value):
    from dirima.security.redaction import redact

    assert redact("POSTGRES_PASSWORD=" + value) == "POSTGRES_PASSWORD=[REDACTED]"


@pytest.mark.parametrize(
    "value",
    ['"sensitive tail with spaces', "'sensitive tail with spaces", '"sensitive tail with spaces\\'],
)
def test_truncated_quoted_credentials_are_fully_redacted(value):
    from dirima.security.redaction import redact

    assert redact("PASSWORD=" + value) == "PASSWORD=[REDACTED]"


class RecoveryProvider(SemanticProvider):
    def generate_structured(self, **kwargs):
        if kwargs["purpose"] in {"risk_classifier", "receipt_generator"}:
            if self.fail:
                raise ValueError("temporarily unavailable")
            result = (
                {"level": "LOW", "irreversible": False, "reason": "Low impact"}
                if kwargs["purpose"] == "risk_classifier"
                else {"summary": "Recorded completion"}
            )
            return ModelResult(result, "test", "fixture", {})
        return super().generate_structured(**kwargs)


def test_receipt_recovery_updates_task_status_immediately(db, client, project_task, monkeypatch):
    monkeypatch.setenv("DIRIMA_MODEL_RECEIPT_GENERATOR", "fixture")
    project, task = project_task
    run, external = add_run(
        client, project, task, "Agent", "gpt", [{"subject": "Choice", "claim": "A"}]
    )
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "verification.reported",
            {"subject": "Choice", "command_result": command()},
        ),
    )
    assert not reduce_task(db, uuid.UUID(task["id"]), RecoveryProvider(fail=True))
    db.commit()
    assert db.get(m.Task, uuid.UUID(task["id"])).status == "NEEDS_ATTENTION"
    assert reduce_task(db, uuid.UUID(task["id"]), RecoveryProvider())
    db.commit()
    assert db.get(m.Task, uuid.UUID(task["id"])).status == "COMPLETED"
    assert not db.scalar(select(m.CompletionReceipt.content))["unresolved_items"]


def test_risk_recovery_retires_temporary_human_case(db, client, project_task, monkeypatch):
    monkeypatch.setenv("DIRIMA_MODEL_RISK_CLASSIFIER", "fixture")
    project, task = project_task
    run, external = add_run(
        client, project, task, "Agent", "gpt", [{"subject": "Choice", "claim": "A"}]
    )
    assert not reduce_task(db, uuid.UUID(task["id"]), RecoveryProvider(fail=True))
    db.commit()
    assert db.scalar(select(m.DecisionCase.status)) == "AWAITING_HUMAN"
    assert reduce_task(db, uuid.UUID(task["id"]), RecoveryProvider())
    db.commit()
    assert db.scalar(select(m.Risk.level)) == "LOW"
    assert db.scalar(select(m.DecisionCase.status)) == "SUPERSEDED"
    assert not db.scalar(
        select(m.AttentionItem.id).where(
            m.AttentionItem.status == "OPEN", m.AttentionItem.decision_case_id.is_not(None)
        )
    )

    class RaisedRiskProvider(RecoveryProvider):
        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == "extractor":
                return ModelResult(
                    {
                        "findings": [{"subject": "Choice", "claim": "A", "severity": "HIGH"}],
                        "risks": [],
                        "unresolved": [],
                        "constraint_violations": [],
                    },
                    "test",
                    "fixture",
                    {},
                )
            return super().generate_structured(**kwargs)

    client.post(
        "/api/v1/events",
        json=event(
            project, task, run, external, "message.completed", {"text": "New high impact source"}
        ),
    )
    assert reduce_task(db, uuid.UUID(task["id"]), RaisedRiskProvider())
    db.commit()
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.DecisionCase)
            .where(m.DecisionCase.status == "AWAITING_HUMAN")
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.DecisionCase)
            .where(m.DecisionCase.status == "SUPERSEDED")
        )
        == 1
    )
    assert db.scalar(select(m.CompletionReceipt.content))["unresolved_items"]


def test_late_source_escalation_and_evidence_merge_are_retained(
    db, client, project_task, monkeypatch, tmp_path
):
    import base64

    monkeypatch.setenv("DIRIMA_ARTIFACT_DIR", str(tmp_path))
    project, task = project_task
    run, external = add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A", "severity": "LOW"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    response = client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "artifact.created",
            {
                "content_base64": base64.b64encode(b"high impact source").decode(),
                "mime_type": "text/plain",
            },
        ),
    )
    artifact_id = response.json()["artifact_id"]

    class EscalatingProvider(SemanticProvider):
        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == "extractor":
                return ModelResult(
                    {
                        "findings": [
                            {
                                "subject": "Choice",
                                "claim": "A",
                                "severity": "HIGH",
                                "evidence_refs": [artifact_id],
                            }
                        ],
                        "risks": [],
                        "unresolved": [],
                        "constraint_violations": [],
                    },
                    "test",
                    "fixture",
                    {},
                )
            return super().generate_structured(**kwargs)

    for _ in range(2):
        assert reduce_task(db, uuid.UUID(task["id"]), EscalatingProvider())
        db.commit()
    assert db.scalar(select(func.count()).select_from(m.Finding)) == 1
    assert db.scalar(select(m.Finding.severity)) == "HIGH"
    assert db.scalar(select(m.Risk.level)) == "HIGH"
    assert db.scalar(select(m.DecisionCase.status)) == "AWAITING_HUMAN"
    assert (
        db.scalar(
            select(func.count())
            .select_from(m.Evidence)
            .where(m.Evidence.artifact_id == uuid.UUID(artifact_id))
        )
        == 1
    )


def test_digest_literal_cannot_alias_a_different_claim(db, client, project_task):
    from dirima.reduction.pipeline import key

    project, task = project_task
    long_claim = "Shared context " * 40
    literal = key(long_claim)
    assert key(long_claim) != key(literal)
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": long_claim}, {"subject": "Choice", "claim": literal}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.FindingCluster)) == 2
    assert db.scalar(select(m.Conflict.status)) == "OPEN"


def test_long_model_identifiers_preserve_provenance(db, client, project_task, monkeypatch):
    from dirima.providers.structured import call_model
    from dirima.schemas import ReceiptSummary

    class LongIdentifierProvider:
        provider = "provider" * 30

        def generate_structured(self, **kwargs):
            return ModelResult({"summary": "Recorded"}, self.provider, "model" * 60, {})

    monkeypatch.setenv("DIRIMA_MODEL_RECEIPT_GENERATOR", "configured" * 30)
    call_model(
        db,
        uuid.UUID("00000000-0000-4000-8000-000000000001"),
        LongIdentifierProvider(),
        purpose="receipt_generator",
        input_data={},
        output_schema=ReceiptSummary.model_json_schema(),
    )
    db.commit()
    row = db.scalar(select(m.ModelCall))
    assert (
        row.status == "SUCCEEDED" and row.provider == "provider" * 30 and row.model == "model" * 60
    )


def test_uuid_identity_comparison_accepts_case_variants(monkeypatch):
    from dirima.main import auth

    workspace = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    monkeypatch.setenv("DIRIMA_WORKSPACE_ID", str(workspace).upper())
    assert auth("Bearer test-only-token", str(workspace).upper()) == workspace


def test_semantic_uuid_case_variants_preserve_valid_classification(db, client, project_task):
    class UppercaseProvider(SemanticProvider):
        def generate_structured(self, **kwargs):
            if kwargs["purpose"] == "cluster":
                return ModelResult(
                    {
                        "equivalent": True,
                        "reason": "same",
                        "finding_ids": [f["id"].upper() for f in kwargs["input_data"]["findings"]],
                    },
                    "test",
                    "fixture",
                    {},
                )
            return super().generate_structured(**kwargs)

    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [
            {"subject": "Choice", "claim": "Use PG"},
            {"subject": "Choice", "claim": "Use PostgreSQL"},
        ],
    )
    assert reduce_task(db, uuid.UUID(task["id"]), UppercaseProvider())
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.FindingCluster)) == 1
    assert db.scalar(select(m.ModelCall.status)) == "SUCCEEDED"


@pytest.mark.parametrize(
    "required,actual",
    [
        ("Use identifier 'ABC' (case-sensitive)", "Use identifier 'abc' (case-sensitive)"),
        ("Use separator '  '", "Use separator ' '"),
    ],
)
def test_literal_case_and_spaces_are_preserved_in_claim_and_constraint_identity(
    db, client, project_task, required, actual
):
    from dirima.reduction.pipeline import claim_key

    assert claim_key(required) != claim_key(actual)
    project, task = project_task
    client.post(
        f"/api/v1/projects/{project['id']}/constraints",
        json={"statement": required, "type": "HARD"},
    )
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": actual}, {"subject": "Choice", "claim": "Other"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    add_run(client, project, task, "Reviewer", "claude", [{"subject": "Choice", "claim": required}])
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.FindingCluster)) == 3
    conflict = db.scalar(select(m.Conflict).where(m.Conflict.status == "AUTO_RESOLVED"))
    assert (
        db.get(m.Finding, db.get(m.Resolution, conflict.resolution_id).selected_finding_id).claim
        == required
    )


def test_incomplete_new_source_invalidates_previous_automatic_conclusion(db, client, project_task):
    project, task = project_task
    previous = client.post(
        f"/api/v1/projects/{project['id']}/constraints", json={"statement": "A", "type": "HARD"}
    ).json()
    run, external = add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "AUTO_RESOLVED"
    client.post(
        f"/api/v1/constraints/{previous['id']}/supersede", json={"statement": "B", "actor": "human"}
    )
    client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "message.completed",
            {"text": "New source requires extraction"},
        ),
    )
    assert not reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert db.scalar(select(m.Conflict.status)) == "OPEN"
    assert db.scalar(select(func.count()).select_from(m.Resolution)) == 1
    content = db.scalar(select(m.CompletionReceipt.content))
    assert content["reduction_status"] == "FAILED" and content["resolved_conflicts"] == []


def test_legacy_duplicate_auto_conflicts_cannot_collide_on_reopen(db, client, project_task):
    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    current = db.scalar(select(m.Conflict))
    db.add(
        m.Conflict(
            workspace_id=current.workspace_id,
            task_id=current.task_id,
            topic_key=current.topic_key,
            finding_ids=current.finding_ids,
            severity="LOW",
            status="AUTO_RESOLVED",
        )
    )
    db.commit()
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert (
        db.scalar(select(func.count()).select_from(m.Conflict).where(m.Conflict.status == "OPEN"))
        == 1
    )
    assert (
        db.scalar(
            select(func.count()).select_from(m.Conflict).where(m.Conflict.status == "INVALIDATED")
        )
        == 1
    )


def test_invalidated_machine_conflict_can_reappear_but_human_dismissal_is_preserved(
    db, client, project_task
):
    from dirima.reduction.pipeline import supersede_conflict

    project, task = project_task
    add_run(
        client,
        project,
        task,
        "Agent",
        "gpt",
        [{"subject": "Choice", "claim": "A"}, {"subject": "Choice", "claim": "B"}],
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    current = db.scalar(select(m.Conflict))
    supersede_conflict(db, db.get(m.Task, uuid.UUID(task["id"])), current)
    db.commit()
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    active = db.scalar(select(m.Conflict).where(m.Conflict.status == "OPEN"))
    assert active is not None and active.id != current.id
    case = db.scalar(select(m.DecisionCase).where(m.DecisionCase.conflict_id == active.id))
    assert (
        client.post(
            f"/api/v1/decision-cases/{case.id}/dismiss",
            json={"actor": "human", "rationale": "Intentionally dismissed"},
        ).status_code
        == 200
    )
    assert reduce_task(db, uuid.UUID(task["id"]))
    db.commit()
    assert not db.scalar(select(m.Conflict.id).where(m.Conflict.status == "OPEN"))
