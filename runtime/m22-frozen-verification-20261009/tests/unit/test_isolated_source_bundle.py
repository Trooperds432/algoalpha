"""Submitted source is opaque canonical provenance, never a host file/import."""

import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.source import build_source_bundle
from quant_terminal.sandbox.protocol import MAX_SOURCE_BYTES


def test_combined_bundle_is_canonical_and_keeps_source_opaque(tmp_path: Path) -> None:
    (tmp_path / "engine.py").write_bytes(b"trusted engine")
    source = b"raise RuntimeError('must never execute on host')\n"
    original = build_source_bundle(tmp_path)
    combined = build_source_bundle(tmp_path, submitted_strategy=source)
    assert combined == build_source_bundle(tmp_path, submitted_strategy=source)
    assert original == build_source_bundle(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / "engine.py"]
    with zipfile.ZipFile(io.BytesIO(combined)) as archive:
        assert archive.namelist() == ["engine.py", "submitted-strategy.py"]
        assert archive.read("submitted-strategy.py") == source
        assert not archive.comment
        for entry in archive.infolist():
            assert entry.date_time == (1980, 1, 1, 0, 0, 0)
            assert entry.compress_type == zipfile.ZIP_STORED
            assert entry.create_system == 3
            assert entry.external_attr == (stat.S_IFREG | 0o644) << 16
            assert not entry.internal_attr and not entry.extra and not entry.comment
    assert (
        hashlib.sha256(combined).digest()
        != hashlib.sha256(
            build_source_bundle(tmp_path, submitted_strategy=b"different opaque source")
        ).digest()
    )


@pytest.mark.parametrize("collision", ["submitted-strategy.py", "SUBMITTED-STRATEGY.PY"])
def test_reserved_source_path_rejects_collision(tmp_path: Path, collision: str) -> None:
    (tmp_path / collision).write_bytes(b"user-owned file")
    with pytest.raises(DomainError, match="reserved"):
        build_source_bundle(tmp_path, submitted_strategy=b"opaque")
    assert (tmp_path / collision).read_bytes() == b"user-owned file"


@pytest.mark.parametrize(
    "source",
    [b"", "text", b"\xff", b"x" * (MAX_SOURCE_BYTES + 1)],
    ids=["empty", "not-bytes", "invalid-utf8", "oversize"],
)
def test_invalid_source_rejects_before_reading_project(tmp_path: Path, source: object) -> None:
    with pytest.raises(DomainError, match="submitted strategy"):
        build_source_bundle(tmp_path / "absent", submitted_strategy=source)  # type: ignore[arg-type]


def test_utf8_byte_limit_and_source_only_project(tmp_path: Path) -> None:
    source = ("é" * (MAX_SOURCE_BYTES // 2)).encode("utf8")
    with zipfile.ZipFile(
        io.BytesIO(build_source_bundle(tmp_path, submitted_strategy=source))
    ) as archive:
        assert archive.namelist() == ["submitted-strategy.py"]
        assert archive.read("submitted-strategy.py") == source
    with pytest.raises(DomainError, match="submitted strategy"):
        build_source_bundle(tmp_path, submitted_strategy=source + b"x")
