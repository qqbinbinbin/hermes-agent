"""Durable run metadata reaches Relay without granting routing or auth authority."""
import asyncio
from unittest.mock import MagicMock, patch
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.asyncio
async def test_run_passes_opaque_metadata_into_conversation():
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    app = web.Application()
    app.router.add_post("/v1/runs", adapter._handle_runs)
    captured = {}
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()
    agent = MagicMock()
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    def run(**kwargs):
        captured.update(kwargs)
        loop.call_soon_threadsafe(finished.set)
        return {"final_response": "synthetic"}
    agent.run_conversation.side_effect = run
    metadata = {"correlation": {"trace": "synthetic-trace"}}
    async with TestClient(TestServer(app)) as client:
        with patch.object(adapter, "_create_agent", return_value=agent):
            response = await client.post("/v1/runs", json={"input": "synthetic", "metadata": metadata})
            assert response.status == 202
            await asyncio.wait_for(finished.wait(), 5)
            assert captured.get("relay_metadata") == metadata
    await adapter.disconnect()
