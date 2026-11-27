"""Shared test setup.

Every writable location is redirected to a temporary directory *before* the
application modules are imported, so running the tests never writes into the
repository and never touches a real installation.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'SheruStudio'
sys.path.insert(0, str(APP))

STATE = Path(tempfile.mkdtemp(prefix='sheru-tests-'))
os.environ.setdefault('SHERU_DATA', str(STATE / 'studio-data'))
os.environ.setdefault('SHERU_CONFIG', str(STATE / 'video-provider.json'))
os.environ.setdefault('SHERU_WAIT', str(STATE / 'provider-wait.json'))
os.environ.setdefault('SHERU_RELEASES', str(STATE / 'releases.sqlite'))
os.environ.setdefault('SHERU_LOGS', str(STATE / 'logs'))


@pytest.fixture
def provider_files(monkeypatch, tmp_path):
    """Point the provider configuration and its saved wait at a per-test folder."""
    import provider
    import provider_pool
    monkeypatch.setattr(provider, 'CONFIG', tmp_path / 'video-provider.json')
    monkeypatch.setattr(provider_pool, 'WAIT', tmp_path / 'provider-wait.json')
    return provider.CONFIG, provider_pool.WAIT


@pytest.fixture
def releases_db(monkeypatch, tmp_path):
    import releases
    monkeypatch.setattr(releases, 'DB', tmp_path / 'releases.sqlite')
    return releases.db()
