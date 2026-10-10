# Domain model

`Workspace` scopes `Project`, and `Project` holds human `Constraint`s, `Goal`s and `Task`s. One Task can have many Runs. A Run is one agent execution with model family and prompt lineage, while an Event is an immutable activity envelope. An Artifact is stored by content hash outside the database.

An AgentReceipt is an agent's report. Extracted Finding rows describe claims. Evidence rows are separately typed and attached many-to-many. Verification records the method, status and evidence references. Clusters group duplicate findings without deleting originals. Conflicts group incompatible findings and have an independent Resolution row when settled.

Risks are classified before attention. AttentionItem is a notification; DecisionCase is reserved for a human choice and has zero or more DecisionOptions. HumanDecision records one selected option, actor, rationale and time. CompletionReceipt summarizes a completed Task, its verification, remaining risks and decisions. AuditLog and ModelCall record provenance without copying secret inputs.

Run states are CREATED, RUNNING, WAITING, SUCCEEDED, FAILED, CANCELLED and UNKNOWN. Terminal states cannot change. A late event may be appended to a terminal Run, and a correction requires a new Run that supersedes the old one. All database times are timezone-aware UTC.
