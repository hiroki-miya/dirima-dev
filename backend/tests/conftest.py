import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from dirima.db import Session, engine
from dirima.main import app


@pytest.fixture(scope="session")
def migrated():
    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://dirima@127.0.0.1:55433/dirima_test18"
    )
    if "_test" not in url.rsplit("/", 1)[-1]:
        raise RuntimeError("Tests require a dedicated *_test PostgreSQL database")
    os.environ["DATABASE_URL"] = url
    os.environ["DIRIMA_TOKEN"] = "test-only-token"
    os.environ["DIRIMA_WORKSPACE_ID"] = "00000000-0000-4000-8000-000000000001"
    os.environ["DIRIMA_AUTH_MODE"] = "token"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(config, "head")
    yield


@pytest.fixture
def clean(migrated):
    with engine().begin() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
                )
            )
        ]
        if tables:
            quoted = ", ".join('"' + t.replace('"', '""') + '"' for t in tables)
            conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    with Session(bind=engine()) as session:
        yield session
        session.rollback()


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer test-only-token"})
        yield c


@pytest.fixture
def project_task(client):
    project = client.post("/api/v1/projects", json={"name": str(uuid.uuid4())}).json()
    task = client.post(
        "/api/v1/tasks", json={"project_id": project["id"], "title": "Review locking"}
    ).json()
    return project, task
