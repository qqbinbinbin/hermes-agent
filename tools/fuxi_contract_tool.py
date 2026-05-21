"""FUXI profile contract HTTP tool.

This tool is intentionally opt-in for goal-runtime profiles such as
``director@<tenant>``. Ordinary profile runtimes do not expose it unless
``HERMES_PROFILE_CONTRACT_TOOLS_ENABLED=1`` is set.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

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
        "fuxi.knowledge.query",
        "fuxi.data.query",
        "fuxi.ontology.query",
        "fuxi.skill.query",
        "fuxi.workforce.task.create",
        "fuxi.workforce.release.request",
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


def _jwt() -> str:
    return (
        os.getenv("FUXI_CONTRACT_JWT", "")
        or os.getenv("FUXI_CONTRACT_BEARER_TOKEN", "")
        or os.getenv("SUPABASE_JWT", "")
    ).strip()


def _build_transport() -> httpx.BaseTransport | None:
    return None


def _validate_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        return "FUXI_CONTRACT_BASE_URL must use https"
    if not parsed.netloc:
        return "FUXI_CONTRACT_BASE_URL must include a host"
    return None


def check_fuxi_contract_requirements() -> bool:
    if not is_truthy_value(os.getenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED")):
        return False
    base_url = _base_url()
    if not base_url or _validate_base_url(base_url):
        return False
    return bool(_jwt())


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

    token = _jwt()
    if not token:
        return tool_error("missing_jwt", detail="FUXI_CONTRACT_JWT or FUXI_CONTRACT_BEARER_TOKEN is required")

    try:
        timeout_seconds = float(args.get("timeout_seconds") or os.getenv("FUXI_CONTRACT_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        timeout_seconds = 30.0
    timeout_seconds = max(1.0, min(timeout_seconds, 120.0))

    request_body = {"tool": tool_name, "payload": payload}
    url = f"{base_url}/{_endpoint()}"
    transport = _build_transport()
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
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
    requires_env=["HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "FUXI_CONTRACT_BASE_URL", "FUXI_CONTRACT_JWT"],
    description="Call allowlisted FUXI contract tools over HTTPS from a profile goal runtime",
    emoji="🔗",
)
