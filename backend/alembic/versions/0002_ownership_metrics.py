"""Enforce scoped references and measure reduction duration.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

REFERENCES = {
    "constraints": [("project_id", "projects"), ("superseded_by", "constraints")],
    "goals": [("project_id", "projects")],
    "tasks": [("project_id", "projects"), ("goal_id", "goals")],
    "runs": [("project_id", "projects"), ("task_id", "tasks"), ("supersedes_run_id", "runs")],
    "run_events": [("project_id", "projects"), ("task_id", "tasks"), ("run_id", "runs")],
    "artifacts": [("project_id", "projects"), ("task_id", "tasks"), ("created_by_run_id", "runs")],
    "agent_receipts": [("run_id", "runs")],
    "findings": [
        ("project_id", "projects"),
        ("task_id", "tasks"),
        ("run_id", "runs"),
        ("cluster_id", "finding_clusters"),
        ("superseded_by", "findings"),
    ],
    "finding_clusters": [("task_id", "tasks")],
    "evidence": [("artifact_id", "artifacts"), ("run_id", "runs")],
    "verifications": [("task_id", "tasks"), ("finding_id", "findings")],
    "conflicts": [("task_id", "tasks"), ("resolution_id", "resolutions")],
    "resolutions": [("conflict_id", "conflicts"), ("selected_finding_id", "findings")],
    "risks": [("task_id", "tasks"), ("finding_id", "findings")],
    "decision_cases": [
        ("project_id", "projects"),
        ("task_id", "tasks"),
        ("conflict_id", "conflicts"),
    ],
    "decision_options": [("decision_case_id", "decision_cases")],
    "human_decisions": [
        ("decision_case_id", "decision_cases"),
        ("selected_option_id", "decision_options"),
    ],
    "attention_items": [("task_id", "tasks"), ("decision_case_id", "decision_cases")],
    "completion_receipts": [("task_id", "tasks")],
}


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS duration_seconds DOUBLE PRECISION")
    op.execute(
        "ALTER TABLE conflicts ADD CONSTRAINT conflicts_resolution_id_fkey "
        "FOREIGN KEY (resolution_id) REFERENCES resolutions(id)"
    )
    op.execute("""
    CREATE FUNCTION dirima_check_scoped_ref() RETURNS trigger AS $$
    DECLARE parent_id uuid; parent_workspace uuid;
    BEGIN
      EXECUTE format('SELECT ($1).%I', TG_ARGV[0]) USING NEW INTO parent_id;
      IF parent_id IS NULL THEN RETURN NEW; END IF;
      EXECUTE format('SELECT workspace_id FROM %I WHERE id = $1', TG_ARGV[1])
        USING parent_id INTO parent_workspace;
      IF parent_workspace IS DISTINCT FROM NEW.workspace_id THEN
        RAISE EXCEPTION 'cross-workspace reference rejected on %.%', TG_TABLE_NAME, TG_ARGV[0];
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    for table, references in REFERENCES.items():
        for column, parent in references:
            op.execute(
                f"CREATE TRIGGER {table}_{column}_workspace BEFORE INSERT OR UPDATE "
                f"ON {table} FOR EACH ROW EXECUTE FUNCTION "
                f"dirima_check_scoped_ref('{column}', '{parent}')"
            )
    op.execute("""
    CREATE FUNCTION dirima_check_finding_evidence() RETURNS trigger AS $$
    DECLARE finding_workspace uuid; evidence_workspace uuid;
    BEGIN
      SELECT workspace_id INTO finding_workspace FROM findings WHERE id = NEW.finding_id;
      SELECT workspace_id INTO evidence_workspace FROM evidence WHERE id = NEW.evidence_id;
      IF finding_workspace IS DISTINCT FROM evidence_workspace THEN
        RAISE EXCEPTION 'cross-workspace finding/evidence link rejected';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER finding_evidence_workspace BEFORE INSERT OR UPDATE ON finding_evidence "
        "FOR EACH ROW EXECUTE FUNCTION dirima_check_finding_evidence()"
    )


def downgrade():
    op.execute("DROP TRIGGER finding_evidence_workspace ON finding_evidence")
    op.execute("DROP FUNCTION dirima_check_finding_evidence()")
    for table, references in REFERENCES.items():
        for column, _ in references:
            op.execute(f"DROP TRIGGER {table}_{column}_workspace ON {table}")
    op.execute("DROP FUNCTION dirima_check_scoped_ref()")
    op.execute("ALTER TABLE conflicts DROP CONSTRAINT conflicts_resolution_id_fkey")
    op.execute("ALTER TABLE jobs DROP COLUMN duration_seconds")
