"""Preserve imports and history while rebuilding derived conclusions under audited policy."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "run_events", "idempotency_key", type_=sa.String(206), existing_type=sa.String(200)
    )
    # Legacy clustering used truncated claims; originals and all decision history are retained.
    op.execute("UPDATE findings SET cluster_id = NULL WHERE status = 'ACTIVE'")
    op.execute(
        "UPDATE finding_clusters SET topic_key = 'legacy:' || id::text, claim_key = 'legacy:' || id::text, status = 'OPEN'"
    )
    # Previous untyped reports cannot prove observed command results.
    op.execute(
        "UPDATE verifications SET status = 'INCONCLUSIVE' WHERE status IN ('PASSED','FAILED')"
    )
    op.execute(
        "INSERT INTO jobs (id, workspace_id, job_type, payload, status, attempts, available_at, created_at) SELECT gen_random_uuid(), workspace_id, 'reduce_task', json_build_object('task_id', id::text), 'READY', 0, now(), now() FROM tasks WHERE EXISTS (SELECT 1 FROM runs WHERE runs.task_id = tasks.id)"
    )


def downgrade():
    # Expanding this column is safe to leave in place; derived history cannot be restored honestly.
    pass
