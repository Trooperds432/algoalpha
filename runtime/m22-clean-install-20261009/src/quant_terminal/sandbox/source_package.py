"""Validate opaque source data without granting execution or filesystem authority."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox import protocol


@dataclass(frozen=True, slots=True)
class SourcePackage:
    """A bounded UTF-8 source string and its exact content digest."""

    source: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise DomainError("source must be nonempty UTF-8 text")
        try:
            encoded = self.source.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise DomainError("source must be valid UTF-8 text") from exc
        if len(encoded) > protocol.MAX_SOURCE_BYTES:
            raise DomainError("source exceeds byte limit")
        if (
            not isinstance(self.source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
        ):
            raise DomainError("source_sha256 must be lowercase 64-hex")
        if hashlib.sha256(encoded).hexdigest() != self.source_sha256:
            raise DomainError("source hash mismatch")

    @classmethod
    def from_payload(cls, value: object) -> SourcePackage:
        """Accept only the exact source, digest and empty dependency JSON fields."""
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise DomainError("source package must be a JSON object with string keys")
        if set(value) != {"source", "source_sha256", "dependencies"}:
            raise DomainError("invalid source package fields")
        if not isinstance(value["dependencies"], list) or value["dependencies"]:
            raise DomainError("dependencies must be an empty JSON array")
        return cls(value["source"], value["source_sha256"])

    def to_payload(self) -> dict[str, object]:
        """Return a fresh JSON-compatible container; source remains opaque."""
        return {"source": self.source, "source_sha256": self.source_sha256, "dependencies": []}
