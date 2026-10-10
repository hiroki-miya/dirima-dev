import uuid
from datetime import UTC, datetime

WORKSPACE = "00000000-0000-4000-8000-000000000001"


def event(project, task, run_id, external, kind, payload=None, key=None):
    return {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "workspace_id": WORKSPACE,
        "project_id": project["id"],
        "task_id": task["id"],
        "run_id": run_id,
        "source": {"adapter": "generic", "runtime": "test-cli", "external_run_id": external},
        "type": kind,
        "occurred_at": datetime.now(UTC).isoformat(),
        "idempotency_key": key or str(uuid.uuid4()),
        "payload": payload or {},
    }


def add_run(client, project, task, name, family, findings):
    run_id = str(uuid.uuid4())
    external = f"external-{run_id}"
    for kind, payload in [
        (
            "run.created",
            {
                "agent_name": name,
                "model_provider": name,
                "model_name": family,
                "model_family": family,
                "prompt_lineage": "v1",
            },
        ),
        ("run.started", {}),
        (
            "run.completed",
            {
                "receipt": {
                    "schema_version": "1.0",
                    "status": "succeeded",
                    "summary": "Done",
                    "artifacts": [],
                    "reported_findings": findings,
                    "reported_verifications": [],
                    "reported_risks": [],
                    "unresolved": [],
                }
            },
        ),
    ]:
        response = client.post(
            "/api/v1/events", json=event(project, task, run_id, external, kind, payload)
        )
        assert response.status_code == 200, response.text
    return run_id, external
