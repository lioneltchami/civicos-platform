#!/usr/bin/env python3
"""Deterministic loopback-only Scheduler harness fake and evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class State:
    def __init__(self) -> None:
        self.entities: dict[str, dict[str, str]] = {}
        self.resources: dict[str, dict[str, str]] = {}
        self.subscribers: dict[str, dict[str, str]] = {}
        self.seen: set[str] = set()
        self.attempts = 0


class Handler(BaseHTTPRequestHandler):
    state = State()

    def log_message(self, *_args: object) -> None:
        return

    def _reply(self, status: int, body: dict[str, object]) -> None:
        data = json.dumps(body, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"status": "ok", "building_block": "Scheduler"})
        elif self.path == "/metrics":
            self._reply(200, {"attempts": self.state.attempts, "dead_lettered": 0})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key", "")
        if self.path == "/entities":
            ident = str(payload.get("id", "entity-1"))
            if key in self.state.seen:
                self._reply(200, {"status": "duplicate", "id": ident})
                return
            self.state.seen.add(key)
            self.state.entities[ident] = {"owner": str(payload.get("owner", "local"))}
            self._reply(201, {"id": ident, "status": "created"})
        elif self.path == "/resources":
            ident, owner = str(payload.get("id", "resource-1")), str(payload.get("owner", "local"))
            if owner not in {"local", "entity-1"}:
                self._reply(403, {"error": "owner_mismatch"})
            else:
                self.state.resources[ident] = {"owner": owner}
                self._reply(201, {"id": ident, "status": "created"})
        elif self.path == "/subscribers":
            ident = str(payload.get("id", "subscriber-1"))
            self.state.subscribers[ident] = {"owner": str(payload.get("owner", "local"))}
            self._reply(201, {"id": ident, "status": "created"})
        elif self.path == "/dispatch":
            self.state.attempts += 1
            self._reply(
                202,
                {"status": "accepted", "attempt": self.state.attempts, "delivery_id": "delivery-1"},
            )
        elif self.path in {"/payments/authorize", "/consent/check"}:
            self._reply(
                200,
                {
                    "authority": self.path.split("/")[1],
                    "status": "approved",
                    "correlation_id": payload.get("correlation_id"),
                },
            )
        else:
            self._reply(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/subscribers/"):
            self.state.subscribers.pop(self.path.rsplit("/", 1)[1], None)
            self._reply(204, {})
        else:
            self._reply(404, {"error": "not_found"})


def request(
    base: str, method: str, path: str, body: dict[str, object] | None = None, key: str = ""
) -> tuple[int, dict[str, object]]:
    data = json.dumps(body or {}).encode()
    req = Request(  # noqa: S310
        base + path.lstrip("/"),
        data=data if method != "GET" else None,
        method=method,
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
    )
    try:
        with urlopen(req, timeout=2) as response:  # nosec B310 - base is validated loopback-only below  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def run(output: Path, port: int) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/"
    scenarios = [
        ("health", "GET", "/health", None, ""),
        ("entity-create", "POST", "/entities", {"id": "entity-1", "owner": "local"}, "entity-key"),
        (
            "entity-duplicate",
            "POST",
            "/entities",
            {"id": "entity-1", "owner": "local"},
            "entity-key",
        ),
        (
            "resource-owner-reject",
            "POST",
            "/resources",
            {"id": "resource-1", "owner": "other"},
            "resource-key",
        ),
        (
            "subscriber-create",
            "POST",
            "/subscribers",
            {"id": "subscriber-1", "owner": "local"},
            "subscriber-key",
        ),
        ("dispatch", "POST", "/dispatch", {"correlation_id": "corr-1"}, "dispatch-key"),
        (
            "payments-authority",
            "POST",
            "/payments/authorize",
            {"correlation_id": "corr-1"},
            "payment-key",
        ),
        (
            "consent-authority",
            "POST",
            "/consent/check",
            {"correlation_id": "corr-1"},
            "consent-key",
        ),
        ("subscriber-cleanup", "DELETE", "/subscribers/subscriber-1", None, "cleanup-key"),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for name, method, path, body, key in scenarios:
            status, response = request(base, method, path, body, key)
            row = {
                "scenario": name,
                "method": method,
                "path": path,
                "status": status,
                "response": response,
            }
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    server.shutdown()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/civicos-scheduler/result/local-topology.jsonl"),
    )
    parser.add_argument("--port", type=int, default=3333)
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.port))

__all__ = ["run"]
