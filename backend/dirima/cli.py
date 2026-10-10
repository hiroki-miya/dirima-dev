from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, cast

import httpx
import typer
import uvicorn

from dirima.adapters.generic import AdapterContext, GenericAdapter
from dirima.infrastructure.logging import UVICORN_LOG_CONFIG
from dirima.workers.queue import run_forever

app = typer.Typer(help="Dirima read-only control plane")
project = typer.Typer()
task = typer.Typer()
app.add_typer(project, name="project")
app.add_typer(task, name="task")


def request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any] | list[Any]:
    base = os.environ.get("DIRIMA_API_URL", "http://127.0.0.1:8000")
    token = os.environ.get("DIRIMA_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"}
    if os.environ.get("DIRIMA_WORKSPACE_ID"):
        headers["X-Workspace-ID"] = os.environ["DIRIMA_WORKSPACE_ID"]
    response = httpx.request(method, f"{base}{path}", headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return cast(dict[str, Any] | list[Any], response.json())


@project.command("create")
def project_create(name: str, description: str = "") -> None:
    typer.echo(
        json.dumps(
            request("POST", "/api/v1/projects", {"name": name, "description": description}),
            indent=2,
        )
    )


@task.command("create")
def task_create(project_id: uuid.UUID, title: str, description: str = "") -> None:
    typer.echo(
        json.dumps(
            request(
                "POST",
                "/api/v1/tasks",
                {"project_id": str(project_id), "title": title, "description": description},
            ),
            indent=2,
        )
    )


@app.command("ingest")
def ingest(
    path: Path,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    external_run_id: str | None = None,
    idempotency_key: str | None = None,
    stream: str = "transcript",
) -> None:
    """Ingest an event envelope, structured receipt, transcript, stdout or stderr."""
    adapter = GenericAdapter()
    context = None
    if (
        project_id is not None
        and task_id is not None
        and run_id is not None
        and external_run_id is not None
    ):
        context = AdapterContext(
            workspace_id=uuid.UUID(os.environ["DIRIMA_WORKSPACE_ID"]),
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            external_run_id=external_run_id,
        )
    if path.suffix.lower() == ".json":
        content = json.loads(path.read_text(encoding="utf-8"))
        if "event_id" in content:
            envelope = adapter.event(content)
        else:
            if context is None:
                raise typer.BadParameter(
                    "Receipt needs --project-id --task-id --run-id --external-run-id"
                )
            envelope = adapter.receipt(content, context, idempotency_key=idempotency_key)
    else:
        if context is None:
            raise typer.BadParameter(
                "Text input needs --project-id --task-id --run-id --external-run-id"
            )
        envelope = adapter.text(
            path.read_bytes(), path.name, stream, context, idempotency_key=idempotency_key
        )
    typer.echo(
        json.dumps(request("POST", "/api/v1/events", envelope.model_dump(mode="json")), indent=2)
    )


@app.command("inbox")
def inbox() -> None:
    typer.echo(json.dumps(request("GET", "/api/v1/decision-cases"), indent=2))


@app.command("status")
def status() -> None:
    typer.echo(json.dumps(request("GET", "/api/v1/metrics"), indent=2))


@app.command("serve")
def serve() -> None:
    uvicorn.run(
        "dirima.main:app",
        host=os.environ.get("DIRIMA_HOST", "127.0.0.1"),
        port=int(os.environ.get("DIRIMA_PORT", "8000")),
        log_config=UVICORN_LOG_CONFIG,
        access_log=False,
    )


@app.command("worker")
def worker() -> None:
    run_forever()


if __name__ == "__main__":
    app()
