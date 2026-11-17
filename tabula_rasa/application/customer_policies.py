"""Versioned public disclosures; beta publication requires exact reviewed bytes."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
BETA_VERSION = 'beta-2026-11-16'
SUPPORT_EMAIL = 'heathcoteadvisoryllc@gmail.com'


@dataclass(frozen=True)
class CustomerPolicies:
    version: str
    effective_date: str
    terms: str
    privacy: str
    support_email: str | None
    beta: bool

    @property
    def digest(self):
        return sha256(json.dumps({
            'version': self.version, 'effective_date': self.effective_date,
            'terms': self.terms, 'privacy': self.privacy, 'support_email': self.support_email,
        }, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def beta_candidate(contact: str, effective_date: str) -> CustomerPolicies:
    """Build an editorial candidate, never a declaration of approval."""
    from datetime import date
    if not re.fullmatch(r'[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', contact):
        raise ValueError('A reviewed support contact is required.')
    try:
        if date.fromisoformat(effective_date).isoformat() != effective_date:
            raise ValueError
    except ValueError:
        raise ValueError('An ISO effective date is required.') from None
    def read(name):
        return (ROOT / 'docs' / 'legal' / name).read_text(encoding='utf-8').replace(
            '{{CONTACT}}', contact).replace('{{EFFECTIVE_DATE}}', effective_date)
    return CustomerPolicies(BETA_VERSION, effective_date, read('BETA_TERMS.md'),
                            read('BETA_PRIVACY.md'), contact, True)


def load_policies(environment) -> CustomerPolicies:
    approved = environment.get('TABULA_RASA_LEGAL_APPROVED_SHA256', '')
    if approved:
        contact = environment.get('TABULA_RASA_SUPPORT_EMAIL', '')
        if contact != SUPPORT_EMAIL:
            raise ValueError('Policy and application support contacts must match.')
        candidate = beta_candidate(contact,
                                   environment.get('TABULA_RASA_LEGAL_EFFECTIVE_DATE', ''))
        if approved != candidate.digest:
            raise ValueError('Reviewed legal bundle does not match configured publication.')
        return candidate
    return CustomerPolicies('internal-2026-09-01', '2026-09-01',
        (ROOT / 'docs' / 'TERMS_OF_USE.md').read_text(encoding='utf-8'),
        (ROOT / 'docs' / 'PRIVACY_POLICY.md').read_text(encoding='utf-8'), None, False)
