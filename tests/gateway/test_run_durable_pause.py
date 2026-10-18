"""Paused run acceptance requires a durable checkpoint and scoped recovery."""
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _api_request_profile
from gateway.platforms.api_server_runs import _http_routes


def test_pause_persistence_failure_does_not_publish_a_checkpoint():
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "synthetic"}))
    request = MagicMock(headers={})
    store = adapter._run_idempotency_store
    run_id = "run_durable_pause"
    initial = {"status": "running", "run_id": run_id}
    scope = adapter._run_idempotency_scope(request)
    try:
        store.reserve(scope, "key", "fingerprint", run_id, initial)
        adapter._run_idempotency_ids.add(run_id)
        adapter._run_statuses[run_id] = dict(initial)
        with patch.object(store, "update_status", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                adapter._set_run_status(run_id, "paused", continuation={"checkpointSha256": "sha"})
        assert adapter._run_statuses[run_id] == initial
        assert store.status_for_run(scope, run_id)["status"] == initial
    finally:
        store.close()


@pytest.mark.asyncio
async def test_lookup_recovers_paused_run_after_restart_and_enforces_scope():
    config = PlatformConfig(enabled=True, extra={"key": "synthetic"})
    first = APIServerAdapter(config)
    request = MagicMock(headers={})
    scope = first._run_idempotency_scope(request)
    run_id = "run_paused_before_restart"
    status = {"run_id": run_id, "status": "paused",
              "continuation": {"checkpointSha256": "checkpoint"}}
    first._run_idempotency_store.reserve(scope, "accepted-key", "fingerprint", run_id, status,
                                         owner_pid=0, owner_started=0)
    await first.disconnect()
    adapter = APIServerAdapter(config)

    @web.middleware
    async def profile_scope(request, handler):
        token = _api_request_profile.set(request.headers.get("X-Test-Profile"))
        try:
            return await handler(request)
        finally:
            _api_request_profile.reset(token)

    app = web.Application(middlewares=[profile_scope])
    for method, path, handler in _http_routes(adapter):
        app.router.add_route(method, path, handler)
    headers = {"Authorization": "Bearer synthetic", "Idempotency-Key": "accepted-key"}
    try:
        with patch.object(adapter, "_create_agent", side_effect=AssertionError("lookup cannot run a model")), \
             patch.object(adapter, "_expected_api_key", return_value="synthetic"):
            async with TestClient(TestServer(app)) as client:
                recovered = await client.get("/v1/runs/lookup", headers=headers)
                assert recovered.status == 200, await recovered.text()
                assert await recovered.json() == status
                again = await client.get("/v1/runs/" + run_id, headers=headers)
                assert again.status == 200
                assert (await again.json())["status"] == "paused"
                other = await client.get("/v1/runs/lookup", headers={**headers, "X-Test-Profile": "other"})
                assert other.status == 404
                unauthorized = await client.get("/v1/runs/lookup", headers={"Idempotency-Key": "accepted-key"})
                assert unauthorized.status == 401
                missing = await client.get("/v1/runs/lookup", headers={"Authorization": "Bearer synthetic"})
                assert missing.status == 400
        assert adapter._run_idempotency_store.lookup_key(scope, "missing") is None
    finally:
        await adapter.disconnect()
