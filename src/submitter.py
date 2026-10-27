import logging
import shlex
import threading
import uuid
from typing import TypeAlias

from .models import Job, JobStatus

logger = logging.getLogger(__name__)

RawCommand: TypeAlias = str | list[str] | None


class InvalidCommandError(ValueError):
    """El comando enviado no cumple RF-02."""


class JobSubmitter:
    """Valida las solicitudes de trabajo y asigna identificadores únicos."""

    def __init__(self, job_storage: dict[str, Job] | None = None) -> None:
        self._jobs = job_storage if job_storage is not None else {}
        self._lock = threading.Lock()

    @staticmethod
    def _parse_string(command: str) -> list[str]:
        if not command.strip():
            raise InvalidCommandError("RF-02: El comando no puede estar vacío.")
        try:
            return shlex.split(command)
        except ValueError as error:
            raise InvalidCommandError(
                f"RF-02: Comando malformado o comillas sin cerrar: {error}"
            ) from error

    @staticmethod
    def _parse_list(command: list[str]) -> list[str]:
        if not command:
            raise InvalidCommandError("RF-02: La lista de comandos no puede estar vacía.")
        if not all(isinstance(arg, str) for arg in command):
            raise InvalidCommandError("RF-02: Todos los argumentos deben ser texto.")
        if not command[0].strip():
            raise InvalidCommandError("RF-02: El ejecutable no puede estar vacío.")
        return list(command)

    def validate_command(self, raw_command: RawCommand) -> list[str]:
        """Rechaza comandos vacíos o malformados y devuelve los argumentos analizados."""
        if raw_command is None:
            raise InvalidCommandError("RF-02: El comando no puede ser nulo.")
        if isinstance(raw_command, str):
            return self._parse_string(raw_command)
        if isinstance(raw_command, list):
            return self._parse_list(raw_command)
        raise InvalidCommandError("RF-02: Formato no soportado. Debe ser string o list.")

    def submit_job(self, raw_command: RawCommand) -> Job:
        """Acepta un comando válido y devuelve el trabajo en cola."""
        args = self.validate_command(raw_command)
        job = Job(id=str(uuid.uuid4()), command=args, status=JobStatus.QUEUED)
        with self._lock:
            self._jobs[job.id] = job
        logger.info("Trabajo aceptado | Job ID: %s | Comando: %r", job.id, args)
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)