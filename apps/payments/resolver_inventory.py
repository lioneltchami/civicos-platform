"""Resolver-derived inventory for all mounted Payments endpoints.

The inventory deliberately records the actual Django resolver output rather than
assuming that only the GovStack URL prefix can initiate provider work. A later
strict admission increment must assign every mutation a verified boundary policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.urls import URLPattern, URLResolver, get_resolver

READ_ONLY = "read_only"
MUTATION = "mutation"


@dataclass(frozen=True)
class PaymentRoute:
    full_route: str
    name: str
    methods: frozenset[str]
    callback_module: str
    callback_name: str
    kind: str


def _walk(patterns: Iterable[object], prefix: str = ""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern


def _methods(callback: object) -> frozenset[str]:
    view_class = getattr(callback, "view_class", None)
    if view_class is None:
        return frozenset({"GET"})
    return frozenset(
        method.upper()
        for method in ("get", "post", "put", "patch", "delete")
        if hasattr(view_class, method)
    )


def build_payment_route_inventory() -> tuple[PaymentRoute, ...]:
    """Return every route implemented by an ``apps.payments`` callback."""
    records: list[PaymentRoute] = []
    for full_route, pattern in _walk(get_resolver().url_patterns):
        callback = pattern.callback
        module = getattr(callback, "__module__", "")
        view_class = getattr(callback, "view_class", None)
        if view_class is not None:
            module = getattr(view_class, "__module__", module)
        if not module.startswith("apps.payments"):
            continue
        methods = _methods(callback)
        records.append(
            PaymentRoute(
                full_route=full_route,
                name=pattern.name or "",
                methods=methods,
                callback_module=module,
                callback_name=getattr(view_class or callback, "__name__", ""),
                kind=MUTATION if methods & {"POST", "PUT", "PATCH", "DELETE"} else READ_ONLY,
            )
        )
    return tuple(sorted(records, key=lambda record: (record.full_route, record.name)))


def mutation_routes() -> tuple[PaymentRoute, ...]:
    return tuple(route for route in build_payment_route_inventory() if route.kind == MUTATION)
