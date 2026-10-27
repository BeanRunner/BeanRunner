"""Append-only structured lifecycle event log."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import StorageError


class EventLog:
    """Writes timestamped JSON-lines events correlated with job IDs."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.path = Path(data_dir).expanduser().resolve() / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        job_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "job_id": job_id,
            "details": details or {},
        }
        try:
            encoded = (
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            with self._lock, self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise StorageError(
                f"No se pudo escribir la bitácora {self.path}: {error}"
            ) from error

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("El límite de eventos debe ser mayor que cero.")
        try:
            with self._lock:
                lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError) as error:
            raise StorageError(
                f"No se pudo leer la bitácora {self.path}: {error}"
            ) from error

        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError("el evento no es un objeto")
                entries.append(entry)
            except (json.JSONDecodeError, ValueError) as error:
                raise StorageError(
                    f"Evento inválido en {self.path}, línea {line_number}: {error}"
                ) from error
        return entries[-limit:]
