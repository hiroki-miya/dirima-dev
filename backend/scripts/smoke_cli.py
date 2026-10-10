"""Live CLI smoke test. Run only against a development acceptance stack."""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx


def cli(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "dirima.cli", *args], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def main() -> None:
    project = cli("project", "create", f"CLI-smoke-{uuid.uuid4()}")
    task = cli("task", "create", project["id"], "CLI import acceptance")
    run, external = str(uuid.uuid4()), str(uuid.uuid4())
    context = [
        "--project-id",
        project["id"],
        "--task-id",
        task["id"],
        "--run-id",
        run,
        "--external-run-id",
        external,
    ]
    with TemporaryDirectory(prefix="dirima-cli-smoke-") as directory:
        root = Path(directory)
        for kind in ("run.created", "run.started"):
            envelope = {
                "schema_version": "1.0",
                "event_id": str(uuid.uuid4()),
                "workspace_id": os.environ["DIRIMA_WORKSPACE_ID"],
                "project_id": project["id"],
                "task_id": task["id"],
                "run_id": run,
                "source": {"adapter": "generic", "runtime": "cli", "external_run_id": external},
                "type": kind,
                "occurred_at": datetime.now(UTC).isoformat(),
                "idempotency_key": str(uuid.uuid4()),
                "payload": {},
            }
            path = root / "event.json"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            assert not cli("ingest", str(path))["duplicate"]
            assert cli("ingest", str(path))["duplicate"]
        receipt = root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "succeeded",
                    "summary": "CLI receipt",
                    "reported_findings": [{"subject": "CLI", "claim": "Receipt imported"}],
                }
            ),
            encoding="utf-8",
        )
        assert not cli("ingest", str(receipt), *context)["duplicate"]
        for stream in ("transcript", "stdout", "stderr"):
            path = root / f"{stream}.txt"
            path.write_text("API_KEY=secret-value\nCLI source evidence", encoding="utf-8")
            result = cli(
                "ingest", str(path), *context, "--stream", stream, "--idempotency-key", stream
            )
            assert result["artifact_id"]
            assert cli(
                "ingest", str(path), *context, "--stream", stream, "--idempotency-key", stream
            )["duplicate"]
    with httpx.Client(
        base_url=os.environ.get("DIRIMA_API_URL", "http://127.0.0.1:8000"),
        headers={"Authorization": f"Bearer {os.environ['DIRIMA_TOKEN']}"},
    ) as client:
        for _ in range(30):
            response = client.get(f"/api/v1/tasks/{task['id']}/receipt")
            if (
                response.is_success
                and len(response.json()["content"]["artifacts"]) == 3
                and response.json()["content"]["reduction_status"] == "FAILED"
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Worker receipt did not appear")
        run_detail = client.get(f"/api/v1/runs/{run}")
        run_detail.raise_for_status()
        assert len(run_detail.json()["events"]) == 6
        assert len(run_detail.json()["artifacts"]) == 3
        assert "content_base64" not in run_detail.text
        # This acceptance stack intentionally has no paid model configured. All sources
        # must be retained and missing extraction must be visible, never silently skipped.
        attention = client.get("/api/v1/attention")
        attention.raise_for_status()
        assert any(
            item["task_id"] == task["id"]
            and item["title"] == "Extractor provider is not configured"
            for item in attention.json()
        )
        for artifact in run_detail.json()["artifacts"]:
            preview = client.get(f"/api/v1/artifacts/{artifact['id']}/content")
            preview.raise_for_status()
            assert "secret-value" not in preview.text
            assert "[REDACTED]" in preview.text
    assert isinstance(cli("inbox"), list)
    assert cli("status")["dirima_runs_total"] >= 1
    print(
        "CLI smoke PASS: project, task, events, receipt, transcript, stdout, stderr, duplicates, redaction, inbox, status"
    )


if __name__ == "__main__":
    main()
