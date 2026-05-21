"""Tests for FUXI profile contract HTTP tool calls."""

import json

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
