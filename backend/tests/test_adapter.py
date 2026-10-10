import base64
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dirima.adapters.generic import AdapterContext, GenericAdapter


def test_generic_adapter_normalizes_receipt_and_streams():
    context = AdapterContext(*(uuid4() for _ in range(4)), external_run_id="external-1")
    adapter = GenericAdapter()
    receipt = {
        "schema_version": "1.0",
        "status": "succeeded",
        "summary": "Done",
        "artifacts": [],
        "reported_findings": [],
        "reported_verifications": [],
        "reported_risks": [],
        "unresolved": [],
    }
    completion = adapter.receipt(receipt, context, idempotency_key="same")
    assert completion.type == "run.completed"
    assert completion.idempotency_key == "same"
    assert completion.payload["receipt"]["status"] == "succeeded"
    stdout = adapter.text(b"hello", "stdout.log", "stdout", context)
    assert stdout.type == "artifact.created"
    assert stdout.payload["stream"] == "stdout"
    assert base64.b64decode(stdout.payload["content_base64"]) == b"hello"
    with pytest.raises(ValueError):
        adapter.text(b"no", "x", "command", context)
    with pytest.raises(ValidationError):
        adapter.receipt({"status": "succeeded"}, context)
