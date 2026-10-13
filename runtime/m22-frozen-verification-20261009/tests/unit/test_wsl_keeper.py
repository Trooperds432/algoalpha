"""Real trusted-process lifecycle checks; no hostile source or containers."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from quant_terminal.sandbox import wsl_keeper
from quant_terminal.sandbox.podman import SandboxUnavailable


@pytest.fixture
def local_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    # Substitute only WSL transport, retaining the real guest wait program and
    # independent guardian. This is not evidence of WSL persistence.
    monkeypatch.setattr(
        wsl_keeper,
        "_transport_command",
        lambda distribution, seconds: (
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-S",
            "-u",
            "-c",
            wsl_keeper._GUEST,
            str(seconds),
        ),
    )


def test_keeper_requires_cleanup_before_close(local_transport: None) -> None:
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=5)
    keeper.start()
    try:
        keeper.ensure_available()
        with pytest.raises(SandboxUnavailable, match="keeper_cleanup_unconfirmed"):
            keeper.close(cleanup_confirmed=False)
        keeper.ensure_available()
    finally:
        keeper.close(cleanup_confirmed=True)
    with pytest.raises(SandboxUnavailable, match="keeper_not_running"):
        keeper.ensure_available()


def test_keeper_expires_and_latches_unavailable(local_transport: None) -> None:
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    keeper.start()
    time.sleep(1.5)
    with pytest.raises(SandboxUnavailable, match="keeper_transport_lost"):
        keeper.ensure_available()
    with pytest.raises(SandboxUnavailable):
        keeper.start()


def test_keeper_rejects_wrong_parent_identity(
    local_transport: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl_keeper, "_parent_token", lambda: "0")
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    with pytest.raises(SandboxUnavailable, match="keeper_not_ready"):
        keeper.start()


def test_keeper_early_transport_exit_blocks_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wsl_keeper,
        "_transport_command",
        lambda distribution, seconds: (
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-c",
            "pass",
        ),
    )
    with pytest.raises(SandboxUnavailable, match="keeper_not_ready"):
        wsl_keeper.WslKeeper(lifetime_seconds=1).start()


@pytest.mark.parametrize("lifetime", [0, -1, 901, True, 1.5])
def test_keeper_rejects_invalid_lifetime(lifetime: int) -> None:
    with pytest.raises(ValueError):
        wsl_keeper.WslKeeper(lifetime_seconds=lifetime)


@pytest.mark.parametrize("distribution", ["bad distro", "-option", "../other", "a;shutdown"])
def test_keeper_rejects_invalid_distribution(distribution: str) -> None:
    with pytest.raises(ValueError):
        wsl_keeper.WslKeeper(distribution=distribution)


def test_guest_holds_after_control_pipe_death_until_deadline() -> None:
    guest = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-S",
            "-u",
            "-c",
            wsl_keeper._GUEST,
            "1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert guest.stdout is not None and guest.stdin is not None
        assert guest.stdout.readline().strip() == b"READY"
        guest.stdin.close()
        time.sleep(0.2)
        assert guest.poll() is None
        assert guest.wait(timeout=3) == 2
    finally:
        if guest.poll() is None:
            guest.kill()
            guest.wait(timeout=3)


def test_silent_startup_is_rejected_and_guardian_reaps_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wsl_keeper,
        "_transport_command",
        lambda distribution, seconds: (
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-c",
            "import time; time.sleep(30)",
        ),
    )
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    with pytest.raises(SandboxUnavailable, match="keeper_not_ready"):
        keeper.start()
    with pytest.raises(SandboxUnavailable, match="keeper_not_running"):
        keeper.ensure_available()
    assert keeper._process is not None
    assert keeper._process.wait(timeout=8) != 0


def test_unknown_guardian_exit_does_not_prove_cleanup() -> None:
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    keeper._process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-c", "raise SystemExit(1)"]
    )
    keeper._process.wait(timeout=3)
    with pytest.raises(SandboxUnavailable, match="keeper_cleanup_unverified"):
        keeper.close(cleanup_confirmed=True)
    assert not keeper._closed


def test_handled_startup_failure_can_reap_but_never_become_available(
    local_transport: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wsl_keeper, "_parent_token", lambda: "0")
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    with pytest.raises(SandboxUnavailable, match="keeper_not_ready"):
        keeper.start()
    keeper.close(cleanup_confirmed=True)
    assert keeper._closed
    with pytest.raises(SandboxUnavailable, match="keeper_not_running"):
        keeper.ensure_available()


@pytest.mark.skipif(sys.platform != "win32", reason="retained Windows process HANDLE test")
def test_guardian_retains_transport_after_abrupt_parent_death() -> None:
    import ctypes
    from ctypes import wintypes

    helper = r"""
import sys,time
sys.path.insert(0,sys.argv[1])
from quant_terminal.sandbox import wsl_keeper as w
w._transport_command=lambda distribution,seconds: (
 sys.executable,'-I','-S','-u','-c',w._GUEST,str(seconds))
k=w.WslKeeper(lifetime_seconds=3)
k.start()
print(k._process.pid,flush=True)
time.sleep(20)
"""
    parent = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            "-u",
            "-c",
            helper,
            str(Path(__file__).parents[2] / "src"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    try:
        assert parent.stdout is not None
        pid = int(parent.stdout.readline())
        handle = kernel.OpenProcess(0x100000, False, pid)
        assert handle
        parent.kill()
        parent.wait(timeout=3)
        assert kernel.WaitForSingleObject(handle, 300) == 258
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if handle:
            kernel.CloseHandle(handle)


def test_expired_keeper_can_teardown_after_cleanup(local_transport: None) -> None:
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=1)
    keeper.start()
    time.sleep(1.5)
    keeper.close(cleanup_confirmed=True)
    with pytest.raises(SandboxUnavailable):
        keeper.ensure_available()


def test_reserve_does_not_renew_lifetime(local_transport: None) -> None:
    keeper = wsl_keeper.WslKeeper(lifetime_seconds=5)
    keeper.start()
    deadline = keeper._deadline
    try:
        keeper.reserve(1)
        with pytest.raises(SandboxUnavailable, match="keeper_lifetime_insufficient"):
            keeper.reserve(5)
        assert keeper._deadline == deadline
    finally:
        keeper.close(cleanup_confirmed=True)
