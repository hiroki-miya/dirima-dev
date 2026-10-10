"""Deterministic safety gates. Model confidence and votes are never authority."""

from __future__ import annotations

import re

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
TRANSITIONS = {
    "CREATED": {"RUNNING"},
    "RUNNING": {"WAITING", "SUCCEEDED", "FAILED", "CANCELLED"},
    "WAITING": {"RUNNING", "FAILED", "CANCELLED"},
    "UNKNOWN": {"RUNNING", "WAITING", "FAILED", "CANCELLED"},
}
SENSITIVE = re.compile(
    r"\b(security|auth(?:entication|orization)?|privacy|legal|compliance|publication|financial|production|destructive|delete|drop|migration)\b|セキュリティ|認証|権限|個人情報|法務|外部公開|財務|本番|破壊",
    re.I,
)
IRREVERSIBLE = re.compile(
    r"\b(irreversible|destructive|production\s+db|drop\s+table|delete\s+data)\b|不可逆|破壊|本番.{0,8}(DB|データベース)|データ削除",
    re.I,
)


def transition(old: str, new: str) -> None:
    if new not in TRANSITIONS.get(old, set()):
        raise ValueError(f"Invalid run transition: {old} -> {new}")


def classify_risk(claim: str, proposed: str = "LOW") -> tuple[str, bool, str]:
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    if proposed not in rank:
        raise ValueError("Invalid risk level")
    irreversible = bool(IRREVERSIBLE.search(claim))
    sensitive = bool(SENSITIVE.search(claim))
    if irreversible or sensitive:
        minimum = (
            "CRITICAL"
            if irreversible and ("production" in claim.lower() or "本番" in claim)
            else "HIGH"
        )
        return (
            max((minimum, proposed), key=rank.__getitem__),
            irreversible,
            "Sensitive or irreversible impact",
        )
    return proposed, False, "Reported risk; independently classified minimum"


def auto_resolvable(
    *,
    risk: str,
    irreversible: bool,
    sensitive: bool,
    passed: bool,
    independent_families: int,
    authoritative: bool = False,
    contrary_failed: bool = False,
    inconclusive: bool = False,
) -> bool:
    if risk in {"HIGH", "CRITICAL"} or irreversible or sensitive or inconclusive:
        return False
    return authoritative or contrary_failed or (passed and independent_families >= 2)


def attention(
    *,
    risk: str,
    irreversible: bool,
    conflict: bool,
    failed: bool,
    blocking: bool = False,
    verified: bool = False,
) -> str:
    if blocking:
        return "BLOCKING"
    if conflict or risk in {"HIGH", "CRITICAL"} or irreversible:
        return "HUMAN_REQUIRED"
    if failed or not verified:
        return "ATTENTION"
    return "NO_ACTION"
