"""OpenAPI credential migration and application-level error regressions."""
import base64
import json
import time
from unittest.mock import Mock

import pytest
import requests

from cloudedge import CloudEdgeClient, AuthenticationError, ConfigurationError


@pytest.fixture
def client(tmp_path):
    c = CloudEdgeClient('user@example.test', 'password', 'IT', '+39',
                        session_cache_file=str(tmp_path / 'session'))
    c.OPENAPI_BASE_URL = 'https://openapi.example.test'
    c.BASE_URL = 'https://api.example.test'
    c.session_data = {'userToken': 'token', 'userID': '123',
                      'iotPlatformKeys': {'accessid': 'old-id', 'accesskey': 'old-key'}}
    c._save_session_cache = Mock()
    return c


def response(data):
    r = Mock(status_code=200)
    r.json.return_value = data
    return r


def test_platform_credentials_replace_bootstrap_keys(client):
    expiry = int((time.time() + 3600) * 1000)
    info = base64.b64encode(json.dumps({'accessid': 'new-id', 'accesskey': 'new-key'}).encode()).decode()
    client._aes_cbc_decrypt = Mock(return_value=info + '-signature')
    client._session.get = Mock(return_value=response({'resultCode': '1001', 'result': {
        'pfApi': {'platform': {'signature': 'encrypted', 'expireTime': expiry},
                  'mqtt': {'host': 'mqtt.example.test', 'port': '1883'}}}}))
    client._fetch_iot_config(force_refresh=True)
    assert client.session_data['iotPlatformKeys'] == {'accessid': 'new-id', 'accesskey': 'new-key'}
    assert client.session_data['iotCredentialsExpireTime'] == expiry / 1000
    assert client._session.get.call_args.kwargs['params']['refresh'] == '1'
    client._make_request = Mock(return_value=response({'iot': {'154': 75}}))
    assert client.get_device_config('123456789')['iot']['154'] == 75
    assert client._make_request.call_args.kwargs['params']['accessid'] == 'new-id'


@pytest.mark.parametrize('http_error', [False, True])
def test_auth_failure_refreshes_once_and_resigns_request(client, http_error):
    client._refresh_device_tokens = Mock()
    denied = response({'errid': 401, 'errstr': 'Authorization Failed', 'reason': 'STError'})
    if http_error:
        denied = requests.HTTPError(response=Mock(status_code=401))
    success = response({'iot': {'154': 75}})
    client._make_request = Mock(side_effect=[denied, success])
    def renew(**kwargs):
        assert kwargs == {'force_refresh': True}
        client.session_data['iotPlatformKeys'] = {'accessid': 'fresh-id', 'accesskey': 'fresh-key'}
    client._fetch_iot_config = Mock(side_effect=renew)
    assert client.get_device_config('123456789')['iot']['154'] == 75
    calls = client._make_request.call_args_list
    assert [c.kwargs['params']['accessid'] for c in calls] == ['old-id', 'fresh-id']
    assert calls[0].kwargs['params']['signature'] != calls[1].kwargs['params']['signature']
    client._fetch_iot_config.assert_called_once()


def test_persistent_rejection_is_not_data_and_does_not_loop(client):
    client._refresh_device_tokens = Mock()
    client._make_request = Mock(return_value=response({'errid': 401, 'reason': 'secret-token'}))
    client._fetch_iot_config = Mock()
    for _ in range(2):
        with pytest.raises(AuthenticationError, match='401') as exc:
            client.get_device_config('123456789')
        assert 'secret-token' not in str(exc.value)
    assert client._make_request.call_count == 3
    client._fetch_iot_config.assert_called_once_with(force_refresh=True)


@pytest.mark.parametrize('payload', [{'errid': 503}, {'resultCode': '1003'}, [], None])
def test_config_errors_are_not_reported_as_configuration(client, payload):
    client._make_request = Mock(return_value=response(payload))
    with pytest.raises(ConfigurationError):
        client.get_device_config('123456789')


def test_rejected_wake_is_not_success(client):
    client._make_request = Mock(return_value=response({'errid': 401}))
    client._fetch_iot_config = Mock()
    assert client.wake_device('123456789') is False


def test_rejected_status_is_not_unknown_success(client):
    client._make_request = Mock(return_value=response({'errid': 401}))
    client._fetch_iot_config = Mock()
    with pytest.raises(AuthenticationError):
        client.get_device_online_status('123456789')


def test_rejected_write_is_not_success(client):
    client._make_request = Mock(return_value=response({'errid': 401, 'code': 100001}))
    client._fetch_iot_config = Mock()
    with pytest.raises(AuthenticationError):
        client.set_device_config('123456789', {'150': 1}, auto_wake=False)


def test_device_signature_and_expiry_authorise_openapi_calls(client):
    """The cloud rejects /openapi/device/config unless the caller echoes back
    the deviceSignature/t pair minted by the device list, plus clientid."""
    client._device_tokens['0000000123456789'] = {'token': 'dev-signature', 't': '1789766455'}
    client._make_request = Mock(return_value=response({'iot': {'150': 1}}))

    client.get_device_config('123456789')

    sent = client._make_request.call_args.kwargs['params']
    assert sent['token'] == 'dev-signature'
    assert sent['t'] == '1789766455'
    assert sent['clientid'] == '123'


def test_device_list_entries_provide_the_openapi_token(client):
    client._remember_device_token({
        'snNum': 'ppsl0123456789abcdef', 'deviceID': 104695450,
        'deviceSignature': 'dev-signature', 't': 1789766455,
    })

    assert client._device_tokens['0123456789abcdef'] == {
        'token': 'dev-signature', 't': '1789766455',
    }


def test_device_entry_without_signature_is_ignored(client):
    client._remember_device_token({'snNum': 'ppsl0123456789abcdef', 't': 1789766455})

    assert client._device_tokens == {}


def test_missing_token_triggers_a_plain_device_list_refresh(client):
    """A caller may set a parameter before anything listed the devices.

    The refresh must walk the *plain* inventory (homes + owned, no ping
    enhancement and no OpenAPI reads) so a device the cloud lists without
    a signature cannot recurse; and it must mint the token through the
    real parsing of the listing response.
    """
    calls = []

    def transport(method, url, **kwargs):
        base = url.split("?")[0]
        calls.append(url)
        if base.endswith("/v1/app/home/list"):
            return response({"resultCode": "1001", "result": {"homes": []}})
        if base.endswith("/ppstrongs/getDevice.action"):
            return response({"resultCode": "1001", "ipc": [{
                "deviceID": 104695450,
                "snNum": "ppsl0000000123456789",
                "deviceName": "Cam",
                "onLine": 1,
                "deviceSignature": "fresh",
                "t": 1789766455,
            }]})
        return response({"iot": {}})

    client._make_request = Mock(side_effect=transport)

    client.get_device_config('123456789')

    # One inventory walk (homes + owned) then the config request: no
    # recursion, bounded request count.
    assert len(calls) == 3
    assert calls[-1].endswith("/openapi/device/config")
    assert client._make_request.call_args.kwargs['params']['token'] == 'fresh'


def test_rejected_call_discards_stale_device_signatures(client):
    """A stale deviceSignature is rejected exactly like stale platform keys,
    so the retry must re-mint both before giving up."""
    client._device_tokens['0000000123456789'] = {'token': 'stale', 't': '1'}
    denied = response({'errid': 401, 'errstr': 'Authorization Failed', 'reason': 'STError'})
    client._make_request = Mock(return_value=denied)
    client._fetch_iot_config = Mock()
    client._refresh_device_tokens = Mock()

    with pytest.raises(AuthenticationError):
        client.get_device_config('123456789')

    assert '0000000123456789' not in client._device_tokens
    assert client._refresh_device_tokens.called
