"""Upload queue state machine.

The regression guarded here: a job entered the uncertain 'check' state before any
local validation, so one bad row blocked every later episode on that platform and
could only be cleared from the command line.
"""
import json
import time

import pytest


@pytest.fixture
def queue(releases_db, monkeypatch):
    import releases
    monkeypatch.setattr(releases, 'credentials', lambda platform: None)
    uploaded = []
    monkeypatch.setattr(releases, 'publish', lambda row, prepared: uploaded.append(row['episode']) or 'remote-id')
    return releases, releases_db, uploaded


def add(c, releases, tmp_path, episode, due, name=None):
    master = tmp_path / (name or episode + '.mp4')
    master.write_bytes(b'master ' + episode.encode())
    with c:
        c.execute(
            'INSERT INTO jobs(episode,platform,path,hash,title,url,due,state,metadata) VALUES(?,?,?,?,?,?,?,?,?)',
            (episode, 'youtube', str(master), releases.digest(master), episode, 'https://example.invalid/x.mp4', due, 'queued', '{}'),
        )
    return master


def row(c, episode):
    return c.execute('SELECT * FROM jobs WHERE episode=?', (episode,)).fetchone()


def test_local_validation_failure_keeps_the_job_queued_and_retryable(queue, tmp_path):
    releases, c, uploaded = queue
    master = add(c, releases, tmp_path, 'S01E001', time.time() - 60)
    master.write_bytes(b'changed after approval')
    releases.run(c)
    saved = row(c, 'S01E001')
    assert saved['state'] == 'queued'
    assert saved['attempts'] == 1
    assert saved['retry_at'] > time.time()
    assert json.loads(saved['last_error'])['message'] == 'Master changed since queueing'
    assert uploaded == []


def test_a_held_job_does_not_silently_block_later_episodes_forever(queue, tmp_path):
    """It is retried with backoff, then held as failed so the user can resolve it."""
    releases, c, uploaded = queue
    add(c, releases, tmp_path, 'S01E001', time.time() - 120)
    add(c, releases, tmp_path, 'S01E002', time.time() - 60)
    (tmp_path / 'S01E001.mp4').write_bytes(b'changed')
    for _ in range(3):
        releases.run(c)
        with c:
            c.execute('UPDATE jobs SET retry_at=0')
    saved = row(c, 'S01E001')
    assert saved['state'] == 'failed'
    assert saved['attempts'] == 3
    # Ordering is still preserved: the later episode waits for a decision.
    assert 'S01E002' not in uploaded


def test_a_resolved_upload_lets_later_episodes_proceed(queue, tmp_path):
    releases, c, uploaded = queue
    add(c, releases, tmp_path, 'S01E001', time.time() - 120)
    add(c, releases, tmp_path, 'S01E002', time.time() - 60)
    master = tmp_path / 'S01E001.mp4'
    master.write_bytes(b'changed')
    releases.run(c)
    master.write_bytes(b'master S01E001')  # the user restores the approved file
    with c:
        c.execute("UPDATE jobs SET state='queued',attempts=0,retry_at=NULL,last_error='{}' WHERE episode='S01E001'")
    releases.run(c)
    assert row(c, 'S01E001')['state'] == 'submitted'
    assert row(c, 'S01E002')['state'] == 'submitted'
    assert uploaded == ['S01E001', 'S01E002']


def test_successful_upload_records_the_remote_identifier(queue, tmp_path):
    releases, c, uploaded = queue
    add(c, releases, tmp_path, 'S01E001', time.time() - 60)
    releases.run(c)
    saved = row(c, 'S01E001')
    assert (saved['state'], saved['remote']) == ('submitted', 'remote-id')
    assert json.loads(saved['last_error']) == {}


def test_an_ambiguous_failure_is_held_for_checking(queue, tmp_path, monkeypatch):
    releases, c, uploaded = queue
    add(c, releases, tmp_path, 'S01E001', time.time() - 60)

    def explode(row, prepared):
        raise ConnectionError('connection reset during upload')

    monkeypatch.setattr(releases, 'publish', explode)
    releases.run(c)
    saved = row(c, 'S01E001')
    assert saved['state'] == 'check'
    assert saved['remote'] is None


def test_a_definite_refusal_is_marked_failed_not_held(queue, tmp_path, monkeypatch):
    releases, c, uploaded = queue
    add(c, releases, tmp_path, 'S01E001', time.time() - 60)

    class Response:
        status_code = 400

    class Refused(Exception):
        response = Response()

    def refuse(row, prepared):
        raise Refused('invalid request body')

    monkeypatch.setattr(releases, 'publish', refuse)
    releases.run(c)
    assert row(c, 'S01E001')['state'] == 'failed'


def test_outcome_classification():
    import releases

    class Response:
        status_code = 500

    class Server(Exception):
        response = Response()

    assert releases.outcome(Server()) == 'check'
    assert releases.outcome(ValueError('Hosted video differs from reviewed master')) == 'failed'


def test_read_db_creates_the_schema_when_missing():
    import releases
    connection = releases.read_db()
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone()
    connection.close()
