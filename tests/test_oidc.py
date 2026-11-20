from base64 import b64decode, urlsafe_b64encode
from hashlib import sha256
import json
from time import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tabula_rasa.application.identity import IdentityError, LoginTransaction
from tabula_rasa.infrastructure.oidc import OidcProvider, OidcSettings
from tabula_rasa.http.config import HttpSettings


def test_provider_callback_and_configuration_use_canonical_public_origin():
    environment = dict(TABULA_RASA_OIDC_ISSUER='https://id.example.test', TABULA_RASA_OIDC_CLIENT_ID='client',
                       TABULA_RASA_OIDC_CLIENT_SECRET='secret', TABULA_RASA_SESSION_GENERATION='g' * 32)
    variants = [OidcSettings.from_environment(HttpSettings('test', origin, access_mode='named_user'), environment)
                for origin in ('HTTPS://NAMED.example.test:443', 'https://named.example.test')]
    assert variants[0].callback == variants[1].callback == 'https://named.example.test/api/v1/auth/callback'
    assert variants[0].configuration_hash == variants[1].configuration_hash


class ProviderResponse:
    status_code = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def iter_content(self, size):
        yield json.dumps(self.data).encode()


@pytest.fixture
def oidc():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = OidcSettings('https://id.example.test', 'client', 'secret', 'g' * 32, 'test', 'http://127.0.0.1')
    transaction = LoginTransaction('s' * 43, 'n' * 43, 'v' * 64, '/')
    claims = dict(iss=settings.issuer, sub='alice', aud='client', exp=int(time()) + 300,
                  iat=int(time()), nonce=transaction.nonce, auth_time=int(time()))
    info = dict(sub='alice', email='synthetic@example.test', email_verified=True)
    calls = []
    class Transport:
        token_override = None
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith('openid-configuration'):
                result = dict(issuer=settings.issuer, authorization_endpoint=settings.issuer + '/authorize',
                              token_endpoint=settings.issuer + '/token', jwks_uri=settings.issuer + '/keys',
                              userinfo_endpoint=settings.issuer + '/userinfo', id_token_signing_alg_values_supported=['RS256'],
                              code_challenge_methods_supported=['S256'], token_endpoint_auth_methods_supported=['client_secret_basic'])
            elif url.endswith('/token'):
                result = dict(id_token=self.token_override or jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'key'}),
                              access_token='synthetic-access', token_type='Bearer')
            elif url.endswith('/keys'):
                result = {'keys': [dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), kid='key')]}
            else:
                result = info
            return ProviderResponse(result)
        def close(self):
            pass
    return OidcProvider(settings, transport=Transport()), transaction, claims, info, calls


@pytest.mark.parametrize('registration', [False, True])
def test_code_pkce_verified_identity_and_bounded_transport(oidc, registration):
    provider, transaction, _, _, calls = oidc
    query = parse_qs(urlsplit(provider.authorization_url(transaction, registration=registration)).query)
    assert query['code_challenge_method'] == ['S256'] and query['max_age'] == ['0']
    assert query['prompt'] == ['create' if registration else 'login']
    assert query['redirect_uri'] == [provider.settings.callback]
    result = provider.verify('synthetic-code', transaction)
    assert result.subject == 'alice'
    token_call = next(call for call in calls if call[0] == 'POST')
    body = parse_qs(token_call[2]['data'])
    assert body['code_verifier'] == [transaction.verifier]
    assert body['grant_type'] == ['authorization_code']
    assert body['redirect_uri'] == [provider.settings.callback]
    assert b64decode(token_call[2]['headers']['Authorization'].split()[1]) == b'client:secret'
    assert all(call[2]['allow_redirects'] is False and call[2]['timeout'] == (3, 5) for call in calls)
    assert 'synthetic@example.test' not in repr(result)


@pytest.mark.parametrize('change', [dict(iss='https://wrong.test'), dict(aud='wrong'), dict(nonce='wrong'),
    dict(exp=lambda: int(time()) - 120), dict(iat=lambda: int(time()) + 120), dict(nbf=lambda: int(time()) + 120),
    dict(auth_time=lambda: int(time()) - 600), dict(auth_time=True), dict(azp='wrong'),
    dict(aud=['client', 'other']), dict(sub=''), dict(at_hash='wrong')])
def test_invalid_signed_claims_fail_closed_without_provider_material(oidc, change):
    provider, transaction, claims, _, _ = oidc
    # Resolve relative times at execution, not collection before a long suite.
    claims.update({key: value() if callable(value) else value for key, value in change.items()})
    with pytest.raises(IdentityError) as caught:
        provider.verify('synthetic-code', transaction)
    assert caught.value.code == 'unauthorized'
    assert caught.value.login_reason == ('login_freshness' if change.keys() == {'auth_time'}
                                        and callable(change['auth_time']) else 'token_validation')
    assert caught.value.__context__ is None
    assert 'synthetic' not in str(caught.value)


@pytest.mark.parametrize('change', [dict(sub='bob'), dict(email_verified=False), dict(email_verified='true'), dict(email='')])
def test_userinfo_requires_same_subject_and_verified_email(oidc, change):
    provider, transaction, _, info, _ = oidc
    info.update(change)
    with pytest.raises(IdentityError) as caught:
        provider.verify('synthetic-code', transaction)
    assert caught.value.login_reason == 'userinfo_validation'


def test_unknown_key_refresh_and_untrusted_endpoint_rejected(oidc):
    provider, transaction, _, _, calls = oidc
    provider._keys, provider._keys_deadline = [], time() + 300
    assert provider.verify('code', transaction).subject == 'alice'
    assert sum(url.endswith('/keys') for _, url, _ in calls) == 1
    for endpoint in ('https://foreign.test/token', 'http://id.example.test/token',
                     'https://id.example.test/token?secret=private', 'https://user@id.example.test/token'):
        with pytest.raises(IdentityError):
            provider._endpoint(endpoint)


@pytest.mark.parametrize('attack', ['algorithm', 'signature', 'unknown_key', 'embedded_key', 'unsigned'])
def test_algorithm_signature_and_unknown_key_fail_closed(oidc, attack):
    provider, transaction, claims, _, calls = oidc
    if attack == 'algorithm':
        token = jwt.encode(claims, 'synthetic-hmac-secret' * 3, algorithm='HS256', headers={'kid': 'key'})
    elif attack == 'unsigned':
        token = jwt.encode(claims, key=None, algorithm='none', headers={'kid': 'key'})
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        headers = {'kid': 'foreign' if attack == 'unknown_key' else 'key'}
        if attack == 'embedded_key':
            headers['jwk'] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        token = jwt.encode(claims, key, algorithm='RS256', headers=headers)
    provider.transport.token_override = token
    with pytest.raises(IdentityError) as caught:
        provider.verify('code', transaction)
    assert caught.value.code == 'unauthorized' and caught.value.__context__ is None
    if attack == 'unknown_key':
        assert sum(url.endswith('/keys') for _, url, _ in calls) == 2


@pytest.mark.parametrize('field,required', [
    ('id_token_signing_alg_values_supported', 'RS256'),
    ('code_challenge_methods_supported', 'S256'),
    ('token_endpoint_auth_methods_supported', 'client_secret_basic'),
])
@pytest.mark.parametrize('invalid', [None, 7, True, 'substring', [], [None], ['unsupported']])
def test_discovery_capabilities_require_arrays_of_strings(oidc, field, required, invalid):
    provider, transaction, _, _, _ = oidc
    original = provider._json
    def malformed(method, url, **kwargs):
        data = original(method, url, **kwargs)
        if url.endswith('openid-configuration'):
            data[field] = required if invalid == 'substring' else invalid
        return data
    provider._json = malformed
    with pytest.raises(IdentityError) as caught:
        provider.authorization_url(transaction)
    assert caught.value.code == 'unavailable'
    assert provider._metadata is None


def test_authlib_valid_hash_multi_audience_and_exchange_isolation(oidc):
    provider, transaction, claims, info, calls = oidc
    claims.update(aud=['client', 'other'], azp='client',
                  at_hash=urlsafe_b64encode(sha256(b'synthetic-access').digest()[:16]).rstrip(b'=').decode())
    assert provider.verify('first-code', transaction).subject == 'alice'
    claims['sub'] = info['sub'] = 'bob'
    assert provider.verify('second-code', transaction).subject == 'bob'
    token_calls = [call for call in calls if call[0] == 'POST']
    assert [parse_qs(call[2]['data'])['code'] for call in token_calls] == [['first-code'], ['second-code']]
    assert all(call[2]['headers']['Authorization'].startswith('Basic ') for call in token_calls)


@pytest.mark.parametrize('change', [dict(nonce=None), dict(auth_time='recent'), dict(at_hash=''), dict(aud=None)])
def test_authlib_rejects_malformed_claims(oidc, change):
    provider, transaction, claims, _, _ = oidc
    claims.update(change)
    with pytest.raises(IdentityError) as caught:
        provider.verify('code', transaction)
    assert caught.value.code == 'unauthorized'
    assert caught.value.__context__ is None


@pytest.mark.parametrize('endpoint,reason', [('/token', 'token_exchange'), ('/keys', 'token_validation'),
                                          ('/userinfo', 'userinfo_validation')])
def test_provider_outage_classifies_boundary_without_retry_or_private_exception(oidc, endpoint, reason):
    provider, transaction, _, _, calls = oidc
    original = provider.transport.request
    def unavailable(method, url, **kwargs):
        if url.endswith(endpoint):
            raise RuntimeError('private-provider-response-and-token')
        return original(method, url, **kwargs)
    provider.transport.request = unavailable
    with pytest.raises(IdentityError) as caught:
        provider.verify('synthetic-code', transaction)
    assert caught.value.code == 'unavailable' and caught.value.login_reason == reason
    assert caught.value.__context__ is None
    assert 'private' not in str(caught.value) + repr(caught.value)
    assert sum(method == 'POST' for method, _, _ in calls) <= 1


@pytest.mark.parametrize('registration', [False, True])
def test_cold_discovery_failure_recovers_without_cached_fallback(oidc, monkeypatch, registration):
    provider, transaction, _, _, calls = oidc
    original = provider.transport.request
    attempts = []

    def unavailable(method, url, **kwargs):
        attempts.append((method, url))
        raise OSError('private-provider-host-and-response')

    with monkeypatch.context() as outage:
        outage.setattr(provider.transport, 'request', unavailable)
        with pytest.raises(IdentityError) as caught:
            provider.authorization_url(transaction, registration=registration)
        assert caught.value.code == 'unavailable'
        assert caught.value.__context__ is None
        assert 'private' not in str(caught.value) + repr(caught.value)
        assert attempts == [('GET', provider.settings.issuer + '/.well-known/openid-configuration')]
        assert not calls  # No authorization-code exchange was attempted.

    # Recovery uses the same provider; a failed cold read must not poison its cache.
    assert provider.transport.request == original
    target = provider.authorization_url(transaction, registration=registration)
    assert urlsplit(target).netloc == urlsplit(provider.settings.issuer).netloc
    assert parse_qs(urlsplit(target).query)['prompt'] == ['create' if registration else 'login']
    assert provider.verify('synthetic-code', transaction).subject == 'alice'
    assert sum(url.endswith('openid-configuration') for _, url, _ in calls) == 1
