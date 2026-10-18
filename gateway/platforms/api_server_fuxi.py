"""FUXI feedback intake and opt-in owning-profile goal runtime."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from typing import Any

from gateway.platforms._shared import get_scoped_secret
from hermes_constants import get_hermes_home

logger = logging.getLogger('gateway.platforms.api_server')


class FuxiRuntimeMixin:
    def _fuxi_capability_features(self):
        from hermes_cli import middleware
        from gateway.platforms.api_server import _durable_yield_available

        return {
            'run_persistent_idempotency': True, 'run_key_lookup': True,
            'run_absolute_deadline': True, 'run_session_continuity': True,
            'run_durable_iteration_yield': _durable_yield_available(),
            'tool_execution_request_budget': getattr(middleware, 'TOOL_EXECUTION_REQUEST_BUDGET', False) is True}

    def _initialize_fuxi_runtime(self, extra):
        self._profile_goal_runtime_home = get_hermes_home()
        enabled = extra.get('goal_runtime_enabled')
        if enabled is None:
            enabled = get_scoped_secret('HERMES_PROFILE_GOAL_RUNTIME_ENABLED', '')
        self._profile_goal_runtime_enabled = str(enabled).strip().lower() in {'1', 'true', 'yes', 'on'}
        interval = extra.get('goal_runtime_interval_seconds')
        if interval is None:
            interval = get_scoped_secret('HERMES_PROFILE_GOAL_RUNTIME_INTERVAL_SECONDS', '60')
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = 60
        self._profile_goal_runtime_interval_seconds = max(5, interval)
        self._profile_goal_runtime_task = None
        self._profile_goal_runtime_last_tick_at = None
        self._profile_goal_runtime_last_tick_count = None
        self._profile_goal_runtime_last_sync = dict.fromkeys(('loaded', 'created', 'updated', 'skipped'), 0)
        self._profile_goal_runtime_last_error = None

    @staticmethod
    def _active_profile_info():
        home = get_hermes_home()
        return {'name': home.name if home.parent.name == 'profiles' else 'default', 'home': str(home)}

    async def _handle_runtime_liveness(self, request):
        from aiohttp import web
        task = self._profile_goal_runtime_task
        running = bool(task and not task.done())
        degraded = (self._profile_goal_runtime_enabled and not running) or self._profile_goal_runtime_last_error
        return web.json_response({
            'status': 'degraded' if degraded else 'ok', 'runtime': 'api_server',
            'platform': 'hermes-agent', 'profile': self._active_profile_info(), 'pid': os.getpid(),
            'goal_loop': {
                'enabled': self._profile_goal_runtime_enabled, 'running': running,
                'interval_seconds': self._profile_goal_runtime_interval_seconds,
                'last_tick_at': self._profile_goal_runtime_last_tick_at,
                'last_tick_count': self._profile_goal_runtime_last_tick_count,
                'last_sync': self._profile_goal_runtime_last_sync,
                'last_error': self._profile_goal_runtime_last_error}})

    async def _handle_fuxi_feedback_ingest(self, request):
        from aiohttp import web
        from gateway.platforms.fuxi_feedback_ingest import append_feedback_experience, verify_hmac_headers

        try:
            body = await request.read()
        except Exception:
            logger.exception('FUXI feedback body read failed')
            return web.json_response({'ok': False, 'error': 'invalid_body'}, status=400)
        ok, reason = verify_hmac_headers(body, request.headers, secret=get_scoped_secret('HERMES_INGEST_HMAC_KEY', ''))
        if not ok:
            return web.json_response({'ok': False, 'error': reason}, status=500 if reason == 'missing_secret' else 401)
        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return web.json_response({'ok': False, 'error': 'invalid_json'}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({'ok': False, 'error': 'invalid_payload'}, status=400)
        try:
            append_feedback_experience(payload)
        except ValueError as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)
        except Exception:
            logger.exception('FUXI feedback ingest failed')
            return web.json_response({'ok': False, 'error': 'persist_failed'}, status=500)
        return web.json_response({'ok': True, 'recorded': True}, status=202)

    async def _restore_api_session_history(self, session_id):
        """Restore the durable compression tip; an unreadable store cannot start a new turn."""
        db = await self._ensure_session_db_async()
        if db is None:
            raise RuntimeError('session_store_unavailable')
        session_id = await asyncio.to_thread(db.get_compression_tip, session_id)
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError('session_continuation_invalid')
        history = await asyncio.to_thread(db.get_messages_as_conversation, session_id)
        return session_id, history

    async def _run_profile_goal_runtime_loop(self):
        from cron.profile_goal_runtime import sync_profile_goal_cron_specs
        from cron.scheduler import tick
        from gateway.run import _profile_runtime_scope

        while True:
            try:
                with _profile_runtime_scope(self._profile_goal_runtime_home):
                    self._profile_goal_runtime_last_sync = sync_profile_goal_cron_specs()
                    count = await asyncio.to_thread(tick, verbose=False)
                self._profile_goal_runtime_last_tick_at = time.time()
                self._profile_goal_runtime_last_tick_count = count
                self._profile_goal_runtime_last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._profile_goal_runtime_last_error = str(exc)
                logger.exception('[%s] profile goal runtime tick failed', self.name)
            await asyncio.sleep(self._profile_goal_runtime_interval_seconds)

    def _start_profile_goal_runtime(self):
        if self._profile_goal_runtime_enabled:
            self._profile_goal_runtime_task = asyncio.create_task(self._run_profile_goal_runtime_loop())
            self._track_background_task(self._profile_goal_runtime_task)

    async def _stop_profile_goal_runtime(self):
        task = getattr(self, '_profile_goal_runtime_task', None)
        if task is not None:
            task.cancel()
            if isinstance(task, asyncio.Task):
                with suppress(asyncio.CancelledError):
                    await task
            self._profile_goal_runtime_task = None

    async def _conversation_history_for_session(self, session_id: str) -> list[dict[str, Any]]:
        db = await self._ensure_session_db_async()
        if db is None:
            return []
        try:
            return await asyncio.to_thread(db.get_messages_as_conversation, session_id)
        except Exception:
            logger.exception("Failed to load session history for %s", session_id)
            return []

    async def _handle_health(self, request):
        """GET /health — simple health check."""
        from aiohttp import web
        from gateway.platforms.api_server import _hermes_version
        return web.json_response({"status": "ok", "platform": "hermes-agent", "version": _hermes_version()})
