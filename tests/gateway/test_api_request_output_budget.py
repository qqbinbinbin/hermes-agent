"""Per-request output budgets reach the native provider without changing defaults."""
import pytest
from unittest.mock import MagicMock
from tests.gateway.test_api_server import _patch_create_agent_runtime, _make_routing_adapter


class TestRequestOutputBudget:
    def test_request_output_budget_reaches_provider(self, monkeypatch):
        from run_agent import AIAgent
        from types import SimpleNamespace

        _patch_create_agent_runtime(monkeypatch, {}, AIAgent)
        monkeypatch.setattr(AIAgent, "_create_openai_client", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("model_tools.get_tool_definitions", lambda **_: [])
        monkeypatch.setattr("model_tools.check_toolset_requirements", lambda **_: {})
        adapter = _make_routing_adapter({})
        monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
        monkeypatch.setattr(adapter, "_session_model_override_for", lambda *_: None)
        agent = adapter._create_agent(session_id="provider-budget", model_options={
            "max_tokens": 8000, "reasoning": {"enabled": False},
        })
        agent._cached_system_prompt = "Synthetic test."
        agent._use_prompt_caching = False
        agent.compression_enabled = False
        agent.save_trajectories = False
        agent.client = MagicMock()
        agent.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None),
                                     finish_reason="stop")], model="global/model", usage=None)
        for method in ("_persist_session", "_save_trajectory", "_cleanup_task_resources"):
            monkeypatch.setattr(agent, method, MagicMock())
        result = agent.run_conversation("Synthetic budget check")
        assert result["completed"]
        request = agent.client.chat.completions.create.call_args.kwargs
        assert request["max_tokens"] == 8000
        assert agent.reasoning_config == {"enabled": False}

    @pytest.mark.parametrize("requested", [8000, 12000, None])
    def test_request_output_budget_overrides_only_this_agent(self, monkeypatch, requested):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        _patch_create_agent_runtime(monkeypatch, captured, FakeAgent)
        monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {
            "provider": "openrouter", "api_key": "synthetic",
            "base_url": "https://provider.invalid/v1", "max_tokens": 4096,
        })
        adapter = _make_routing_adapter({})
        monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
        monkeypatch.setattr(adapter, "_session_model_override_for", lambda *_: None)
        options = {"reasoning": {"enabled": False}}
        if requested is not None:
            options["max_tokens"] = requested
        adapter._create_agent(session_id="budget-test", model_options=options)
        assert captured["max_tokens"] == (requested or 4096)
        assert captured["reasoning_config"] == {"enabled": False}

    @pytest.mark.parametrize("requested", [True, False, 0, -1, 1.5, "8000", None])
    def test_invalid_request_output_budget_never_creates_agent(self, monkeypatch, requested):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        _patch_create_agent_runtime(monkeypatch, captured, FakeAgent)
        adapter = _make_routing_adapter({})
        monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
        monkeypatch.setattr(adapter, "_session_model_override_for", lambda *_: None)
        with pytest.raises(ValueError, match="max_tokens_must_be_positive_integer"):
            adapter._create_agent(session_id="invalid-budget", model_options={"max_tokens": requested})
        assert captured == {}

