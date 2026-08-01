"""TDD contract for the Director model-call restore gates."""

from __future__ import annotations

import pytest

from cron.director_restore_gate import (
    DirectorRestoreGate,
    DirectorRestoreGateBlocked,
    DirectorRestoreGateTerminalWriteFailed,
)


TENANT_ID = "11111111-2222-3333-4444-555555555555"
GOAL_ID = "22222222-3333-4444-5555-666666666666"


class FakeContract:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, tool, payload):
        self.calls.append((tool, payload))
        if not self.responses:
            raise AssertionError(f"unexpected contract call: {tool}")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _job():
    return {
        "id": "director-cycle-1",
        "name": "director",
        "profile_name": f"director@{TENANT_ID}",
        "goal_id": GOAL_ID,
        "model": "qwen3.6-plus",
        "provider": "dashscope",
        "est_tokens": 1200,
        "est_cost_cny": 0.12,
    }


def _allowed_preflight():
    return {
        "ok": True,
        "data": {
            "budget_allowed": True,
            "model_call_allowed": True,
            "actionable_goal_count": 1,
            "error_loop_breaker_tripped": False,
        },
    }


def test_non_builtin_cron_provider_is_blocked_before_contract_or_model():
    contract = FakeContract([])
    gate = DirectorRestoreGate(contract)

    with pytest.raises(DirectorRestoreGateBlocked, match="cronProviderLocked"):
        gate.before_model_call(_job(), cron_provider="chronos")

    assert contract.calls == []


def test_budget_fuse_blocks_model_call():
    contract = FakeContract([
        {
            "ok": True,
            "data": {
                "budget_allowed": False,
                "reason": "budget_exhausted",
                "actionable_goal_count": 1,
                "error_loop_breaker_tripped": False,
            },
        },
    ])
    gate = DirectorRestoreGate(contract)

    with pytest.raises(DirectorRestoreGateBlocked, match="budgetFuse"):
        gate.before_model_call(_job(), cron_provider="builtin")

    assert [tool for tool, _ in contract.calls] == ["fuxi.director.model.preflight"]


def test_zero_actionable_goals_blocks_model_call():
    contract = FakeContract([
        {
            "ok": True,
            "data": {
                "budget_allowed": True,
                "actionable_goal_count": 0,
                "error_loop_breaker_tripped": False,
            },
        },
    ])
    gate = DirectorRestoreGate(contract)

    with pytest.raises(DirectorRestoreGateBlocked, match="zeroModelPolling"):
        gate.before_model_call(_job(), cron_provider="builtin")


def test_error_loop_breaker_blocks_model_call():
    contract = FakeContract([
        {
            "ok": True,
            "data": {
                "budget_allowed": True,
                "actionable_goal_count": 1,
                "error_loop_breaker_tripped": True,
            },
        },
    ])
    gate = DirectorRestoreGate(contract)

    with pytest.raises(DirectorRestoreGateBlocked, match="errorLoopBreaker"):
        gate.before_model_call(_job(), cron_provider="builtin")


def test_usage_started_failure_blocks_model_call():
    contract = FakeContract([
        _allowed_preflight(),
        {"ok": False, "error": "usage_write_failed"},
    ])
    gate = DirectorRestoreGate(contract)

    with pytest.raises(DirectorRestoreGateBlocked, match="usageLedger"):
        gate.before_model_call(_job(), cron_provider="builtin")

    assert [tool for tool, _ in contract.calls] == [
        "fuxi.director.model.preflight",
        "fuxi.director.model.usage.started",
    ]


def test_terminal_usage_failure_is_observable_after_model_call():
    contract = FakeContract([
        _allowed_preflight(),
        {"ok": True, "data": {"invocation_id": "inv-1"}},
        {"ok": False, "error": "usage_terminal_write_failed"},
    ])
    gate = DirectorRestoreGate(contract)
    invocation = gate.before_model_call(_job(), cron_provider="builtin")

    with pytest.raises(
        DirectorRestoreGateTerminalWriteFailed,
        match="usageLedger",
    ):
        gate.after_model_call(invocation, {"final_response": "ok"})

    assert [tool for tool, _ in contract.calls] == [
        "fuxi.director.model.preflight",
        "fuxi.director.model.usage.started",
        "fuxi.director.model.usage.terminal",
    ]


def test_terminal_usage_is_written_for_failed_model_result():
    contract = FakeContract([
        _allowed_preflight(),
        {"ok": True, "data": {"invocation_id": "inv-2"}},
        {"ok": True, "data": {"recorded": True}},
    ])
    gate = DirectorRestoreGate(contract)
    invocation = gate.before_model_call(_job(), cron_provider="builtin")

    gate.after_model_call(
        invocation,
        {"failed": True, "error": "provider_failed", "usage": {"total_tokens": 12}},
        status="failed",
        error_code="provider_failed",
    )

    terminal_payload = contract.calls[-1][1]
    assert terminal_payload["status"] == "failed"
    assert terminal_payload["error_code"] == "provider_failed"
    assert terminal_payload["total_tokens"] == 12
