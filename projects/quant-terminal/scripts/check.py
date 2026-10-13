"""Run the same bounded checks locally and in CI; retain every result."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = [
    [sys.executable, "-m", "ruff", "check", "src", "tests"],
    [sys.executable, "-m", "ruff", "format", "--check", "src", "tests"],
    [sys.executable, "-m", "mypy", "src"],
    [sys.executable, "-m", "pytest", "-q"],
    [sys.executable, "-m", "pip", "check"],
]


def main() -> int:
    rows = []
    for command in COMMANDS:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        rows.append({"command": command, "exit_code": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ("src", "tests") for p in sorted((ROOT / folder).rglob("*.py"))}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = ROOT / "docs" / "validation" / f"checks-{stamp}.json"
    target.write_text(json.dumps({"at": stamp, "python": sys.version, "files": hashes,
                                 "results": rows}, indent=2), encoding="utf8")
    print(f"Evidence: {target.relative_to(ROOT)}")
    return int(any(row["exit_code"] != 0 for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
