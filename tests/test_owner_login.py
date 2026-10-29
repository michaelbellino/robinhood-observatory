"""Synthetic owner OAuth only; no provider, brokerage or production requests."""
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from observatory.api import create_app
from observatory.security import owner_return_path


@pytest.mark.parametrize('value', [
    '//attacker.invalid', 'https://attacker.invalid', '/\\attacker.invalid',
    '/%2f%2fattacker.invalid', '/trades?next=https://attacker.invalid',
    '/auth/logout', '/api/orders', '/trades/../auth/login', '/trades#<script>',
    '/trades\r\nLocation: https://attacker.invalid', '/workbench#' + 'a' * 129,
    '/local-connect?port=65536&challenge=' + 'a' * 64, None,
])
def test_return_destination_rejects_unsafe_values(value):
    assert owner_return_path(value) == '/'


@pytest.mark.parametrize('value', ['/', '/trades', '/workbench', '/trades#research',
                                    '/workbench#12345678-abcd'])
def test_return_destination_preserves_known_review_pages(value):
    assert owner_return_path(value) == value


@pytest.fixture
def owner_oauth(db, settings):
    settings.github_client_id = 'synthetic'
    app = create_app(settings, db)
    provider = MagicMock()

    async def redirect(request, redirect_uri, state):
        return RedirectResponse('https://github.com/login/oauth/authorize?' + urlencode({'state': state}))

    provider.authorize_redirect = AsyncMock(side_effect=redirect)
    provider.authorize_access_token = AsyncMock(return_value={'access_token': 'synthetic'})
    response = MagicMock()
    response.json.return_value = {'id': settings.owner_subject}
    provider.get = AsyncMock(return_value=response)
    app.state.oauth._clients['owner'] = provider
    with TestClient(app, base_url='http://127.0.0.1:8000', follow_redirects=False) as client:
        yield client, provider


def begin(client, destination):
    response = client.get('/auth/login', params={'next': destination})
    assert response.status_code == 307
    return parse_qs(urlsplit(response.headers['location']).query)['state'][0]


def test_review_anchor_survives_owner_login_and_session_rotates(owner_oauth):
    client, provider = owner_oauth
    state = begin(client, '/workbench#12345678-abcd')
    response = client.get('/auth/callback', params={'state': state, 'code': 'synthetic'})
    assert response.status_code == 303
    assert response.headers['location'] == '/workbench#12345678-abcd'
    provider.authorize_access_token.assert_awaited_once()
    assert client.get('/api/session').json()['csrf']
    assert client.post('/api/logout', json={}).status_code == 403


def test_interrupted_and_repeated_login_keeps_destination_bound_to_attempt(owner_oauth):
    client, _ = owner_oauth
    first = begin(client, '/workbench#first')
    second = begin(client, '/trades#second')
    assert first != second
    response = client.get('/auth/callback', params={'state': first})
    assert response.headers['location'] == '/workbench#first'


def test_unsafe_login_destination_falls_back_home(owner_oauth):
    client, _ = owner_oauth
    state = begin(client, '//attacker.invalid')
    assert client.get('/auth/callback', params={'state': state}).headers['location'] == '/'


def test_enrollment_return_remains_supported_but_cannot_override_explicit_review(owner_oauth):
    client, _ = owner_oauth
    enrollment = '/local-connect?port=12345&challenge=' + 'a' * 64
    assert client.get(enrollment).status_code == 200
    state = begin(client, '/trades#research')
    assert client.get('/auth/callback', params={'state': state}).headers['location'] == '/trades#research'


def test_enrollment_return_used_for_original_helper_login(owner_oauth):
    client, _ = owner_oauth
    enrollment = '/local-connect?port=12345&challenge=' + 'a' * 64
    assert client.get(enrollment).status_code == 200
    login = client.get('/auth/login')
    state = parse_qs(urlsplit(login.headers['location']).query)['state'][0]
    assert client.get('/auth/callback', params={'state': state}).headers['location'] == enrollment


def test_wrong_owner_cannot_follow_return_link_or_access_account(owner_oauth):
    client, provider = owner_oauth
    state = begin(client, '/trades#proposal')
    provider.get.return_value.json.return_value = {'id': 'other-owner'}
    assert client.get('/auth/callback', params={'state': state}).status_code == 403
    assert client.get('/api/session').status_code == 401


def test_sign_in_links_work_without_javascript(client):
    for page in ('trades', 'workbench'):
        html = client.get('/' + page).text
        assert f'/auth/login?next=%2F{page}' in html
        assert '/static/auth-return.js' in html


def test_real_oauth_rejects_unbound_state_without_network(db, settings, monkeypatch):
    settings.github_client_id = 'synthetic'
    app = create_app(settings, db)
    fetch = AsyncMock(side_effect=AssertionError('No token exchange for unbound state'))
    monkeypatch.setattr(app.state.oauth.owner, 'fetch_access_token', fetch)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        response = client.get('/auth/callback?code=synthetic-private-code&state=unbound')
        assert response.status_code == 400
        assert 'Sign-in did not finish' in response.text
        assert 'synthetic-private-code' not in response.text
        assert client.get('/api/session').status_code == 401
    fetch.assert_not_awaited()


def test_cancel_has_safe_recovery_without_echoing_provider_details(db, settings):
    settings.github_client_id = 'synthetic'
    app = create_app(settings, db)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        response = client.get('/auth/callback?error=access_denied&error_description=synthetic-private-value')
        assert response.status_code == 400
        assert 'synthetic-private-value' not in response.text
        assert 'href="/workbench"' in response.text
        assert 'href="/trades"' in response.text
        assert client.get('/api/session').status_code == 401


def test_real_oauth_state_roundtrip_and_replay_rejection(db, settings, monkeypatch):
    settings.github_client_id = 'synthetic'
    app = create_app(settings, db)
    provider = app.state.oauth.owner
    fetch = AsyncMock(return_value={'access_token': 'synthetic'})
    monkeypatch.setattr(provider, 'fetch_access_token', fetch)
    response = MagicMock()
    response.json.return_value = {'id': settings.owner_subject}
    monkeypatch.setattr(provider, 'get', AsyncMock(return_value=response))
    with TestClient(app, base_url='http://127.0.0.1:8000', follow_redirects=False) as client:
        login = client.get('/auth/login', params={'next': '/workbench#real-state-test'})
        assert login.status_code == 302
        state = parse_qs(urlsplit(login.headers['location']).query)['state'][0]
        callback = '/auth/callback?' + urlencode({'state': state, 'code': 'synthetic'})
        result = client.get(callback)
        assert result.status_code == 303
        assert result.headers['location'] == '/workbench#real-state-test'
        assert client.get(callback).status_code == 400
        fetch.assert_awaited_once()


def test_signin_anchor_history_and_navigation_ui():
    import subprocess
    subprocess.run(['node', 'tests/owner_login_ui.mjs'], check=True, capture_output=True)
