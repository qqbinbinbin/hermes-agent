"""Content-free acknowledgement of a native, durably saved iteration boundary."""
import hashlib
import json
import math

DURABLE_YIELD_CHECKPOINT = True


def _sha(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("yield_history_too_large")
    return hashlib.sha256(data).hexdigest()


def _history(value):
    from agent.memory_manager import sanitize_context

    if not isinstance(value, list) or not value:
        raise ValueError("yield_history_invalid")
    result = []
    pending = set()
    for message in value:
        if not isinstance(message, dict):
            raise ValueError("yield_history_invalid")
        role = message.get("role")
        if role == "system":
            continue
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("yield_history_invalid")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            content = sanitize_context(content).strip()
        item = {"role": role, "content": content}
        for key in ("tool_calls", "tool_call_id", "api_content", "reasoning_content",
                    "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if message.get(key) is not None:
                item[key] = message[key]
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError("yield_tools_unsettled")
            pending.remove(call_id)
        else:
            if pending:
                raise ValueError("yield_tools_unsettled")
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ValueError("yield_tools_unsettled")
            for call in calls:
                call_id = call.get("id") if isinstance(call, dict) else None
                if role != "assistant" or not isinstance(call_id, str) or not call_id or call_id in pending:
                    raise ValueError("yield_tools_unsettled")
                pending.add(call_id)
        result.append(item)
    if pending:
        raise ValueError("yield_tools_unsettled")
    if not result:
        raise ValueError("yield_history_invalid")
    return result


def _identity(root_session_id, session_id, deadline_at, now):
    if any(not isinstance(value, str) or not value for value in (root_session_id, session_id)):
        raise ValueError("yield_session_invalid")
    if (type(deadline_at) not in (int, float) or not math.isfinite(deadline_at)
            or type(now) not in (int, float) or not math.isfinite(now) or deadline_at <= now):
        raise ValueError("yield_deadline_invalid")


def build_yield_checkpoint(result, *, root_session_id, session_id, history, deadline_at, now):
    _identity(root_session_id, session_id, deadline_at, now)
    if (not isinstance(result, dict) or result.get("yielded") is not True
            or result.get("completed") is not False or result.get("failed") is not False
            or result.get("interrupted") is not False or result.get("cleanup_errors")
            or result.get("turn_exit_reason") != "iteration_limit_yield"
            or result.get("session_id") != session_id
            or type(result.get("api_calls")) is not int or result["api_calls"] < 1):
        raise ValueError("yield_result_invalid")
    saved = _history(history)
    expected = _history(result.get("messages"))
    if saved != expected:
        raise ValueError("yield_history_mismatch")
    checkpoint = {"version": "hermes.run-yield/v1", "sessionId": root_session_id,
                  "tipSessionId": session_id, "historySha256": _sha(saved),
                  "messageCount": len(saved), "apiCalls": result["api_calls"],
                  "deadlineAt": deadline_at, "stopReason": "iteration_limit"}
    return {**checkpoint, "checkpointSha256": _sha(checkpoint)}


def verify_yield_resume(parent, *, root_session_id, session_id, history, deadline_at, now):
    _identity(root_session_id, session_id, deadline_at, now)
    if not isinstance(parent, dict) or parent.get("status") != "paused":
        raise ValueError("yield_parent_invalid")
    checkpoint = parent.get("continuation")
    keys = {"version", "sessionId", "tipSessionId", "historySha256", "messageCount",
            "apiCalls", "deadlineAt", "stopReason", "checkpointSha256"}
    if not isinstance(checkpoint, dict) or set(checkpoint) != keys:
        raise ValueError("yield_checkpoint_invalid")
    content = {key: value for key, value in checkpoint.items() if key != "checkpointSha256"}
    if (checkpoint["checkpointSha256"] != _sha(content)
            or checkpoint["version"] != "hermes.run-yield/v1"
            or checkpoint["sessionId"] != root_session_id
            or checkpoint["tipSessionId"] != session_id
            or checkpoint["deadlineAt"] != deadline_at
            or checkpoint["stopReason"] != "iteration_limit"
            or type(checkpoint["apiCalls"]) is not int or checkpoint["apiCalls"] < 1):
        raise ValueError("yield_checkpoint_invalid")
    saved = _history(history)
    if checkpoint["messageCount"] != len(saved) or checkpoint["historySha256"] != _sha(saved):
        raise ValueError("yield_history_mismatch")
    return checkpoint
