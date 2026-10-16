"""Real async API, agent loop, file tools and SQLite; provider is synthetic."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _durable_yield_available
from hermes_state import SessionDB
from run_agent import AIAgent
from tools.file_tools import WRITE_FILE_SCHEMA


def response(path=None, content=None):
    calls = [SimpleNamespace(id='call-' + path, type='function', function=SimpleNamespace(
        name='write_file', arguments=json.dumps({'path': path, 'content': content})))] if path else None
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='' if calls else '合成任务完成。', tool_calls=calls),
        finish_reason='tool_calls' if calls else 'stop')], model='synthetic', usage=None)


class NativeYieldAPITest(unittest.IsolatedAsyncioTestCase):
    def test_capability_requires_coherent_core_persistence_and_checkpoint_modules(self):
        for target in ('agent.turn_finalizer.DURABLE_ITERATION_YIELD',
                       'gateway.platforms.api_run_store.DURABLE_RESUME_CLAIM',
                       'gateway.platforms.native_yield.DURABLE_YIELD_CHECKPOINT'):
            for value in (False, None, 'true'):
                with self.subTest(target=target, value=value), patch(target, value):
                    self.assertFalse(_durable_yield_available())
        self.assertTrue(_durable_yield_available())

    async def test_invalid_or_unsupported_yield_is_rejected_before_agent_creation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'HERMES_HOME': directory}):
            adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'synthetic-key'}))
            self.addAsyncCleanup(adapter.disconnect)
            app = web.Application()
            app.router.add_post('/v1/runs', adapter._handle_runs)
            headers = {'Authorization': 'Bearer synthetic-key'}
            with patch.object(adapter, '_create_agent', side_effect=AssertionError('must not call model')) as create:
                async with TestClient(TestServer(app)) as client:
                    for body in ({'input': '合成', 'iteration_limit_action': 'invalid'},
                                 {'input': '合成', 'iteration_limit_action': 'yield'},
                                 {'input': '合成', 'resume_from_run_id': 'run_bad'}):
                        response = await client.post('/v1/runs', json=body, headers=headers)
                        self.assertEqual(response.status, 400, await response.text())
                    with patch('agent.turn_finalizer.DURABLE_ITERATION_YIELD', False):
                        response = await client.post('/v1/runs', headers={**headers,
                            'X-Hermes-Session-Id': 'synthetic', 'Idempotency-Key': 'synthetic'},
                            json={'input': '合成', 'iteration_limit_action': 'yield', 'deadline_at': time.time() + 60})
                        self.assertEqual(response.status, 503)
                    self.assertEqual(create.call_count, 0)

    async def test_yield_restore_and_idempotency_use_the_actual_native_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'HERMES_HOME': directory, 'HERMES_WRITE_SAFE_ROOT': directory}):
                db = SessionDB(root / 'state.db')
                self.addCleanup(db.close)
                adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'synthetic-key'}))
                self.addAsyncCleanup(adapter.disconnect)
                adapter._session_db = db
                app = web.Application()
                app.router.add_post('/v1/runs', adapter._handle_runs)
                app.router.add_get('/v1/runs/{run_id}', adapter._handle_get_run)
                requests = []
                agents = []

                def provider(**kwargs):
                    requests.append(kwargs['messages'])
                    if len(requests) == 1:
                        return response(str(root / 'first.md'), '# 合成成果一\n真实工具保存。')
                    if len(requests) == 2:
                        self.assertTrue(any(message.get('role') == 'tool'
                            and json.loads(message['content']).get('resolved_path') == str(root / 'first.md')
                            for message in kwargs['messages']))
                        return response(str(root / 'second.md'), '# 合成成果二\n恢复原生历史后完成。')
                    if len(requests) == 3:
                        last_tool = next(message for message in reversed(kwargs['messages'])
                                         if message.get('role') == 'tool')
                        self.assertNotIn('"error"', last_tool.get('content', ''), last_tool)
                        self.assertTrue((root / 'second.md').is_file(), last_tool)
                        return response()
                    raise AssertionError('native work or summary was replayed')

                def create_agent(**kwargs):
                    with patch('run_agent.get_tool_definitions', return_value=[
                            {'type': 'function', 'function': WRITE_FILE_SCHEMA}]), \
                         patch('run_agent.check_toolset_requirements', return_value={}), patch('run_agent.OpenAI'):
                        agent = AIAgent(api_key='synthetic', base_url='https://example.invalid/v1/',
                            session_id=kwargs['session_id'], session_db=db, enabled_toolsets=['file'],
                            quiet_mode=True, skip_context_files=True, skip_memory=True)
                    agent._cached_system_prompt = '仅整理合成材料，使用已授权文件。'
                    agent._use_prompt_caching = False
                    agent.compression_enabled = False
                    agent.save_trajectories = False
                    agent.max_iterations = 1 if not agents else 2
                    agent.valid_tool_names = {'write_file'}
                    agent.client = MagicMock()
                    agent.client.chat.completions.create.side_effect = provider
                    agent._handle_max_iterations = MagicMock(side_effect=AssertionError('extra summary request'))
                    agents.append(agent)
                    return agent

                async def terminal(client, run_id):
                    for _ in range(200):
                        value = await (await client.get('/v1/runs/' + run_id,
                            headers={'Authorization': 'Bearer synthetic-key'})).json()
                        if value['status'] in ('paused', 'completed', 'failed', 'cancelled'):
                            return value
                        await asyncio.sleep(.01)
                    self.fail('synthetic native run did not terminate')

                headers = {'Authorization': 'Bearer synthetic-key', 'X-Hermes-Session-Id': 'synthetic-root',
                           'Idempotency-Key': 'synthetic-first'}
                body = {'input': '整理合成来源。', 'iteration_limit_action': 'yield',
                        'deadline_at': time.time() + 300}
                with patch.object(adapter, '_create_agent', side_effect=create_agent):
                    async with TestClient(TestServer(app)) as client:
                        accepted = await client.post('/v1/runs', json=body, headers=headers)
                        self.assertEqual(accepted.status, 202, await accepted.text())
                        parent = await terminal(client, (await accepted.json())['run_id'])
                        self.assertEqual(parent['status'], 'paused', parent)
                        self.assertTrue((root / 'first.md').is_file())
                        before = (root / 'first.md').read_bytes()
                        self.assertEqual(len(requests), 1)
                        self.assertNotIn('summary', str(db.get_messages_as_conversation('synthetic-root')))
                        resume_body = {**body, 'input': '继续当前已授权任务。', 'resume_from_run_id': parent['run_id']}
                        resume_headers = {**headers, 'Idempotency-Key': 'synthetic-second'}
                        accepted = await client.post('/v1/runs', json=resume_body, headers=resume_headers)
                        self.assertEqual(accepted.status, 202, await accepted.text())
                        child_id = (await accepted.json())['run_id']
                        child = await terminal(client, child_id)
                        self.assertEqual(child['status'], 'completed', child)
                        self.assertEqual(len(requests), 3)
                        self.assertEqual((root / 'first.md').read_bytes(), before)
                        self.assertTrue((root / 'second.md').is_file())
                        replay = await client.post('/v1/runs', json=resume_body, headers=resume_headers)
                        self.assertEqual(replay.status, 202)
                        self.assertEqual((await replay.json())['run_id'], child_id)
                        conflict = await client.post('/v1/runs', json=resume_body,
                            headers={**headers, 'Idempotency-Key': 'synthetic-third'})
                        self.assertEqual(conflict.status, 409)
                        self.assertEqual(len(requests), 3)
                        self.assertTrue(all(agent._handle_max_iterations.call_count == 0 for agent in agents))
                for store in getattr(adapter, '_native_run_stores', {}).values():
                    store.close()


if __name__ == '__main__':
    unittest.main()
