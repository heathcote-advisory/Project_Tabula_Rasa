"""Public legal-route and content boundary tests."""

from __future__ import annotations

from pathlib import Path


def test_checked_in_legal_content_includes_google_policy_links() -> None:
    terms = Path("docs/TERMS_OF_USE.md").read_text(encoding="utf-8")
    privacy = Path("docs/PRIVACY_POLICY.md").read_text(encoding="utf-8")

    for content, heading in (
        (terms, "# Terms of Use"),
        (privacy, "# Privacy Policy"),
    ):
        assert heading in content
        assert "**Effective Date:** September 1, 2026" in content
        assert "https://maps.google.com/help/terms_maps/" in content
        assert "https://policies.google.com/privacy" in content
