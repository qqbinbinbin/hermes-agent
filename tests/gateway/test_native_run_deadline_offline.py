"""Exercise the real API adapter with a synthetic agent and no provider calls."""
import asyncio
import json
import os
import threading
import time
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


class NativeDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_guard = patch("socket.socket.connect", side_effect=RuntimeError("offline_probe_network_forbidden"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    async def exercise(self, *, timeout=None, finish_after=None, stop=False, failed=False,
                       delayed_interrupt=False, check_replay=False):
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter

        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "synthetic"}))
        released = threading.Event()
        entered = threading.Event()
        agent = MagicMock()
        agent.session_prompt_tokens = 17
        agent.session_completion_tokens = 3
        agent.session_total_tokens = 20
        agent.interrupt.side_effect = (lambda *_: None) if delayed_interrupt else (lambda *_: released.set())

        def run(**_):
            entered.set()
            if not released.wait(130):
                raise AssertionError("synthetic release missing")
            return {"failed": True, "error": "synthetic"} if failed else {"final_response": "synthetic"}

        agent.run_conversation.side_effect = run
        req = MagicMock()
        req.headers = {"Authorization": "Bearer synthetic", "Idempotency-Key": uuid.uuid4().hex}
        body = {"input": "synthetic deadline test"}
        if timeout is not None:
            body["deadline_at"] = time.time() + timeout
        req.json = AsyncMock(return_value=body)
        started = time.monotonic()
        release_handle = None
        with patch.object(adapter, "_create_agent", return_value=agent):
            try:
                response = await adapter._handle_runs(req)
                self.assertEqual(response.status, 202)
                run_id = json.loads(response.body)["run_id"]
                task = adapter._active_run_tasks[run_id]
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                if check_replay:
                    replay = await adapter._handle_runs(req)
                    self.assertEqual(json.loads(replay.body)["run_id"], run_id)
                    req.json = AsyncMock(return_value={**body, "deadline_at": body["deadline_at"] + 60})
                    self.assertEqual((await adapter._handle_runs(req)).status, 409)
                if finish_after is not None:
                    release_handle = asyncio.get_running_loop().call_later(finish_after, released.set)
                if stop:
                    req.match_info = {"run_id": run_id}
                    self.assertEqual((await adapter._handle_stop_run(req)).status, 200)
                if delayed_interrupt:
                    for _ in range(100):
                        if agent.interrupt.called:
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(agent.interrupt.called)
                    self.assertEqual(adapter._run_statuses[run_id]["status"], "stopping")
                    self.assertFalse(task.done())
                    self.assertIn(run_id, adapter._active_run_agents)
                    released.set()
                # Deliberately no observer or SSE reader while the task runs.
                await asyncio.wait_for(asyncio.shield(task), min(125, (timeout or 3) + 2))
                status = adapter._run_statuses[run_id]
                self.assertEqual(status["usage"], {"input_tokens": 17, "output_tokens": 3, "total_tokens": 20})
                agent.run_conversation.assert_called_once()
                return status, agent, time.monotonic() - started
            finally:
                released.set()
                if release_handle:
                    release_handle.cancel()
                tasks = list(adapter._active_run_tasks.values())
                if tasks:
                    await asyncio.wait_for(asyncio.gather(*tasks), 5)

    async def test_deadline_interrupts_without_gateway_observer(self):
        status, agent, _ = await self.exercise(timeout=0.2)
        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(status["stop_reason"], "deadline_exceeded")
        agent.interrupt.assert_called_once()

    async def test_explicit_stop_retains_observed_usage(self):
        status, agent, _ = await self.exercise(timeout=20, stop=True)
        self.assertEqual(status["status"], "cancelled")
        agent.interrupt.assert_called_once()

    async def test_deadline_does_not_claim_thread_stopped_before_it_returns(self):
        status, _, _ = await self.exercise(timeout=0.2, delayed_interrupt=True)
        self.assertEqual(status["status"], "cancelled")

    async def test_same_key_cannot_extend_absolute_deadline(self):
        status, _, _ = await self.exercise(timeout=20, finish_after=0.01, check_replay=True)
        self.assertEqual(status["status"], "completed")

    async def test_finished_task_does_not_get_a_late_interrupt(self):
        status, agent, _ = await self.exercise(timeout=0.2, finish_after=0.01)
        await asyncio.sleep(0.25)
        self.assertEqual(status["status"], "completed")
        agent.interrupt.assert_not_called()

    async def test_failed_task_retains_observed_usage(self):
        status, _, _ = await self.exercise(finish_after=0.01, failed=True)
        self.assertEqual(status["status"], "failed")

    async def test_invalid_deadline_never_creates_agent(self):
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "synthetic"}))
        for value in [True, "tomorrow", float("nan"), float("inf"), time.time() - 1]:
            req = MagicMock()
            req.headers = {"Authorization": "Bearer synthetic"}
            req.json = AsyncMock(return_value={"input": "synthetic", "deadline_at": value})
            with patch.object(adapter, "_create_agent") as create:
                self.assertEqual((await adapter._handle_runs(req)).status, 400)
                create.assert_not_called()

    @unittest.skipUnless(os.environ.get("HERMES_NATIVE_LONG_PROBE") == "1", "explicit 110-second probe")
    async def test_actual_110_seconds_without_http_observation(self):
        status, agent, elapsed = await self.exercise(timeout=120, finish_after=110)
        self.assertGreaterEqual(elapsed, 110)
        self.assertEqual(status["status"], "completed")
        agent.interrupt.assert_not_called()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeDeadlineTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
