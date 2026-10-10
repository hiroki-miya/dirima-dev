# Development

Requirements: Python 3.13+, Node.js, PostgreSQL 18 (the specification permits 16+), and Docker Compose for complete acceptance testing.

```text
cd backend
uv sync --extra dev
alembic upgrade head
uv run pytest --cov=dirima.application --cov=dirima.policy --cov=dirima.reduction --cov-report=term-missing
uv run ruff check .
uv run mypy dirima

cd ../frontend
npm ci
npx playwright install chromium
npm run test
npm run typecheck
npm run lint
npm run build
npm run test:e2e
```

Backend integration tests use `TEST_DATABASE_URL` pointing to a dedicated PostgreSQL database with a name containing `_test`. They truncate test tables between scenarios and must never target a production database. SQLite is not used. Browser E2E tests require the API, worker, PostgreSQL and frontend to be running, with `DIRIMA_TOKEN` and `DIRIMA_WORKSPACE_ID` set for the test runner.

Configuration variables are listed in `.env.example`. Model role names are configured through `DIRIMA_MODEL_<ROLE>`. Receipt-only Runs can be reduced without a model; Runs with transcripts, tool/status text or text artifacts require the extractor. A receipt never bypasses these sources. Missing extraction configuration creates a visible failed reduction. Direct provider calls are recorded in `model_calls` with hashes, timing, usage, cost and status.

Stop API/Worker processes before applying an upgrade to an existing development database. Migrations 0006–0008 preserve source records and decisions, rebuild derived keys, and enqueue Tasks for re-evaluation. Old summaries are hidden until the audited policy generates a fresh receipt. Legacy verification without a captured command result becomes INCONCLUSIVE; import a captured result to establish a new check. These migrations deliberately do not attempt to restore obsolete derived conclusions on downgrade.

Model advice can veto a deterministic resolution or raise its risk, and cannot authorize a weaker result. The receipt generator supplies only a narrative; recorded verification, unresolved items and decisions are preserved. Explicit model role settings without an endpoint create a visible failure and do not silently enable automatic resolution.

For Windows with the dedicated WSL development distribution, use `scripts/test-compose.ps1 -EnvFile .tools/acceptance.env -WslDistribution DirimaDev`. With ordinary Docker, use `scripts/test-compose.ps1 -EnvFile .env`. Node.js/npm must be on PATH. The script loads the selected development environment into its process and starts the `dirima-acceptance` project. The backend test database remains separate from its persistent browser-test data.

`backend/scripts/smoke_cli.py` exercises every implemented CLI input type against a running development stack using `DIRIMA_TOKEN`, `DIRIMA_WORKSPACE_ID` and optional `DIRIMA_API_URL`. It creates a uniquely named test project and retains its records.

The optional `scripts/wsl-docker-bootstrap.sh` installs the official Docker Engine/Compose packages and starts Docker only inside an already-created `DirimaDev` Ubuntu 24.04 WSL distribution. It is not part of normal product startup. Restart an installed daemon when needed with:

```powershell
wsl.exe -d DirimaDev -u root --exec bash -lc 'nohup dockerd > /var/log/dirima-docker.log 2>&1 < /dev/null &'
```

Stop the acceptance services without deleting evidence or database volumes:

```powershell
wsl.exe -d DirimaDev -u root --exec docker compose --project-directory /mnt/c/20_PG/Dirima --env-file /mnt/c/20_PG/Dirima/.tools/acceptance.env -p dirima-acceptance stop
```
