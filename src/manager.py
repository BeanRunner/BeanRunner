"""Consulta y cancela trabajos registrados por una instancia de JobRunner."""

import logging
import threading

from .executor import JobExecutor
from .models import Job, JobStatus


logger = logging.getLogger("JobManager")


class JobManager:
    """Ofrece consultas y cancelación sobre el almacenamiento del JobRunner.

    No crea ni ejecuta trabajos: lee el diccionario compartido y delega la
    terminación de procesos activos a JobExecutor.
    """

    def __init__(
        self,
        job_storage: dict[str, Job],
        executor: JobExecutor,
        lock: threading.RLock,
    ) -> None:
        # Estas referencias se comparten con JobRunner; no se hacen copias.
        self.jobs = job_storage
        self.executor = executor
        self._lock = lock

    def get_job(self, job_id: str) -> Job | None:
        """Busca por ID y devuelve None si el trabajo no existe."""
        with self._lock:
            return self.jobs.get(job_id)

    def list_jobs(self, status_filter: JobStatus | None = None) -> list[Job]:
        """Devuelve los trabajos y, si se indicó, filtra por estado."""
        with self._lock:
            # Copiar los valores evita exponer directamente la vista del diccionario.
            jobs = list(self.jobs.values())
            if status_filter is None:
                return jobs
            return [job for job in jobs if job.status == status_filter]

    def format_table(self, jobs: list[Job]) -> str:
        """Convierte metadatos de trabajos en una tabla para la CLI."""
        if not jobs:
            return "No hay trabajos registrados."

        header = f"{'JOB ID':<36} {'PID':<8} {'ESTADO':<12} {'CÓDIGO':<8} COMANDO"
        separator = "-" * len(header)
        lines = [header, separator]

        for job in jobs:
            # PID y código aún no existen mientras el trabajo espera en cola.
            pid = str(job.pid) if job.pid is not None else "-"
            exit_code = str(job.exit_code) if job.exit_code is not None else "-"
            command = " ".join(job.command)
            lines.append(
                f"{job.id:<36} {pid:<8} {job.status.value:<12} "
                f"{exit_code:<8} {command}"
            )

        return "\n".join(lines)

    def cancel_job(self, job_id: str) -> tuple[bool, str]:
        """Cancela un trabajo en cola o delega la terminación al executor."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return False, f"No se encontró ningún trabajo con ID {job_id}."

            if job.status == JobStatus.QUEUED:
                # Un trabajo en cola no tiene proceso que enviar a executor.cancel().
                job.transition_to(JobStatus.CANCELED)
                logger.info("Trabajo en cola %s cancelado.", job_id)
                return True, f"Trabajo {job_id} cancelado antes de ejecutarse."

            if job.status != JobStatus.RUNNING:
                return (
                    False,
                    f"No se puede cancelar: el trabajo está en estado {job.status.value}.",
                )

            success, message = self.executor.cancel(job)
            if success:
                logger.info("Se solicitó cancelar el trabajo %s.", job_id)
            return success, message
