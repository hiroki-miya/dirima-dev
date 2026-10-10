"""Initial operational schema.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


INITIAL_DDL = [
    "\nCREATE TABLE workspaces (\n\tname VARCHAR(200) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "\nCREATE TABLE worker_heartbeats (\n\tworker_id VARCHAR(100) NOT NULL, \n\tseen_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (worker_id)\n)\n\n",
    "\nCREATE TABLE projects (\n\tname VARCHAR(200) NOT NULL, \n\tdescription TEXT NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (id, workspace_id), \n\tUNIQUE (workspace_id, name), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_projects_workspace_id ON projects (workspace_id)",
    "\nCREATE TABLE audit_logs (\n\tactor VARCHAR(200) NOT NULL, \n\taction VARCHAR(100) NOT NULL, \n\ttarget_type VARCHAR(100) NOT NULL, \n\ttarget_id UUID, \n\tdetail JSON NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_audit_logs_workspace_id ON audit_logs (workspace_id)",
    "\nCREATE TABLE model_calls (\n\tprovider VARCHAR(100) NOT NULL, \n\tmodel VARCHAR(100) NOT NULL, \n\tpurpose VARCHAR(100) NOT NULL, \n\tprompt_version VARCHAR(50) NOT NULL, \n\tinput_hash VARCHAR(64) NOT NULL, \n\toutput_hash VARCHAR(64), \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\ttoken_usage JSON NOT NULL, \n\tcost NUMERIC(12, 6), \n\tstatus VARCHAR(20) NOT NULL, \n\ttrace_id VARCHAR(100) NOT NULL, \n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_model_calls_workspace_id ON model_calls (workspace_id)",
    "\nCREATE TABLE jobs (\n\tjob_type VARCHAR(50) NOT NULL, \n\tpayload JSON NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlocked_by VARCHAR(100), \n\tlocked_at TIMESTAMP WITH TIME ZONE, \n\tlast_error TEXT, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tduration_seconds FLOAT, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (status IN ('READY','RUNNING','COMPLETED','FAILED','DEAD')), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_jobs_claim ON jobs (status, available_at)",
    "CREATE INDEX ix_jobs_workspace_id ON jobs (workspace_id)",
    "\nCREATE TABLE constraints (\n\tproject_id UUID NOT NULL, \n\tstatement TEXT NOT NULL, \n\ttype VARCHAR(8) NOT NULL, \n\tscope VARCHAR(200) NOT NULL, \n\tsource VARCHAR(200) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tsuperseded_by UUID, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id, workspace_id) REFERENCES projects (id, workspace_id), \n\tCHECK (type IN ('HARD','SOFT')), \n\tCHECK (status IN ('ACTIVE','SUPERSEDED')), \n\tFOREIGN KEY(superseded_by) REFERENCES constraints (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_constraints_workspace_id ON constraints (workspace_id)",
    "\nCREATE TABLE goals (\n\tproject_id UUID NOT NULL, \n\ttitle VARCHAR(250) NOT NULL, \n\tdescription TEXT NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id, workspace_id) REFERENCES projects (id, workspace_id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_goals_workspace_id ON goals (workspace_id)",
    "\nCREATE TABLE tasks (\n\tproject_id UUID NOT NULL, \n\tgoal_id UUID, \n\ttitle VARCHAR(250) NOT NULL, \n\tdescription TEXT NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (id, workspace_id), \n\tFOREIGN KEY(project_id, workspace_id) REFERENCES projects (id, workspace_id), \n\tFOREIGN KEY(goal_id) REFERENCES goals (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_tasks_workspace_id ON tasks (workspace_id)",
    "\nCREATE TABLE runs (\n\tproject_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\truntime_type VARCHAR(100) NOT NULL, \n\tagent_name VARCHAR(100) NOT NULL, \n\tmodel_provider VARCHAR(100) NOT NULL, \n\tmodel_name VARCHAR(100) NOT NULL, \n\tmodel_family VARCHAR(100) NOT NULL, \n\tprompt_lineage VARCHAR(200) NOT NULL, \n\texternal_run_id VARCHAR(200) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tsupersedes_run_id UUID, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (id, workspace_id), \n\tUNIQUE (workspace_id, runtime_type, external_run_id), \n\tFOREIGN KEY(task_id, workspace_id) REFERENCES tasks (id, workspace_id), \n\tCHECK (status IN ('CREATED','RUNNING','WAITING','SUCCEEDED','FAILED','CANCELLED','UNKNOWN')), \n\tFOREIGN KEY(supersedes_run_id) REFERENCES runs (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_runs_workspace_id ON runs (workspace_id)",
    "\nCREATE TABLE finding_clusters (\n\ttask_id UUID NOT NULL, \n\ttopic_key VARCHAR(300) NOT NULL, \n\tclaim_key VARCHAR(300) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(task_id, workspace_id) REFERENCES tasks (id, workspace_id), \n\tUNIQUE (task_id, topic_key, claim_key), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_finding_clusters_workspace_id ON finding_clusters (workspace_id)",
    "\nCREATE TABLE conflicts (\n\ttask_id UUID NOT NULL, \n\ttopic_key VARCHAR(300) NOT NULL, \n\tfinding_ids JSON NOT NULL, \n\tseverity VARCHAR(12) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tresolution_id UUID, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (task_id, topic_key, status), \n\tCHECK (status IN ('OPEN','AUTO_RESOLVED','HUMAN_RESOLVED','INVALIDATED')), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_conflicts_workspace_id ON conflicts (workspace_id)",
    "\nCREATE TABLE completion_receipts (\n\ttask_id UUID NOT NULL, \n\tcontent JSON NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (task_id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_completion_receipts_workspace_id ON completion_receipts (workspace_id)",
    "\nCREATE TABLE run_events (\n\tevent_id UUID NOT NULL, \n\tproject_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tschema_version VARCHAR(20) NOT NULL, \n\tsource_adapter VARCHAR(100) NOT NULL, \n\tsource_runtime VARCHAR(100) NOT NULL, \n\texternal_run_id VARCHAR(200) NOT NULL, \n\texternal_event_id VARCHAR(200), \n\ttype VARCHAR(50) NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texternal_sequence INTEGER, \n\tidempotency_key VARCHAR(200) NOT NULL, \n\tpayload JSON NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(run_id, workspace_id) REFERENCES runs (id, workspace_id), \n\tUNIQUE (source_adapter, external_run_id, idempotency_key), \n\tUNIQUE (source_adapter, external_run_id, external_event_id), \n\tUNIQUE (event_id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_run_events_workspace_id ON run_events (workspace_id)",
    "\nCREATE TABLE artifacts (\n\tproject_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tmime_type VARCHAR(150) NOT NULL, \n\tsize_bytes INTEGER NOT NULL, \n\toriginal_name VARCHAR(255) NOT NULL, \n\tstorage_key VARCHAR(100) NOT NULL, \n\tcreated_by_run_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(created_by_run_id, workspace_id) REFERENCES runs (id, workspace_id), \n\tCHECK (sha256 ~ '^[0-9a-f]{64}$'), \n\tCHECK (size_bytes >= 0), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_artifacts_workspace_id ON artifacts (workspace_id)",
    "\nCREATE TABLE agent_receipts (\n\trun_id UUID NOT NULL, \n\tcontent JSON NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(run_id, workspace_id) REFERENCES runs (id, workspace_id), \n\tUNIQUE (run_id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_agent_receipts_workspace_id ON agent_receipts (workspace_id)",
    "\nCREATE TABLE findings (\n\tproject_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tkind VARCHAR(50) NOT NULL, \n\tsubject VARCHAR(250) NOT NULL, \n\tclaim TEXT NOT NULL, \n\tseverity VARCHAR(12) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tcluster_id UUID, \n\tsuperseded_by UUID, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(run_id, workspace_id) REFERENCES runs (id, workspace_id), \n\tCHECK (status IN ('ACTIVE','SUPERSEDED','RETRACTED')), \n\tCHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')), \n\tFOREIGN KEY(cluster_id) REFERENCES finding_clusters (id), \n\tFOREIGN KEY(superseded_by) REFERENCES findings (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_findings_workspace_id ON findings (workspace_id)",
    "\nCREATE TABLE decision_cases (\n\tproject_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tconflict_id UUID, \n\ttitle VARCHAR(250) NOT NULL, \n\tquestion TEXT NOT NULL, \n\treason_human_required TEXT NOT NULL, \n\trisk_level VARCHAR(12) NOT NULL, \n\treversibility VARCHAR(30) NOT NULL, \n\tattention_class VARCHAR(20) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tresolved_at TIMESTAMP WITH TIME ZONE, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (status IN ('OPEN','AWAITING_HUMAN','RESOLVED','DISMISSED','SUPERSEDED')), \n\tFOREIGN KEY(project_id) REFERENCES projects (id), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(conflict_id) REFERENCES conflicts (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_decision_cases_workspace_id ON decision_cases (workspace_id)",
    "\nCREATE TABLE evidence (\n\ttype VARCHAR(30) NOT NULL, \n\tsource_ref VARCHAR(500) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tartifact_id UUID, \n\trun_id UUID, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (type IN ('USER_CONSTRAINT','SOURCE_CODE','TEST_RESULT','RUNTIME_OBSERVATION','OFFICIAL_SPEC','ARTIFACT','AGENT_ASSERTION')), \n\tFOREIGN KEY(artifact_id) REFERENCES artifacts (id), \n\tFOREIGN KEY(run_id) REFERENCES runs (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_evidence_workspace_id ON evidence (workspace_id)",
    "\nCREATE TABLE verifications (\n\ttask_id UUID NOT NULL, \n\tfinding_id UUID, \n\tmethod VARCHAR(100) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tevidence_refs JSON NOT NULL, \n\tperformed_by VARCHAR(100) NOT NULL, \n\tperformed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (status IN ('PENDING','PASSED','FAILED','INCONCLUSIVE','ERROR')), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(finding_id) REFERENCES findings (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_verifications_workspace_id ON verifications (workspace_id)",
    "\nCREATE TABLE resolutions (\n\tconflict_id UUID NOT NULL, \n\tmethod VARCHAR(50) NOT NULL, \n\trationale TEXT NOT NULL, \n\tselected_finding_id UUID, \n\tevidence_refs JSON NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conflict_id) REFERENCES conflicts (id), \n\tFOREIGN KEY(selected_finding_id) REFERENCES findings (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_resolutions_workspace_id ON resolutions (workspace_id)",
    "\nCREATE TABLE risks (\n\ttask_id UUID NOT NULL, \n\tfinding_id UUID, \n\tlevel VARCHAR(12) NOT NULL, \n\treason TEXT NOT NULL, \n\tirreversible BOOLEAN NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (level IN ('LOW','MEDIUM','HIGH','CRITICAL')), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(finding_id) REFERENCES findings (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_risks_workspace_id ON risks (workspace_id)",
    "\nCREATE TABLE decision_options (\n\tdecision_case_id UUID NOT NULL, \n\tlabel VARCHAR(200) NOT NULL, \n\tdescription TEXT NOT NULL, \n\tsupporting_evidence JSON NOT NULL, \n\topposing_evidence JSON NOT NULL, \n\tdownstream_impact TEXT NOT NULL, \n\treversibility VARCHAR(30) NOT NULL, \n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(decision_case_id) REFERENCES decision_cases (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_decision_options_workspace_id ON decision_options (workspace_id)",
    "\nCREATE TABLE attention_items (\n\ttask_id UUID, \n\tdecision_case_id UUID, \n\tkind VARCHAR(60) NOT NULL, \n\tclassification VARCHAR(20) NOT NULL, \n\ttitle VARCHAR(250) NOT NULL, \n\tdetail TEXT NOT NULL, \n\tstatus VARCHAR(12) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (classification IN ('ATTENTION','HUMAN_REQUIRED','BLOCKING')), \n\tFOREIGN KEY(task_id) REFERENCES tasks (id), \n\tFOREIGN KEY(decision_case_id) REFERENCES decision_cases (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_attention_items_workspace_id ON attention_items (workspace_id)",
    "\nCREATE TABLE finding_evidence (\n\tfinding_id UUID NOT NULL, \n\tevidence_id UUID NOT NULL, \n\tPRIMARY KEY (finding_id, evidence_id), \n\tFOREIGN KEY(finding_id) REFERENCES findings (id), \n\tFOREIGN KEY(evidence_id) REFERENCES evidence (id)\n)\n\n",
    "\nCREATE TABLE human_decisions (\n\tdecision_case_id UUID NOT NULL, \n\tselected_option_id UUID NOT NULL, \n\tactor VARCHAR(200) NOT NULL, \n\trationale TEXT NOT NULL, \n\tdecided_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (decision_case_id), \n\tFOREIGN KEY(decision_case_id) REFERENCES decision_cases (id), \n\tFOREIGN KEY(selected_option_id) REFERENCES decision_options (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id)\n)\n\n",
    "CREATE INDEX ix_human_decisions_workspace_id ON human_decisions (workspace_id)",
]


def upgrade():
    for statement in INITIAL_DDL:
        op.execute(statement)
    op.execute("""
    CREATE FUNCTION dirima_reject_mutation() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION 'append-only record cannot be updated or deleted'; END;
    $$ LANGUAGE plpgsql
    """)
    for table in ("run_events", "audit_logs"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dirima_reject_mutation()"
        )


def downgrade():
    for table in [
        "human_decisions",
        "finding_evidence",
        "verifications",
        "risks",
        "resolutions",
        "evidence",
        "decision_options",
        "attention_items",
        "run_events",
        "findings",
        "decision_cases",
        "artifacts",
        "agent_receipts",
        "runs",
        "finding_clusters",
        "conflicts",
        "completion_receipts",
        "tasks",
        "goals",
        "constraints",
        "projects",
        "model_calls",
        "jobs",
        "audit_logs",
        "workspaces",
        "worker_heartbeats",
    ]:
        op.execute(f'DROP TABLE "{table}" CASCADE')
    op.execute("DROP FUNCTION IF EXISTS dirima_reject_mutation()")
