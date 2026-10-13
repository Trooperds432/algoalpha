import sys
from pathlib import Path
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig
backend=PodmanBackend(PodmanConfig(sys.argv[1],sys.argv[2],audit_path=Path(sys.argv[3])))
backend.execute(sys.stdin.buffer.read())
