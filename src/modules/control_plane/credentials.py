"""Password hashing for per-project write credentials (ADR 0019).

A project's owner credential is the only thing between a shared deployment and
someone else's edits, paid runs and deletes, so the password is never stored:
only a salted scrypt digest is, in a table of its own that no API model reads.

scrypt comes from the standard library, which keeps this free of a new
dependency. The cost parameters are recorded beside each digest so they can be
raised later without invalidating existing credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from pydantic import Field

from src.core.schemas import StrictModel

__all__ = ["CredentialRecord", "hash_password", "verify_password"]

_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
# scrypt needs 128 * N * r bytes (16 MiB here); the default ceiling is too close to it.
_MAXMEM = 64 * 1024 * 1024
_SCHEME = f"scrypt-{_N}-{_R}-{_P}"


class CredentialRecord(StrictModel):
    """What is persisted for one project's credential. Never serialised to a client."""

    owner: str = Field(min_length=1, max_length=64)
    scheme: str = Field(min_length=1)
    salt: str = Field(min_length=2, description="Hex.")
    digest: str = Field(min_length=2, description="Hex.")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM
    )


def hash_password(owner: str, password: str) -> CredentialRecord:
    """Hash `password` under a fresh random salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, _N, _R, _P)
    return CredentialRecord(owner=owner, scheme=_SCHEME, salt=salt.hex(), digest=digest.hex())


def verify_password(record: CredentialRecord, owner: str, password: str) -> bool:
    """True when `owner` and `password` match `record`.

    The digest is always derived, even when the owner already differs, so the
    response time does not reveal which half was wrong. An unreadable scheme is
    a mismatch rather than an exception: a corrupt row must lock, not crash.
    """
    try:
        name, n, r, p = record.scheme.split("-")
        if name != "scrypt":
            return False
        derived = _derive(password, bytes.fromhex(record.salt), int(n), int(r), int(p))
        expected = bytes.fromhex(record.digest)
    except ValueError:
        return False
    owner_ok = hmac.compare_digest(owner.encode("utf-8"), record.owner.encode("utf-8"))
    return hmac.compare_digest(derived, expected) and owner_ok
