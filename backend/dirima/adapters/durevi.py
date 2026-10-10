"""Optional long-term memory boundary. Dirima does not write to Durevi in v0.1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

ALLOWED_KINDS = {"FINAL_HUMAN_DECISION", "STABLE_PROJECT_CONSTRAINT", "CONFIRMED_LONG_LIVED_FACT"}


@dataclass(frozen=True)
class DurableCandidate:
    workspace_id: UUID
    project_id: UUID
    kind: str
    content: str
    provenance_ref: str
    confirmed_by: str


def eligible(candidate: DurableCandidate) -> bool:
    return bool(
        candidate.kind in ALLOWED_KINDS
        and candidate.content.strip()
        and candidate.provenance_ref.strip()
        and candidate.confirmed_by.strip()
    )


class DureviMcpPort(Protocol):
    def propose(self, candidate: DurableCandidate) -> str:
        """Explicit opt-in adapter operation; no implementation is active in v0.1."""
        ...
