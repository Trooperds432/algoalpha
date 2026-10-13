"""Portable canonical source-bundle tests."""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.source import build_source_bundle


def test_source_bundle_is_byte_stable_and_excludes_runtime_directories(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "strategy.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"strategy source\n")
    (root / "pyproject.toml").write_text("name = 'fixture'\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "volatile").write_text("ignored", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "volatile").write_text("ignored", encoding="utf-8")
    (root / "tmp").mkdir()
    (root / "tmp" / "volatile").write_text("ignored", encoding="utf-8")
    (root / "apps" / "web" / "node_modules").mkdir(parents=True)
    (root / "apps" / "web" / "node_modules" / "dependency.js").write_text(
        "ignored", encoding="utf-8"
    )
    (root / "apps" / "web" / "dist").mkdir()
    (root / "apps" / "web" / "dist" / "index.html").write_text("ignored", encoding="utf-8")
    (root / "apps" / "web" / "tsconfig.tsbuildinfo").write_text("ignored", encoding="utf-8")
    (root / "coordination").mkdir()
    (root / "coordination" / "milestone-state.json").write_text("volatile", encoding="utf-8")
    (root / "docs" / "validation").mkdir(parents=True)
    (root / "docs" / "validation" / "gate.json").write_text("volatile", encoding="utf-8")

    first = build_source_bundle(root)
    (source).touch()
    (root / ".git" / "volatile").write_text("different ignored content", encoding="utf-8")
    (root / "coordination" / "milestone-state.json").write_text("changed", encoding="utf-8")
    (root / "docs" / "validation" / "gate.json").write_text("changed", encoding="utf-8")
    (root / "apps" / "web" / "node_modules" / "dependency.js").write_text(
        "different ignored content", encoding="utf-8"
    )
    (root / "apps" / "web" / "dist" / "index.html").write_text(
        "different ignored content", encoding="utf-8"
    )
    (root / "apps" / "web" / "tsconfig.tsbuildinfo").write_text(
        "different ignored content", encoding="utf-8"
    )
    second = build_source_bundle(root)

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["pyproject.toml", "src/strategy.py"]
        assert [info.filename for info in archive.infolist()] == sorted(archive.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        assert all(info.extra == b"" and info.comment == b"" for info in archive.infolist())
        assert archive.read("src/strategy.py") == b"strategy source\n"


def test_source_bundle_hash_changes_when_source_bytes_change(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "main.py"
    source.write_bytes(b"value = 1\n")
    first = build_source_bundle(root)
    source.write_bytes(b"value = 2\n")

    assert hashlib.sha256(first).digest() != hashlib.sha256(build_source_bundle(root)).digest()


def test_source_bundle_rejects_symlinked_root_and_nested_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("secret", encoding="utf-8")
    link = tmp_path / "project-link"
    nested_link = root / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
        nested_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows account")

    with pytest.raises(DomainError, match="links"):
        build_source_bundle(link)
    with pytest.raises(DomainError, match="symlinks or junctions"):
        build_source_bundle(root)
    assert (outside / "secret.py").read_text(encoding="utf-8") == "secret"


def test_source_bundle_rejects_symlinked_ancestor_before_resolving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "linked-parent" / "project"
    real_parent = Path.is_symlink
    link_ancestor = tmp_path / "linked-parent"

    def simulated_parent_link(path: Path) -> bool:
        if path.absolute() == link_ancestor.absolute():
            return True
        return real_parent(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_parent_link)
    with pytest.raises(DomainError, match="ancestors"):
        build_source_bundle(root)


@pytest.mark.parametrize("directory_name", ["nested-junction", ".git"])
def test_source_bundle_rejects_nested_and_excluded_junctions_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory_name: str
) -> None:
    root = tmp_path / "project"
    junction = root / directory_name
    junction.mkdir(parents=True)
    protected = junction / "secret.py"
    protected.write_bytes(b"must not be read")
    real_is_junction = Path.is_junction

    def simulated_junction(path: Path) -> bool:
        if path.absolute() == junction.absolute():
            return True
        return real_is_junction(path)

    monkeypatch.setattr(Path, "is_junction", simulated_junction)
    with pytest.raises(DomainError, match="junctions"):
        build_source_bundle(root)
