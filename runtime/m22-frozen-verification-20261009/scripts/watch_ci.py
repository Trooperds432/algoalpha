"""Local continuous checks on source/test changes. No repository publishing."""
import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprint() -> str:
    digest = hashlib.sha256()
    for base in (ROOT / "src", ROOT / "tests"):
        for path in sorted(base.rglob("*.py")):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    for path in (ROOT / "pyproject.toml", ROOT / "requirements-dev.lock"):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Check current state, then exit")
    args = parser.parse_args()
    previous = None
    while True:
        current = fingerprint()
        if current != previous:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/check.py")], cwd=ROOT)
            previous = current
            if args.once:
                return result.returncode
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
