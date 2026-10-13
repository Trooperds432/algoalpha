"""Strict local API/CLI access to manually controlled virtual paper sessions."""

from __future__ import annotations

import re
from pathlib import Path
from threading import RLock

from quant_terminal.application.runtime import _integer, _object, _text
from quant_terminal.core.exceptions import DomainError

from .clock import LiveClock
from .engine import PaperSession
from .schema import bar


class PaperService:
    def __init__(self, root: Path, *, clock: LiveClock | None = None) -> None:
        self.root = Path(root).resolve() / "paper"
        self.clock = clock or LiveClock()
        self._sessions: dict[str, PaperSession] = {}
        self._lock = RLock()

    def _path(self, identity: str) -> Path:
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identity) is None
        ):
            raise DomainError("Paper session ID must be 1-64 safe ASCII characters")
        path = self.root / (identity + ".sqlite3")
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise DomainError("Paper session path is outside its state directory")
        return path

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        data = _object(payload, {"session_id", "config"}, {"session_id", "config"})
        identity = _text(data["session_id"])
        settings = data["config"]
        if not isinstance(settings, dict):
            raise DomainError("Paper config must be an object")
        with self._lock:
            path = self._path(identity)
            if path.exists():
                raise DomainError("Paper session already exists")
            session = PaperSession.create(path, settings, clock=self.clock)
            self._sessions[identity] = session
            return {"session_id": identity, **session.snapshot()}

    def _session(self, identity: str) -> PaperSession:
        if identity not in self._sessions:
            self._sessions[identity] = PaperSession(self._path(identity), clock=self.clock)
        return self._sessions[identity]

    def list(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            if not self.root.exists():
                return ()
            return tuple(self.get(path.stem) for path in sorted(self.root.glob("*.sqlite3")))

    def get(self, identity: str) -> dict[str, object]:
        with self._lock:
            return {"session_id": identity, **self._session(identity).snapshot()}

    def command(self, identity: str, action: str, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            session = self._session(identity)
            if action == "reconnect":
                data = _object(payload, {"event_id", "snapshot"}, {"event_id", "snapshot"})
                snapshot = data["snapshot"]
                if not isinstance(snapshot, dict):
                    raise DomainError("Reconciliation snapshot must be an object")
                result = session.reconnect(_text(data["event_id"]), snapshot)
            elif action == "disconnect":
                data = _object(payload, {"event_id", "reason"}, {"event_id"})
                result = session.disconnect(
                    _text(data["event_id"]), _text(data.get("reason", "disconnected"))
                )
            elif action == "events":
                data = _object(
                    payload, {"event_id", "sequence", "bar"}, {"event_id", "sequence", "bar"}
                )
                result = session.ingest(
                    _text(data["event_id"]), _integer(data["sequence"]), bar(data["bar"])
                )
            elif action == "fills":
                data = _object(payload, {"fill"}, {"fill"})
                fill = data["fill"]
                if not isinstance(fill, dict):
                    raise DomainError("Fill acknowledgement must be an object")
                result = session.confirm_fill(fill)
            else:
                raise DomainError("Unknown paper action")
            return {"session_id": identity, **result}
