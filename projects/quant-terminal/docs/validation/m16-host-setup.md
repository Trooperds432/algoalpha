# M16 host isolation setup

Status: platform installed, **reboot and runtime proof pending** (2026-09-30).

The owner requested completion of M16, including necessary host setup. An elevated `wsl.exe --install --no-distribution` completed with exit code 0 and installed Windows Subsystem for Linux 3.0.1 plus the VirtualMachinePlatform optional component. Windows reported that the feature changes will not take effect until reboot. The install log and exit code are retained outside the repository under `C:\Users\Jonat\algoalpha\runtime\m16`.

Podman 5.8.3 was installed from the Red Hat winget package; winget verified the published installer SHA-256 before launch. The first silent winget handoff stalled at the elevation step and was stopped. A direct elevated retry initially encountered and removed a partially registered same-version install; the final retry completed with installer exit code 0. `C:\Program Files\RedHat\Podman\podman.exe --version` reports 5.8.3. The prior failed installer logs are retained under the same runtime folder.

`podman machine init` downloaded the official 5.8 machine image but could not register/start the WSL VM: `HCS_E_SERVICE_NOT_AVAILABLE` because virtualization features are not active until reboot. `podman machine list` remains empty. No container, strategy, hostile fixture, broker connection or order has run. The backend remains fail-closed.

After reboot: verify WSL2 service/version, create a dedicated Podman machine, start it, inspect rootless/cgroup/seccomp/network controls, build a pinned minimal strategy image, run the 16-case hostile suite and full regression checks, then obtain independent security review. Podman's WSL provider automatically mounts Windows drives inside the machine; its `--volume` flag does not narrow that integration. Strategy containers must have no host-path mounts, and that absence must be verified. An installed CLI or VM alone does not satisfy the M16 gate.
