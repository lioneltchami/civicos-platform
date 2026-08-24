"""Owned operational controls; integrations must enforce authorization at the caller boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    action: str
    attempt_id: str
    owner: str
    reason: str


def review(attempt_id: str, owner: str, reason: str) -> Operation:
    return Operation("review", attempt_id[:100], owner[:100], reason[:255])


def defer(attempt_id: str, owner: str, reason: str) -> Operation:
    return Operation("defer", attempt_id[:100], owner[:100], reason[:255])


def replay(attempt_id: str, owner: str, reason: str) -> Operation:
    return Operation("replay", attempt_id[:100], owner[:100], reason[:255])
