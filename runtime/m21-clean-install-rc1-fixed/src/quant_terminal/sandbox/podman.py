"""Constrained Podman supervisor with a fixed effective-control preflight.

Technical availability never substitutes for the public hostile-workload gate.
Conmon enforces the container wall lifetime independently of this client.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO, Final

from quant_terminal.sandbox.protocol import (
    ProtocolError,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    decode_request,
    decode_response,
)

_IMAGE: Final = re.compile(r"(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}\Z")
_VM_PATH: Final = re.compile(r"/[A-Za-z0-9_./-]+\Z")
_CONTAINER_ID: Final = re.compile(r"[0-9a-f]{64}\Z")
_OWNER_LABEL: Final = "algoalpha.owner=quant-terminal-m16"
_ACTIVE_LEASES: set[Path] = set()
_PROCESS_UNHEALTHY: set[Path] = set()
_HEALTH_LOCK = threading.Lock()


class SandboxUnavailable(RuntimeError):
    """A required isolation control is absent or unverified."""


@dataclass(frozen=True, slots=True)
class PodmanConfig:
    image: str
    seccomp_profile: str
    executable: str = "podman"
    audit_path: Path | None = None
    wsl_distribution: str = "podman-algoalpha-m16"

    def __post_init__(self) -> None:
        if _IMAGE.fullmatch(self.image) is None:
            raise ValueError("a digest-pinned OCI image is required")
        if (
            _VM_PATH.fullmatch(self.seccomp_profile) is None
            or ".." in PurePosixPath(self.seccomp_profile).parts
        ):
            raise ValueError("an absolute Linux VM seccomp profile path is required")
        if self.executable != "podman":
            raise ValueError("the Podman executable must be resolved by the host probe")
        if self.audit_path is not None and not self.audit_path.is_absolute():
            raise ValueError("audit path must be absolute")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.wsl_distribution) is None:
            raise ValueError("invalid WSL distribution")


@dataclass(frozen=True, slots=True)
class PodmanCapability:
    available: bool
    reason: str
    engine_version: str | None = None


def command_for(
    config: PodmanConfig,
    budget: SandboxBudget,
    cidfile: Path,
    job_id: str,
) -> tuple[str, ...]:
    """Return an argument array; never interpolate untrusted source into a shell."""
    if not cidfile.is_absolute():
        raise ValueError("cidfile must be absolute")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id) is None:
        raise ValueError("invalid job ID")
    # This backend deliberately offers no writable scratch. Podman's CLI does
    # not enforce the declared inode quota, so positive scratch must reject.
    if budget.scratch_bytes != 0 or budget.scratch_files != 0:
        raise SandboxUnavailable("scratch_limit_unenforceable")
    if budget.cpu_seconds > budget.wall_seconds:
        raise SandboxUnavailable("cpu_budget_exceeds_wall_budget")
    cpu_fraction = math.floor(budget.cpu_seconds / budget.wall_seconds * 10_000) / 10_000
    if cpu_fraction < 0.01:
        raise SandboxUnavailable("cpu_quota_unenforceable")
    return (
        config.executable,
        "run",
        "--interactive",
        "--rm",
        "--pull=never",
        "--restart=no",
        f"--cidfile={cidfile}",
        f"--label={_OWNER_LABEL}",
        f"--label=algoalpha.job={job_id}",
        "--network=none",
        "--read-only",
        "--read-only-tmpfs=false",
        "--log-driver=none",
        "--http-proxy=false",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--security-opt=seccomp={config.seccomp_profile}",
        "--user=65532:65532",
        "--userns=auto",
        "--ipc=none",
        "--pid=private",
        "--uts=private",
        f"--pids-limit={budget.process_count}",
        f"--memory={budget.memory_bytes}",
        f"--memory-swap={budget.memory_bytes}",
        f"--cpus={cpu_fraction:.4f}",
        f"--ulimit=cpu={budget.cpu_seconds}:{budget.cpu_seconds}",
        f"--ulimit=nofile={budget.open_files}:{budget.open_files}",
        f"--ulimit=fsize={budget.output_bytes}:{budget.output_bytes}",
        "--cgroupns=private",
        "--no-hosts",
        f"--timeout={budget.wall_seconds}",
        "--workdir=/tmp",
        "--env=PYTHONNOUSERSITE=1",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint=/usr/local/bin/python",
        config.image,
        "-I",
        "-S",
        "/opt/algoalpha/strategy_worker.py",
    )


def read_container_id(cidfile: Path) -> str | None:
    """Read a launcher-owned ID without accepting arbitrary command operands."""
    try:
        value = cidfile.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    return value if _CONTAINER_ID.fullmatch(value) is not None else None


def cleanup_command(container_id: str) -> tuple[str, ...]:
    """Target only this project's container ID, guarded by its owner label."""
    if _CONTAINER_ID.fullmatch(container_id) is None:
        raise ValueError("invalid container ID")
    return (
        "podman",
        "rm",
        "--force",
        "--ignore",
        f"--filter=label={_OWNER_LABEL}",
        f"--filter=id=^{container_id}$",
    )


# This fixed program contains no request source and executes before admission.
_CONTROL_PROBE = """import os,json,pathlib,hashlib,sys,socket
p=pathlib.Path
status=dict(x.split(':',1) for x in p('/proc/self/status').read_text().splitlines() if ':' in x)
denied=True
for family in (socket.AF_INET,socket.AF_INET6,socket.AF_UNIX):
 try:
  s=socket.socket(family);s.close();denied=False
 except PermissionError: pass
out={'uid':os.getuid(),'seccomp':status['Seccomp'].strip(),
 'nnp':status['NoNewPrivs'].strip(),'caps':status['CapEff'].strip(),
 'memory':p('/sys/fs/cgroup/memory.max').read_text().strip(),
 'swap':p('/sys/fs/cgroup/memory.swap.max').read_text().strip(),
 'pids':p('/sys/fs/cgroup/pids.max').read_text().strip(),
 'cpu':p('/sys/fs/cgroup/cpu.max').read_text().strip(),
 'net':sorted(x.name for x in p('/sys/class/net').iterdir()),'sockets_denied':denied,
 'python_version':sys.version.split()[0],
 'image_files':{f:hashlib.sha256(p('/opt/algoalpha/'+f).read_bytes()).hexdigest()
  for f in ('strategy_worker.py','quant_terminal/sandbox/protocol.py')}}
print(json.dumps(out))
"""
_PROBE_BUDGET = SandboxBudget(10, 5, 64_000_000, 4, 32, 0, 0, 4096)

# A trusted, detached process owns a retained HANDLE (Windows) or pidfd (Linux).
# It never receives source, data, strategy output or credentials.
_WATCHDOG = r"""
import ctypes,datetime,json,os,select,subprocess,sys,time
parent=int(sys.argv[1]);token=sys.argv[2];cid=sys.argv[3];podman=sys.argv[4]
audit=sys.argv[5];health=sys.argv[6];payload=json.loads(sys.argv[7]);wall=int(sys.argv[8])
lease=sys.argv[9]
def unhealthy():
 try:
  with open(health,'ab') as f:
   f.write(b'watchdog_cleanup_unhealthy\n');f.flush();os.fsync(f.fileno())
 except OSError: pass
def event(name,outcome,reason):
 item=dict(payload,event=name,outcome=outcome,reason=reason,
  timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),watchdog_pid=os.getpid())
 try:
  with open(audit,'ab') as f:
   f.write(json.dumps(item,sort_keys=True).encode()+b'\n');f.flush();os.fsync(f.fileno())
 except OSError:
  unhealthy();raise
try:
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
  handle=kernel.OpenProcess(0x100000|0x1000,False,parent)
  if not handle: raise RuntimeError('parent_handle_missing')
  times=[wintypes.FILETIME() for _ in range(4)]
  if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):
   raise RuntimeError('parent_identity_missing')
  creation=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
  if str(creation)!=token or kernel.WaitForSingleObject(handle,0)!=258:
   raise RuntimeError('parent_identity_mismatch')
  def dead():
   result=kernel.WaitForSingleObject(handle,100)
   if result not in (0,258): raise RuntimeError('parent_wait_failed')
   return result==0
 else:
  handle=os.pidfd_open(parent)
  start=open('/proc/'+str(parent)+'/stat').read().rsplit(')',1)[1].split()[19]
  if start!=token: raise RuntimeError('parent_identity_mismatch')
  poll=select.poll();poll.register(handle,select.POLLIN)
  def dead(): return bool(poll.poll(100))
 event('watchdog_ready','ok','retained_parent_handle')
 sys.stdout.write('READY\n');sys.stdout.flush();sys.stdout.close()
 deadline=time.monotonic()+wall+30
 while not dead():
  if time.monotonic()>deadline:
   # Main supervision should have removed us by now. Fail closed.
   unhealthy();event('watchdog_failure','failed','watchdog_lifetime_exceeded');sys.exit(1)
 event('watchdog_parent_death','interrupted','scheduler_death')
 owner='--filter=label=algoalpha.owner=quant-terminal-m16'
 subprocess.run((podman,'rm','--force','--ignore',owner,'--filter=id=^'+cid+'$'),
  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
  timeout=15,check=True)
 r=subprocess.run((podman,'ps','-a','--no-trunc',owner,'--filter=id='+cid,'--format={{.ID}}'),
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,check=True)
 if r.stdout.strip(): raise RuntimeError('container_remains')
 event('cleanup','ok','scheduler_death')
 event('termination','interrupted','scheduler_death')
 os.unlink(lease)
except BaseException:
 unhealthy()
 try: event('watchdog_failure','failed','watchdog_failed')
 except BaseException: pass
 sys.exit(1)
"""


def _client_environment() -> dict[str, str]:
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "HOMEDRIVE",
        "HOMEPATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "LANG",
        "LC_ALL",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _parent_token() -> str:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(
            kernel.GetCurrentProcess(), *[ctypes.byref(t) for t in times]
        ):
            raise SandboxUnavailable("parent_identity_unverified")
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    try:
        return Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise SandboxUnavailable("parent_identity_unverified") from exc


class PodmanBackend:
    """Trusted supervisor; public admission also requires the separate hostile gate."""

    def __init__(self, config: PodmanConfig) -> None:
        self.config = config
        self._unhealthy = False
        self._lock = threading.Lock()
        self._version: str | None = None
        self._image_id: str | None = None
        self._image_provenance: dict[str, Any] = {}
        self._image_env: set[str] = set()
        self._verified = False

    def identity(self) -> str:
        if not self._verified or self._is_unhealthy():
            capability = self.probe()
            if not capability.available:
                raise SandboxUnavailable(capability.reason)
        return json.dumps(
            {"backend": "podman", "engine_version": self._version, **self._image_provenance},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _profile_digest(self) -> str:
        if os.name == "nt":
            executable = shutil.which("wsl.exe")
            if executable is None:
                raise SandboxUnavailable("seccomp_digest_unverified")
            result, code = self._stream(
                (
                    executable,
                    "-d",
                    self.config.wsl_distribution,
                    "--",
                    "sha256sum",
                    self.config.seccomp_profile,
                ),
                b"",
                1024,
                5,
                None,
            )
            digest = result.decode("ascii").split()[0] if result.strip() else ""
            if code != 0 or _CONTAINER_ID.fullmatch(digest) is None:
                raise SandboxUnavailable("seccomp_digest_unverified")
            return digest
        import hashlib

        return hashlib.sha256(Path(self.config.seccomp_profile).read_bytes()).hexdigest()

    def _mark_unhealthy(self) -> None:
        self._unhealthy = True
        if self.config.audit_path is not None:
            with _HEALTH_LOCK:
                _PROCESS_UNHEALTHY.add(self.config.audit_path)
            try:
                with self.config.audit_path.with_suffix(".unhealthy").open("ab") as stream:
                    stream.write(b"cleanup_unhealthy\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise SandboxUnavailable("health_write_failed") from exc

    def _leases(self) -> tuple[Path, ...]:
        path = self.config.audit_path
        if path is None:
            return ()
        return tuple(path.parent.glob(path.name + ".*.pending"))

    def _begin_lease(self) -> Path:
        path = self.config.audit_path
        if path is None:
            raise SandboxUnavailable("audit_unavailable")
        lease = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
        value = {"scheduler_pid": os.getpid(), "scheduler_token": _parent_token()}
        try:
            with lease.open("xb") as stream:
                stream.write(json.dumps(value).encode())
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self._unhealthy = True
            raise SandboxUnavailable("health_preflight_failed") from exc
        with _HEALTH_LOCK:
            _ACTIVE_LEASES.add(lease)
        return lease

    def _is_unhealthy(self) -> bool:
        if self._unhealthy:
            return True
        try:
            with _HEALTH_LOCK:
                if self.config.audit_path in _PROCESS_UNHEALTHY:
                    return True
                active = frozenset(_ACTIVE_LEASES)
            if (
                self.config.audit_path is not None
                and self.config.audit_path.with_suffix(".unhealthy").exists()
            ):
                return True
            for lease in self._leases():
                if lease.stat().st_size > 512:
                    return True
                value = json.loads(lease.read_bytes())
                if lease not in active or value != {
                    "scheduler_pid": os.getpid(),
                    "scheduler_token": _parent_token(),
                }:
                    return True
            return False
        except (OSError, ValueError, SandboxUnavailable):
            return True

    def _executable(self) -> str:
        found = shutil.which(self.config.executable)
        if found is None and os.name == "nt":
            candidate = (
                Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                / "RedHat/Podman/podman.exe"
            )
            if candidate.is_file():
                found = str(candidate)
        if found is None:
            raise SandboxUnavailable("podman_missing")
        return found

    def _run(self, args: tuple[str, ...], limit: int = 64_000) -> bytes:
        result, code = self._stream((self._executable(), *args), b"", limit, 10, None)
        if code != 0:
            raise SandboxUnavailable("podman_command_failed")
        return result

    def _stream(
        self,
        args: tuple[str, ...],
        blob: bytes,
        limit: int,
        wall: float,
        cancel: threading.Event | None,
        watcher: subprocess.Popen[bytes] | None = None,
    ) -> tuple[bytes, int]:
        """Drain both pipes without retaining more than the combined output budget."""
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_client_environment(),
            )
        except OSError as exc:
            raise SandboxUnavailable("podman_engine_unreachable") from exc
        output = bytearray()
        overflow = threading.Event()
        pipe_error = threading.Event()
        mutex = threading.Lock()

        def read(pipe: BinaryIO | None, keep: bool) -> None:
            if pipe is None:
                return
            try:
                while chunk := pipe.read(4096):
                    with mutex:
                        nonlocal_count[0] += len(chunk)
                        if nonlocal_count[0] > limit:
                            overflow.set()
                        elif keep:
                            output.extend(chunk)
            except OSError:
                pipe_error.set()
            finally:
                pipe.close()

        def write() -> None:
            if process.stdin is not None:
                try:
                    process.stdin.write(blob)
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        nonlocal_count = [0]
        threads = [
            threading.Thread(target=read, args=(process.stdout, True), daemon=True),
            threading.Thread(target=read, args=(process.stderr, False), daemon=True),
            threading.Thread(target=write, daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + wall
        reason = ""
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                reason = "cancelled"
            elif overflow.is_set():
                reason = "output_limit"
            elif watcher is not None and watcher.poll() is not None:
                reason = "watchdog_exit"
            elif time.monotonic() >= deadline:
                reason = "wall_timeout"
            if reason:
                process.kill()
                break
            time.sleep(0.01)
        try:
            code = process.wait(timeout=3)
        except subprocess.TimeoutExpired as exc:
            self._mark_unhealthy()
            raise SandboxUnavailable("client_cleanup_failed") from exc
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            self._mark_unhealthy()
            raise SandboxUnavailable("pipe_cleanup_failed")
        if reason:
            raise SandboxUnavailable(reason)
        if overflow.is_set():
            raise SandboxUnavailable("output_limit")
        if pipe_error.is_set():
            raise SandboxUnavailable("pipe_failed")
        return bytes(output), code

    def _payload(
        self, event: str, request: SandboxRequest | None = None, outcome: str = "", reason: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "backend": "podman",
            "version": self._version,
            "image": self.config.image,
            "outcome": outcome,
            "reason": reason,
            "image_provenance": self._image_provenance,
        }
        if request is not None:
            payload.update(
                job_id=request.job_id,
                source_sha256=request.source_sha256,
                data_sha256=request.data_sha256,
                seed=request.seed,
                dependencies=dict(request.dependencies),
                budget=asdict(request.budget),
            )
        return payload

    def _audit(
        self, event: str, request: SandboxRequest | None = None, outcome: str = "", reason: str = ""
    ) -> None:
        path = self.config.audit_path
        if path is None:
            raise SandboxUnavailable("audit_unavailable")
        payload = self._payload(event, request, outcome, reason)
        try:
            with path.open("ab") as stream:
                stream.write(json.dumps(payload, sort_keys=True).encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise SandboxUnavailable("audit_write_failed") from exc

    def _watchdog(self, cid: str, request: SandboxRequest, lease: Path) -> subprocess.Popen[bytes]:
        path = self.config.audit_path
        if path is None:
            raise SandboxUnavailable("audit_unavailable")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        args = (
            sys.executable,
            "-I",
            "-S",
            "-u",
            "-c",
            _WATCHDOG,
            str(os.getpid()),
            _parent_token(),
            cid,
            self._executable(),
            str(path),
            str(path.with_suffix(".unhealthy")),
            json.dumps(self._payload("watchdog", request)),
            str(request.budget.wall_seconds),
            str(lease),
        )
        try:
            watcher = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_client_environment(),
                creationflags=flags,
            )
        except OSError as exc:
            raise SandboxUnavailable("watchdog_unavailable") from exc
        ready: list[bytes] = []

        def read_ready() -> None:
            if watcher.stdout is not None:
                ready.append(watcher.stdout.readline(16))
                watcher.stdout.close()

        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        reader.join(timeout=3)
        if (
            reader.is_alive()
            or ready not in ([b"READY\n"], [b"READY\r\n"])
            or watcher.poll() is not None
        ):
            self._stop_watchdog(watcher)
            reader.join(timeout=1)
            raise SandboxUnavailable("watchdog_not_ready")
        return watcher

    def _stop_watchdog(self, watcher: subprocess.Popen[bytes]) -> None:
        try:
            if watcher.poll() is not None:
                self._mark_unhealthy()
                raise SandboxUnavailable("watchdog_exit")
            watcher.terminate()
            watcher.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._mark_unhealthy()
            raise SandboxUnavailable("watchdog_cleanup_failed") from exc

    def _cleanup(self, cid: str) -> None:
        try:
            self._run(cleanup_command(cid)[1:])
            listing = self._run(
                (
                    "ps",
                    "-a",
                    "--no-trunc",
                    f"--filter=label={_OWNER_LABEL}",
                    f"--filter=id={cid}",
                    "--format={{.ID}}",
                )
            )
            if listing.strip():
                raise SandboxUnavailable("container_remains")
        except SandboxUnavailable as exc:
            self._mark_unhealthy()
            raise SandboxUnavailable("container_cleanup_failed") from exc

    def reconcile(self) -> int:
        """Remove only owner-labelled remnants before recovering admission."""
        with self._lock:
            self._audit("reconciliation")
            try:
                raw = self._run(
                    ("ps", "-a", "--no-trunc", f"--filter=label={_OWNER_LABEL}", "--format={{.ID}}")
                )
                ids = raw.decode("ascii").split()
                if any(_CONTAINER_ID.fullmatch(cid) is None for cid in ids):
                    raise SandboxUnavailable("orphan_ids_invalid")
                for cid in ids:
                    self._cleanup(cid)
            except (SandboxUnavailable, UnicodeError):
                self._mark_unhealthy()
                raise SandboxUnavailable("reconciliation_failed") from None
            self._audit("reconciled", outcome="ok")
            if self.config.audit_path is not None:
                self.config.audit_path.with_suffix(".unhealthy").unlink(missing_ok=True)
            for lease in self._leases():
                lease.unlink()
                with _HEALTH_LOCK:
                    _ACTIVE_LEASES.discard(lease)
            with _HEALTH_LOCK:
                if self.config.audit_path is not None:
                    _PROCESS_UNHEALTHY.discard(self.config.audit_path)
            self._unhealthy = False
            return len(ids)

    def _create(
        self, budget: SandboxBudget, cidfile: Path, job_id: str, probe: bool = False
    ) -> str:
        command = list(command_for(self.config, budget, cidfile, job_id))
        launch_id = uuid.uuid4().hex
        command.insert(2, f"--label=algoalpha.launch={launch_id}")
        command[1] = "create"
        command.remove("--rm")
        if probe:
            command[-1:] = ["-c", _CONTROL_PROBE]
        try:
            self._run(tuple(command[1:]))
            cid = read_container_id(cidfile)
            if cid is None:
                raise SandboxUnavailable("container_id_missing")
            return cid
        except BaseException:
            # The daemon might create a container after its client is lost.
            self._mark_unhealthy()
            try:
                listing = self._run(
                    (
                        "ps",
                        "-a",
                        "--no-trunc",
                        f"--filter=label={_OWNER_LABEL}",
                        f"--filter=label=algoalpha.launch={launch_id}",
                        "--format={{.ID}}",
                    )
                )
                for orphan in listing.decode("ascii").split():
                    if _CONTAINER_ID.fullmatch(orphan) is None:
                        raise SandboxUnavailable("orphan_ids_invalid")
                    self._cleanup(orphan)
            except (SandboxUnavailable, UnicodeError):
                pass  # durable unhealthy latch requires explicit reconciliation
            raise

    def _inspect(self, cid: str, budget: SandboxBudget) -> None:
        try:
            info = json.loads(self._run(("inspect", cid)))[0]
            host = info["HostConfig"]
            config = info["Config"]
            checks = (
                config["User"] == "65532:65532",
                config["Labels"].get("algoalpha.owner") == "quant-terminal-m16",
                host["ReadonlyRootfs"] is True,
                host["Privileged"] is False,
                host["NetworkMode"] == "none",
                host["Memory"] == budget.memory_bytes,
                host["MemorySwap"] == budget.memory_bytes,
                host["PidsLimit"] == budget.process_count,
                host["LogConfig"]["Type"] == "none",
                "no-new-privileges" in host["SecurityOpt"],
                info.get("Mounts", []) == [],
                not host["Binds"],
                not host["Devices"],
                not host["Tmpfs"],
                not host["PortBindings"],
                host["IpcMode"] == "none",
                host["PidMode"] == "private",
                host["UTSMode"] == "private",
                host["CgroupMode"] == "private",
                host["RestartPolicy"]["Name"] == "no",
                f"seccomp={self.config.seccomp_profile}" in host["SecurityOpt"],
                set(config["Env"])
                == self._image_env
                | {"container=podman", "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1"},
                host["CpuQuota"] / host["CpuPeriod"] <= budget.cpu_seconds / budget.wall_seconds,
                {item["Name"]: (item["Soft"], item["Hard"]) for item in host["Ulimits"]}
                == {
                    "RLIMIT_CPU": (budget.cpu_seconds, budget.cpu_seconds),
                    "RLIMIT_NOFILE": (budget.open_files, budget.open_files),
                    "RLIMIT_FSIZE": (budget.output_bytes, budget.output_bytes),
                },
                config["Timeout"] == budget.wall_seconds,
                info["Image"] == self._image_id,
            )
            if not all(checks):
                raise SandboxUnavailable("container_controls_mismatch")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise SandboxUnavailable("container_inspection_failed") from exc

    def probe(self) -> PodmanCapability:
        capability = self._probe()
        if self.config.audit_path is not None:
            try:
                self._audit(
                    "capability_probe",
                    outcome="ok" if capability.available else "failed",
                    reason=capability.reason,
                )
            except SandboxUnavailable:
                self._verified = False
                return PodmanCapability(False, "audit_write_failed", self._version)
        return capability

    def _probe(self) -> PodmanCapability:
        self._verified = False
        if self._is_unhealthy():
            return PodmanCapability(False, "cleanup_unhealthy", self._version)
        cid: str | None = None
        try:
            info = json.loads(self._run(("info", "--format=json")))
            image = json.loads(self._run(("image", "inspect", self.config.image)))[0]
            self._image_id = image["Id"]
            self._image_env = set(image["Config"].get("Env") or [])
            if _CONTAINER_ID.fullmatch(self._image_id) is None or image["Config"].get("Volumes"):
                raise SandboxUnavailable("image_configuration_unverified")
            if self.config.image.startswith("sha256:") and self.config.image[7:] != self._image_id:
                raise SandboxUnavailable("image_digest_mismatch")
            host = info["host"]
            self._version = str(info["version"]["Version"])
            if not host["security"]["rootless"] or not host["security"]["seccompEnabled"]:
                raise SandboxUnavailable("rootless_seccomp_unverified")
            if host["cgroupVersion"] != "v2" or not {"cpu", "memory", "pids"} <= set(
                host["cgroupControllers"]
            ):
                raise SandboxUnavailable("cgroup_limits_unverified")
            with TemporaryDirectory(prefix="algoalpha-probe-") as directory:
                cid = self._create(
                    _PROBE_BUDGET, Path(directory) / "probe.cid", "control-probe", True
                )
                self._inspect(cid, _PROBE_BUDGET)
                blob, code = self._stream(
                    (self._executable(), "start", "--attach", cid), b"", 4096, 15, None
                )
                if code != 0:
                    raise SandboxUnavailable("effective_probe_failed")
                controls = json.loads(blob)
                expected = {
                    "uid": 65532,
                    "seccomp": "2",
                    "nnp": "1",
                    "caps": "0000000000000000",
                    "memory": "64000000",
                    "swap": "0",
                    "pids": "4",
                    "net": ["lo"],
                    "sockets_denied": True,
                }
                if any(controls.get(key) != value for key, value in expected.items()):
                    raise SandboxUnavailable("effective_controls_mismatch")
                quota, period = map(int, controls["cpu"].split())
                if quota / period != 0.5:
                    raise SandboxUnavailable("effective_cpu_mismatch")
                self._image_provenance = {
                    "image_id": self._image_id,
                    "python_version": controls["python_version"],
                    "image_files": controls["image_files"],
                    "seccomp_sha256": self._profile_digest(),
                    "client_version": str(info.get("Client", info["version"])["Version"]),
                }
        except (SandboxUnavailable, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, SandboxUnavailable) else "podman_info_unverified"
            return PodmanCapability(False, reason, self._version)
        finally:
            if cid is not None:
                try:
                    self._cleanup(cid)
                except SandboxUnavailable:
                    return PodmanCapability(False, "container_cleanup_failed", self._version)
        self._verified = True
        return PodmanCapability(True, "controls_verified", self._version)

    def execute(
        self, request_blob: bytes, cancel: threading.Event | None = None
    ) -> SandboxResponse:
        """Launch after effective control proof; cleanup precedes every returned result."""
        request = decode_request(request_blob)
        with self._lock:
            self._audit("admission", request)
            if cancel is not None and cancel.is_set():
                self._audit("rejection", request, "cancelled", "cancelled")
                raise SandboxUnavailable("cancelled")
            if request.dependencies:
                self._audit("rejection", request, "failed", "dependencies_unsupported")
                raise SandboxUnavailable("dependencies_unsupported")
            capability = self.probe()
            if not capability.available:
                self._audit("rejection", request, "failed", capability.reason)
                raise SandboxUnavailable(capability.reason)
            cid: str | None = None
            watcher: subprocess.Popen[bytes] | None = None
            lease: Path | None = None
            cleanup_proven = False
            reason = "failed"
            try:
                with TemporaryDirectory(prefix="algoalpha-job-") as directory:
                    lease = self._begin_lease()
                    cid = self._create(request.budget, Path(directory) / "job.cid", request.job_id)
                    self._inspect(cid, request.budget)
                    if cancel is not None and cancel.is_set():
                        raise SandboxUnavailable("cancelled")
                    watcher = self._watchdog(cid, request, lease)
                    self._audit("launch", request, "running")
                    blob, code = self._stream(
                        (self._executable(), "start", "--attach", "--interactive", cid),
                        request_blob,
                        request.budget.output_bytes,
                        request.budget.wall_seconds + 2,
                        cancel,
                        watcher,
                    )
                    if not blob and code != 0:
                        raise SandboxUnavailable("container_exit_failure")
                    response = decode_response(blob)
                    if code != 0 and response.status != "error":
                        raise SandboxUnavailable("container_exit_failure")
                    if response.job_id != request.job_id:
                        raise SandboxUnavailable("response_job_mismatch")
                    reason = "ok" if response.status == "ok" else "strategy_error"
                    # Diagnostics are untrusted even after schema validation.
                    response = SandboxResponse(
                        response.job_id,
                        response.status,
                        response.intents,
                        "" if response.status == "ok" else "Strategy execution failed.",
                        None if response.status == "ok" else "strategy_error",
                    )
            except ProtocolError as exc:
                reason = "response_invalid"
                raise SandboxUnavailable(reason) from exc
            except SandboxUnavailable as exc:
                reason = str(exc)
                raise
            finally:
                try:
                    try:
                        if cid is not None:
                            self._cleanup(cid)
                            if watcher is not None:
                                self._stop_watchdog(watcher)
                            self._audit("cleanup", request, "ok")
                            cleanup_proven = True
                        else:
                            cleanup_proven = not self._unhealthy
                    except SandboxUnavailable as exc:
                        reason = str(exc)
                        raise
                    finally:
                        self._audit("termination", request, reason, reason)
                        if lease is not None and cleanup_proven:
                            try:
                                lease.unlink()
                            except OSError as exc:
                                self._mark_unhealthy()
                                raise SandboxUnavailable("health_cleanup_failed") from exc
                finally:
                    if lease is not None:
                        with _HEALTH_LOCK:
                            _ACTIVE_LEASES.discard(lease)
            return response
