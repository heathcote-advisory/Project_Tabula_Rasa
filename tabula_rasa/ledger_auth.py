"""Password authentication helpers for the local operator tools."""

from __future__ import annotations

import base64
import hashlib
import secrets


SCHEME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DERIVED_KEY_LENGTH = 32


def hash_password(password: str) -> str:
    """Return a salted, self-describing scrypt password hash."""
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")

    salt = secrets.token_bytes(16)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DERIVED_KEY_LENGTH,
    )
    return "$".join(
        (
            SCHEME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived_key).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password without exposing timing-sensitive equality checks."""
    try:
        scheme, n, r, p, salt_text, expected_text = encoded_hash.split("$")
        if scheme != SCHEME:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (TypeError, ValueError):
        return False
    return secrets.compare_digest(actual, expected)
