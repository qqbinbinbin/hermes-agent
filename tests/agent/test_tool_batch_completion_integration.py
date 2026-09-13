"""Exercise the real native loop with a fake provider, never a network call."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def response(content, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=calls),
        finish_reason="tool_calls" if calls else "stop")], model="test/model", usage=None)


class NativeLoopCompletionTests(unittest.TestCase):
    def run_case(self, decision, *, persistence_failed=False, cancel=False, max_iterations=10):
        definitions = [{"type": "function", "function": {"name": "write_file",
                       "description": "test", "parameters": {"type": "object", "properties": {}}}}]
        with patch("run_agent.get_tool_definitions", return_value=definitions), \
             patch("run_agent.check_toolset_requirements", return_value={}), patch("run_agent.OpenAI"):
            agent = AIAgent(api_key="synthetic", base_url="https://example.invalid/v1/",
                            quiet_mode=True, skip_context_files=True, skip_memory=True)
        agent._cached_system_prompt = "Synthetic test."
        agent._use_prompt_caching = False
        agent.compression_enabled = False
        agent.save_trajectories = False
        agent.valid_tool_names = {"write_file"}
        agent.max_iterations = max_iterations
        agent.client = MagicMock()
        call = SimpleNamespace(id="receipt", type="function",
                               function=SimpleNamespace(name="write_file", arguments="{}"))
        agent.client.chat.completions.create.side_effect = [response("", [call]), response("ordinary end")]

        def tool(*args, **kwargs):
            if persistence_failed:
                agent._incremental_persistence_failed = True
            return '{"synthetic":true}'

        def vote(*args, **kwargs):
            if cancel:
                agent._interrupt_requested = True
            return decision

        with patch("run_agent.handle_function_call", side_effect=tool), \
             patch.object(agent, "_persist_session"), patch.object(agent, "_save_trajectory"), \
             patch.object(agent, "_cleanup_task_resources"), \
             patch("hermes_cli.middleware._has_middleware", side_effect=lambda kind: kind == "tool_batch_completion"), \
             patch("hermes_cli.middleware._invoke_middleware", side_effect=vote) as middleware:
            result = agent.run_conversation("submit synthetic receipt")
        return result, agent, middleware

    def test_trusted_completion_avoids_second_provider_request(self):
        result, agent, middleware = self.run_case([{"action": "complete", "message": "receipt submitted"}])
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["turn_exit_reason"], "native_tool_completion")
        self.assertTrue(result["completed"])
        self.assertEqual([m["role"] for m in middleware.call_args.kwargs["messages"]], ["assistant", "tool"])
        self.assertEqual(result["messages"][-1]["content"], "receipt submitted")

    def test_no_completion_vote_keeps_ordinary_second_request(self):
        result, agent, _ = self.run_case([])
        self.assertEqual(agent.client.chat.completions.create.call_count, 2)
        self.assertEqual(result["final_response"], "ordinary end")

    def test_persistence_failure_cannot_be_finished_by_plugin(self):
        result, agent, middleware = self.run_case(
            [{"action": "complete", "message": "receipt submitted"}], persistence_failed=True)
        self.assertFalse(result["completed"])
        self.assertEqual(result["turn_exit_reason"], "session_persistence_failed")
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)
        middleware.assert_not_called()

    def test_cancel_during_callback_wins(self):
        result, agent, _ = self.run_case([{"action": "complete", "message": "receipt submitted"}], cancel=True)
        self.assertNotEqual(result["turn_exit_reason"], "native_tool_completion")
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)

    def test_iteration_limit_is_not_relabelled_as_success(self):
        result, agent, _ = self.run_case([{"action": "complete", "message": "receipt submitted"}], max_iterations=1)
        self.assertFalse(result["completed"])
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)
        self.assertTrue(result["failed"])
        self.assertEqual(result["turn_exit_reason"], "budget_exhausted")


if __name__ == "__main__":
    unittest.main()
