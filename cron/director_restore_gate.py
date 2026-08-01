"""Fail-closed control plane for Director cron model calls.

The Director runtime is the Hermes cron process. FUXI remains the authority
for budget, goal polling, usage attribution, and contract-error breakers. This
module only orchestrates those contract calls immediately before and after the
model call; it does not maintain a second ledger or budget implementation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

PREFLIGHT_TOOL = "fuxi.director.model.preflight"
USAGE_STARTED_TOOL = "fuxi.director.model.usage.started"
USAGE_TERMINAL_TOOL = "fuxi.director.model.usage.terminal"


class DirectorRestoreGateBlocked(RuntimeError):
    """Raised when a Director model call is not allowed to start."""


class DirectorRestoreGateTerminalWriteFailed(RuntimeError):
    """Raised when terminal usage attribution cannot be recorded."""


@dataclass(frozen=True)
class DirectorInvocation:
    """Stable attribution values passed from preflight to terminal usage."""

    invocation_id: str
    job_id: str
    profile_name: str
    tenant_id: str
    goal_id: str
    provider: str
    model: str
    est_tokens: int
    est_cost_cny: float


def _response_data(response: Any, stage: str) -> dict[str, Any]:
    """Normalize the JSON returned by fuxi_contract_call.

    A missing success marker or data object is a contract failure. Accepting a
    truthy-but-unstructured response would turn a transport problem into a
    model-call permission, which is precisely the unsafe path this gate stops.
    """
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except (TypeError, ValueError) as exc:
            raise DirectorRestoreGateBlocked(
                f"{stage}: invalid contract response"
            ) from exc
    if not isinstance(response, dict):
        raise DirectorRestoreGateBlocked(f"{stage}: invalid contract response")
    if response.get("ok") is False or response.get("success") is False:
        detail = response.get("error") or response.get("detail") or "contract_call_failed"
        raise DirectorRestoreGateBlocked(f"{stage}: {detail}")
    if response.get("ok") is not True and response.get("success") is not True:
        raise DirectorRestoreGateBlocked(f"{stage}: contract success marker missing")
    data = response.get("data")
    if not isinstance(data, dict):
        raise DirectorRestoreGateBlocked(f"{stage}: contract data missing")
    # fuxi_contract_call wraps the gateway body in its own success envelope;
    # accept the direct fake-client shape used by unit tests as well as the
    # real HTTP shape without weakening the required inner data object.
    if data.get("ok") is True:
        data = data.get("data")
        if not isinstance(data, dict):
            raise DirectorRestoreGateBlocked(f"{stage}: contract data missing")
    return data


def _required_text(data: dict[str, Any], key: str, stage: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DirectorRestoreGateBlocked(f"{stage}: {key} missing")
    return value.strip()


class DirectorRestoreGate:
    """Orchestrate the FUXI-owned Director restore controls."""

    def __init__(self, contract: Any = None):
        self._contract = contract or _DefaultContract()

    def _call(self, tool: str, payload: dict[str, Any]) -> Any:
        try:
            caller = self._contract.call
            return caller(tool, payload)
        except DirectorRestoreGateBlocked:
            raise
        except Exception as exc:
            logger.error("Director restore gate contract call failed: %s", tool)
            raise DirectorRestoreGateBlocked(
                f"{tool}: contract unavailable"
            ) from exc

    def before_model_call(
        self,
        job: dict[str, Any],
        *,
        cron_provider: str,
    ) -> DirectorInvocation:
        if cron_provider != "builtin":
            raise DirectorRestoreGateBlocked("cronProviderLocked: provider must be builtin")

        profile_name = _required_job_text(job, "profile_name")
        tenant_id = _tenant_from_profile(profile_name)
        goal_id = _required_job_text(job, "goal_id")
        provider = _required_job_text(job, "provider")
        model = _required_job_text(job, "model")
        job_id = _required_job_text(job, "id")
        est_tokens = _positive_int(job.get("est_tokens"))
        if est_tokens is None:
            raise DirectorRestoreGateBlocked(
                "budgetFuse: estimated token budget missing"
            )
        est_cost_cny = _non_negative_float(job.get("est_cost_cny"), 0.0)
        if est_cost_cny is None:
            raise DirectorRestoreGateBlocked("budgetFuse: estimated cost is invalid")
        payload = {
            "job_id": job_id,
            "profile_name": profile_name,
            "tenant_id": tenant_id,
            "goal_id": goal_id,
            "provider": provider,
            "model": model,
            "est_tokens": est_tokens,
            "est_cost_cny": est_cost_cny,
        }

        preflight = _response_data(
            self._call(PREFLIGHT_TOOL, payload),
            "budgetFuse/zeroModelPolling/errorLoopBreaker",
        )
        if preflight.get("budget_allowed") is not True:
            raise DirectorRestoreGateBlocked("budgetFuse: model call blocked")
        actionable_count = preflight.get("actionable_goal_count")
        if not isinstance(actionable_count, int) or isinstance(actionable_count, bool):
            raise DirectorRestoreGateBlocked("zeroModelPolling: actionable count missing")
        if actionable_count < 1:
            raise DirectorRestoreGateBlocked("zeroModelPolling: no actionable goals")
        if preflight.get("error_loop_breaker_tripped") is not False:
            raise DirectorRestoreGateBlocked("errorLoopBreaker: model call blocked")
        if preflight.get("model_call_allowed") is not True:
            raise DirectorRestoreGateBlocked("restoreGate: model call blocked")

        started = _response_data(
            self._call(USAGE_STARTED_TOOL, payload),
            "usageLedger",
        )
        invocation_id = _required_text(started, "invocation_id", "usageLedger")
        return DirectorInvocation(
            invocation_id=invocation_id,
            job_id=job_id,
            profile_name=profile_name,
            tenant_id=tenant_id,
            goal_id=goal_id,
            provider=provider,
            model=model,
            est_tokens=est_tokens,
            est_cost_cny=est_cost_cny,
        )

    def after_model_call(
        self,
        invocation: DirectorInvocation,
        result: dict[str, Any],
        *,
        status: str = "succeeded",
        error_code: str | None = None,
    ) -> None:
        usage = result.get("usage") if isinstance(result, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        payload = {
            "invocation_id": invocation.invocation_id,
            "job_id": invocation.job_id,
            "profile_name": invocation.profile_name,
            "tenant_id": invocation.tenant_id,
            "goal_id": invocation.goal_id,
            "provider": invocation.provider,
            "model": invocation.model,
            "est_tokens": invocation.est_tokens,
            "est_cost_cny": invocation.est_cost_cny,
            "status": status,
            "error_code": error_code,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        try:
            data = _response_data(
                self._call(USAGE_TERMINAL_TOOL, payload),
                "usageLedger",
            )
        except DirectorRestoreGateBlocked as exc:
            raise DirectorRestoreGateTerminalWriteFailed(str(exc)) from exc
        if data.get("recorded") is not True:
            raise DirectorRestoreGateTerminalWriteFailed(
                "usageLedger: terminal usage was not recorded"
            )


class _DefaultContract:
    def call(self, tool: str, payload: dict[str, Any]) -> Any:
        from tools.fuxi_contract_tool import fuxi_contract_call

        raw = fuxi_contract_call({"tool": tool, "payload": payload})
        return json.loads(raw) if isinstance(raw, str) else raw


def _required_job_text(job: dict[str, Any], key: str) -> str:
    value = job.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DirectorRestoreGateBlocked(f"usageLedger: job {key} missing")
    return value.strip()


def _tenant_from_profile(profile_name: str) -> str:
    if not profile_name.startswith("director@"):
        raise DirectorRestoreGateBlocked("usageLedger: Director profile name required")
    tenant_id = profile_name.removeprefix("director@").strip()
    if not tenant_id:
        raise DirectorRestoreGateBlocked("usageLedger: tenant id missing")
    return tenant_id


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _non_negative_float(value: Any, default: float) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
