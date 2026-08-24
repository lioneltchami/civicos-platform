#!/usr/bin/env python3
"""Fail-closed local ship-range secret scan with exact reviewed exceptions only.

This tool does not access secret stores, the network, staging, or external APIs.
It scans added lines in a Git range and permits a match only when its path, line,
and SHA-256 exactly match one approved review record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = ROOT / "config" / "ship-secret-scan-allowlist.json"
DEFAULT_REPORT = ROOT / "docs" / "evidence" / "civicos-ship-secret-scan-20260823.log"

# Deliberately narrow high-signal patterns. Matches are assessed individually;
# no detector, path, filename, directory, or range is disabled by this tool.
SECRET_PATTERNS = [
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"aws_secret_access_key\s*=", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY\s*="),
    re.compile(r"STRIPE_(?:SECRET|API)_KEY\s*="),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"password\s*=\s*[^\s]{8,}", re.IGNORECASE),
]


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllowRecord:
    path: str
    line: int
    sha256: str
    triage_id: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    sha256: str
    pattern_index: int


def run_git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603, S607
    if result.returncode != 0:
        raise ScanError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def parse_allowlist(path: Path) -> list[AllowRecord]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"invalid allowlist: {exc}") from exc
    if raw.get("version") != 1 or not isinstance(raw.get("approved_false_positives"), list):
        raise ScanError("allowlist schema is invalid")
    records: list[AllowRecord] = []
    for item in raw["approved_false_positives"]:
        if not isinstance(item, dict):
            raise ScanError("allowlist entry is not an object")
        path_value = item.get("path")
        line = item.get("line")
        digest = item.get("sha256")
        triage_id = item.get("triage_id")
        if (
            not isinstance(path_value, str)
            or not isinstance(line, int)
            or line < 1
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(triage_id, str)
            or not re.fullmatch(r"SHIP-SEC-\d{3}", triage_id)
        ):
            raise ScanError("allowlist entry has invalid fields")
        pure = Path(path_value)
        if pure.is_absolute() or ".." in pure.parts or any(char in path_value for char in "*?[]"):
            raise ScanError("allowlist path must be an exact repository-relative path")
        records.append(AllowRecord(path_value, line, digest, triage_id))
    keys = [(record.path, record.line, record.sha256) for record in records]
    if len(set(keys)) != len(keys) or len({record.triage_id for record in records}) != len(records):
        raise ScanError("allowlist contains duplicate records")
    return records


def current_line_digest(record: AllowRecord) -> str:
    path = ROOT / record.path
    if not path.is_file():
        raise ScanError(f"approved path is absent: {record.path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if record.line > len(lines):
        raise ScanError(f"approved line is out of range: {record.path}:{record.line}")
    return hashlib.sha256(lines[record.line - 1].encode("utf-8")).hexdigest()


def scan_added_lines(base: str, head: str) -> list[Finding]:
    diff = run_git(
        "-c", "color.ui=false", "diff", "--no-ext-diff", "--unified=0", f"{base}..{head}", "--"
    )
    findings: list[Finding] = []
    current_path: str | None = None
    new_line: int | None = None
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            continue
        if raw_line.startswith("+++ /dev/null"):
            current_path = None
            continue
        if raw_line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            if not match:
                raise ScanError("unparseable diff hunk")
            new_line = int(match.group(1))
            continue
        if current_path is None or new_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            content = raw_line[1:]
            for index, pattern in enumerate(SECRET_PATTERNS):
                if pattern.search(content):
                    findings.append(
                        Finding(
                            current_path,
                            new_line,
                            hashlib.sha256(content.encode("utf-8")).hexdigest(),
                            index,
                        )
                    )
                    break
            new_line += 1
        elif raw_line.startswith(" "):
            new_line += 1
        elif raw_line.startswith("-"):
            continue
    return findings


def write_report(
    path: Path,
    *,
    base: str,
    head: str,
    records: list[AllowRecord],
    findings: list[Finding],
    status: str,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_key = {(record.path, record.line, record.sha256): record for record in records}
    lines = [
        f"SHIP_SECRET_SCAN={status}",
        f"BASE={base}",
        f"HEAD={head}",
        f"ALLOWLIST_RECORDS={len(records)}",
        f"FINDINGS={len(findings)}",
    ]
    for finding in findings:
        record = by_key.get((finding.path, finding.line, finding.sha256))
        if record:
            lines.append(
                f"ALLOWLIST_MATCH={record.triage_id}|{finding.path}|{finding.line}|{finding.sha256}"
            )
        else:
            lines.append(
                f"UNREVIEWED_MATCH={finding.path}|{finding.line}|{finding.sha256}|pattern-{finding.pattern_index}"
            )
    if error:
        lines.append(f"ERROR={error}")
    lines.append("NO_SECRET_VALUES_RECORDED=TRUE")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        records = parse_allowlist(args.allowlist)
        for record in records:
            if current_line_digest(record) != record.sha256:
                raise ScanError(f"approved line digest changed: {record.path}:{record.line}")
        if args.validate_only:
            write_report(
                args.report,
                base=args.base,
                head=args.head,
                records=records,
                findings=[],
                status="VALIDATE_ONLY_PASS",
            )
            return 0
        # Verify both endpoints before scanning so a missing or invalid baseline fails closed.
        run_git("rev-parse", "--verify", args.base)
        run_git("rev-parse", "--verify", args.head)
        findings = scan_added_lines(args.base, args.head)
        allowed = {(record.path, record.line, record.sha256) for record in records}
        actual = {(finding.path, finding.line, finding.sha256) for finding in findings}
        if actual != allowed:
            write_report(
                args.report,
                base=args.base,
                head=args.head,
                records=records,
                findings=findings,
                status="FAIL",
            )
            raise ScanError(
                "full-range findings do not exactly equal the reviewed allowlist records"
            )
        write_report(
            args.report,
            base=args.base,
            head=args.head,
            records=records,
            findings=findings,
            status="PASS",
        )
        return 0
    except ScanError as exc:
        try:
            if "records" in locals():
                write_report(
                    args.report,
                    base=args.base,
                    head=args.head,
                    records=records,
                    findings=locals().get("findings", []),
                    status="FAIL",
                    error=str(exc),
                )
        except OSError:
            pass
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
