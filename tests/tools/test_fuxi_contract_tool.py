"""Tests for FUXI profile contract HTTP tool calls."""

import json
import re

import httpx
import pytest


def _configure_hmac_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "https://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "business-contract-tools")
    monkeypatch.setenv("FUXI_CONTRACT_AUTH_MODE", "hmac")
    monkeypatch.setenv("HERMES_INGEST_HMAC_KEY", "hmac-secret")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("FUXI_CONTRACT_EMPLOYEE_ID", "22222222-3333-4444-5555-666666666666")
    monkeypatch.setenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "fuxi.knowledge.qa")


def _contract_call(fuxi_contract_tool):
    return json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.qa",
                "payload": {"question": "sensitive business input"},
                "timeout_seconds": 3,
            }
        )
    )


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
        "fuxiToolContext": {
            "tenantId": "11111111-2222-3333-4444-555555555555",
            "workerId": "22222222-3333-4444-5555-666666666666",
        },
        "input": {
            "question": "policy",
            "fuxiToolContext": {
                "tenantId": "11111111-2222-3333-4444-555555555555",
                "workerId": "22222222-3333-4444-5555-666666666666",
            },
        },
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


def test_business_contract_hmac_allows_internal_http_base_url(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "http://host.docker.internal:8000/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "business-contract-tools")
    monkeypatch.setenv("FUXI_CONTRACT_AUTH_MODE", "hmac")
    monkeypatch.setenv("HERMES_INGEST_HMAC_KEY", "hmac-secret")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("FUXI_CONTRACT_EMPLOYEE_ID", "22222222-3333-4444-5555-666666666666")
    monkeypatch.setenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "fuxi.knowledge.qa")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["signature"] = request.headers.get("X-FUXI-HERMES-SIGNATURE")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

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
    assert seen["url"] == "http://host.docker.internal:8000/functions/v1/business-contract-tools"
    assert seen["signature"].startswith("sha256=")
    assert seen["body"]["tenant_id"] == "11111111-2222-3333-4444-555555555555"


def test_business_contract_hmac_uses_profile_identity_over_model_payload(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "http://host.docker.internal:8000/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "business-contract-tools")
    monkeypatch.setenv("FUXI_CONTRACT_AUTH_MODE", "hmac")
    monkeypatch.setenv("HERMES_INGEST_HMAC_KEY", "hmac-secret")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("FUXI_CONTRACT_EMPLOYEE_ID", "22222222-3333-4444-5555-666666666666")
    monkeypatch.setenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "kb.parse_document")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "kb.parse_document",
                "employee_id": "model-invented-worker",
                "payload": {
                    "tenant_id": "model-invented-tenant",
                    "employee_id": "fuxi__tenant__kb_ingestor",
                    "caller_session": "model-runtime-session",
                    "fuxiToolContext": {
                        "tenantId": "model-invented-tenant",
                        "workerId": "fuxi__tenant__kb_ingestor",
                        "chatSessionId": "33333333-4444-5555-6666-777777777777",
                    },
                },
            }
        )
    )

    assert result["success"] is True
    body = seen["body"]
    assert body["tenant_id"] == "11111111-2222-3333-4444-555555555555"
    assert body["employee_id"] == "22222222-3333-4444-5555-666666666666"
    assert body["caller_session"] == "33333333-4444-5555-6666-777777777777"
    assert body["fuxiToolContext"]["tenantId"] == body["tenant_id"]
    assert body["fuxiToolContext"]["workerId"] == body["employee_id"]
    assert body["input"]["fuxiToolContext"] == body["fuxiToolContext"]


def test_business_contract_hmac_rejects_external_http_base_url(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_CONTRACT_TOOLS_ENABLED", "1")
    monkeypatch.setenv("FUXI_CONTRACT_BASE_URL", "http://fuxi.example/functions/v1")
    monkeypatch.setenv("FUXI_CONTRACT_ENDPOINT", "business-contract-tools")
    monkeypatch.setenv("FUXI_CONTRACT_AUTH_MODE", "hmac")
    monkeypatch.setenv("HERMES_INGEST_HMAC_KEY", "hmac-secret")
    monkeypatch.setenv("FUXI_CONTRACT_TENANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("FUXI_CONTRACT_EMPLOYEE_ID", "22222222-3333-4444-5555-666666666666")
    monkeypatch.setenv("FUXI_CONTRACT_TOOL_ALLOWLIST", "fuxi.knowledge.qa")

    from tools.fuxi_contract_tool import fuxi_contract_call

    result = json.loads(
        fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.qa",
                "payload": {"question": "policy"},
            }
        )
    )

    assert result["error"] == "invalid_base_url"


@pytest.mark.parametrize(
    ("events", "exception_type", "expected_error_class"),
    [
        (
            ["connection.connect_tcp.started", "connection.connect_tcp.failed"],
            httpx.ConnectTimeout,
            "connect_timeout",
        ),
        (
            [
                "connection.connect_tcp.started",
                "connection.connect_tcp.complete",
                "connection.start_tls.started",
                "connection.start_tls.failed",
            ],
            httpx.ConnectTimeout,
            "tls_timeout",
        ),
        (
            [
                "connection.connect_tcp.started",
                "connection.connect_tcp.complete",
                "connection.start_tls.started",
                "connection.start_tls.complete",
                "http11.receive_response_headers.started",
                "http11.receive_response_headers.failed",
            ],
            httpx.ReadTimeout,
            "ttfb_timeout",
        ),
        (
            [
                "connection.connect_tcp.started",
                "connection.connect_tcp.complete",
                "connection.start_tls.started",
                "connection.start_tls.complete",
                "http11.receive_response_headers.started",
                "http11.receive_response_headers.complete",
                "http11.receive_response_body.started",
            ],
            httpx.ReadTimeout,
            "total_timeout",
        ),
    ],
)
def test_contract_tool_classifies_timeout_phase_and_writes_redacted_ledger(
    monkeypatch,
    tmp_path,
    events,
    exception_type,
    expected_error_class,
):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.extensions["trace"]
        for event in events:
            trace(event, {})
        raise exception_type("synthetic timeout", request=request)

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["error"] == "contract_call_failed"
    assert result["error_class"] == expected_error_class
    assert result["http_status"] is None
    assert re.fullmatch(r"[0-9a-f-]{36}", result["request_id"])
    assert "detail" not in result
    assert "知识库检索超时" not in json.dumps(result, ensure_ascii=False)

    ledger_path = tmp_path / "logs" / "fuxi-contract-call-ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "tool_name",
        "target_url",
        "http_status",
        "error_class",
        "dns_ms",
        "connect_ms",
        "tls_ms",
        "ttfb_ms",
        "total_ms",
        "request_id",
    }
    assert record["tool_name"] == "fuxi.knowledge.qa"
    assert record["target_url"] == "https://fuxi.example/functions/v1/business-contract-tools"
    assert record["http_status"] is None
    assert record["error_class"] == expected_error_class
    assert record["request_id"] == result["request_id"]
    assert record["dns_ms"] is None
    assert record["connect_ms"] is not None
    if expected_error_class != "connect_timeout":
        assert record["tls_ms"] is not None
    if expected_error_class in {"ttfb_timeout", "total_timeout"}:
        assert record["ttfb_ms"] is not None
    assert record["total_ms"] >= 0
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "sensitive business input" not in ledger_text
    assert "hmac-secret" not in ledger_text


def test_contract_tool_returns_structured_http_error_and_ledgers_status(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.extensions["trace"]
        trace("connection.connect_tcp.complete", {})
        trace("connection.start_tls.complete", {})
        trace("http11.receive_response_headers.complete", {})
        return httpx.Response(401, json={"error": "invalid_signature"})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["error"] == "contract_call_failed"
    assert result["error_class"] == "http_error"
    assert result["http_status"] == 401
    assert result["upstream_error"] == {"error": "invalid_signature"}
    assert re.fullmatch(r"[0-9a-f-]{36}", result["request_id"])

    ledger_path = tmp_path / "logs" / "fuxi-contract-call-ledger.jsonl"
    record = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert record["http_status"] == 401
    assert record["error_class"] == "http_error"
    assert record["request_id"] == result["request_id"]


def test_contract_tool_does_not_forward_free_form_upstream_error_text(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_payload",
                "reason": "schema_mismatch",
                "message": "sensitive business input",
                "payload": {"question": "sensitive business input"},
            },
        )

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["upstream_error"] == {
        "error": "invalid_payload",
        "reason": "schema_mismatch",
    }
    assert "sensitive business input" not in json.dumps(result)


def test_contract_tool_ledgers_success_without_payload_or_secret(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.extensions["trace"]
        trace("connection.connect_tcp.complete", {})
        trace("connection.start_tls.complete", {})
        trace("http11.receive_response_headers.complete", {})
        return httpx.Response(200, json={"ok": True})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["success"] is True
    assert result["status_code"] == 200
    assert re.fullmatch(r"[0-9a-f-]{36}", result["request_id"])
    ledger_path = tmp_path / "logs" / "fuxi-contract-call-ledger.jsonl"
    record = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert record["http_status"] == 200
    assert record["error_class"] is None
    assert record["request_id"] == result["request_id"]
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "sensitive business input" not in ledger_text
    assert "hmac-secret" not in ledger_text


def test_contract_tool_redacts_network_exception_detail(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "sensitive upstream host detail and token",
            request=request,
        )

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["error"] == "contract_call_failed"
    assert result["error_class"] == "network_error"
    assert result["http_status"] is None
    assert "detail" not in result
    assert "sensitive" not in json.dumps(result)
    ledger_text = (tmp_path / "logs" / "fuxi-contract-call-ledger.jsonl").read_text(encoding="utf-8")
    assert "sensitive" not in ledger_text
    assert "token" not in ledger_text


def test_contract_tool_ledger_failure_does_not_replace_upstream_result(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    from tools import fuxi_contract_tool

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        fuxi_contract_tool,
        "_append_contract_ledger",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("synthetic ledger failure")),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["success"] is True
    assert result["status_code"] == 200


def test_contract_tool_applies_distinct_phase_timeout_caps(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)
    now_ns = [0]

    from tools import fuxi_contract_tool

    monkeypatch.setattr(fuxi_contract_tool.time, "monotonic_ns", lambda: now_ns[0])

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.extensions["trace"]
        timeouts = request.extensions["timeout"]

        connect_info = {"timeout": timeouts.get("connect")}
        trace("connection.connect_tcp.started", connect_info)
        assert connect_info["timeout"] == 5.0
        now_ns[0] = 1_000_000_000
        trace("connection.connect_tcp.complete", {})
        tls_info = {"timeout": timeouts.get("connect")}
        trace("connection.start_tls.started", tls_info)
        assert tls_info["timeout"] == 10.0
        now_ns[0] = 2_000_000_000
        trace("connection.start_tls.complete", {})
        trace("http11.receive_response_headers.started", {"request": request})
        assert timeouts.get("read") == 15.0
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = json.loads(
        fuxi_contract_tool.fuxi_contract_call(
            {
                "tool": "fuxi.knowledge.qa",
                "payload": {},
                "timeout_seconds": 30,
            }
        )
    )

    assert result["success"] is True


def test_contract_tool_total_deadline_shrinks_later_phase_budget(monkeypatch, tmp_path):
    _configure_hmac_contract(monkeypatch, tmp_path)
    now_ns = [0]

    from tools import fuxi_contract_tool

    monkeypatch.setattr(fuxi_contract_tool.time, "monotonic_ns", lambda: now_ns[0])

    def handler(request: httpx.Request) -> httpx.Response:
        trace = request.extensions["trace"]
        timeouts = request.extensions["timeout"]

        now_ns[0] = 500_000_000
        connect_info = {"timeout": timeouts.get("connect")}
        trace("connection.connect_tcp.started", connect_info)
        assert connect_info["timeout"] == 2.5
        now_ns[0] = 1_000_000_000
        trace("connection.connect_tcp.complete", {})
        tls_info = {"timeout": timeouts.get("connect")}
        trace("connection.start_tls.started", tls_info)
        assert tls_info["timeout"] == 2.0
        now_ns[0] = 2_500_000_000
        trace("connection.start_tls.complete", {})
        trace("http11.receive_response_headers.started", {"request": request})
        assert timeouts.get("read") == 0.5
        now_ns[0] = 3_000_000_000
        trace("http11.receive_response_headers.failed", {})
        raise httpx.ReadTimeout("synthetic total deadline", request=request)

    monkeypatch.setattr(
        fuxi_contract_tool,
        "_build_transport",
        lambda: httpx.MockTransport(handler),
    )

    result = _contract_call(fuxi_contract_tool)

    assert result["error_class"] == "total_timeout"
