"""Preserve closed conflict versions and checkpoint extraction inputs.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE runs ADD COLUMN extraction_hash VARCHAR(64)")
    op.execute(
        "ALTER TABLE conflicts ADD COLUMN conflict_type VARCHAR(100) NOT NULL DEFAULT 'INCOMPATIBLE_CLAIMS'"
    )
    op.execute(
        "ALTER TABLE conflicts ADD COLUMN reason TEXT NOT NULL DEFAULT 'Claims for the same subject differ'"
    )
    op.execute("ALTER TABLE conflicts DROP CONSTRAINT conflicts_task_id_topic_key_status_key")
    op.execute(
        "CREATE UNIQUE INDEX conflicts_one_open_topic ON conflicts(task_id, topic_key) WHERE status = 'OPEN'"
    )


def downgrade():
    op.execute("DROP INDEX conflicts_one_open_topic")
    op.execute(
        "ALTER TABLE conflicts ADD CONSTRAINT conflicts_task_id_topic_key_status_key UNIQUE(task_id, topic_key, status)"
    )
    op.execute("ALTER TABLE conflicts DROP COLUMN reason")
    op.execute("ALTER TABLE conflicts DROP COLUMN conflict_type")
    op.execute("ALTER TABLE runs DROP COLUMN extraction_hash")
