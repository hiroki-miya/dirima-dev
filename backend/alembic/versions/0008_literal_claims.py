"""Preserve literal semantics when rebuilding claims and automatic resolutions."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE findings SET cluster_id=NULL WHERE status='ACTIVE'")
    op.execute(
        "UPDATE finding_clusters SET topic_key='legacy:'||id::text,claim_key='legacy:'||id::text,status='OPEN'"
    )
    op.execute(
        "INSERT INTO jobs(id,workspace_id,job_type,payload,status,attempts,available_at,created_at) SELECT gen_random_uuid(),workspace_id,'reduce_task',json_build_object('task_id',id::text),'READY',0,now(),now() FROM tasks WHERE EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id) AND NOT EXISTS(SELECT 1 FROM jobs WHERE jobs.payload->>'task_id'=tasks.id::text AND jobs.status IN ('READY','RUNNING'))"
    )


def downgrade():
    # Preserve source records and decision history; rebuild instead of restoring old identities.
    pass
