"""Real HTTP and SQLite resume checks; the model boundary is never executed."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB


class NativeChatResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = SessionDB(db_path=Path(self.directory.name) / "state.db")
        self.adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "synthetic-only"}))
        self.adapter._session_db = self.db
        self.runner = AsyncMock(return_value=(
            {"final_response": "synthetic", "messages": [], "api_calls": 0},
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        ))
        self.patch = patch.object(self.adapter, "_run_agent", self.runner)
        self.patch.start()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.adapter._handle_chat_completions)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        self.db.close()
        self.directory.cleanup()

    async def request(self, session_id="original", *, authenticated=True):
        headers = {"X-Hermes-Session-Id": session_id} if session_id else {}
        if authenticated:
            headers["Authorization"] = "Bearer synthetic-only"
        return await self.client.post("/v1/chat/completions", headers=headers, json={
            "model": "hermes-agent", "messages": [
                {"role": "user", "content": "Continue the authorized synthetic task"},
            ],
        })

    def original(self):
        self.db.create_session("original", "api")
        self.db.append_message("original", "user", "old transcript")
        self.db.append_message("original", "assistant", "old progress")

    async def test_compressed_tip_restores_latest_history_after_database_reopen(self):
        self.original()
        self.db.publish_compression_child(parent_session_id="original", child_session_id="latest",
            source="api", messages=[{"role": "user", "content": "compacted progress"},
                                    {"role": "assistant", "content": "draft is preserved"}],
            require_compression_lease=False)
        self.db.create_session("newer-branch", "api", parent_session_id="original",
                               model_config={"_branched_from": "original"})
        self.db.append_message("newer-branch", "user", "not a compression continuation")
        self.db.close()
        self.db = SessionDB(db_path=Path(self.directory.name) / "state.db")
        self.adapter._session_db = self.db
        response = await self.request()
        self.assertEqual(response.status, 200, await response.text())
        call = self.runner.call_args.kwargs
        self.assertEqual(call["session_id"], "latest")
        self.assertEqual(call["conversation_history"][-1]["content"], "draft is preserved")
        self.assertEqual(response.headers["X-Hermes-Session-Id"], "latest")
        self.assertEqual(self.runner.await_count, 1)

    async def test_branch_and_other_session_do_not_replace_original(self):
        self.original()
        self.db.create_session("branch", "api", parent_session_id="original",
                               model_config={"_branched_from": "original"})
        self.db.append_message("branch", "user", "branch-only content")
        self.db.create_session("other", "api")
        self.db.append_message("other", "user", "other-only content")
        response = await self.request()
        self.assertEqual(response.status, 200)
        self.assertEqual(self.runner.call_args.kwargs["session_id"], "original")
        history = self.runner.call_args.kwargs["conversation_history"]
        self.assertEqual(history[-1]["content"], "old progress")

    async def test_history_read_failure_never_starts_a_blank_model_turn(self):
        self.original()
        with patch.object(self.db, "get_messages_as_conversation", side_effect=RuntimeError("private-detail")):
            response = await self.request()
        self.assertEqual(response.status, 503, await response.text())
        self.assertNotIn("private-detail", await response.text())
        self.runner.assert_not_awaited()

    async def test_unavailable_database_never_starts_a_model_turn(self):
        with patch.object(self.adapter, "_ensure_session_db_async", AsyncMock(return_value=None)):
            response = await self.request()
        self.assertEqual(response.status, 503, await response.text())
        self.runner.assert_not_awaited()

    async def test_compression_resolution_failure_never_uses_old_history(self):
        self.original()
        with patch.object(self.db, "get_compression_tip", side_effect=RuntimeError("broken lineage")):
            response = await self.request()
        self.assertEqual(response.status, 503, await response.text())
        self.runner.assert_not_awaited()

    async def test_new_authenticated_session_is_still_allowed(self):
        response = await self.request("new-session")
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.runner.call_args.kwargs["conversation_history"], [])

    async def test_stateless_request_does_not_require_session_history(self):
        with patch.object(self.adapter, "_ensure_session_db_async", AsyncMock(return_value=None)):
            response = await self.request(None)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.runner.await_count, 1)

    async def test_provider_usage_deltas_survive_two_turns_and_database_reopen(self):
        self.original()

        async def synthetic_provider_turn(**kwargs):
            # Two provider requests in ONE Hermes turn, using the native
            # accounting API. These are synthetic counts, not billing evidence.
            for input_tokens, output_tokens in [(100, 10), (150, 20)]:
                self.db.update_token_counts(kwargs["session_id"], input_tokens=input_tokens,
                    output_tokens=output_tokens, api_call_count=1, model="synthetic")
            return ({"final_response": "synthetic", "messages": [], "api_calls": 2},
                    {"input_tokens": 250, "output_tokens": 30, "total_tokens": 280})

        self.runner.side_effect = synthetic_provider_turn
        self.assertEqual((await self.request()).status, 200)
        self.db.close()
        self.db = SessionDB(db_path=Path(self.directory.name) / "state.db")
        self.adapter._session_db = self.db
        self.assertEqual((await self.request()).status, 200)
        row = self.db.get_session("original")
        self.assertEqual(self.runner.await_count, 2)
        self.assertEqual(row["api_call_count"], 4)
        self.assertEqual(row["input_tokens"], 500)
        self.assertEqual(row["output_tokens"], 60)

    async def test_unauthenticated_resume_is_rejected_before_history_read(self):
        with patch.object(self.db, "get_messages_as_conversation") as read:
            response = await self.request(authenticated=False)
        self.assertEqual(response.status, 401)
        read.assert_not_called()
        self.runner.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
