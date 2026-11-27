"""Provider error parsing and saved routing."""
import json
import sys
import types

import pytest


REAL_QUOTA_MESSAGE = (
    'ZeroGPU quota exceeded: 68s requested vs. 65s left. '
    'You exceeded your GPU quota, try again in 23:55:46'
)


def test_quota_details_separates_compute_seconds_from_the_retry_window():
    import provider
    details = provider.quota_details(REAL_QUOTA_MESSAGE)
    assert details['requested_gpu_seconds'] == 68
    assert details['remaining_gpu_seconds'] == 65
    assert details['retry_seconds'] == 23 * 3600 + 55 * 60 + 46


def test_retry_window_reads_the_wait_instruction_not_the_gpu_numbers():
    import provider
    error = RuntimeError(REAL_QUOTA_MESSAGE)
    assert provider.retry_window(error) == 23 * 3600 + 55 * 60 + 46 + 60


def test_retry_window_defaults_to_six_hours_when_nothing_is_stated():
    import provider
    assert provider.retry_window(RuntimeError('quota exceeded')) == 21600


def test_explain_maps_a_quota_refusal_to_a_bounded_retry():
    import provider
    explained = provider.explain(RuntimeError(REAL_QUOTA_MESSAGE))
    assert isinstance(explained, provider.ProviderError)
    assert explained.reason == 'quota'
    assert explained.retry_after > 0
    assert 'compute time' in str(explained)


def test_open_provider_keeps_the_saved_fallback_choice(provider_files, monkeypatch):
    """A provider check used to rewrite the config and silently re-enable fallback."""
    import provider
    import provider_pool
    provider.CONFIG, provider_pool.WAIT = provider_files
    provider.CONFIG.write_text(json.dumps({'space': 'zerogpu-aoti/wan2-2-fp8da-aoti-faster', 'endpoint': '', 'use_auth': False, 'auto_fallback': False}))
    monkeypatch.setitem(sys.modules, 'gradio_client', fake_gradio())
    monkeypatch.setattr(provider, 'space_info', lambda data, token: 'RUNNING')
    assert [item['space'] for item in provider_pool.candidates()] == ['zerogpu-aoti/wan2-2-fp8da-aoti-faster']
    provider.open_provider(provider.CONFIG.parent)
    saved = json.loads(provider.CONFIG.read_text())
    assert saved['auto_fallback'] is False
    assert saved['last_check']['stage'] == 'RUNNING'
    assert [item['space'] for item in provider_pool.candidates()] == ['zerogpu-aoti/wan2-2-fp8da-aoti-faster']


def fake_gradio():
    module = types.ModuleType('gradio_client')

    class Client:
        def __init__(self, *arguments, **keywords):
            pass

        def view_api(self, **keywords):
            return {'named_endpoints': {'/generate_video': {
                'parameters': [
                    {'parameter_name': 'prompt', 'parameter_has_default': False},
                    {'parameter_name': 'input_image', 'parameter_has_default': False},
                ],
                'returns': [{'label': 'video'}],
            }}}

    module.Client = Client
    return module


def test_choose_endpoint_rejects_an_endpoint_without_image_conditioning():
    import provider
    schema = {'named_endpoints': {'/generate_video': {
        'parameters': [{'parameter_name': 'prompt', 'parameter_has_default': False,
                        'type': {'description': ''}}],
        'returns': [{'label': 'video'}],
    }}}
    with pytest.raises(provider.ProviderError):
        provider.choose_endpoint(schema)


def test_normalize_space_accepts_a_space_url():
    import provider
    assert provider.normalize_space('https://huggingface.co/spaces/owner/space-name') == 'owner/space-name'
    with pytest.raises(provider.ProviderError):
        provider.normalize_space('owner')
