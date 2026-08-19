#!/usr/bin/env python3
"""Validate local deterministic evidence only; never labels external/provider evidence."""
from __future__ import annotations
import argparse, json, pathlib, sys
REQUIRED = {"scope", "tests", "external_deferred"}

def validate(path: pathlib.Path) -> list[str]:
    data = json.loads(path.read_text())
    errors = sorted(REQUIRED - data.keys())
    if data.get("scope") != "repository-only-deterministic": errors.append("scope must be repository-only-deterministic")
    if not isinstance(data.get("tests"), list) or not data["tests"]: errors.append("tests must be non-empty list")
    if not data.get("external_deferred"): errors.append("external_deferred must be non-empty")
    return errors

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("manifest", type=pathlib.Path); args = p.parse_args()
    failures = validate(args.manifest)
    if failures: print("invalid: " + "; ".join(failures)); sys.exit(1)
    print("valid: repository-only deterministic evidence manifest")
