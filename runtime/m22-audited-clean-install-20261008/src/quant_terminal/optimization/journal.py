"""Append-only SQLite journal for reproducible optimization campaigns."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_terminal.analytics.metrics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.models import canonical_json

from .engine import OptimizationTrial
from .models import OptimizationSpec, TrialScore, enumerate_candidates


class JournalIntegrityError(DomainError):
    """A campaign journal is corrupt or bound to different inputs."""


class OptimizationJournal:
    """Persist one campaign's immutable spec and append-only candidate outcomes."""

    def __init__(self, path: str | Path, spec: OptimizationSpec) -> None:
        self.path = Path(path).absolute()
        for component in (self.path, *self.path.parents):
            if component.is_symlink() or component.is_junction():
                raise DomainError("Optimization journal paths cannot contain symlinks or junctions")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spec = spec
        self._candidates = enumerate_candidates(spec)
        raw_spec = canonical_json(spec.to_dict())
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS campaigns ("
                    "optimization_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                    "specification BLOB NOT NULL, specification_sha256 TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS trials ("
                    "optimization_id TEXT NOT NULL REFERENCES campaigns(optimization_id), "
                    "candidate_index INTEGER NOT NULL, payload BLOB NOT NULL, "
                    "payload_sha256 TEXT NOT NULL, PRIMARY KEY(optimization_id,candidate_index))"
                )
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS campaigns_no_{operation.lower()} "
                        f"BEFORE {operation} ON campaigns BEGIN "
                        "SELECT RAISE(ABORT, 'campaign specs are immutable'); END"
                    )
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS trials_no_{operation.lower()} "
                        f"BEFORE {operation} ON trials BEGIN "
                        "SELECT RAISE(ABORT, 'campaign trials are append-only'); END"
                    )
                existing = connection.execute(
                    "SELECT fingerprint,specification,specification_sha256 FROM campaigns "
                    "WHERE optimization_id=?",
                    (spec.optimization_id,),
                ).fetchone()
                digest = hashlib.sha256(raw_spec).hexdigest()
                if existing is None:
                    connection.execute(
                        "INSERT INTO campaigns VALUES (?,?,?,?)",
                        (spec.optimization_id, spec.fingerprint, raw_spec, digest),
                    )
                elif (
                    existing[0] != spec.fingerprint
                    or bytes(existing[1]) != raw_spec
                    or existing[2] != digest
                ):
                    raise JournalIntegrityError("Journal belongs to a different optimization spec")
                connection.commit()
        except sqlite3.Error as exc:
            raise JournalIntegrityError("Unable to initialize optimization journal") from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        for component in (self.path, *self.path.parents):
            if component.is_symlink() or component.is_junction():
                raise JournalIntegrityError("Journal path acquired a symlink or junction")
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _metric(data: dict[str, Any]) -> Metric:
        return Metric(
            None if data["value"] is None else Decimal(data["value"]),
            data["unit"],
            data["observations"],
            data["status"],
            data["reason"],
        )

    @classmethod
    def _trial_from_dict(cls, data: dict[str, Any]) -> OptimizationTrial:
        score_data = data["score"]
        score = None
        if score_data is not None:
            score = TrialScore(
                score_data["eligible"],
                None if score_data["objective"] is None else Decimal(score_data["objective"]),
                tuple(score_data["reasons"]),
                tuple(
                    (name, None if value is None else Decimal(value), passed)
                    for name, value, passed in score_data["constraint_values"]
                ),
            )
        metrics = {name: cls._metric(value) for name, value in data["metrics"].items()}
        return OptimizationTrial(
            data["index"],
            data["specification_fingerprint"],
            data["parameters"],
            data["status"],
            score,
            metrics,
            data["run_id"],
            data["input_fingerprint"],
            data["result_fingerprint"],
            Decimal(data["elapsed_seconds"]),
            data["error"],
        )

    def record(self, trial: OptimizationTrial) -> None:
        """Atomically append exactly the next trial; never overwrite an outcome."""
        if trial.specification_fingerprint != self.spec.fingerprint:
            raise JournalIntegrityError("Trial belongs to a different optimization spec")
        if not 0 <= trial.index < self.spec.candidate_count:
            raise JournalIntegrityError("Trial index is outside the candidate space")
        if dict(trial.parameters) != dict(self._candidates[trial.index]):
            raise JournalIntegrityError("Trial parameters do not match their candidate index")
        payload = canonical_json(trial.to_dict())
        digest = hashlib.sha256(payload).hexdigest()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT COALESCE(MAX(candidate_index)+1,0) FROM trials WHERE optimization_id=?",
                    (self.spec.optimization_id,),
                ).fetchone()
                if row[0] != trial.index:
                    raise JournalIntegrityError("Journal accepts only contiguous trial indices")
                connection.execute(
                    "INSERT INTO trials VALUES (?,?,?,?)",
                    (self.spec.optimization_id, trial.index, payload, digest),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise JournalIntegrityError("Duplicate or invalid journal trial") from exc
        except sqlite3.Error as exc:
            raise JournalIntegrityError("Unable to persist optimization trial") from exc

    def load(self) -> tuple[OptimizationTrial, ...]:
        """Read, checksum, and reconstruct the contiguous immutable trial prefix."""
        try:
            with self._connection() as connection:
                campaign = connection.execute(
                    "SELECT fingerprint,specification,specification_sha256 FROM campaigns "
                    "WHERE optimization_id=?",
                    (self.spec.optimization_id,),
                ).fetchone()
                raw_spec = canonical_json(self.spec.to_dict())
                if (
                    campaign is None
                    or campaign[0] != self.spec.fingerprint
                    or bytes(campaign[1]) != raw_spec
                    or hashlib.sha256(raw_spec).hexdigest() != campaign[2]
                ):
                    raise JournalIntegrityError("Stored optimization spec failed verification")
                rows = connection.execute(
                    "SELECT candidate_index,payload,payload_sha256 FROM trials "
                    "WHERE optimization_id=? ORDER BY candidate_index",
                    (self.spec.optimization_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise JournalIntegrityError("Unable to read optimization journal") from exc
        output: list[OptimizationTrial] = []
        for index, payload_blob, digest in rows:
            payload = bytes(payload_blob)
            if hashlib.sha256(payload).hexdigest() != digest:
                raise JournalIntegrityError("Stored optimization trial checksum mismatch")
            try:
                data = json.loads(payload)
                trial = self._trial_from_dict(data)
            except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
                raise JournalIntegrityError("Stored optimization trial is malformed") from exc
            if (
                trial.index != index
                or trial.index != len(output)
                or trial.specification_fingerprint != self.spec.fingerprint
                or dict(trial.parameters) != dict(self._candidates[index])
            ):
                raise JournalIntegrityError("Stored optimization trials are not contiguous")
            output.append(trial)
        if len(output) > self.spec.candidate_count:
            raise JournalIntegrityError("Stored trials exceed the candidate space")
        return tuple(output)
