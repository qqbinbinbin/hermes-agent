"""Trusted completion decisions use the real plugin delivery contract."""

import pytest

from hermes_cli import plugins
from hermes_cli.middleware import resolve_tool_batch_completion
from hermes_cli.plugins import PluginManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = PluginManager()
    result._discovered = True
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: result)
    return result


@pytest.mark.parametrize("votes", [
    [{}], [{"action": "complete", "message": ""}],
    [{"action": "complete", "message": "ok"}] * 2,
    [{"action": "complete", "message": "ok", "extra": True}],
    [{"action": "complete", "message": "x" * 501}],
])
def test_ambiguous_or_malformed_votes_cannot_complete(manager, votes):
    manager._middleware["tool_batch_completion"] = [
        lambda decision=vote, **_kw: decision for vote in votes
    ]
    assert resolve_tool_batch_completion(messages=[], task_id="t", turn_id="u") is None


def test_no_plugins_preserves_normal_loop(manager):
    assert resolve_tool_batch_completion(messages=[], task_id="t", turn_id="u") is None


def test_completion_callback_cannot_mutate_native_history(manager):
    messages = [{"role": "tool", "content": "saved"}]
    seen = []

    def complete(**kwargs):
        seen.append((kwargs["task_id"], kwargs["turn_id"]))
        kwargs["messages"][0]["content"] = "changed"
        return {"action": "complete", "message": "receipt submitted"}

    manager._middleware["tool_batch_completion"] = [complete]
    assert resolve_tool_batch_completion(messages=messages, task_id="t", turn_id="u") == "receipt submitted"
    assert messages == [{"role": "tool", "content": "saved"}]
    assert seen == [("t", "u")]
