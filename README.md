# Dirima

Dirima is a human-attention control plane for AI work.  
It collects work from heterogeneous agents, resolves what can be resolved automatically, and surfaces only the conflicts, risks, and decisions that deserve human attention.

v0.1 is an observation, reduction and decision inbox. It does not launch agents, execute commands, change project files, push code, or publish anything. A structured agent receipt remains self-reported evidence until separate verification supports it.

## Start

1. Copy `.env.example` to `.env`, set a strong `DIRIMA_TOKEN` and `POSTGRES_PASSWORD`, and generate a UUID for `DIRIMA_WORKSPACE_ID`.
2. Run `docker compose up --build -d`.
3. Open <http://127.0.0.1:5173> and enter the token. API documentation is at <http://127.0.0.1:8000/docs>.

The Compose stack uses PostgreSQL 18.6. Its migration service applies Alembic before the API and worker start. The API itself never creates tables. The worker uses a PostgreSQL lease queue with `FOR UPDATE SKIP LOCKED`; jobs are retried after crashes. Artifacts are addressed by SHA-256 under the `artifacts` volume.

## CLI

Install Python 3.13 or later and run `cd backend && pip install -e .`. Set `DIRIMA_API_URL`, `DIRIMA_TOKEN` and `DIRIMA_WORKSPACE_ID` in your shell.

```text
dirima project create "My project"
dirima task create PROJECT_UUID "Review migration"
dirima ingest event.json
dirima ingest receipt.json --project-id PROJECT_UUID --task-id TASK_UUID --run-id RUN_UUID --external-run-id AGENT_RUN_ID
dirima ingest transcript.txt --project-id PROJECT_UUID --task-id TASK_UUID --run-id RUN_UUID --external-run-id AGENT_RUN_ID
dirima ingest stdout.log --stream stdout --project-id PROJECT_UUID --task-id TASK_UUID --run-id RUN_UUID --external-run-id AGENT_RUN_ID
dirima inbox
dirima status
dirima serve
dirima worker
```

For a new Run, ingest a `run.created` envelope first, followed by `run.started` and its later events. See [API documentation](docs/api.md). Transcript extraction requires a configured structured model endpoint; validated completion receipts can be reduced without one. Unavailable models and invalid outputs produce visible attention items.

## Development and validation

See [development setup](docs/development.md) for PostgreSQL-backed integration tests and frontend tests. [Acceptance results](docs/acceptance.md) record the actual PostgreSQL 18.6, Compose, Worker, CLI and browser checks completed on 2026-10-07, together with validation limits.

The operating boundaries and decisions are described in [architecture](docs/architecture.md), [domain model](docs/domain-model.md), and [security](docs/security.md).
