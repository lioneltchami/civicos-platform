"""
apps/core/fields.py — Shared custom Django model fields.

EncryptedCharField: Fernet/AES-128-CBC symmetric encryption at rest.
Transparent to the ORM: reads/writes plain text; the DB column holds encrypted bytes.
Import from here in all apps — do NOT duplicate this class.
"""
from __future__ import annotations
import base64
import warnings
from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.db import models


@lru_cache(maxsize=None)
def _get_fernet() -> MultiFernet:
    """
    Build a MultiFernet instance from FERNET_KEYS setting.
    The first key encrypts; all keys are tried for decryption (supports rotation).
    Keys can be any string; non-Fernet strings are SHA-256-derived to a valid key.

    Cached at module level — keys are immutable at runtime (only change on redeploy).
    The cache is safe because Django settings are frozen after startup.

    IMPORTANT for tests: any test that swaps FERNET_KEYS via override_settings MUST
    call ``_get_fernet.cache_clear()`` in setUp() and tearDown() so the new setting
    is picked up and the original is restored after the test.

    Production requirement: set FERNET_KEYS to a list of dedicated Fernet keys.
    Do NOT rely on the SECRET_KEY fallback in production — rotating SECRET_KEY
    (e.g. after a breach) would simultaneously invalidate all encrypted field values.
    """
    keys_setting = getattr(settings, "FERNET_KEYS", None)
    if not keys_setting:
        if not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False):
            warnings.warn(
                "FERNET_KEYS is not set. Falling back to SECRET_KEY for EncryptedCharField. "
                "This ties encryption key rotation to Django's signing key — set FERNET_KEYS "
                "to a dedicated Fernet key in production.",
                stacklevel=2,
            )
        raw_keys = [settings.SECRET_KEY]
    else:
        raw_keys = keys_setting
    fernets = []
    for key in raw_keys:
        raw = key.encode() if isinstance(key, str) else key
        try:
            fernets.append(Fernet(raw))
        except Exception:
            import hashlib
            derived = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
            fernets.append(Fernet(derived))
    return MultiFernet(fernets)


class EncryptedCharField(models.BinaryField):
    """
    CharField that stores Fernet ciphertext in a BinaryField.
    Transparent to the ORM: reads/writes plain text; the DB column holds encrypted bytes.
    Compatible with Django 4.x and 5.x (no dependency on removed force_text).

    Key management: set FERNET_KEYS in settings (list of strings or url-safe base64
    Fernet keys).  To rotate, prepend the new key; old rows decrypt with old keys.
    """

    description = "Fernet-encrypted character field"

    def __init__(self, max_length: int = 255, **kwargs):
        self._char_max_length = max_length
        kwargs.setdefault("editable", True)
        super().__init__(**kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["max_length"] = self._char_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        if value is None or value == b"" or value == "":
            return ""
        try:
            raw = bytes(value) if not isinstance(value, bytes) else value
            return _get_fernet().decrypt(raw).decode("utf-8")
        except Exception:
            return ""  # corrupt/missing data: return empty rather than crash

    def get_prep_value(self, value):
        if value is None or value == "":
            return b""
        if isinstance(value, (bytes, memoryview)):
            return value  # already encrypted (guard)
        return _get_fernet().encrypt(value.encode("utf-8"))

    def to_python(self, value):
        if isinstance(value, (bytes, memoryview)):
            return self.from_db_value(value, None, None)
        return value or ""
