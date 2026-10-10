# Dirima v0.1 acceptance — 2026-10-07

Result: **DIRIMA_V0_1_COMPLETE**, for the v0.1 scope and fixtures in the supplied implementation specification (sections 58–65).

The original acceptance was followed by 7 independent Codex audits and repeated fixes/re-audits. All 37 confirmed findings were addressed; the final independent audit reported zero unresolved confirmed findings within scope. Current executed results include the audit regression suite; see [audit record](audit.md) for changes and validation limits.

| Requirement | Executed evidence | Result |
| --- | --- | --- |
| Compose infrastructure | Dedicated WSL 2 `DirimaDev`, Docker Engine 29.8.2, Compose 5.6.0; PostgreSQL, Backend, Worker and Frontend healthy; migration container exited 0 | PASS |
| PostgreSQL 18 | `SELECT version()` returned PostgreSQL 18.6; persisted Compose volume uses `/var/lib/postgresql` | PASS |
| Clean migration and upgrade | Disposable PostgreSQL database: frozen initial migration, legacy Findings and human-resolved Conflict inserted, upgrade to 0008, source and audit history retained, derived keys rebuilt, re-evaluation queued | PASS |
| DB guards | Actual PostgreSQL FK, workspace ownership, enum, event identity and append-only audit/event checks | PASS |
| Reduction pipeline | Ingest, validated extraction, duplicate clusters, deterministic and semantic conflicts, verification, resolution, risk, attention, cases and receipts | PASS |
| Security fixtures | Prompt injection cannot mark verification PASS or delete Findings; invalid output rejected; secret redaction, unauthenticated rejection and cross-workspace isolation | PASS |
| Reliability | Duplicate and late events, concurrent workers, retries, expired crash lease recovery, missing job payload, stale case rejection | PASS |
| Corrections | Superseding terminal Run, manual Finding correction, risk override, constraint replacement, retained evidence and audits | PASS |
| Large and damaged evidence | Artifact over 100000 characters reaches extractor tail; all chunks must succeed; corrupted CAS content blocks extraction and can recover | PASS |
| Browser agreement | Independent Claude/Codex claims with a separate successful test produce a receipt and no human case | PASS |
| Browser conflict | Inbox, decision detail, evidence and Run drill-down, human choice saved, conflict HUMAN_RESOLVED, removed from Inbox | PASS |
| Browser high risk | Unanimous destructive production DB claim remains CRITICAL and requires a human rationale | PASS |
| Live CLI | Project/task creation, event and receipt ingest, transcript/stdout/stderr artifacts, duplicate recognition, redacted previews, Inbox/status against Compose; missing extractor visibly produces FAILED reduction with all sources retained | PASS |
| Backend tests | **107 passed** on actual PostgreSQL 18.6 (Windows Python 3.14.7) | PASS |
| Coverage | Application 91%, deterministic policy 97%, reduction 93%; combined **93%**, exceeding the 85% target | PASS |
| Frontend tests | **2 unit tests passed**, Vitest 5.0.3 | PASS |
| Browser tests | **3 Playwright scenarios passed**, Chromium; receipt and decision screenshots visually inspected | PASS |
| Static checks/build | Ruff, MyPy strict, ESLint, TypeScript and Vite production build | PASS |
| Frontend dependency audit | `npm audit`: **0 detected vulnerabilities** in the tested lockfile | PASS |

The acceptance stack listens at <http://127.0.0.1:5173>; API documentation is at <http://127.0.0.1:8000/docs>. Its development token is `DIRIMA_TOKEN` in the ignored local `.tools/acceptance.env`. No credential value is included in this report. Data and evidence volumes are retained.

## Reproduce

From the repository root in PowerShell, with Node.js available on PATH:

```powershell
./scripts/test-compose.ps1 -EnvFile .tools/acceptance.env -WslDistribution DirimaDev
```

With a normal Docker installation, omit `-WslDistribution` and use the configured `.env`. The script builds and starts the stack, checks the API and worker, and runs the browser scenarios. It creates isolated acceptance records; it does not erase volumes. See [development setup](development.md) for the separate test database and full check commands.

## Validation limits

Configured model roles were tested through schema-validated fixture providers, timeout/invalid-output fixtures, and the HTTP adapter with a mocked transport. No paid external model API or live Durevi instance was contacted. Durevi integration and process wrapping are optional in v0.1; the MCP boundary is present, and a process wrapper is not provided. Token volume and cognitive compression metrics are estimates when source usage is absent. A dependency audit only reports advisories known to the audit service at execution time.

Non-failing upstream warnings remain: Starlette's TestClient recommends migrating from httpx to httpx2; Rollup removes two misplaced annotation comments from Zod. They do not affect the recorded passing results.
