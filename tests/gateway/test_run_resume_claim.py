"""One durable authority admits both ordinary and resumed API runs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore


def test_competing_resume_claims_have_one_child_and_replay_survives(tmp_path):
    stores = [RunIdempotencyStore(str(tmp_path / 'runs.db')) for _ in range(2)]
    try:
        parent = {'status': 'paused', 'continuation': {'checkpointSha256': 'checkpoint'}}
        stores[0].reserve('owner', 'parent-key', 'parent-fp', 'parent', parent)
        barrier = Barrier(2)

        def claim(index):
            barrier.wait(timeout=5)
            return stores[index].reserve(
                'owner', f'key-{index}', f'fp-{index}', f'child-{index}', {'status': 'queued'},
                resume_claim={'run_id': 'parent', 'checkpoint_sha256': 'checkpoint'})

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, range(2)))
        assert sorted(outcome[0] for outcome in outcomes) == ['created', 'resume_conflict']
        winner = next(i for i, outcome in enumerate(outcomes) if outcome[0] == 'created')
        assert stores[0].reserve(
            'owner', f'key-{winner}', f'fp-{winner}', 'ignored', {'status': 'queued'},
            resume_claim={'run_id': 'parent', 'checkpoint_sha256': 'checkpoint'}
        )[0] == 'reused'
        saved = stores[0].status_for_run('owner', 'parent')['status']
        assert saved['resumed_by'] == f'child-{winner}'
        assert stores[1].reserve(
            'other-owner', 'other-key', 'other-fp', 'other-child', {'status': 'queued'},
            resume_claim={'run_id': 'parent', 'checkpoint_sha256': 'checkpoint'}
        )[0] == 'resume_conflict'
    finally:
        for store in stores:
            store.close()


def test_stale_parent_status_cannot_erase_a_durable_resume_claim(tmp_path):
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    try:
        parent = {'status': 'paused', 'continuation': {'checkpointSha256': 'checkpoint'}}
        store.reserve('owner', 'parent-key', 'parent-fp', 'parent', parent)
        claim = {'run_id': 'parent', 'checkpoint_sha256': 'checkpoint'}
        assert store.reserve('owner', 'child-key', 'child-fp', 'child', {'status': 'queued'},
                             resume_claim=claim)[0] == 'created'
        store.update_status('parent', {**parent, 'shutdown_requested_at': 1})
        assert store.status_for_run('owner', 'parent')['status']['resumed_by'] == 'child'
        assert store.reserve('owner', 'other-key', 'other-fp', 'other', {'status': 'queued'},
                             resume_claim=claim)[0] == 'resume_conflict'
        assert 'resumed_by' not in parent
    finally:
        store.close()
