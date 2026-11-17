"""Anonymous disclosures, exact reviewed-byte activation and transport boundaries."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tabula_rasa.application.customer_policies import beta_candidate, load_policies
from tabula_rasa.http.app import create_app
from tests.http.test_transport import FakeRuntime, SETTINGS


def test_default_publication_keeps_editorial_beta_private(monkeypatch):
    monkeypatch.delenv('TABULA_RASA_LEGAL_APPROVED_SHA256', raising=False)
    with TestClient(create_app(settings=SETTINGS, runtime_factory=lambda _: FakeRuntime())) as client:
        response = client.get('/api/v1/public/policies')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        assert not data['beta'] and data['version'] == 'internal-2026-09-01'
        assert 'heathcoteadvisoryllc@gmail.com' in data['privacy']
        assert 'yaodongy1018' not in response.text
        assert 'Invitation-only beta Terms' not in data['terms']
        assert client.get('/api/v1/policies/acknowledgement').status_code in {401, 403}
        assert client.post('/api/v1/policies/acknowledgement', json={'accepted': True, 'digest': data['digest']}).status_code in {401, 403}


def test_publication_requires_exact_contact_date_and_policy_bytes():
    candidate = beta_candidate('heathcoteadvisoryllc@gmail.com', '2026-11-16')
    env = {'TABULA_RASA_LEGAL_APPROVED_SHA256': candidate.digest,
           'TABULA_RASA_SUPPORT_EMAIL': candidate.support_email,
           'TABULA_RASA_LEGAL_EFFECTIVE_DATE': candidate.effective_date}
    assert load_policies(env) == candidate
    assert '{{CONTACT}}' not in candidate.terms
    for field, value in [('TABULA_RASA_SUPPORT_EMAIL', 'other@example.com'),
                         ('TABULA_RASA_LEGAL_EFFECTIVE_DATE', '2026-11-17'),
                         ('TABULA_RASA_LEGAL_APPROVED_SHA256', 'f' * 64)]:
        with pytest.raises(ValueError):
            load_policies({**env, field: value})
    assert replace(candidate, terms=candidate.terms + '\nChanged.').digest != candidate.digest
    with pytest.raises(ValueError):
        beta_candidate('bad\r\ncontact@example.com', '2026-11-16')
    with pytest.raises(ValueError):
        beta_candidate('contact@example.com', 'tomorrow')
