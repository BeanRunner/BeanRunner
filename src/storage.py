"""Atomic persistence for job metadata and captured process output."""

import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from .models import Job


class StorageError(RuntimeError):
    """Raised when persisted BeanRunner state cannot safely be read or written."""


class JobStore:
    """Stores each job in its own directory using replace-based atomic updates."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self._lock = threading.RLock()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Job]:
        """Load all jobs and fail explicitly if a record is malformed."""
        jobs: dict[str, Job] = {}
        with self._lock:
            for job_dir in sorted(self.jobs_dir.iterdir()):
                if not job_dir.is_dir():
                    continue
                metadata_path = job_dir / "metadata.json"
                if not metadata_path.is_file():
                    raise StorageError(
                        f"Falta metadata.json para el trabajo persistido {job_dir.name}."
                    )
                try:
                    data = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("los metadatos deben ser un objeto JSON")
                    if data.get("schema_version") != 1:
                        raise ValueError("versión de metadatos no compatible")
                    job = Job.from_dict(data)
                    if job.id != job_dir.name:
                        raise ValueError("el ID no coincide con el directorio")
                    job.stdout = self._read_output(job_dir / "stdout.log")
                    job.stderr = self._read_output(job_dir / "stderr.log")
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                    raise StorageError(
                        f"No se pudieron cargar los metadatos de {metadata_path}: {error}"
                    ) from error
                jobs[job.id] = job
        return jobs

    def save(self, job: Job) -> None:
        """Persist output and metadata with each file replaced atomically."""
        if not job.id or Path(job.id).name != job.id or job.id in {".", ".."}:
            raise StorageError(f"ID de trabajo no válido para almacenamiento: {job.id!r}")
        job_dir = self.jobs_dir / job.id
        try:
            with self._lock:
                job_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_write(job_dir / "stdout.log", job.stdout.encode("utf-8"))
                self._atomic_write(job_dir / "stderr.log", job.stderr.encode("utf-8"))
                metadata = {
                    "schema_version": 1,
                    **job.to_dict(),
                }
                serialized = json.dumps(
                    metadata,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self._atomic_write(job_dir / "metadata.json", serialized)
        except OSError as error:
            raise StorageError(
                f"No se pudo persistir el trabajo {job.id} en {job_dir}: {error}"
            ) from error

    @staticmethod
    def _read_output(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except (OSError, UnicodeError) as error:
            raise StorageError(f"No se pudo leer la salida {path}: {error}") from error

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary_path.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary_path, path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
