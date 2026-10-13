"""Frozen candidates preserve checksums and reject unsafe/overwriting archives."""

import json
import zipfile
from pathlib import Path

import pytest

from quant_terminal import __version__
from quant_terminal.core.exceptions import DomainError
from quant_terminal.release.distribution import DOCS, build_candidate, verify_candidate


def source(root: Path) -> None:
    for name in (
        "pyproject.toml",
        "uv.lock",
        "requirements-dev.lock",
        "README.md",
        "src/quant_terminal/__init__.py",
        "examples/example.json",
        "apps/web/dist/index.html",
        *("docs/" + name for name in DOCS),
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic candidate fixture", encoding="utf-8")


def test_candidate_freeze_checksums_and_no_overwrite(tmp_path: Path) -> None:
    project = tmp_path / "source"
    source(project)
    output = tmp_path / "candidate.zip"
    result = build_candidate(project, output)
    assert result["version"] == __version__
    assert result["final_release"] is False
    assert verify_candidate(output)["verified"] is True
    with zipfile.ZipFile(output) as archive:
        assert "requirements-dev.lock" in archive.namelist()
    before = output.read_bytes()
    with pytest.raises(DomainError):
        build_candidate(project, output)
    assert output.read_bytes() == before
    with pytest.raises(DomainError):
        build_candidate(project, project / "candidate.zip")


def test_candidate_tampered_payload_fails(tmp_path: Path) -> None:
    project = tmp_path / "source"
    source(project)
    valid = tmp_path / "valid.zip"
    build_candidate(project, valid)
    with zipfile.ZipFile(valid) as archive:
        values = {name: archive.read(name) for name in archive.namelist()}
    values["README.md"] = b"tampered"
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as archive:
        for name, value in values.items():
            archive.writestr(name, value)
    with pytest.raises(DomainError, match="checksum"):
        verify_candidate(corrupt)


@pytest.mark.parametrize("name", ["../escape", "A/./B", "A\\B", "C:/bad", "CON.txt", "x. "])
def test_candidate_unsafe_paths_reject(tmp_path: Path, name: str) -> None:
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "bad")
        archive.writestr("release-manifest.json", json.dumps({}))
    with pytest.raises(DomainError):
        verify_candidate(path)
