"""Validate enum values and preserve verification run provenance.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CHECKS = {
    "tasks": (
        "valid_status",
        "status IN ('OPEN','RUNNING','COMPLETED','FAILED','NEEDS_ATTENTION')",
    ),
    "run_events": (
        "valid_type",
        "type IN ('run.created','run.started','run.status_changed','message.completed','tool.called','tool.completed','tool.failed','artifact.created','verification.reported','run.completed','run.failed')",
    ),
    "finding_clusters": ("valid_status", "status IN ('OPEN','AUTO_RESOLVED')"),
    "conflicts": ("valid_severity", "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')"),
    "attention_items": ("valid_status", "status IN ('OPEN','CLOSED')"),
}


def upgrade():
    for table, (suffix, expression) in CHECKS.items():
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_{suffix} CHECK ({expression})")
    op.execute(
        "ALTER TABLE decision_cases ADD CONSTRAINT decision_cases_valid_risk CHECK (risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL'))"
    )
    op.execute(
        "ALTER TABLE decision_cases ADD CONSTRAINT decision_cases_valid_attention CHECK (attention_class IN ('HUMAN_REQUIRED','BLOCKING'))"
    )
    op.execute("ALTER TABLE verifications ADD COLUMN run_id UUID REFERENCES runs(id)")
    op.execute(
        "UPDATE verifications v SET run_id=e.run_id FROM evidence e WHERE v.workspace_id=e.workspace_id AND v.evidence_refs::jsonb ? e.id::text"
    )
    op.execute(
        "CREATE TRIGGER verifications_run_id_workspace BEFORE INSERT OR UPDATE ON verifications FOR EACH ROW EXECUTE FUNCTION dirima_check_scoped_ref('run_id','runs')"
    )


def downgrade():
    op.execute("DROP TRIGGER verifications_run_id_workspace ON verifications")
    op.execute("ALTER TABLE verifications DROP COLUMN run_id")
    op.execute("ALTER TABLE decision_cases DROP CONSTRAINT decision_cases_valid_attention")
    op.execute("ALTER TABLE decision_cases DROP CONSTRAINT decision_cases_valid_risk")
    for table, (suffix, _) in CHECKS.items():
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_{suffix}")
