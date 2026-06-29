"""
Guest/anonymous session token support.

Allows citizens to submit service requests without creating an account.
The token is a random string stored in the Django session — no PII, no DB record.
"""
from __future__ import annotations
import secrets

GUEST_SESSION_KEY = "govstack_guest_token"
GUEST_TOKEN_BYTES = 32


def generate_guest_token() -> str:
    return secrets.token_urlsafe(GUEST_TOKEN_BYTES)


def get_or_create_guest_token(request) -> str:
    token = request.session.get(GUEST_SESSION_KEY)
    if not token:
        token = generate_guest_token()
        request.session[GUEST_SESSION_KEY] = token
        request.session.modified = True
    return token


class GuestTokenManager:
    @staticmethod
    def get(request) -> str | None:
        return request.session.get(GUEST_SESSION_KEY)

    @staticmethod
    def create(request) -> str:
        token = generate_guest_token()
        request.session[GUEST_SESSION_KEY] = token
        request.session.modified = True
        return token

    @staticmethod
    def clear(request) -> None:
        request.session.pop(GUEST_SESSION_KEY, None)
        request.session.modified = True
