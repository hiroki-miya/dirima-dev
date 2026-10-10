# API

The API is mounted under `/api/v1` and documented automatically at `/docs`. All operational endpoints require `Authorization: Bearer <DIRIMA_TOKEN>` and are scoped to `DIRIMA_WORKSPACE_ID`. A mismatched `X-Workspace-ID` is rejected. `/healthz` and `/readyz` support infrastructure health checks.

Minimal flow:

1. `POST /projects` with `{"name":"Project"}`.
2. `POST /tasks` with `{"project_id":"uuid","title":"Task"}`.
3. `POST /events` with a `run.created` envelope and Run metadata in `payload`.
4. Send `run.started`, completed messages or artifacts, `verification.reported`, then `run.completed` with an optional structured `receipt`.
5. The worker reduces events. Read `/decision-cases`, `/attention`, and `/tasks/{id}/receipt`.

Each event has `schema_version`, `event_id`, workspace/project/task/run UUIDs, `source` (`adapter`, `runtime`, `external_run_id`, optional `external_event_id`), `type`, timezone-aware `occurred_at`, `idempotency_key`, and JSON `payload`. Duplicates return HTTP 200 with `duplicate: true`. Invalid events return HTTP 400. Content hashes are used for artifacts, never event identity.

Optional `external_sequence` is a strict integer from 0 through 2147483647. Run identifiers and labels follow the bounds in the published event schema; invalid metadata is rejected before DB writes. Duplicate Project names return HTTP 409. Verification events received before Run completion remain in the event log and are classified after source extraction establishes their possible Finding targets.

`verification.reported` requires an observed `command_result` to establish PASSED or FAILED: `{"command":["pytest","tests/target.py"],"exit_code":0,"stdout":"captured output","stderr":"","observer":"generic"}`. The command must be nonempty, exit_code must be an integer (booleans are rejected), stdout/stderr must be strings, and observer must equal `source.adapter`. The trusted importer captures this result; matching an observer name establishes provenance consistency, not independent proof that execution occurred. Dirima does not execute imported commands. Legacy status/exit_code fields and AgentCompletionReceipt claims remain INCONCLUSIVE.

Use `finding_id` to target an ACTIVE Finding in the same Task and Workspace, including independent review from another Run. A `subject` fallback must uniquely identify an ACTIVE Finding in the reporting Run. Invalid or ambiguous targets remain INCONCLUSIVE. An explicitly declared `scope: "task"` may record a Task check, but Task checks cannot automatically establish the truth of individual Findings or select a conflict candidate.

`artifact.created` accepts `name`, `mime_type` and `content_base64`; the filename is never used as a storage path. The response includes `artifact_id`. Stored events replace the encoded body with `artifact_id`, `sha256` and `size_bytes`; the original bytes live in content-addressed storage. Finding `evidence_refs` may reference these artifact UUIDs.

All artifacts are checked against their size and SHA-256 before reduction, including binary artifacts and Runs with receipts. Text, JSON, XML and JavaScript artifacts are also extracted together with message, tool, and status/failure events; receipt assertions never replace these sources. All source content is processed in overlapping chunks, with constraints repeated for each chunk. `DIRIMA_EXTRACTION_CHARS` sets source characters per chunk (default 24000, minimum 2048; metadata and constraints add overhead). Every chunk must succeed before Findings and the extraction checkpoint are saved. Missing or changed evidence creates a visible blocking attention item. `/artifacts/{id}/content` provides a redacted text preview with an explicit truncation flag; this preview limit does not limit extraction.

`POST /decision-cases/{id}/resolve` accepts `selected_option_id`, `actor`, `rationale`. HIGH and CRITICAL risk cases require a nonempty rationale. Dismiss and investigate endpoints also require actor and rationale. A resolved case cannot be resolved again.

Corrections preserve history:

- Include `supersedes_run_id` in a new `run.created` payload to replace a terminal Run belonging to the same Task. The old Run and events remain available. A submitted receipt cannot be overwritten.
- `POST /findings/{id}/correct` accepts `claim`, optional `subject`, `actor`, and `rationale`. It supersedes the original Finding and retains its evidence and the human correction.
- `POST /findings/{id}/risk-override` accepts `level`, `actor`, and `rationale`. A manual risk level cannot go below the deterministic safety minimum.
- `POST /constraints/{id}/supersede` accepts the replacement `statement` and `actor`.

These operations are audited and obsolete decision cases are superseded. Task receipt reads hide older summaries while a Run is active or new events await reduction.

Automatic HARD matching requires the complete claim and constraint statement to be identical, preserving case and internal whitespace. Topic labels may be normalized for grouping; identifier and literal semantics in claims are preserved. New incomplete source reduction invalidates prior automatic conclusions until the sources can be evaluated. Human decisions and original Resolution records remain in history.
