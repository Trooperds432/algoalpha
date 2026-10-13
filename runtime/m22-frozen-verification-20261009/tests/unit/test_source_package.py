"""Requirement checks for opaque, bounded source packages."""

import hashlib
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox.protocol import MAX_SOURCE_BYTES
from quant_terminal.sandbox.source_package import SourcePackage


def payload(source: str = "opaque text: not Python!\x00 café") -> dict[str, Any]:
    return {
        "source": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "dependencies": [],
    }


def test_round_trip_frozen_and_fresh_containers() -> None:
    value = payload()
    package = SourcePackage.from_payload(value)
    assert package.to_payload() == value
    assert SourcePackage.from_payload(package.to_payload()) == package
    with pytest.raises(FrozenInstanceError):
        package.source = "changed"  # type: ignore[misc]
    first = package.to_payload()
    second = package.to_payload()
    assert first is not second
    assert first["dependencies"] is not second["dependencies"]
    value["source"] = "changed"
    assert package.source != value["source"]


@pytest.mark.parametrize("source", [None, 1, True, b"text", [], "", "\ud800", "\udfff"])
def test_invalid_source_in_payload_and_constructor(source: Any) -> None:
    value = payload()
    value["source"] = source
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)
    with pytest.raises(DomainError):
        SourcePackage(source, value["source_sha256"])


@pytest.mark.parametrize(
    "digest",
    [
        None,
        1,
        True,
        b"a" * 64,
        "",
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "0" * 64,
        payload()["source_sha256"].upper(),
        "a" * 64 + "\n",
    ],
)
def test_invalid_hash_in_payload_and_constructor(digest: Any) -> None:
    value = payload()
    value["source_sha256"] = digest
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)
    with pytest.raises(DomainError):
        SourcePackage(value["source"], digest)


@pytest.mark.parametrize(
    "source", ["a" * MAX_SOURCE_BYTES, "é" * (MAX_SOURCE_BYTES // 2)], ids=["ascii", "multibyte"]
)
def test_utf8_byte_boundary(source: str) -> None:
    assert SourcePackage.from_payload(payload(source)).source == source
    oversized = payload(source + "a")
    with pytest.raises(DomainError):
        SourcePackage.from_payload(oversized)
    with pytest.raises(DomainError):
        SourcePackage(oversized["source"], oversized["source_sha256"])


@pytest.mark.parametrize("dependencies", [None, (), {}, "", False, 0, ["library"], [[]]])
def test_dependencies_must_be_empty_list(dependencies: object) -> None:
    value = payload()
    value["dependencies"] = dependencies
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)


@pytest.mark.parametrize("field", ["path", "host_path", "verified", "extra", 1])
def test_unknown_fields(field: Any) -> None:
    value = payload()
    value[field] = "C:/host/private"
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)


@pytest.mark.parametrize("field", ["source", "source_sha256", "dependencies"])
def test_missing_fields(field: str) -> None:
    value = payload()
    del value[field]
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)


@pytest.mark.parametrize("value", [None, [], (), "{}", 1, True])
def test_requires_json_object(value: object) -> None:
    with pytest.raises(DomainError):
        SourcePackage.from_payload(value)
