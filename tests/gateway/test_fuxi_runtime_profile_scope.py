"""Periodic work remains bound to its owning profile across task and thread hops."""
import asyncio
import json

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.run import _profile_runtime_scope
from hermes_constants import get_hermes_home


@pytest.mark.asyncio
async def test_goal_loops_keep_owning_profiles_in_worker_threads(tmp_path, monkeypatch):
    homes = [tmp_path / 'profiles' / name for name in ('employee-a', 'employee-b')]
    adapters = []
    seen = []
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    for home in homes:
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('model: synthetic\n')
        with _profile_runtime_scope(home):
            adapters.append(APIServerAdapter(PlatformConfig(enabled=True, extra={})))

    def sync():
        seen.append(('sync', get_hermes_home()))
        return {'loaded': 1, 'created': 0, 'updated': 0, 'skipped': 0}

    def tick(**kwargs):
        seen.append(('tick', get_hermes_home()))
        if len([row for row in seen if row[0] == 'tick']) == 2:
            loop.call_soon_threadsafe(ready.set)
        return 1

    monkeypatch.setattr('cron.profile_goal_runtime.sync_profile_goal_cron_specs', sync)
    monkeypatch.setattr('cron.scheduler.tick', tick)
    tasks = [asyncio.create_task(adapter._run_profile_goal_runtime_loop()) for adapter in adapters]
    try:
        await asyncio.wait_for(ready.wait(), 10)
        assert sorted(home for kind, home in seen if kind == 'sync') == homes
        assert sorted(home for kind, home in seen if kind == 'tick') == homes
        for adapter in adapters:
            assert adapter._profile_goal_runtime_last_error is None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for adapter in adapters:
            await adapter.disconnect()


@pytest.mark.asyncio
async def test_liveness_reports_each_selected_profile(tmp_path):
    for name in ('employee-a', 'employee-b'):
        home = tmp_path / 'profiles' / name
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('model: synthetic\n')
        with _profile_runtime_scope(home):
            adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
            try:
                response = await adapter._handle_runtime_liveness(None)
                assert json.loads(response.body)['profile'] == {'name': name, 'home': str(home)}
            finally:
                await adapter.disconnect()
