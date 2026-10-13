"""Canonical, non-executable source snapshots for experiment provenance."""

from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path

from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox.protocol import MAX_SOURCE_BYTES

_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".vite",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "tmp",
    }
)
_EXCLUDED_PATHS = frozenset({"coordination", "docs/validation"})
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _source_files(root: Path) -> tuple[tuple[str, Path], ...]:
    root = root.absolute()
    for component in (root, *root.parents):
        if component.is_symlink() or component.is_junction():
            raise DomainError("Source bundle root and ancestors cannot contain links")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise DomainError("Source bundle root must be a real directory")
    entries: list[tuple[str, Path]] = []
    for current, directory_names, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directory_names):
            candidate = current_path / name
            if candidate.is_symlink() or candidate.is_junction():
                raise DomainError("Source bundle does not follow symlinks or junctions")
            relative = candidate.relative_to(root).as_posix()
            if name in _EXCLUDED_DIRECTORIES or any(
                relative == excluded or relative.startswith(excluded + "/")
                for excluded in _EXCLUDED_PATHS
            ):
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(filenames):
            if name.endswith(".tsbuildinfo"):
                continue
            path = current_path / name
            if path.is_symlink() or path.is_junction() or not path.is_file():
                raise DomainError("Source bundle accepts regular files only")
            relative = path.relative_to(root).as_posix()
            if relative.startswith("../") or relative.startswith("/"):
                raise DomainError("Source file escapes the bundle root")
            entries.append((relative, path))
    entries.sort(key=lambda item: item[0])
    folded = [name.casefold() for name, _ in entries]
    if len(folded) != len(set(folded)):
        raise DomainError("Source bundle paths must be unique ignoring case")
    return tuple(entries)


def build_source_bundle(root: str | Path, *, submitted_strategy: bytes | None = None) -> bytes:
    """Return a byte-stable ZIP of the project, excluding VCS/runtime caches.

    Entries are sorted POSIX-relative regular files with fixed timestamps,
    permissions and no compression. The archive stores data only; it is never
    automatically imported or executed by experiment services. Optional submitted
    strategy bytes occupy a fixed reserved entry; they are never staged on disk.
    """
    if submitted_strategy is not None:
        if (
            type(submitted_strategy) is not bytes
            or not 0 < len(submitted_strategy) <= MAX_SOURCE_BYTES
        ):
            raise DomainError("Invalid submitted strategy bytes or size")
        try:
            submitted_strategy.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise DomainError("Invalid submitted strategy UTF-8") from exc
    base = Path(root)
    try:
        files = _source_files(base)
    except (OSError, RuntimeError) as exc:
        raise DomainError("Could not safely enumerate project source") from exc
    reserved = "submitted-strategy.py"
    if submitted_strategy is not None and any(name.casefold() == reserved for name, _ in files):
        raise DomainError("Project source collides with reserved submitted strategy path")
    if not files and submitted_strategy is None:
        raise DomainError("Source bundle cannot be empty")
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        entries: list[tuple[str, Path | None]] = [(name, path) for name, path in files]
        if submitted_strategy is not None:
            entries.append((reserved, None))
        for name, path in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            content = submitted_strategy if path is None else path.read_bytes()
            assert content is not None
            archive.writestr(info, content)
    return output.getvalue()
