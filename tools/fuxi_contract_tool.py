"""FUXI profile contract HTTP tool.

This tool is intentionally opt-in for goal-runtime profiles such as
``director@<tenant>``. Ordinary profile runtimes do not expose it unless
``HERMES_PROFILE_CONTRACT_TOOLS_ENABLED=1`` is set.
"""

from __future__ import annotations

import json
import os
import hmac
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from tools.registry import registry, tool_error, tool_result
from utils import is_truthy_value

DEFAULT_ENDPOINT = "fuxi-contract-tools"
DEFAULT_ALLOWED_TOOLS = frozenset(
    {
        "fuxi.director.goal.read",
        "fuxi.director.loop_event.append",
        "fuxi.director.intervention.request",
        "fuxi.director.intervention.poll",
        "fuxi.director.readback.write",
        "fuxi.director.health_alert.pull",
        "fuxi.director.plan.propose",
        "fuxi.director.solution.upsert",
        "fuxi.director.solution.submit",
        "fuxi.director.acceptance.run",
        "fuxi.director.export.pack",
        "fuxi.knowledge.query",
        "fuxi.data.query",
        "fuxi.ontology.query",
        "fuxi.skill.query",
        "fuxi.workforce.task.create",
        "fuxi.workforce.release.request",
        "fuxi.worker.read",
        "fuxi.workflow.read",
    }
)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _allowed_tools() -> set[str]:
    configured = os.getenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "").strip()
    return set(_split_csv(configured)) if configured else set(DEFAULT_ALLOWED_TOOLS)


def _base_url() -> str:
    return os.getenv("FUXI_CONTRACT_BASE_URL", "").strip().rstrip("/")


def _endpoint() -> str:
    return os.getenv("FUXI_CONTRACT_ENDPOINT", DEFAULT_ENDPOINT).strip().strip("/") or DEFAULT_ENDPOINT


def _uses_tool_path(endpoint: str) -> bool:
    return (
        endpoint == "director-contract-gateway"
        or "{tool}" in endpoint
        or is_truthy_value(os.getenv("FUXI_CONTRACT_TOOL_PATH_ENABLED"))
    )


def _contract_url(base_url: str, endpoint: str, tool_name: str) -> str:
    if "{tool}" in endpoint:
        return f"{base_url}/{endpoint.replace('{tool}', quote(tool_name, safe='.'))}"
    if _uses_tool_path(endpoint):
        return f"{base_url}/{endpoint}/{quote(tool_name, safe='.')}"
    return f"{base_url}/{endpoint}"


def _jwt() -> str:
    return (
        os.getenv("FUXI_CONTRACT_JWT", "")
        or os.getenv("FUXI_CONTRACT_BEARER_TOKEN", "")
        or os.getenv("SUPABASE_JWT", "")
    ).strip()


def _profile_name() -> str:
    return (
        os.getenv("FUXI_CONTRACT_PROFILE_NAME", "")
        or os.getenv("HERMES_PROFILE_NAME", "")
        or os.getenv("API_SERVER_MODEL_NAME", "")
    ).strip()


def _tenant_id() -> str:
    configured = (
        os.getenv("FUXI_CONTRACT_TENANT_ID", "")
        or os.getenv("HERMES_PROFILE_TENANT_ID", "")
    ).strip()
    if configured:
        return configured
    profile_name = _profile_name()
    if profile_name.startswith("director@"):
        return profile_name.removeprefix("director@").strip()
    return ""


def _employee_id() -> str:
    return (
        os.getenv("FUXI_CONTRACT_EMPLOYEE_ID", "")
        or os.getenv("FUXI_CONTRACT_WORKER_ID", "")
        or os.getenv("HERMES_PROFILE_WORKER_ID", "")
    ).strip()


def _auth_mode() -> str:
    return os.getenv("FUXI_CONTRACT_AUTH_MODE", "").strip().lower()


def _hmac_secret_env() -> str:
    return os.getenv("FUXI_CONTRACT_HMAC_SECRET_ENV", "HERMES_INGEST_HMAC_KEY").strip() or "HERMES_INGEST_HMAC_KEY"


def _hmac_secret() -> str:
    return os.getenv(_hmac_secret_env(), "").strip()


def _caller_session() -> str:
    try:
        from gateway.session_context import get_session_env

        session_id = get_session_env("HERMES_SESSION_ID", "")
        if session_id:
            return session_id
    except Exception:
        pass
    return (
        os.getenv("HERMES_SESSION_ID", "")
        or os.getenv("HERMES_SESSION_KEY", "")
        or os.getenv("FUXI_CONTRACT_CALLER_SESSION", "")
    ).strip()


def _jwt_endpoint() -> str:
    configured = (
        os.getenv("FUXI_CONTRACT_JWT_ENDPOINT", "")
        or os.getenv("DIRECTOR_JWT_ENDPOINT", "")
    ).strip()
    if configured:
        return configured.rstrip("/")

    jwks_url = os.getenv("DIRECTOR_JWT_JWKS_URL", "").strip()
    suffix = "/.well-known/director-jwks.json"
    if jwks_url.endswith(suffix):
        return f"{jwks_url.removesuffix(suffix)}/internal/director/jwt"
    return ""


def _build_transport() -> httpx.BaseTransport | None:
    return None


def _validate_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        return "FUXI_CONTRACT_BASE_URL must use https"
    if not parsed.netloc:
        return "FUXI_CONTRACT_BASE_URL must include a host"
    return None


def _dynamic_jwt_ready() -> bool:
    return bool(
        os.getenv("EXECUTOR_INTERNAL_TOKEN", "").strip()
        and _jwt_endpoint()
        and _profile_name()
        and _tenant_id()
    )


def check_fuxi_contract_requirements() -> bool:
    if not is_truthy_value(os.getenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED")):
        return False
    base_url = _base_url()
    if not base_url or _validate_base_url(base_url):
        return False
    if _auth_mode() == "hmac":
        return bool(_hmac_secret() and _tenant_id() and _employee_id())
    return bool(_jwt()) or _dynamic_jwt_ready()


def _exchange_director_jwt(tool_name: str, goal_id: str | None, timeout_seconds: float) -> tuple[str, str | None]:
    endpoint = _jwt_endpoint()
    internal_token = os.getenv("EXECUTOR_INTERNAL_TOKEN", "").strip()
    profile_name = _profile_name()
    tenant_id = _tenant_id()
    missing = [
        name
        for name, value in {
            "FUXI_CONTRACT_JWT_ENDPOINT or DIRECTOR_JWT_JWKS_URL": endpoint,
            "EXECUTOR_INTERNAL_TOKEN": internal_token,
            "FUXI_CONTRACT_PROFILE_NAME or API_SERVER_MODEL_NAME": profile_name,
            "FUXI_CONTRACT_TENANT_ID or director@<tenant> profile name": tenant_id,
        }.items()
        if not value
    ]
    if missing:
        return "", ", ".join(missing)

    body: dict[str, Any] = {
        "profile_name": profile_name,
        "tenant_id": tenant_id,
        "scope": [tool_name],
    }
    if goal_id:
        body["goal_id"] = goal_id

    try:
        with httpx.Client(transport=_build_transport(), timeout=timeout_seconds) as client:
            response = client.post(
                endpoint,
                headers={
                    "X-Internal-Token": internal_token,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            try:
                data: Any = response.json()
            except ValueError:
                data = {}
            if response.status_code >= 400:
                return "", f"executor-gateway returned HTTP {response.status_code}: {data}"
            token = str(data.get("access_token") or "").strip() if isinstance(data, dict) else ""
            if not token:
                return "", "executor-gateway response did not include access_token"
            return token, None
    except httpx.HTTPError as exc:
        return "", str(exc)


def _request_body(endpoint: str, payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if not _uses_tool_path(endpoint):
        return {
            "tool": str(args.get("tool") or "").strip(),
            "payload": payload,
        }

    goal_id = str(args.get("goal_id") or payload.get("goal_id") or os.getenv("FUXI_CONTRACT_GOAL_ID", "")).strip()
    body: dict[str, Any] = {
        "idempotency_key": str(args.get("idempotency_key") or f"hermes-{uuid.uuid4()}"),
        "payload": payload,
    }
    if goal_id:
        body["goal_id"] = goal_id
    if args.get("sequence_no") is not None:
        body["sequence_no"] = args.get("sequence_no")
    return body


def _business_contract_body(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": str(args.get("tool") or "").strip(),
        "tenant_id": str(args.get("tenant_id") or payload.get("tenant_id") or _tenant_id()).strip(),
        "employee_id": str(args.get("employee_id") or payload.get("employee_id") or _employee_id()).strip(),
        "caller_session": str(args.get("caller_session") or payload.get("caller_session") or _caller_session()).strip(),
        "input": payload,
    }


def _sign_hmac_body(body_text: str, timestamp: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body_text}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fuxi_contract_call(args: dict[str, Any], **_kw) -> str:
    """POST one allowlisted FUXI contract call to the configured Edge endpoint."""
    if not is_truthy_value(os.getenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED")):
        return tool_error("contract_tools_disabled", detail="FUXI contract tools are not enabled for this profile")

    tool_name = str(args.get("tool") or "").strip()
    if not tool_name:
        return tool_error("missing_tool", detail="'tool' is required")
    allowed = _allowed_tools()
    if tool_name not in allowed:
        return tool_error("tool_not_allowed", detail=f"{tool_name} is not in FUXI_CONTRACT_TOOL_ALLOWLIST")

    payload = args.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return tool_error("invalid_payload", detail="'payload' must be an object")

    base_url = _base_url()
    base_error = _validate_base_url(base_url)
    if base_error:
        return tool_error("invalid_base_url", detail=base_error)

    try:
        timeout_seconds = float(args.get("timeout_seconds") or os.getenv("FUXI_CONTRACT_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        timeout_seconds = 30.0
    timeout_seconds = max(1.0, min(timeout_seconds, 120.0))

    endpoint = _endpoint()
    if _auth_mode() == "hmac":
        request_body = _business_contract_body(payload, args)
    else:
        request_body = _request_body(endpoint, payload, args)
    url = _contract_url(base_url, endpoint, tool_name)
    transport = _build_transport()
    headers = {"Content-Type": "application/json"}
    if _auth_mode() == "hmac":
        secret = _hmac_secret()
        if not secret:
            return tool_error("missing_hmac_secret", detail=f"{_hmac_secret_env()} is required")
        if not request_body["tenant_id"]:
            return tool_error("missing_tenant_id", detail="FUXI_CONTRACT_TENANT_ID or tenant_id is required")
        if not request_body["employee_id"]:
            return tool_error("missing_employee_id", detail="FUXI_CONTRACT_EMPLOYEE_ID or employee_id is required")
        body_text = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
        timestamp = _utc_timestamp()
        headers["X-FUXI-HERMES-TIMESTAMP"] = timestamp
        headers["X-FUXI-HERMES-SIGNATURE"] = _sign_hmac_body(body_text, timestamp, secret)
        request_content: Any = body_text
    else:
        goal_id = request_body.get("goal_id")
        token = _jwt()
        if not token:
            token, exchange_error = _exchange_director_jwt(
                tool_name,
                goal_id if isinstance(goal_id, str) else None,
                timeout_seconds,
            )
            if exchange_error:
                return tool_error("missing_jwt", detail=exchange_error)
        headers["Authorization"] = f"Bearer {token}"
        request_content = request_body

    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            if _auth_mode() == "hmac":
                response = client.post(url, headers=headers, content=request_content)
            else:
                response = client.post(url, headers=headers, json=request_content)
            try:
                data: Any = response.json()
            except ValueError:
                data = response.text
            if response.status_code >= 400:
                return tool_error(
                    "http_error",
                    status_code=response.status_code,
                    data=data,
                )
            return tool_result({"success": True, "status_code": response.status_code, "data": data})
    except httpx.HTTPError as exc:
        return tool_error("network_error", detail=str(exc))


FUXI_CONTRACT_CALL_SCHEMA = {
    "name": "fuxi_contract_call",
    "description": (
        "Call one allowlisted FUXI contract tool from a goal-runtime profile. "
        "Use only for profile-owned contract tools such as fuxi.director.* and fuxi.knowledge.query."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Allowlisted FUXI contract tool name, for example fuxi.director.goal.read.",
            },
            "payload": {
                "type": "object",
                "description": "JSON payload passed to the FUXI contract endpoint.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Optional request timeout between 1 and 120 seconds.",
            },
            "goal_id": {
                "type": "string",
                "description": "Optional Director goal id for ledger mutations and scoped runtime JWT claims.",
            },
            "idempotency_key": {
                "type": "string",
                "description": "Optional idempotency key. Generated automatically when omitted.",
            },
            "sequence_no": {
                "type": "number",
                "description": "Optional per-goal sequence number for ordered Director ledger writes.",
            },
        },
        "required": ["tool", "payload"],
    },
}


registry.register(
    name="fuxi_contract_call",
    toolset="fuxi_contract",
    schema=FUXI_CONTRACT_CALL_SCHEMA,
    handler=fuxi_contract_call,
    check_fn=check_fuxi_contract_requirements,
    requires_env=["HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "FUXI_CONTRACT_BASE_URL"],
    description="Call allowlisted FUXI contract tools over HTTPS from a profile goal runtime",
    emoji="🔗",
)
