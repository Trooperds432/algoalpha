"""Bounded trusted WSL transport owner, separate from strategy confinement.

This component does not enable admission. Its guest does nothing except wait;
after owner death it retains transport until its independently enforced expiry.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from quant_terminal.sandbox.podman import (
    SandboxUnavailable,
    _client_environment,
    _parent_token,
)

# EOF must not end the guest early: a dead host guardian must still leave a
# bounded cleanup window. Only the trusted cleanup acknowledgement ends it.
_GUEST = r"""
import os,queue,sys,threading,time
seconds=int(sys.argv[1])
if not 1<=seconds<=900: sys.exit(1)
messages=queue.Queue()
def read():
 message=b''
 while len(message)<8 and not message.endswith(b'\n'):
  part=os.read(0,8-len(message))
  if not part: break
  message+=part
 messages.put(message)
threading.Thread(target=read,daemon=True).start()
deadline=time.monotonic()+seconds
print('READY',flush=True)
try: message=messages.get(timeout=max(0,deadline-time.monotonic()))
except queue.Empty: message=b''
if message==b'CLEAN\n': sys.exit(0)
time.sleep(max(0,deadline-time.monotonic()))
sys.exit(2)
"""

# Parent identity is retained using a real process HANDLE/pidfd, not PID polling.
# A lost owner/control pipe does not send CLEAN to the guest. No strategy bytes,
# runtime paths, audit secrets, shell commands or request arguments reach it.
_GUARDIAN = r"""
import ctypes,json,os,queue,select,subprocess,sys,threading,time
parent=int(sys.argv[1]);token=sys.argv[2];command=json.loads(sys.argv[3])
seconds=int(sys.argv[4]);handle=None;guest=None
expiry=time.monotonic()+seconds+10
try:
 if not 1<=seconds<=900: raise RuntimeError('lifetime')
 if os.name=='nt':
  from ctypes import wintypes
  kernel=ctypes.WinDLL('kernel32',use_last_error=True)
  kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
  kernel.OpenProcess.restype=wintypes.HANDLE
  kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
  kernel.WaitForSingleObject.restype=wintypes.DWORD
  kernel.GetProcessTimes.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.FILETIME),
   ctypes.POINTER(wintypes.FILETIME),ctypes.POINTER(wintypes.FILETIME),
   ctypes.POINTER(wintypes.FILETIME))
  kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
  handle=kernel.OpenProcess(0x100000|0x1000,False,parent)
  if not handle: raise RuntimeError('parent_handle')
  times=[wintypes.FILETIME() for _ in range(4)]
  if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):
   raise RuntimeError('parent_times')
  creation=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
  if str(creation)!=token: raise RuntimeError('parent_identity')
  def dead():
   result=kernel.WaitForSingleObject(handle,0)
   if result not in (0,258): raise RuntimeError('parent_wait')
   return result==0
 else:
  handle=os.pidfd_open(parent)
  start=open('/proc/'+str(parent)+'/stat').read().rsplit(')',1)[1].split()[19]
  if start!=token: raise RuntimeError('parent_identity')
  poll=select.poll();poll.register(handle,select.POLLIN)
  def dead(): return bool(poll.poll(0))
 if dead(): raise RuntimeError('parent_dead')
 flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
 guest=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
  stderr=subprocess.DEVNULL,creationflags=flags)
 ready=queue.Queue()
 def guest_ready(): ready.put(guest.stdout.readline(16))
 threading.Thread(target=guest_ready,daemon=True).start()
 if ready.get(timeout=5) not in (b'READY\n',b'READY\r\n'):
  raise RuntimeError('guest_not_ready')
 if guest.poll() is not None: raise RuntimeError('guest_dead')
 controls=queue.Queue()
 def control():
  message=b''
  while len(message)<8 and not message.endswith(b'\n'):
   part=os.read(0,8-len(message))
   if not part: break
   message+=part
  controls.put(message)
 threading.Thread(target=control,daemon=True).start()
 print('READY',flush=True)
 deadline=time.monotonic()+seconds+5
 owner_dead=False
 while guest.poll() is None:
  owner_dead=owner_dead or dead()
  try: message=controls.get_nowait()
  except queue.Empty: message=None
  if message==b'CLEAN\n' and not owner_dead:
   guest.stdin.write(b'CLEAN\n');guest.stdin.flush()
   guest.wait(timeout=5)
   if guest.returncode!=0: raise RuntimeError('guest_close_failed')
   sys.exit(0)
  if time.monotonic()>=deadline: raise RuntimeError('guest_expiry_failed')
  time.sleep(0.05)
 sys.exit(3)
except Exception:
 # Exit 2 is a handled failure only if the finally block completes reaping.
 # Any exception in finally overrides it with an unverified nonzero exit.
 sys.exit(2)
finally:
 if guest is not None:
  if guest.stdin: guest.stdin.close()
  # Closing the pipe leaves the fixed guest waiting only until its own expiry.
  # Stop only this transport client if it fails to honour its bound.
  if guest.poll() is None:
   try: guest.wait(timeout=max(0,expiry-time.monotonic()))
   except subprocess.TimeoutExpired:
    guest.kill();guest.wait(timeout=3)
  # Reap before closing the buffered reader: a concurrent readiness readline
  # can otherwise hold its lock forever when a transport never emits READY.
  if guest.stdout: guest.stdout.close()
 if handle is not None:
  if os.name=='nt': kernel.CloseHandle(handle)
  else: os.close(handle)
"""


def _transport_command(distribution: str, seconds: int) -> tuple[str, ...]:
    if os.name != "nt":
        raise SandboxUnavailable("keeper_windows_required")
    executable = shutil.which("wsl.exe")
    if executable is None:
        raise SandboxUnavailable("keeper_wsl_missing")
    return (
        executable,
        "--distribution",
        distribution,
        "--exec",
        "python3",
        "-I",
        "-S",
        "-u",
        "-c",
        _GUEST,
        str(seconds),
    )


class WslKeeper:
    """One-use internal owner; caller must prove sandbox cleanup before closing.

    Failed or expired owners cannot be restarted or used for admission. This is
    an availability prerequisite; it does not attest isolation controls.
    """

    def __init__(
        self, *, distribution: str = "podman-algoalpha-m16", lifetime_seconds: int = 300
    ) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", distribution) is None:
            raise ValueError("invalid WSL distribution")
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 900:
            raise ValueError("keeper lifetime must be 1 to 900 seconds")
        self.distribution = distribution
        self.lifetime_seconds = lifetime_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._used = False
        self._closed = False
        self._ready = False
        self._deadline = 0.0

    def start(self) -> None:
        if self._used:
            raise SandboxUnavailable("keeper_already_used")
        self._used = True
        self._deadline = time.monotonic() + self.lifetime_seconds
        command = _transport_command(self.distribution, self.lifetime_seconds)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                (
                    getattr(sys, "_base_executable", sys.executable),
                    "-I",
                    "-S",
                    "-u",
                    "-c",
                    _GUARDIAN,
                    str(os.getpid()),
                    _parent_token(),
                    json.dumps(command),
                    str(self.lifetime_seconds),
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_client_environment(),
                creationflags=flags,
            )
        except OSError as exc:
            raise SandboxUnavailable("keeper_launch_failed") from exc
        process = self._process
        ready: list[bytes] = []

        def read_ready() -> None:
            if process.stdout is not None:
                ready.append(process.stdout.readline(16))
                process.stdout.close()

        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        reader.join(timeout=6)
        if (
            reader.is_alive()
            or ready not in ([b"READY\n"], [b"READY\r\n"])
            or process.poll() is not None
        ):
            if process.stdin is not None:
                process.stdin.close()
            # Retain the guardian through its bounded startup cleanup window.
            # Killing it here could strand a WSL client still in startup.
            raise SandboxUnavailable("keeper_not_ready")
        self._ready = True

    def ensure_available(self) -> None:
        if self._process is None or self._closed or not self._ready:
            raise SandboxUnavailable("keeper_not_running")
        if self._process.poll() is not None or time.monotonic() >= self._deadline:
            raise SandboxUnavailable("keeper_transport_lost")

    def reserve(self, seconds: float) -> None:
        self.ensure_available()
        if seconds <= 0 or not seconds < self._deadline - time.monotonic():
            raise SandboxUnavailable("keeper_lifetime_insufficient")

    def close(self, *, cleanup_confirmed: bool) -> None:
        if cleanup_confirmed is not True:
            raise SandboxUnavailable("keeper_cleanup_unconfirmed")
        if self._closed:
            return
        process = self._process
        if process is None:
            self._closed = True
            self._ready = False
            return
        try:
            if process.poll() is None:
                if self._ready and process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"CLEAN\n")
                        process.stdin.flush()
                    except OSError:
                        pass  # Lost transport still has its independent expiry.
                    process.stdin.close()
                # Failed startup may still own a client. Wait for the guardian's
                # original bound; never kill a guardian and strand its transport.
                process.wait(timeout=max(6, self._deadline + 13 - time.monotonic()))
            # The fixed guardian emits 0 for acknowledged closure, 2 for a
            # handled failure after its final reap, and 3 for reaped expiry.
            # Other exits (including failure in finally) provide no proof.
            if process.returncode not in (0, 2, 3):
                raise SandboxUnavailable("keeper_cleanup_unverified")
            self._closed = True
            self._ready = False
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxUnavailable("keeper_close_failed") from exc
