"""Quota readings and saved waits.

The regression guarded here: a successful live quota check used to create a long
wait even when the account had its full allowance, which blocked every render.
"""
import io
import json
import time
from datetime import datetime, timedelta, timezone

import pytest


def stub_quota(monkeypatch, remaining, base=1500, reset_hours=20):
    import urllib.request
    reset = (datetime.now(timezone.utc) + timedelta(hours=reset_hours)).isoformat().replace('+00:00', 'Z')

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *arguments):
            return False

    def fake_urlopen(request, timeout=None):
        assert 'zero-gpu/quota' in request.full_url
        return Response(json.dumps({'current': remaining, 'base': base, 'resetsAt': reset}).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    return reset


@pytest.fixture
def configured(provider_files, monkeypatch):
    import provider
    import provider_pool
    provider.CONFIG, provider_pool.WAIT = provider_files
    provider.CONFIG.write_text(json.dumps({'space': 'owner/space', 'endpoint': '', 'use_auth': True, 'auto_fallback': True}))
    monkeypatch.setenv('HF_TOKEN', 'hf_' + 'x' * 30)
    return provider, provider_pool


def test_live_check_without_a_failed_request_creates_no_wait(configured, monkeypatch):
    provider, provider_pool = configured
    stub_quota(monkeypatch, 1500)
    live = provider_pool.refresh_quota(force=True)
    assert live['remaining_gpu_seconds'] == 1500
    assert provider_pool.cooldown().get('until', 0) <= time.time()
    provider_pool.check_cooldown()  # must not raise


def test_live_check_clears_a_wait_when_allowance_covers_the_request(configured, monkeypatch):
    provider, provider_pool = configured
    provider_pool.WAIT.write_text(json.dumps({'until': time.time() + 3600, 'reason': 'quota', 'quota': {'requested_gpu_seconds': 68}}))
    stub_quota(monkeypatch, 1500)
    provider_pool.refresh_quota(force=True)
    saved = provider_pool.cooldown()
    assert saved['wait_status'] == 'cleared_by_live_allowance'
    assert saved['until'] == 0
    provider_pool.check_cooldown()


def test_live_check_confirms_a_wait_that_is_genuinely_too_small(configured, monkeypatch):
    provider, provider_pool = configured
    provider_pool.WAIT.write_text(json.dumps({'until': 0, 'reason': 'quota', 'quota': {'requested_gpu_seconds': 68}}))
    stub_quota(monkeypatch, 65)
    provider_pool.refresh_quota(force=True)
    saved = provider_pool.cooldown()
    assert saved['wait_status'] == 'confirmed_insufficient'
    assert saved['until'] > time.time()


def test_live_check_cannot_shorten_an_unexpired_wait_for_an_unknown_request(configured, monkeypatch):
    provider, provider_pool = configured
    until = time.time() + 3600
    provider_pool.WAIT.write_text(json.dumps({'until': until, 'reason': 'quota', 'quota': {}}))
    stub_quota(monkeypatch, 1500)
    provider_pool.refresh_quota(force=True)
    assert provider_pool.cooldown()['until'] == pytest.approx(until)


def test_live_check_is_reused_for_ten_minutes(configured, monkeypatch):
    provider, provider_pool = configured
    calls = []
    reset = stub_quota(monkeypatch, 900)
    provider_pool.refresh_quota(force=True)
    import urllib.request
    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: calls.append(1) or real(*a, **k))
    provider_pool.refresh_quota()
    assert calls == []
    assert provider_pool.cooldown()['live_quota']['remaining_gpu_seconds'] == 900
