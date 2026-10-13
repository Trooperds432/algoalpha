"""Executable worker entry point; invoked only with server-generated job IDs."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import cast

from .jobs import JobStore


def _watch_parent(parent_pid: int) -> None:
    """Exit within one polling interval after scheduler death, including on Windows."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(2)

        def watch() -> None:
            # A real process handle cannot accidentally follow a reused PID.
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0x00000102:
                os._exit(2)
    else:

        def watch() -> None:
            while os.getppid() == parent_pid:
                time.sleep(0.1)
            os._exit(2)

    threading.Thread(target=watch, name="scheduler-liveness", daemon=True).start()


def main() -> int:
    root, project, identity, parent = sys.argv[1:]
    _watch_parent(int(parent))
    from .service import ResearchService

    store = JobStore(Path(root))
    job = store.get(identity)
    if job["status"] != "running":
        return 1
    try:
        service = ResearchService(Path(root), Path(project))
        result = service.execute(
            str(job["kind"]),
            cast(dict[str, object], job["request"]),
            identity,
            lambda row: store.record(identity, row),
        )
        store.finish(identity, "succeeded", result=result)
    except Exception as exc:
        store.finish(
            identity,
            "failed",
            error={
                "type": type(exc).__name__,
                "message": str(exc)[:2000],
            },
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
