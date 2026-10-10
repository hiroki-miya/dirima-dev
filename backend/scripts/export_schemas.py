"""Publish the same validated contracts used by the API and model boundary."""

import json
from pathlib import Path

from dirima.schemas import (
    AgentCompletionReceipt,
    ClusterClassification,
    CommandResult,
    ConflictClassification,
    EventEnvelope,
    Extraction,
    ReceiptSummary,
    ResolutionAdvice,
    RiskAssessment,
)

output = Path(__file__).resolve().parents[2] / "schemas"
output.mkdir(exist_ok=True)
for name, contract in {
    "event": EventEnvelope,
    "command-result": CommandResult,
    "receipt": AgentCompletionReceipt,
    "extraction": Extraction,
    "cluster": ClusterClassification,
    "conflict": ConflictClassification,
    "resolver": ResolutionAdvice,
    "risk": RiskAssessment,
    "receipt-summary": ReceiptSummary,
}.items():
    (output / f"{name}.schema.json").write_text(
        json.dumps(contract.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
