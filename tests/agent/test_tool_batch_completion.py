"""No-provider regression for the real post-tool conversation boundary."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ToolBatchCompletionTest(unittest.TestCase):
    def boundary(self, agent):
        agent.max_iterations = 10
        agent.iteration_budget = SimpleNamespace(remaining=9)
        # Compile the maintained boundary itself, not a copied implementation.
        source = Path(__file__).parents[2] / "agent/conversation_loop.py"
        tree = ast.parse(source.read_text())
        matches = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                   and any(isinstance(c, ast.Name) and c.id == "tool_batch_completion"
                           for c in ast.walk(n.test))]
        self.assertEqual(len(matches), 1, "native completion boundary must exist exactly once")
        loop = ast.While(test=ast.Constant(True), body=[matches[0], ast.Break()], orelse=[])
        namespace = {"agent": agent, "messages": [], "effective_task_id": "task",
                     "turn_id": "turn", "api_call_count": 1, "tool_batch_start": 0,
                     "tool_batch_completion": "receipt submitted", "final_response": None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])),
                     str(source), "exec"), namespace)
        return namespace

    def test_boundary_finishes_without_provider_call(self):
        agent = SimpleNamespace(_interrupt_requested=False)
        result = self.boundary(agent)
        self.assertEqual(result["final_response"], "receipt submitted")
        self.assertEqual(result["_turn_exit_reason"], "native_tool_completion")

    def test_cancel_wins_over_completion(self):
        result = self.boundary(SimpleNamespace(_interrupt_requested=True))
        self.assertIsNone(result["final_response"])

    def test_contract_rejects_ambiguous_and_malformed_votes(self):
        from hermes_cli.middleware import resolve_tool_batch_completion
        for votes in [[{}], [{"action": "complete", "message": ""}],
                      [{"action": "complete", "message": "ok"}] * 2]:
            with patch("hermes_cli.middleware._has_middleware", return_value=True), \
                 patch("hermes_cli.middleware._invoke_middleware", return_value=votes):
                self.assertIsNone(resolve_tool_batch_completion(messages=[], task_id="t", turn_id="u"))

    def test_contract_no_plugins_preserves_normal_loop(self):
        from hermes_cli.middleware import resolve_tool_batch_completion
        with patch("hermes_cli.middleware._has_middleware", return_value=False):
            self.assertIsNone(resolve_tool_batch_completion(messages=[], task_id="t", turn_id="u"))


if __name__ == "__main__":
    unittest.main()
