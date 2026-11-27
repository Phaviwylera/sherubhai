"""Studio checkpoint recovery and task ownership."""
import hashlib
import json
import time

import pytest


@pytest.fixture(scope='module')
def studio():
    import studio
    return studio


def write_clip(studio, name, data):
    path = studio.project_dir(studio.PID) / 'runs' / 'test' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.relative_to(studio.project_dir(studio.PID)).as_posix()


def test_checkpoint_is_reused_when_the_file_is_intact(studio):
    clip = write_clip(studio, 'intact.mp4', b'reviewed')
    output = {'shots': [{'index': 0, 'clip_sha256': hashlib.sha256(b'reviewed').hexdigest(),
                         'attempts': [{'clip': clip, 'report': {'status': 'pass'}}]}]}
    kept, rejected = studio.checkpoints(output, completed=True)
    assert list(kept) == [0]
    assert rejected == set()


def test_missing_checkpoint_is_rejected_instead_of_blocking_the_run(studio):
    output = {'shots': [{'index': 3, 'attempts': [{'clip': 'runs/test/deleted.mp4', 'report': {'status': 'fail'}}]}]}
    kept, rejected = studio.checkpoints(output)
    assert kept == {}
    assert rejected == {3}


def test_modified_checkpoint_is_not_reused_with_its_old_review(studio):
    clip = write_clip(studio, 'modified.mp4', b'reviewed')
    output = {'shots': [{'index': 1, 'clip_sha256': hashlib.sha256(b'reviewed').hexdigest(),
                         'attempts': [{'clip': clip, 'report': {'status': 'pass'}}]}]}
    (studio.project_dir(studio.PID) / clip).write_bytes(b'changed afterwards')
    kept, rejected = studio.checkpoints(output, completed=True)
    assert kept == {}
    assert rejected == {1}


def test_newest_clip_skips_a_trailing_error_record(studio):
    entry = {'index': 0, 'attempts': [{'clip': 'runs/test/good.mp4', 'report': {'status': 'pass'}}, {'error': 'provider stopped'}]}
    assert studio.newest_clip(entry)['clip'] == 'runs/test/good.mp4'
    assert studio.newest_clip({'index': 1, 'attempts': [{'error': 'stopped'}]}) is None


def test_recoverable_honours_the_selected_shot(studio):
    first = write_clip(studio, 'first.mp4', b'one')
    second = write_clip(studio, 'second.mp4', b'two')
    output = {'shots': [
        {'index': 0, 'attempts': [{'clip': first, 'report': {'status': 'fail'}}, {'error': 'stopped'}]},
        {'index': 1, 'attempts': [{'clip': second, 'report': {'status': 'fail'}}]},
    ]}
    assert set(studio.recoverable(output)) == {0, 1}
    assert list(studio.recoverable(output, selected=1)) == [1]


def test_busy_episode_raises_its_own_error(studio, monkeypatch):
    with studio.connect() as connection:
        connection.execute("UPDATE runs SET state='done'")
        connection.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', ('busy' + '0' * 28, studio.PID, 1, 'render', 'running', '{}'))
    try:
        with pytest.raises(studio.Busy):
            studio.idle(studio.PID)
        assert issubclass(studio.Busy, ValueError)
    finally:
        with studio.connect() as connection:
            connection.execute("UPDATE runs SET state='done'")


def test_state_endpoint_reports_queue_and_provider_without_writing(studio, monkeypatch, tmp_path):
    import releases
    monkeypatch.setattr(releases, 'DB', tmp_path / 'releases.sqlite')
    client = studio.app.test_client()
    response = client.get('/api/state', base_url='http://127.0.0.1:%d' % studio.PORT)
    assert response.status_code == 200
    payload = response.get_json()
    for key in ('document', 'revision', 'runs', 'accounts', 'provider', 'queue', 'prompts'):
        assert key in payload
    assert len(payload['prompts']) == len(payload['document']['shots'])


def test_mutation_endpoints_require_the_studio_token(studio):
    client = studio.app.test_client()
    response = client.post('/api/save', json={'document': {}, 'revision': 1}, base_url='http://127.0.0.1:%d' % studio.PORT)
    assert response.status_code == 403


def test_a_busy_episode_is_not_reported_as_an_unexpected_failure(studio):
    """automation_loop must only swallow the busy signal, not every ValueError."""
    import inspect
    source = inspect.getsource(studio.automation_loop)
    assert 'except Busy:' in source
    assert 'except ValueError:' not in source


def test_release_resolve_requires_a_known_job(studio, monkeypatch, tmp_path):
    import releases
    monkeypatch.setattr(releases, 'DB', tmp_path / 'releases.sqlite')
    client = studio.app.test_client()
    response = client.post('/api/release/resolve', json={'episode': 'S01E001', 'platform': 'youtube', 'action': 'retry'},
                           headers={'X-Studio-Token': studio.TOKEN}, base_url='http://127.0.0.1:%d' % studio.PORT)
    assert response.status_code == 400
    assert 'resolved' in response.get_json()['error'] or 'Only' in response.get_json()['error']
