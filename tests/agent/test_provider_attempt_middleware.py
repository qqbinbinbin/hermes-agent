"""Physical provider middleware covers main, auxiliary and streaming attempts."""
import asyncio
from contextlib import contextmanager

import pytest

from agent import relay_llm, relay_runtime
from hermes_cli import plugins
from hermes_cli.plugins import PluginManager


@contextmanager
def turn_context(metadata):
    lease = relay_runtime.ConversationLease(
        profile_key=relay_runtime.current_profile_key(), session_id="synthetic-session",
        platform="api_server", host=None, session=None)
    coordinator = relay_runtime.RelaySessionCoordinator()
    turn = coordinator.begin_turn(lease, turn_id="turn", task_id="task", metadata=metadata)
    try:
        yield turn
    finally:
        coordinator.end_turn(turn, outcome="success")
        coordinator.release_conversation(lease)


def test_turn_retains_isolated_metadata_without_a_relay_backend():
    original = {"correlation": {"trace": "first"}}
    with turn_context(original) as turn:
        original["correlation"]["trace"] = "mutated"
        assert getattr(turn, "metadata", {}) == {"correlation": {"trace": "first"}}
        assert relay_runtime.current_turn() is turn
    assert relay_runtime.current_turn() is None


@pytest.fixture
def provider_plugin(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    plugin = home / "plugins" / "attempt_fixture"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: attempt_fixture\nversion: 1.0.0\n")
    (plugin / "__init__.py").write_text('''
SEEN = []
def register(ctx):
    def before(request, call_role="", api_request_id="", **kwargs):
        from agent.relay_runtime import current_turn
        turn = current_turn()
        trace = getattr(turn, "metadata", {}).get("correlation", {}).get("trace")
        SEEN.append((call_role, api_request_id, trace))
        return {"request": {**request, "extra_headers": {
            **request.get("extra_headers", {}), "x-fixture-trace": trace}}}
    ctx.register_middleware("provider_request", before)
''')
    (home / "config.yaml").write_text("plugins:\n  enabled: [attempt_fixture]\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    monkeypatch.setattr(relay_llm._ManagedAttempt, "resolve", lambda *a, **k: None)
    return manager._plugins["attempt_fixture"].module.SEEN


@pytest.mark.parametrize("mode", ["sync", "async", "stream", "auxiliary"])
def test_physical_attempt_middleware_reaches_real_callback(provider_plugin, mode):
    request = {"model": "synthetic-model", "extra_headers": {"x-existing": "kept"}}
    metadata = {"api_mode": "chat_completions", "api_request_id": "physical-1",
                "call_role": "auxiliary:compression" if mode == "auxiliary" else "primary"}
    seen = []

    def callback(actual):
        seen.append(actual)
        return {"ok": True}

    with turn_context({"correlation": {"trace": "trusted-trace"}}):
        options = dict(name="custom", model_name="synthetic-model", metadata=metadata)
        if mode == "async":
            async def invoke(actual):
                return callback(actual)
            assert asyncio.run(relay_llm.execute_async(request, invoke, **options)) == {"ok": True}
        elif mode == "stream":
            stream = relay_llm.stream(request, lambda actual: iter([callback(actual)]),
                                      session_id="synthetic-session", finalizer=dict, **options)
            assert list(stream) == [{"ok": True}]
        else:
            assert relay_llm.execute(request, callback, **options) == {"ok": True}
    assert seen[0]["extra_headers"] == {"x-existing": "kept", "x-fixture-trace": "trusted-trace"}
    assert request["extra_headers"] == {"x-existing": "kept"}
    assert provider_plugin == [(metadata["call_role"], "physical-1", "trusted-trace")]
