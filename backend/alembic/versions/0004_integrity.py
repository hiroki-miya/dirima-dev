"""Bind risk cases to findings and enforce artifact storage identity.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE artifacts ADD CONSTRAINT artifacts_storage_digest CHECK (storage_key = sha256)"
    )
    op.execute("ALTER TABLE decision_cases ADD COLUMN finding_id UUID REFERENCES findings(id)")
    op.execute(
        "CREATE TRIGGER decision_cases_finding_id_workspace BEFORE INSERT OR UPDATE ON decision_cases FOR EACH ROW EXECUTE FUNCTION dirima_check_scoped_ref('finding_id','findings')"
    )


def downgrade():
    op.execute("DROP TRIGGER decision_cases_finding_id_workspace ON decision_cases")
    op.execute("ALTER TABLE decision_cases DROP COLUMN finding_id")
    op.execute("ALTER TABLE artifacts DROP CONSTRAINT artifacts_storage_digest")
