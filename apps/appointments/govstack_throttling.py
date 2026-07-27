"""
GovStack Scheduler BB — custom rate-limit throttling.

Bug 7 fix (see MASTER_BB_CERTIFIABILITY_REPORT.md, "Appointments/Scheduler
BB"): all 37 GovStack Scheduler views set
``throttle_classes = [ScopedRateThrottle]`` with ``throttle_scope =
"govstack_bb"``. DRF's stock ``ScopedRateThrottle`` (a subclass of
``SimpleRateThrottle``) generates its cache key from ``request.user.pk`` when
``request.user.is_authenticated``, and otherwise falls back to
``get_ident(request)`` — effectively the client IP address (via
``X-Forwarded-For``/``REMOTE_ADDR``, see ``BaseThrottle.get_ident``).

GovStack Scheduler BB-to-BB calls are authenticated by
``GovStackSchedulerAuth.authenticate()`` (apps/appointments/govstack_auth.py),
which ALWAYS returns ``(None, "govstack_scheduler")`` — no Django ``User`` is
ever linked to a BB-to-BB call, so ``request.user`` is always falsy for these
requests. That means the stock throttle's user-based branch never fires, and
every GovStack Scheduler request is throttled purely by IP. This is wrong for
a credential-based B2B API:

  - Two different registered BBs calling from behind the same NAT/gateway
    (shared egress IP) collide into a single shared rate-limit bucket, so one
    noisy/misbehaving BB can exhaust the quota for every other BB sharing
    that IP.
  - A single BB that changes IP address (failover, redeploy, IP rotation)
    silently resets its own rate-limit history, defeating the purpose of the
    limit for that caller.

The fix: key the throttle bucket on the calling BB's resolved identity
instead of its network address, whenever that identity is available.
``GovStackSchedulerAuth.authenticate()`` already stashes
``request.META["_gs_requestor_id"]`` (the caller's public, non-secret
``GovStackRegisteredBB.bb_id`` string) on every successful authentication —
see the module docstring and ``authenticate()`` docstring in
``govstack_auth.py``. ``GovStackBBIdentityThrottle`` below overrides
``get_cache_key()`` to use that value when present.

When no resolved BB identity is present on the request (e.g.
``GOVSTACK_SCHEDULER_REQUIRE_TOKEN=False`` dev/harness mode with no
``GovStackRegisteredBB`` row involved some other way, GovStack auth simply
not having run for this request, or non-GovStack traffic reusing this
throttle class), this falls back to the exact stock ``ScopedRateThrottle``
behaviour (user-pk-or-IP) so that throttling never errors and never silently
stops applying — it just isn't identity-aware in that case.
"""
from __future__ import annotations

from typing import Any

from rest_framework.throttling import ScopedRateThrottle


class GovStackBBIdentityThrottle(ScopedRateThrottle):
    """
    ScopedRateThrottle that keys its cache bucket on the calling GovStack BB's
    resolved identity (``request.META["_gs_requestor_id"]``) instead of
    falling through to client-IP-based throttling.

    See module docstring for the full rationale (Bug 7 / audit finding: rate
    limiting was keying on client IP rather than calling-BB identity because
    GovStack BB-to-BB calls never set ``request.user``).

    ``request.META["_gs_requestor_id"]`` is set by
    ``GovStackSchedulerAuth.authenticate()`` (apps/appointments/govstack_auth.py)
    on every successful GovStack authentication, BB-to-BB or citizen-JWT alike
    (``GovStackCitizenAuth`` delegates to ``GovStackSchedulerAuth`` first). It
    is a public, non-secret identifier (the registered BB's ``bb_id``) — safe
    to use as a cache key component and safe to log.

    Falls back to the parent ``ScopedRateThrottle.get_cache_key()`` (stock
    user-pk-or-IP behaviour) when no resolved BB identity is present on the
    request, so this class is always safe to use as a drop-in replacement for
    ``ScopedRateThrottle`` even on views/requests where GovStack auth did not
    run (e.g. dev/harness requests that bypass authentication entirely, or
    non-GovStack traffic that happens to reuse this throttle class).
    """

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        requestor_id = request.META.get("_gs_requestor_id")
        if requestor_id:
            return self.cache_format % {
                "scope": self.scope,
                "ident": requestor_id,
            }
        return super().get_cache_key(request, view)
