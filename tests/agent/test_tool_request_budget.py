"""Tool middleware receives the model-loop budget on every native dispatch path."""
from types import SimpleNamespace

import pytest

from hermes_cli import plugins
from hermes_cli.plugins import PluginManager


@pytest.mark.parametrize("path", ["managed", "direct"])
def test_tool_callbacks_receive_one_budget_snapshot(path, monkeypatch):
    from agent import agent_runtime_helpers, tool_executor

    manager = PluginManager()
    manager._discovered = True
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    seen = []
    agent = SimpleNamespace(
        max_iterations=16, _api_call_count=13, iteration_budget=SimpleNamespace(remaining=9),
        session_id="", valid_tool_names={"synthetic"}, _current_turn_id="turn", _memory_manager=None,
    )

    def request(**context):
        seen.append(("request", context.get("request_budget")))
        agent._api_call_count = 14

    def execution(next_call, **context):
        seen.append(("execution", context.get("request_budget")))
        return next_call(context["args"])

    manager._middleware["tool_request"] = [request]
    manager._middleware["tool_execution"] = [execution]
    if path == "managed":
        monkeypatch.setattr(tool_executor, "_dispatch_authorized_once",
                            lambda *a, **kw: kw["execute"](a[2].args))
        result = tool_executor._run_agent_tool_execution_middleware(
            agent, function_name="synthetic", function_args={}, effective_task_id="task",
            tool_call_id="call", execute=lambda args: "executed")
        assert result.result == "executed"
    else:
        import model_tools
        monkeypatch.setattr(model_tools, "handle_function_call", lambda *a, **kw: "executed")
        assert agent_runtime_helpers.invoke_tool(
            agent, "synthetic", {}, "task", "call", pre_tool_block_checked=True
        ) == "executed"
    expected = {"max_requests": 16, "used_requests": 13, "remaining_requests": 3}
    assert seen == [("request", expected), ("execution", expected)]
