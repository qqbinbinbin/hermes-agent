"""Native yield is a durable transport checkpoint, never semantic completion."""
import copy
import unittest

from gateway.platforms.native_yield import build_yield_checkpoint, verify_yield_resume


def fixture():
    history = [{"role": "user", "content": "整理合成来源"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "call-1", "type": "function", "function": {
                       "name": "write_file", "arguments": '{"path":"note.md"}'}}]},
               {"role": "tool", "tool_call_id": "call-1", "content": '{"success":true}'}]
    result = {"yielded": True, "completed": False, "failed": False, "interrupted": False,
              "turn_exit_reason": "iteration_limit_yield", "api_calls": 2,
              "session_id": "session-tip", "messages": copy.deepcopy(history)}
    options = {"root_session_id": "session-root", "session_id": "session-tip",
               "history": history, "deadline_at": 2000, "now": 1000}
    return result, options


class NativeYieldTest(unittest.TestCase):
    def test_verified_yield_retains_only_content_free_checkpoint(self):
        result, options = fixture()
        checkpoint = build_yield_checkpoint(result, **options)
        self.assertEqual(checkpoint["messageCount"], 3)
        self.assertEqual(checkpoint["apiCalls"], 2)
        self.assertNotIn("整理", str(checkpoint))
        self.assertEqual(len(checkpoint["historySha256"]), 64)
        self.assertEqual(len(checkpoint["checkpointSha256"]), 64)
        verify_yield_resume({"status": "paused", "continuation": checkpoint}, **options)

    def test_history_loss_and_content_changes_cannot_yield(self):
        result, options = fixture()
        for history in [options["history"][:-1], [], [*options["history"],
                {"role": "assistant", "content": "unacknowledged"}]]:
            with self.subTest(history=history), self.assertRaisesRegex(ValueError, "yield_(history|tools)"):
                build_yield_checkpoint(result, **{**options, "history": history})

    def test_pending_tools_and_duplicate_results_cannot_yield(self):
        for kind in ("pending", "duplicate", "foreign"):
            result, options = fixture()
            if kind == "pending":
                options["history"].pop()
            elif kind == "duplicate":
                options["history"].append(copy.deepcopy(options["history"][-1]))
            else:
                options["history"][-1]["tool_call_id"] = "other"
            result["messages"] = copy.deepcopy(options["history"])
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "yield_tools"):
                build_yield_checkpoint(result, **options)

    def test_failure_cleanup_cancel_and_fabricated_stop_reason_are_not_yield(self):
        result, options = fixture()
        for patch in [{"failed": True}, {"interrupted": True}, {"completed": True},
                      {"cleanup_errors": ["persist_session"]}, {"yielded": False},
                      {"turn_exit_reason": "text_response"}, {"api_calls": True},
                      {"session_id": "foreign"}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                build_yield_checkpoint({**result, **patch}, **options)

    def test_resume_preserves_identity_deadline_tip_and_history(self):
        result, options = fixture()
        parent = {"status": "paused", "continuation": build_yield_checkpoint(result, **options)}
        for patch in [{"root_session_id": "foreign"}, {"session_id": "foreign"},
                      {"deadline_at": 2001}, {"now": 2000}, {"history": []}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                verify_yield_resume(parent, **{**options, **patch})

    def test_unknown_failed_or_completed_parent_cannot_resume(self):
        result, options = fixture()
        checkpoint = build_yield_checkpoint(result, **options)
        for state in ("unknown", "failed", "cancelled", "completed", "running"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                verify_yield_resume({"status": state, "continuation": checkpoint}, **options)

    def test_checkpoint_metadata_tamper_is_rejected(self):
        result, options = fixture()
        checkpoint = build_yield_checkpoint(result, **options)
        checkpoint["apiCalls"] += 1
        with self.assertRaises(ValueError):
            verify_yield_resume({"status": "paused", "continuation": checkpoint}, **options)

    def test_metadata_only_differences_do_not_fork_the_history(self):
        result, options = fixture()
        options["history"][0]["timestamp"] = 99
        options["history"][0]["_db_persisted"] = True
        self.assertEqual(build_yield_checkpoint(result, **options)["messageCount"], 3)


if __name__ == "__main__":
    unittest.main()
