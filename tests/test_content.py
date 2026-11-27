"""Bundled content integrity, redaction, metadata limits and archive safety."""
import hashlib
import io
import json
import zipfile

import pytest

from conftest import APP


def test_episode_matches_the_bundled_frame_and_voice_manifests():
    """A changed script silently disables the bundled pilot assets."""
    manifest = json.loads((APP / 'assets' / 'pilot-frames' / 'manifest.json').read_text(encoding='utf8'))
    episode = json.loads((APP / 'episode01.json').read_text(encoding='utf8'))
    signature = hashlib.sha256(json.dumps(
        [{key: shot.get(key) for key in manifest['fields']} for shot in episode['shots']],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert signature in manifest['signatures']
    assert len(episode['shots']) == len(manifest['frames']) == len(manifest['contracts'])
    reference = hashlib.sha256((APP / 'assets' / 'sheru-reference.png').read_bytes()).hexdigest()
    assert reference == manifest['reference_sha256']

    voices = json.loads((APP / 'assets' / 'pilot-voices' / 'manifest.json').read_text(encoding='utf8'))['shots']
    assert len(voices) == len(episode['shots'])
    for shot, recording in zip(episode['shots'], voices):
        assert (shot['speaker'], shot['line']) == (recording['speaker'], recording['line'])
        data = (APP / 'assets' / 'pilot-voices' / recording['file']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == recording['sha256']


def test_episode_total_is_exactly_thirty_seconds():
    import sheru_studio
    episode = json.loads((APP / 'episode01.json').read_text(encoding='utf8'))
    sheru_studio.validate(episode)


def test_redaction_removes_tokens_and_query_strings():
    import diagnostics
    text = diagnostics.redact('token=hf_abcdef123456 and https://example.com/v?X-Amz-Signature=secret&x=1')
    assert 'hf_abcdef123456' not in text
    assert 'X-Amz-Signature' not in text
    assert diagnostics.redact({'access_token': 'abc', 'other': 'keep'}) == {'access_token': '[REDACTED]', 'other': 'keep'}


def test_metadata_limits_are_enforced():
    import growth
    with pytest.raises(ValueError):
        growth.validate_metadata('youtube', {'title': 'x' * 101, 'description': ''})
    with pytest.raises(ValueError):
        growth.validate_metadata('youtube', {'title': 'ok', 'description': '', 'hashtags': ['#a', '#b', '#c', '#d', '#e', '#f']})
    result = growth.validate_metadata('instagram', {'caption': 'கேப்ஷன் #ஒன்று', 'hashtags': ['#ஒன்று']})
    assert result['caption'] == 'கேப்ஷன் #ஒன்று'


def test_archive_extraction_rejects_unsafe_paths(tmp_path):
    import setup_progress
    archive = tmp_path / 'evil.zip'
    with zipfile.ZipFile(archive, 'w') as handle:
        handle.writestr('../escape.txt', 'no')
    with pytest.raises(ValueError):
        setup_progress.extract(archive, tmp_path / 'out')
    assert not (tmp_path / 'escape.txt').exists()


def test_archive_extraction_accepts_a_normal_archive(tmp_path):
    import setup_progress
    archive = tmp_path / 'good.zip'
    with zipfile.ZipFile(archive, 'w') as handle:
        handle.writestr('folder/file.txt', 'hello')
    setup_progress.extract(archive, tmp_path / 'out')
    assert (tmp_path / 'out' / 'folder' / 'file.txt').read_text() == 'hello'


def test_tamil_number_normalisation_still_flags_a_missing_number():
    import auto_repair
    assert auto_repair.missing_numbers('எட்டு கோடி', 'கோடி') == ['எட்டு']
    assert auto_repair.missing_numbers('எட்டு கோடி', 'எட்டு கோடி') == []
