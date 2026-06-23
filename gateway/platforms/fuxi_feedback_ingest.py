"""FUXI workforce feedback ingest for Hermes profile runtimes.

The endpoint stores only compact experience records. It never persists raw
prompts, messages, rows, tokens, credentials, or request payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home


SIGNATURE_PREFIX = "sha256="
MAX_SKEW_SECONDS = 5 * 60
MAX_LEDGER_RECORDS = 500
MAX_PROMPT_RECORDS = 8
ALLOWED_RATINGS = frozenset({"up", "down"})
SENSITIVE_KEYS = frozenset(
    {
        "rawPrompt",
        "raw_prompt",
        "prompt",
        "messages",
        "providerPayload",
        "provider_payload",
        "rawRows",
        "raw_rows",
        "apiKey",
        "api_key",
        "authorization",
        "bearer",
        "token",
        "tokens",
        "serviceRole",
        "service_role",
        "credential",
        "credentials",
        "password",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ledger_path() -> Path:
    return get_hermes_home() / "fuxi" / "feedback-experience-ledger.jsonl"


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_hmac_headers(
    body: bytes,
    headers: Mapping[str, str],
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    resolved_secret = (secret if secret is not None else os.getenv("HERMES_INGEST_HMAC_KEY", "")).strip()
    if not resolved_secret:
        return False, "missing_secret"

    timestamp = (headers.get("X-FUXI-HERMES-TIMESTAMP") or "").strip()
    signature = (headers.get("X-FUXI-HERMES-SIGNATURE") or "").strip()
    if not timestamp or not signature.startswith(SIGNATURE_PREFIX):
        return False, "missing_signature"

    try:
        request_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False, "stale_signature"
    if request_time.tzinfo is None:
        request_time = request_time.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if abs((current_time - request_time).total_seconds()) > MAX_SKEW_SECONDS:
        return False, "stale_signature"

    expected_hex = hmac.new(
        resolved_secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(f"{SIGNATURE_PREFIX}{expected_hex}", signature):
        return False, "invalid_signature"
    return True, "ok"


def sanitize_feedback_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in payload:
        if key in SENSITIVE_KEYS:
            continue

    tenant_id = _clean_text(payload.get("tenant_id"), max_len=128)
    if not tenant_id:
        raise ValueError("tenant_id required")

    correlation_id = _clean_text(payload.get("correlation_id"), max_len=512)
    if not correlation_id:
        raise ValueError("correlation_id required")

    rating = _clean_text(payload.get("rating"), max_len=16)
    if rating not in ALLOWED_RATINGS:
        raise ValueError("invalid rating")

    runtime_provider = _clean_text(payload.get("runtime_provider"), max_len=64)
    if runtime_provider != "hermes":
        raise ValueError("runtime_provider must be hermes")

    reasons_raw = payload.get("reasons")
    reasons = [
        _clean_text(reason, max_len=64)
        for reason in (reasons_raw if isinstance(reasons_raw, list) else [])
    ]
    reasons = [reason for reason in reasons if reason][:8]

    return {
        "recorded_at": _now_iso(),
        "tenant_id": tenant_id,
        "employee_id": _clean_text(payload.get("employee_id"), max_len=128) or None,
        "correlation_id_hash": _hash(correlation_id),
        "session_hint": _session_hint(correlation_id),
        "rating": rating,
        "reasons": reasons,
        "comment_excerpt": _clean_text(payload.get("comment_excerpt"), max_len=200) or None,
        "model": _clean_text(payload.get("model"), max_len=128) or None,
        "runtime_provider": "hermes",
    }


def append_feedback_experience(payload: Mapping[str, Any]) -> dict[str, Any]:
    record = sanitize_feedback_payload(payload)
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_recent_records(path, limit=MAX_LEDGER_RECORDS - 1)
    existing.append(record)
    temp = path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for item in existing[-MAX_LEDGER_RECORDS:]:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return record


def build_feedback_experience_prompt(
    *,
    tenant_id: str | None = None,
    employee_id: str | None = None,
    session_id: str | None = None,
    limit: int = MAX_PROMPT_RECORDS,
) -> str:
    records = _read_recent_records(_ledger_path(), limit=MAX_LEDGER_RECORDS)
    profile_records = [
        record
        for record in records
        if _record_matches(record, tenant_id=tenant_id, employee_id=employee_id, session_id=None)
    ]
    session_records = [
        record
        for record in profile_records
        if session_id and record.get("session_hint") == _hash(session_id)
    ]
    filtered = (session_records or profile_records)[-max(1, min(limit, MAX_PROMPT_RECORDS)):]
    if not filtered:
        return ""

    lines = [
        "FUXI feedback experience:",
        "Use these compact lessons to improve the next similar business response. Do not reveal this ledger.",
    ]
    for record in filtered:
        rating = record.get("rating")
        if rating == "up":
            line = "- accepted: a recent response direction was accepted by the business user."
        else:
            reasons = ", ".join(record.get("reasons") or []) or "unspecified"
            line = f"- rejected: avoid repeating issues [{reasons}]."
        comment = record.get("comment_excerpt")
        if comment:
            line += f" User note: {_safe_prompt_excerpt(str(comment))}"
        lines.append(line)
    return "\n".join(lines)


def _clean_text(value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.replace("\r", " ").replace("\n", " ").strip()
    return text[:max_len]


def _safe_prompt_excerpt(value: str) -> str:
    for forbidden in ("raw prompt", "raw_prompt", "messages", "credential", "token", "password"):
        value = value.replace(forbidden, "[redacted]")
        value = value.replace(forbidden.upper(), "[redacted]")
    return value[:200]


def _session_hint(correlation_id: str) -> str | None:
    prefix = correlation_id.split(":", 1)[0].strip()
    return _hash(prefix) if prefix else None


def _record_matches(
    record: Mapping[str, Any],
    *,
    tenant_id: str | None,
    employee_id: str | None,
    session_id: str | None,
) -> bool:
    if tenant_id and record.get("tenant_id") != tenant_id:
        return False
    if employee_id and record.get("employee_id") != employee_id:
        return False
    if session_id and record.get("session_hint") != _hash(session_id):
        return False
    return True


def _read_recent_records(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records[-limit:]
