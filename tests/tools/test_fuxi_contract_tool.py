"""Tests for FUXI profile contract HTTP tool calls."""

import json
import re

import httpx


def test_contract_tool_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("FUXI_CONTRACT_BASE_URL", raising=False)

    from tools.fuxi_contract_tool import check_fuxi_contract_requirements

    assert check_fuxi_contract_requirements() is False


def test_contract_tool_requires_allowlisted_tool(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "https://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_JWT", "jwt-token")
    monkeypatch.setenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "fuxi.director.goal.read")

    from tools.fuxi_contract_tool import fuxi_contract_call

    result = json.loads(
        fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.query",
                "payload": {"query": "x"},
            }
        )
    )

    assert result["error"] == "tool_not_allowed"
    assert "fuxi.knowledge.query" in result["detail"]


def test_contract_tool_posts_json_with_bearer_jwt(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "https://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_JWT", "jwt-token")
    monkeypatch.setenv(
        "FUXI_CONTRACT_TOOL_ALLOWLIST",
        "fuxi.director.goal.read,fuxi.knowledge.query",
    )

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "items": [1]})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.query",
                "payload": {"query": "policy"},
                "timeout_seconds": 3,
            }
        )
    )

    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["data"] == {"ok": True, "items": [1]}
    assert seen == {
        "method": "POST",
        "url": "https://fuxi.example/functions/v1/fuxi-contract-tools",
        "authorization": "Bearer jwt-token",
        "content_type": "application/json",
        "body": {
            "tool": "fuxi.knowledge.query",
            "payload": {"query": "policy"},
        },
    }


def test_contract_tool_posts_business_contract_payload_with_hmac(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "https://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "business-contract-tools")
    monkeypatch.setenv("FUXI_CONTRACT_AUTH_MODE", "hmac")
    monkeypatch.setenv("HERMES_INGEST_HMAC_KEY", "hmac-secret")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("FUXI_CONTRACT_EMPLOYEE_ID", "22222222-3333-4444-5555-666666666666")
    monkeypatch.setenv("HERMES_SESSION_ID", "session-1")
    monkeypatch.setenv(
        "FUXI_CONTRACT_TOOL_ALLOWLIST",
        "fuxi.knowledge.qa,fuxi.askdata.execute",
    )
    monkeypatch.delenv("FUXI_CONTRACT_JWT", raising=False)
    monkeypatch.delenv("FUXI_CONTRACT_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_JWT", raising=False)

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["timestamp"] = request.headers.get("X-FUXI-HERMES-TIMESTAMP")
        seen["signature"] = request.headers.get("X-FUXI-HERMES-SIGNATURE")
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"tool": "fuxi.knowledge.qa", "result": {"answer": "ok"}})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.qa",
                "payload": {"question": "policy"},
                "timeout_seconds": 3,
            }
        )
    )

    assert result["success"] is True
    assert seen["method"] == "POST"
    assert seen["url"] == "https://fuxi.example/functions/v1/business-contract-tools"
    assert seen["authorization"] is None
    assert seen["content_type"] == "application/json"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", seen["timestamp"])
    assert re.match(r"^sha256=[a-f0-9]{64}$", seen["signature"])
    assert seen["body"] == {
        "action": "fuxi.knowledge.qa",
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "employee_id": "22222222-3333-4444-5555-666666666666",
        "caller_session": "session-1",
        "input": {"question": "policy"},
    }


def test_contract_tool_exchanges_executor_token_for_short_lived_jwt(monkeypatch):
    tenant_id = "11111111-2222-3333-4444-555555555555"
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "https://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "director-contract-gateway")
    monkeypatch.setenv("FUXI_CONTRACT_JWT_ENDPOINT", "http://executor-gateway:9130/internal/director/jwt")
    monkeypatch.setenv("EXECUTOR_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setenv("FUXI_CONTRACT_PROFILE_NAME", f"director@{tenant_id}")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", tenant_id)
    monkeypatch.setenv("FUXI_CONTRACT_GOAL_ID", "goal-1")
    monkeypatch.delenv("FUXI_CONTRACT_JWT", raising=False)
    monkeypatch.delenv("FUXI_CONTRACT_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_JWT", raising=False)

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(
            {
                "method": request.method,
                "url": str(request.url),
                "internal_token": request.headers.get("X-Internal-Token"),
                "authorization": request.headers.get("Authorization"),
                "body": payload,
            }
        )
        if str(request.url) == "http://executor-gateway:9130/internal/director/jwt":
            return httpx.Response(200, json={"access_token": "runtime-jwt", "token_type": "Bearer"})
        return httpx.Response(200, json={"ok": True, "data": {"accepted": True}})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "fuxi.director.goal.read",
                "payload": {"status_filter": ["submitted"]},
                "idempotency_key": "idem-1",
                "timeout_seconds": 3,
            }
        )
    )

    assert result["success"] is True
    assert seen == [
        {
            "method": "POST",
            "url": "http://executor-gateway:9130/internal/director/jwt",
            "internal_token": "internal-token",
            "authorization": None,
            "body": {
                "profile_name": f"director@{tenant_id}",
                "tenant_id": tenant_id,
                "goal_id": "goal-1",
                "scope": ["fuxi.director.goal.read"],
            },
        },
        {
            "method": "POST",
            "url": "https://fuxi.example/functions/v1/director-contract-gateway/fuxi.director.goal.read",
            "internal_token": None,
            "authorization": "Bearer runtime-jwt",
            "body": {
                "goal_id": "goal-1",
                "idempotency_key": "idem-1",
                "payload": {"status_filter": ["submitted"]},
            },
        },
    ]


def test_contract_tool_rejects_non_https_base_url(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "http://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_JWT", "jwt-token")

    from tools.fuxi_contract_tool import fuxi_contract_call

    result = json.loads(
        fuxi_contract_call(
            {
                "tool": "fuxi.director.goal.read",
                "payload": {},
            }
        )
    )

    assert result["error"] == "invalid_base_url"
