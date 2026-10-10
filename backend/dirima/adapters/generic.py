"""Documented, vendor-neutral input adapter for events, receipts and text streams."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dirima.schemas import AgentCompletionReceipt, EventEnvelope


@dataclass(frozen=True)
class AdapterContext:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID
    run_id: uuid.UUID
    external_run_id: str
    runtime: str = "generic-cli"


class GenericAdapter:
    def event(self, raw: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope.model_validate(raw)

    def receipt(
        self, raw: dict[str, Any], context: AdapterContext, *, idempotency_key: str | None = None
    ) -> EventEnvelope:
        receipt = AgentCompletionReceipt.model_validate(raw)
        return self._envelope(
            "run.completed", {"receipt": receipt.model_dump(mode="json")}, context, idempotency_key
        )

    def text(
        self,
        content: bytes,
        name: str,
        stream: str,
        context: AdapterContext,
        *,
        idempotency_key: str | None = None,
    ) -> EventEnvelope:
        if stream not in {"transcript", "stdout", "stderr"}:
            raise ValueError("stream must be transcript, stdout or stderr")
        payload = {
            "name": name,
            "mime_type": "text/plain",
            "stream": stream,
            "content_base64": base64.b64encode(content).decode(),
        }
        return self._envelope("artifact.created", payload, context, idempotency_key)

    def _envelope(
        self,
        event_type: str,
        payload: dict[str, Any],
        context: AdapterContext,
        idempotency_key: str | None,
    ) -> EventEnvelope:
        return EventEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "event_id": str(uuid.uuid4()),
                "workspace_id": str(context.workspace_id),
                "project_id": str(context.project_id),
                "task_id": str(context.task_id),
                "run_id": str(context.run_id),
                "source": {
                    "adapter": "generic",
                    "runtime": context.runtime,
                    "external_run_id": context.external_run_id,
                },
                "type": event_type,
                "occurred_at": datetime.now(UTC),
                "idempotency_key": idempotency_key or str(uuid.uuid4()),
                "payload": payload,
            }
        )
