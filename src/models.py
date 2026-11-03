from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class JobStatus(str, Enum):
    """Ciclo de vida oficial del trabajo según RF-06."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.CANCELED, JobStatus.FAILED}
    ),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELED: frozenset(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} debe ser una fecha ISO.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} no contiene una fecha ISO válida.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} debe incluir zona horaria.")
    return parsed


@dataclass(slots=True)
class Job:
    """Datos compartidos del trabajo entre runner, executor, manager y CLI."""

    command: list[str]
    id: str = field(default_factory=lambda: str(uuid4()))
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    pid: int | None = None
    error_message: str | None = None
    stdout: str = ""
    stderr: str = ""
    cancel_requested: bool = False
    # Idempotency key supplied by a client so transport retries do not duplicate work.
    request_id: str | None = None
    # Working directory belongs to the submitted command, not the daemon process.
    cwd: str | None = None

    @property
    def is_terminal(self) -> bool:
        return not _ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, new_status: JobStatus) -> None:
        """Cambia el estado validando RF-06 y actualiza las marcas de tiempo."""
        if new_status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(
                f"Transición inválida: {self.status.value} -> {new_status.value}"
            )
        now = _utcnow()
        if new_status is JobStatus.RUNNING:
            self.started_at = now
        elif _ALLOWED_TRANSITIONS[new_status] == frozenset():
            self.finished_at = now
        self.status = new_status

    def to_dict(self, include_output: bool = False) -> dict[str, Any]:
        """Convierte metadatos a valores JSON serializables."""
        data: dict[str, Any] = {
            "id": self.id,
            "command": list(self.command),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "error_message": self.error_message,
            "cancel_requested": self.cancel_requested,
            "request_id": self.request_id,
            "cwd": self.cwd,
        }
        if include_output:
            data["stdout"] = self.stdout
            data["stderr"] = self.stderr
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        """Restore a persisted job, rejecting malformed or unsupported records."""
        command = data.get("command")
        if not isinstance(command, list) or not all(
            isinstance(argument, str) for argument in command
        ):
            raise ValueError("command debe ser una lista de texto")
        if not command or not command[0].strip():
            raise ValueError("command debe incluir un ejecutable")
        job_id = data.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("id debe ser texto no vacío")

        try:
            status = JobStatus(data["status"])
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError("status no es un estado reconocido") from error

        exit_code = data.get("exit_code")
        pid = data.get("pid")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise ValueError("exit_code debe ser entero o null")
        if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
            raise ValueError("pid debe ser entero o null")

        error_message = data.get("error_message")
        cancel_requested = data.get("cancel_requested", False)
        request_id = data.get("request_id")
        cwd = data.get("cwd")
        if error_message is not None and not isinstance(error_message, str):
            raise ValueError("error_message debe ser texto o null")
        if not isinstance(cancel_requested, bool):
            raise ValueError("cancel_requested debe ser booleano")
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError("request_id debe ser texto o null")
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError("cwd debe ser texto o null")

        created_at = _parse_datetime(data.get("created_at"), "created_at")
        started_at = _parse_datetime(
            data.get("started_at"),
            "started_at",
            optional=True,
        )
        finished_at = _parse_datetime(
            data.get("finished_at"),
            "finished_at",
            optional=True,
        )
        if created_at is None:
            raise ValueError("created_at no puede ser null")
        return cls(
            id=job_id,
            command=command,
            status=status,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=exit_code,
            pid=pid,
            error_message=error_message,
            cancel_requested=cancel_requested,
            request_id=request_id,
            cwd=cwd,
        )
