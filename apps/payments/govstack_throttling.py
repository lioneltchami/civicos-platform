"""
GovStack Payments BB — custom identity-keyed rate-limit throttling.

Finding 3 (MEDIUM, Payments certifiability audit): ``GovStackAPIView``
(apps/payments/govstack_views.py) hardcodes ``throttle_classes =
[ScopedRateThrottle]`` with ``throttle_scope = "govstack_bb"`` across all 13
Payments endpoints. DRF's stock ``ScopedRateThrottle`` (a subclass of
``SimpleRateThrottle``) generates its cache key from ``request.user.pk`` when
``request.user.is_authenticated``, and otherwise falls back to
``get_ident(request)`` — effectively the client IP address (via
``X-Forwarded-For``/``REMOTE_ADDR``, see ``BaseThrottle.get_ident``).

Payments' GovStack auth layer (``apps.payments.govstack_auth``) never sets
``request.user`` for any of its header-whitelist permission classes
(``IsTrustedSourceBB``, ``IsTrustedPayerFI``, ``RequirePayerFI``,
``IsTrustedBiller``) or the voucher/JWT permission classes — so the stock
throttle's user-based branch never fires, and every Payments request is
throttled purely by IP. This is wrong for a credential-based B2B API:

  - Two different registered BBs/FIs calling from behind the same NAT/gateway
    (shared egress IP) collide into a single shared rate-limit bucket, so one
    high-volume legitimate integration can starve every other caller sharing
    that IP.
  - A single caller that changes IP address (failover, redeploy, IP rotation)
    silently resets its own rate-limit history, defeating the purpose of the
    limit for that caller.

This is the exact same class of finding already fixed for the Appointments/
Scheduler BB this session — see ``apps.appointments.govstack_throttling
.GovStackBBIdentityThrottle`` for the sibling fix and its fuller rationale.

The fix: key the throttle bucket on the calling party's resolved identity
instead of its network address, whenever that identity is available.
``apps.payments.govstack_auth._HeaderWhitelistBBPermission.has_permission()``
stashes ``request.META["_gs_payer_identity"]`` (a public, non-secret caller
identifier) on every successful authentication via any of the header-
whitelist permission classes. ``GovStackPaymentsIdentityThrottle`` below
overrides ``get_cache_key()`` to use that value when present.

When no resolved payer/caller identity is present on the request (e.g.
``AllowAnyBB``/``HasVoucherJWT``-authenticated requests that never stash this
key, or any request where GovStack auth did not run at all), this falls back
to the exact stock ``ScopedRateThrottle`` behaviour (user-pk-or-IP) so that
throttling never errors and never silently stops applying — it just isn't
identity-aware in that case.
"""

from __future__ import annotations

from typing import Any

from rest_framework.throttling import ScopedRateThrottle


class GovStackPaymentsIdentityThrottle(ScopedRateThrottle):
    """
    ScopedRateThrottle that keys its cache bucket on the calling party's
    resolved identity (``request.META["_gs_payer_identity"]``) instead of
    falling through to client-IP-based throttling.

    See module docstring for the full rationale (Finding 3: rate limiting
    was keying on client IP rather than calling-BB/FI identity because
    Payments' GovStack header-whitelist auth never sets ``request.user``).

    ``request.META["_gs_payer_identity"]`` is set by
    ``apps.payments.govstack_auth._HeaderWhitelistBBPermission
    .has_permission()`` whenever a caller successfully authenticates via any
    of ``IsTrustedSourceBB``, ``IsTrustedPayerFI``, ``RequirePayerFI``, or
    ``IsTrustedBiller``. It is a public, non-secret identifier — safe to use
    as a cache key component and safe to log.

    Falls back to the parent ``ScopedRateThrottle.get_cache_key()`` (stock
    user-pk-or-IP behaviour) when no resolved identity is present on the
    request, so this class is always safe to use as a drop-in replacement for
    ``ScopedRateThrottle`` even on views/requests where GovStack header-
    whitelist auth did not run (e.g. ``AllowAnyBB``/``HasVoucherJWT``-gated
    endpoints, or dev/harness requests that bypass authentication entirely).
    """

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        payer_identity = request.META.get("_gs_payer_identity")
        if payer_identity:
            return self.cache_format % {
                "scope": self.scope,
                "ident": payer_identity,
            }
        return super().get_cache_key(request, view)
