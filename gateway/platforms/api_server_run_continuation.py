"""FUXI native continuation contracts layered on the upstream run authority."""
import asyncio
import math
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger('gateway.platforms.api_server')


def parse_options(owner, request, body, *, api_server):
    try:
        return request_options(owner, request, body, capability=api_server._durable_yield_available), None
    except (ValueError, PermissionError, RuntimeError) as exc:
        from gateway.platforms.api_server_room_grants import _json_error
        statuses = {RuntimeError: 503, PermissionError: 403, ValueError: 400}
        return None, _json_error(api_server._openai_error, str(exc), status=statuses[type(exc)])


async def restore_for_request(owner, options, scope, *, api_server):
    from gateway.platforms.api_server_room_grants import _json_error
    try:
        tip, history = await restore_continuation(owner, options, scope)
        return tip, history, None
    except ValueError as exc:
        return None, None, _json_error(api_server._openai_error, str(exc),
                                       status=409 if options['resume_run_id'] else 400)
    except Exception:
        logger.exception('Native continuation restoration failed')
        return None, None, _json_error(api_server._openai_error, 'session_history_unavailable', status=503)


async def select_history(owner, session_id, gateway_session_key, history, previous_response_id, restored_id):
    from gateway.platforms.api_server_runs import _resolve_live_session_id
    declared = not session_id and bool(gateway_session_key)
    selected = session_id or (await asyncio.to_thread(owner._declared_conversation_session, gateway_session_key)
                              if declared else None)
    if selected and not restored_id:
        selected = await _resolve_live_session_id(owner, str(selected))
    delivery = bool(restored_id) or (not previous_response_id and not history)
    if not history and selected and not previous_response_id and not restored_id:
        history = await owner._conversation_history_for_session(str(selected))
    return selected, history, declared, delivery


def restored_selection(session_id, history, restored_id, restored_history):
    return (restored_id, restored_history) if restored_id else (session_id, history)


def reserve_run(owner, request, *, key, scope, fingerprint, run_id, status, options, gateway_session_key, api_server):
    from gateway.platforms.api_server_room_grants import _json_error
    from gateway.platforms.api_server_runs import _forget_run, _replay_or_conflict, _room_retention_until
    outcome, record = owner._run_idempotency_store.reserve(
        scope, key, fingerprint, run_id, status, owner_pid=owner._run_owner_pid,
        owner_started=owner._run_owner_started, retention_until=_room_retention_until(request),
        resume_claim=options['resume_claim'])
    if outcome == 'created':
        owner._run_idempotency_ids.add(run_id)
        return None
    _forget_run(owner, run_id, owner._run_streams, owner._run_streams_created,
                owner._run_approval_sessions, owner._run_statuses, owner._run_owners)
    if outcome == 'resume_conflict':
        return _json_error(api_server._openai_error, 'run_resume_already_claimed', status=409)
    return _replay_or_conflict(owner, request, outcome, record,
                               gateway_session_key, api_server._openai_error)


def request_options(owner, request, body, *, capability):
    if not isinstance(body, dict):
        raise ValueError('Request body must be an object')
    deadline = body.get('deadline_at')
    if deadline is not None and (type(deadline) not in (int, float)
            or not math.isfinite(deadline) or deadline <= time.time()):
        raise ValueError('Invalid or expired deadline_at')
    action = body.get('iteration_limit_action', 'summary')
    if action not in ('summary', 'yield'):
        raise ValueError('Invalid iteration_limit_action')
    continuity = request.headers.get('X-Hermes-Session-Id', '')
    if continuity:
        from gateway.session import _is_path_unsafe
        if not owner._expected_api_key():
            raise PermissionError('Session continuation requires API key authentication')
        if (not isinstance(continuity, str) or len(continuity) > owner._MAX_SESSION_HEADER_LEN
                or any(ord(c) < 32 or ord(c) == 127 for c in continuity)
                or _is_path_unsafe(continuity) or body.get('conversation_history')
                or body.get('previous_response_id')
                or (body.get('session_id') and body['session_id'] != continuity)
                or (isinstance(body.get('input'), list) and len(body['input']) > 1)):
            raise ValueError('Invalid session continuation')
    resume_id = body.get('resume_from_run_id')
    if resume_id is not None and (action != 'yield' or not isinstance(resume_id, str)
            or not re.fullmatch(r'run_[a-f0-9]{32}', resume_id)):
        raise ValueError('Invalid resume_from_run_id')
    if action == 'yield':
        if not capability() or not owner._run_idempotency_store.durable:
            raise RuntimeError('run_yield_capability_unavailable')
        if (not owner._expected_api_key() or owner._room_grant_token(request)
                or deadline is None or not continuity or not request.headers.get('Idempotency-Key', '').strip()):
            raise ValueError('Durable yield requires authenticated session, key and deadline')
    return dict(deadline_at=deadline, iteration_action=action, root_session_id=continuity,
                resume_run_id=resume_id, resume_claim=None)

async def restore_continuation(owner, options, scope):
    """Validate persisted history before claiming a parent or creating an agent."""
    root = options['root_session_id']
    if not root:
        return None, None
    tip, history = await owner._restore_api_session_history(root)
    if options['resume_run_id']:
        from gateway.platforms.native_yield import verify_yield_resume
        record = owner._run_idempotency_store.status_for_run(scope, options['resume_run_id'])
        checkpoint = verify_yield_resume(
            record['status'] if record else None, root_session_id=root, session_id=tip, history=history,
            deadline_at=options['deadline_at'], now=time.time())
        options['resume_claim'] = dict(run_id=options['resume_run_id'],
                                      checkpoint_sha256=checkpoint['checkpointSha256'])
    if options['iteration_action'] == 'yield':
        # A live Desktop owns its own native loop and cannot honor this run's yield contract.
        db = await owner._ensure_session_db_async()
        from tools.bot_live_delivery import find_canonical_live_owner
        live = await asyncio.to_thread(find_canonical_live_owner, Path(db.db_path).parent)
        if live and live['session_id'] == tip:
            raise ValueError('run_yield_live_owner_unsupported')
    return tip, history

def arm_deadline(owner, run, *, interrupt):
    if run.deadline_at is None:
        return None

    def expired():
        if run.run_id in owner._stopping_run_ids:
            return
        owner._stopping_run_ids.add(run.run_id)
        owner._set_run_status(run.run_id, 'stopping', stop_reason='deadline_exceeded')
        agent = owner._active_run_agents.get(run.run_id)
        if agent is not None:
            interrupt(agent, 'Run absolute deadline exceeded')
    return asyncio.get_running_loop().call_later(max(0, run.deadline_at - time.time()), expired)

async def yielded_fields(owner, run, result):
    from gateway.platforms.native_yield import build_yield_checkpoint
    if run.iteration_action != 'yield':
        raise ValueError('run_unexpected_yield')
    with owner._profile_scope(run.request_profile):
        tip, history = await owner._restore_api_session_history(result.get('session_id'))
    checkpoint = build_yield_checkpoint(
        result, root_session_id=run.root_session_id, session_id=tip, history=history,
        deadline_at=run.deadline_at, now=time.time())
    return dict(continuation=checkpoint, completed=False, partial=False, interrupted=False)
