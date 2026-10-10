"""Separate source text from digest identity and rebuild derived conclusions."""

import hashlib
import re

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("model_calls", "provider", type_=sa.Text(), existing_type=sa.String(100))
    op.alter_column("model_calls", "model", type_=sa.Text(), existing_type=sa.String(100))
    connection = op.get_bind()
    for row in connection.execute(
        sa.text(
            "SELECT c.id,c.topic_key,c.finding_ids,f.subject FROM conflicts c JOIN findings f ON f.id::text=(c.finding_ids->>0)"
        )
    ).all():
        semantic_key = "semantic:" + ":".join(sorted(row.finding_ids))
        if row.topic_key == semantic_key:
            continue
        normalized = re.sub(r"\s+", " ", row.subject.strip().casefold())
        topic = "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()
        connection.execute(
            sa.text("UPDATE conflicts SET topic_key=:topic WHERE id=:id"),
            {"topic": topic, "id": row.id},
        )
    op.execute("UPDATE findings SET cluster_id = NULL WHERE status = 'ACTIVE'")
    op.execute(
        "UPDATE finding_clusters SET topic_key = 'legacy:' || id::text, claim_key = 'legacy:' || id::text, status = 'OPEN'"
    )
    # Re-extraction now retains severity/evidence from matching source claims.
    op.execute("UPDATE runs SET extraction_hash = NULL")
    op.execute(
        "INSERT INTO jobs (id,workspace_id,job_type,payload,status,attempts,available_at,created_at) SELECT gen_random_uuid(),workspace_id,'reduce_task',json_build_object('task_id',id::text),'READY',0,now(),now() FROM tasks WHERE EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id) AND NOT EXISTS(SELECT 1 FROM jobs WHERE jobs.payload->>'task_id'=tasks.id::text AND jobs.status IN ('READY','RUNNING'))"
    )


def downgrade():
    # Keep the expanded provenance storage and immutable history intact.
    pass
