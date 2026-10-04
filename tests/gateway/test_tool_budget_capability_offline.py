"""Read the real API capability handler without a server, provider or network."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gateway.platforms.api_server import APIServerAdapter


class ToolBudgetCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_follows_the_loaded_native_middleware(self):
        adapter = SimpleNamespace(_check_auth=lambda request: None,
                                  _model_name="synthetic", _api_key="", _cors_origins=[])
        for supported in (True, False, None):
            with patch("hermes_cli.middleware.TOOL_EXECUTION_REQUEST_BUDGET", supported):
                response = await APIServerAdapter._handle_capabilities(adapter, None)
            body = json.loads(response.body)
            self.assertIs(body["features"]["tool_execution_request_budget"], supported is True)


if __name__ == "__main__":
    unittest.main()
