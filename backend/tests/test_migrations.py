"""Exercise a frozen initial migration and upgrade with preserved data."""

import os
import uuid
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy import select as sa_select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from dirima import models as m


def test_clean_migration_and_upgrade_preserve_data(monkeypatch):
    test_url = make_url(
        os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://dirima@127.0.0.1:55433/dirima_test18"
        )
    )
    assert "_test" in test_url.database
    database_name = "dirima_migration_test_" + uuid.uuid4().hex
    admin = create_engine(test_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    db_engine = create_engine(test_url.set(database=database_name))
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        monkeypatch.setenv(
            "DATABASE_URL",
            test_url.set(database=database_name).render_as_string(hide_password=False),
        )
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        command.upgrade(config, "0001")
        workspace_id = uuid.uuid4()
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workspaces(id,name,created_at) VALUES (:id,'Upgrade fixture',now())"
                ),
                {"id": workspace_id},
            )
        command.upgrade(config, "0005")
        with Session(db_engine) as session:
            project = m.Project(workspace_id=workspace_id, name="Legacy project")
            session.add(project)
            session.flush()
            task = m.Task(workspace_id=workspace_id, project_id=project.id, title="Legacy task")
            session.add(task)
            session.flush()
            run = m.Run(
                workspace_id=workspace_id,
                project_id=project.id,
                task_id=task.id,
                runtime_type="legacy",
                agent_name="Agent",
                external_run_id="legacy",
                status="SUCCEEDED",
                extraction_hash="f" * 64,
            )
            session.add(run)
            session.flush()
            cluster = m.FindingCluster(
                workspace_id=workspace_id,
                task_id=task.id,
                topic_key="choice",
                claim_key="x" * 300,
                status="AUTO_RESOLVED",
            )
            session.add(cluster)
            session.flush()
            for ending in ["A", "B"]:
                session.add(
                    m.Finding(
                        workspace_id=workspace_id,
                        project_id=project.id,
                        task_id=task.id,
                        run_id=run.id,
                        kind="OBSERVATION",
                        subject="Choice",
                        claim="x" * 350 + ending,
                        severity="LOW",
                        cluster_id=cluster.id,
                    )
                )
            session.flush()
            finding_ids = [str(value) for value in session.scalars(sa_select(m.Finding.id)).all()]
            session.add(
                m.Conflict(
                    workspace_id=workspace_id,
                    task_id=task.id,
                    topic_key="choice",
                    finding_ids=finding_ids,
                    severity="LOW",
                    status="HUMAN_RESOLVED",
                )
            )
            session.add(
                m.Verification(
                    workspace_id=workspace_id,
                    task_id=task.id,
                    run_id=run.id,
                    method="untyped legacy",
                    status="PASSED",
                    evidence_refs=[],
                    performed_by="Agent",
                    performed_at=m.now(),
                )
            )
            session.add(
                m.AuditLog(
                    workspace_id=workspace_id,
                    actor="human",
                    action="legacy.fixture",
                    target_type="task",
                    target_id=task.id,
                    detail={"preserved": True},
                )
            )
            session.commit()
        command.upgrade(config, "head")
        with db_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM workspaces")) == 1
            assert (
                connection.scalar(text("SELECT count(*) FROM findings WHERE cluster_id IS NULL"))
                == 2
            )
            assert connection.scalar(text("SELECT status FROM verifications")) == "INCONCLUSIVE"
            assert connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1
            assert connection.scalar(text("SELECT status FROM conflicts")) == "HUMAN_RESOLVED"
            assert connection.scalar(text("SELECT topic_key FROM conflicts")).startswith("sha256:")
            assert connection.scalar(text("SELECT extraction_hash FROM runs")) is None
            assert (
                connection.scalar(
                    text(
                        "SELECT data_type FROM information_schema.columns WHERE table_name='model_calls' AND column_name='model'"
                    )
                )
                == "text"
            )
            assert (
                connection.scalar(text("SELECT job_type FROM jobs WHERE status='READY'"))
                == "reduce_task"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT character_maximum_length FROM information_schema.columns WHERE table_name='run_events' AND column_name='idempotency_key'"
                    )
                )
                == 206
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == ScriptDirectory.from_config(config).get_current_head()
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='conflicts_one_open_topic'"
                    )
                )
                == 1
            )
    finally:
        db_engine.dispose()
        # Only the unique database created by this test is removed.
        assert database_name.startswith("dirima_migration_test_")
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}"'))
        admin.dispose()
