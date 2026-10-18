"""Read the real API capability handler without a server, provider or network."""
import json
import unittest
from unittest.mock import patch

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


class ToolBudgetCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_follows_the_loaded_native_middleware(self):
        adapter = APIServerAdapter(PlatformConfig(enabled=True))
        self.addAsyncCleanup(adapter.disconnect)
        for supported in (True, False, None):
            with patch("hermes_cli.middleware.TOOL_EXECUTION_REQUEST_BUDGET", supported):
                response = await APIServerAdapter._handle_capabilities(adapter, None)
            body = json.loads(response.body)
            self.assertIs(body["features"]["tool_execution_request_budget"], supported is True)
            self.assertTrue(body["features"]["run_key_lookup"])
            self.assertTrue(body["features"]["run_absolute_deadline"])
            self.assertTrue(body["features"]["run_session_continuity"])
            self.assertEqual(body["endpoints"]["run_lookup"]["path"], "/v1/runs/lookup")


if __name__ == "__main__":
    unittest.main()
