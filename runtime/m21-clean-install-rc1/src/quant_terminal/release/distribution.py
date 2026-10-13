"""Build a local frozen source/UI release candidate; never publish or claim v1.0."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from quant_terminal import __version__
from quant_terminal.core.exceptions import DomainError

FILES = ("pyproject.toml", "uv.lock", "README.md")
DIRECTORIES = ("src", "examples", "apps/web/dist")
DOCS = (
    "release-operations.md",
    "headless-usage.md",
    "ui-usage.md",
    "paper-trading-usage.md",
    "portfolio-research-usage.md",
    "advanced-assets-usage.md",
)


def _check_path(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise DomainError("Candidate inputs/output cannot traverse links")


def build_candidate(project_root: Path, output: Path) -> dict[str, object]:
    _check_path(project_root)
    _check_path(output)
    root, target = project_root.resolve(), output.absolute()
    if target.is_relative_to(root) or target.exists():
        raise DomainError("Candidate output must be new and outside the project tree")
    paths = [root / name for name in FILES] + [root / "docs" / name for name in DOCS]
    for name in DIRECTORIES:
        directory = root / name
        if not directory.is_dir():
            raise DomainError("Required candidate source/UI directory missing")
        paths.extend(
            p
            for p in directory.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and not any(part.endswith(".egg-info") for part in p.parts)
            and not p.name.endswith((".pyc", ".tsbuildinfo"))
        )
    data: dict[str, bytes] = {}
    for path in sorted(paths):
        _check_path(path)
        if not path.is_file():
            raise DomainError("Required candidate input missing")
        relative = path.relative_to(root).as_posix()
        if len(data) >= 1024 or path.stat().st_size > 20_000_000:
            raise DomainError("Candidate exceeds size/count budget")
        data[relative] = path.read_bytes()
    if sum(map(len, data.values())) > 100_000_000:
        raise DomainError("Candidate exceeds total size budget")
    manifest = {
        "version": __version__,
        "release_type": "candidate",
        "final_release": False,
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in data.items()},
    }
    data["release-manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in sorted(data.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, value)
        # Verify the completed bytes including manifest and ZIP overhead before promotion.
        verify_candidate(temporary)
        # Hard-link promotion refuses overwrite even if another writer creates target.
        os.link(temporary, target)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DomainError("Could not build candidate atomically") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "version": __version__,
        "path": str(target),
        "file_count": len(data),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "final_release": False,
    }


def verify_candidate(path: Path) -> dict[str, object]:
    _check_path(path)
    if not path.is_file() or path.stat().st_size > 100_000_000:
        raise DomainError("Candidate archive is missing or exceeds size budget")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) > 1025 or len({name.casefold() for name in names}) != len(names):
                raise DomainError("Candidate entry count/duplicate violation")
            for info in infos:
                parts = PurePosixPath(info.filename).parts
                if (
                    not parts
                    or any(p in (".", "..") or ":" in p for p in parts)
                    or info.filename.startswith("/")
                    or info.filename != str(PurePosixPath(info.filename))
                    or any(
                        p.endswith((" ", "."))
                        or any(ord(c) < 32 for c in p)
                        or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                        for p in parts
                    )
                    or "\\" in info.filename
                    or info.file_size > 20_000_000
                    or info.external_attr >> 28 not in (0, 8)
                ):
                    raise DomainError("Candidate contains unsafe path/type/size")
            if sum(item.file_size for item in infos) > 100_000_000:
                raise DomainError("Candidate exceeds total size budget")
            manifest = json.loads(archive.read("release-manifest.json"))
            if not isinstance(manifest, dict) or set(manifest) != {
                "version",
                "release_type",
                "final_release",
                "files",
            }:
                raise DomainError("Invalid release manifest")
            if manifest["release_type"] != "candidate" or manifest["final_release"] is not False:
                raise DomainError("Expected an explicitly nonfinal candidate")
            hashes = manifest["files"]
            if not isinstance(hashes, dict) or set(hashes) != set(names) - {
                "release-manifest.json"
            }:
                raise DomainError("Candidate manifest entry mismatch")
            for name, digest in hashes.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    raise DomainError("Candidate checksum mismatch")
    except DomainError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise DomainError("Candidate is unavailable or malformed") from exc
    return {
        "version": manifest["version"],
        "verified": True,
        "file_count": len(hashes),
        "final_release": False,
    }
