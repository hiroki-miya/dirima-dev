import json
import logging
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from dirima import models as m
from dirima.db import database_url
from dirima.infrastructure.logging import FIELDS, JsonFormatter

from .helpers import add_run, event


def test_connection_password_reserved_characters(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    password = "percent%slash/colon:at@question?"
    monkeypatch.setenv("DIRIMA_DB_PASSWORD", password)
    assert make_url(database_url()).password == password


def test_log_json_has_context_and_redacts_quoted_secrets():
    record = logging.LogRecord(
        "dirima.api", logging.INFO, __file__, 1, """{"API_KEY": "secret-value"}""", (), None
    )
    output = JsonFormatter().format(record)
    assert "secret-value" not in output
    assert set(FIELDS) <= json.loads(output).keys()


@pytest.mark.usefixtures("clean")
def test_event_identity_and_enum_are_enforced_by_postgres(db, client, project_task):
    project, task = project_task
    run, _ = add_run(client, project, task, "Agent", "gpt", [])
    original = db.scalar(select(m.RunEvent).where(m.RunEvent.run_id == uuid.UUID(run)))
    with pytest.raises(DBAPIError):
        db.execute(
            update(m.RunEvent).where(m.RunEvent.id == original.id).values(event_id=uuid.uuid4())
        )
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(update(m.Run).where(m.Run.id == uuid.UUID(run)).values(status="invalid-state"))
    db.rollback()


@pytest.mark.usefixtures("clean")
def test_absent_token_and_existing_other_workspace_record_are_rejected(db, client, project_task):
    other_workspace = m.Workspace(name="Other", id=uuid.uuid4())
    db.add(other_workspace)
    db.flush()
    other_project = m.Project(workspace_id=other_workspace.id, name="Private project")
    db.add(other_project)
    db.commit()
    assert client.get(f"/api/v1/projects/{other_project.id}").status_code == 404
    del client.headers["Authorization"]
    assert client.get("/api/v1/projects").status_code == 401


@pytest.mark.usefixtures("clean")
def test_validation_errors_do_not_leak_nested_secret(db, client, project_task):
    project, task = project_task
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    for kind in ("run.created", "run.started"):
        client.post("/api/v1/events", json=event(project, task, run, external, kind))
    response = client.post(
        "/api/v1/events",
        json=event(
            project,
            task,
            run,
            external,
            "run.completed",
            {"receipt": {"status": "succeeded", "api_key": "secret-value"}},
        ),
    )
    assert response.status_code == 400
    assert "secret-value" not in response.text
    assert "input" not in response.json()["detail"][0]
