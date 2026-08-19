#!/usr/bin/env python3
"""Run the deterministic, local/CI File Management pytest contract.

This runner intentionally targets only CivicOS File Management tests. It never
starts containers, calls network services, or invokes the pinned official
GovStack harness. Its artifacts are local diagnostic evidence, not conformance
or certification evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE = "apps/documents/tests"
DEFAULT_REPORT_DIR = ROOT / "test-results" / "file-management"
LAYER_MANIFEST = ROOT / "tools" / "file_management_test_layers.json"
LAYERS = ("all", "unit", "integration", "e2e")
SENSITIVE_VALUE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization|cookie)\b\s*([=:])\s*[^\s,;]+"
)
WORKSPACE = re.compile(re.escape(str(ROOT)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=LAYERS, default="all")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(os.environ.get("FILE_MANAGEMENT_REPORT_DIR", DEFAULT_REPORT_DIR)),
    )
    parser.add_argument("--collect-only", action="store_true")
    return parser.parse_args()


def load_layer_manifest() -> dict[str, list[str]]:
    try:
        payload = json.loads(LAYER_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"unable to read layer manifest: {error}") from error
    if payload.get("scope_root") != DEFAULT_SCOPE:
        raise RuntimeError("layer manifest scope_root does not match the canonical File Management scope")
    layers = payload.get("layers")
    if not isinstance(layers, dict) or set(layers) != {"unit", "integration", "e2e"}:
        raise RuntimeError("layer manifest must define exactly unit, integration, and e2e selections")
    resolved: dict[str, list[str]] = {}
    for layer, names in layers.items():
        if not isinstance(names, list) or not names:
            raise RuntimeError(f"layer {layer!r} must contain at least one test module")
        paths = [f"{DEFAULT_SCOPE}/{name}" for name in names]
        missing = [path for path in paths if not (ROOT / path).is_file()]
        if missing:
            raise RuntimeError(f"layer {layer!r} references missing test modules: {', '.join(missing)}")
        resolved[layer] = paths
    return resolved


def selector_for(layer: str, layers: dict[str, list[str]]) -> list[str]:
    if layer == "all":
        return [DEFAULT_SCOPE]
    return layers[layer]


def redact(text: str) -> str:
    text = WORKSPACE.sub("<workspace>", text)
    return SENSITIVE_VALUE.sub(r"\1\2<redacted>", text)


def run_and_capture(command: Iterable[str], *, env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout


def main() -> int:
    args = parse_args()
    try:
        layers = load_layer_manifest()
    except RuntimeError as error:
        print(f"File Management runner configuration error: {error}", file=sys.stderr)
        return 2

    report_dir = args.report_dir if args.report_dir.is_absolute() else ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    selector = selector_for(args.layer, layers)
    raw = report_dir / f"pytest-{args.layer}.log"
    collection_log = report_dir / f"collection-{args.layer}.log"
    junit = report_dir / f"junit-{args.layer}.xml"
    coverage = report_dir / f"coverage-{args.layer}.xml"
    metadata = report_dir / f"metadata-{args.layer}.json"

    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    base_command = [sys.executable, "-m", "pytest", *selector, "--color=no"]
    collect_code, collect_output = run_and_capture([*base_command, "--collect-only", "-q"], env=env)
    collection_log.write_text(redact(collect_output), encoding="utf-8")
    if collect_code != 0:
        raw.write_text(
            redact(f"COLLECTION COMMAND: {' '.join(base_command)} --collect-only -q\n\n{collect_output}"),
            encoding="utf-8",
        )
        print(f"File Management collection failed; see {collection_log}", file=sys.stderr)
        return collect_code

    match = re.search(r"(\d+)\s+tests? collected", collect_output)
    collected = int(match.group(1)) if match else 0
    if collected == 0:
        raw.write_text(
            redact(f"ERROR: selector produced zero tests\nCOMMAND: {' '.join(base_command)}\n\n{collect_output}"),
            encoding="utf-8",
        )
        print(f"zero tests collected for layer={args.layer}; see {collection_log}", file=sys.stderr)
        return 5

    metadata.write_text(
        json.dumps(
            {
                "runner": "local/CI File Management candidate runner",
                "layer": args.layer,
                "selector": selector,
                "collected": collected,
                "django_settings_module": env["DJANGO_SETTINGS_MODULE"],
                "official_harness_executed": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.collect_only:
        raw.write_text(
            redact(f"COLLECTION COMMAND: {' '.join(base_command)} --collect-only -q\n\n{collect_output}"),
            encoding="utf-8",
        )
        return 0

    command = [
        *base_command,
        "--junitxml",
        str(junit),
        "--cov=apps.documents",
        f"--cov-report=xml:{coverage}",
        "--cov-report=term-missing",
    ]
    # The repository's global 80% threshold remains enforced for the full File
    # Management run. A single isolated layer cannot represent total coverage,
    # so it emits coverage evidence without turning a passing layer into a
    # false failure solely because other layers are intentionally excluded.
    if args.layer != "all":
        command.append("--cov-fail-under=0")
    exit_code, output = run_and_capture(command, env=env)
    raw.write_text(
        redact(
            f"COMMAND: {' '.join(command)}\nLAYER: {args.layer}\nCOLLECTED: {collected}\n\n{output}\nEXIT_CODE: {exit_code}\n"
        ),
        encoding="utf-8",
    )
    if not junit.exists():
        print(f"runner did not produce expected JUnit report: {junit}", file=sys.stderr)
        return exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
