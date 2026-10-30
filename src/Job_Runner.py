"""Coordina el envío, la ejecución y la consulta de trabajos locales."""

import threading
from collections import deque
from pathlib import Path

from .events import EventLog
from .executor import JobExecutor
from .manager import JobManager
from .models import Job, JobStatus
from .storage import JobStore, StorageError
from .submitter import JobSubmitter, RawCommand


DEFAULT_MAX_QUEUE_SIZE = 5


class QueueCapacityError(ValueError):
    """Raised when a new job would exceed the waiting queue capacity."""


class JobRunner:
    """Coordina los componentes que participan en el ciclo de vida de un trabajo.

    `JobRunner` es la fachada que usa la CLI: recibe solicitudes, conserva los
    trabajos, los pone en cola y conecta al ejecutor con el gestor.
    """

    def __init__(
        self,
        max_concurrency: int = 3,
        data_dir: str | Path | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency debe ser al menos 1.")
        if max_queue_size < 1:
            raise ValueError("max_queue_size debe ser al menos 1.")

        # Almacenamiento compartido: submitter crea trabajos y manager los consulta.
        self.store = JobStore(data_dir) if data_dir is not None else None
        self.event_log = EventLog(data_dir) if data_dir is not None else None
        self.jobs: dict[str, Job] = self.store.load() if self.store else {}
        # La cola guarda IDs, no copias de Job; el orden es FIFO.
        self.queue: deque[str] = deque()
        # Límite de procesos hijos que pueden ejecutarse simultáneamente.
        self.max_concurrency = max_concurrency
        # Límite de trabajos en espera; los procesos activos no cuentan.
        self.max_queue_size = max_queue_size
        # El mismo lock coordina cambios en trabajos, cola y operaciones del manager.
        self._lock = threading.RLock()
        # Impide iniciar trabajos nuevos cuando shutdown ya comenzó.
        self._shutting_down = False
        self._request_jobs: dict[str, Job] = {}
        for job in self.jobs.values():
            if job.request_id is not None:
                if job.request_id in self._request_jobs:
                    raise ValueError(
                        f"Clave de solicitud duplicada en el almacenamiento: "
                        f"{job.request_id}"
                    )
                self._request_jobs[job.request_id] = job

        # Los tres componentes colaboran sobre los mismos trabajos:
        # submitter valida/crea, executor ejecuta y manager consulta/cancela.
        self.submitter = JobSubmitter(self.jobs)
        self.executor = JobExecutor()
        self.manager = JobManager(self.jobs, self.executor, self._lock)
        self._recover_incomplete_jobs()

    def submit_job(
        self,
        command: RawCommand,
        request_id: str | None = None,
        cwd: str | None = None,
    ) -> Job:
        """Valida, registra, encola e inicia si hay capacidad disponible."""
        valid_command = self.submitter.validate_command(command)
        if request_id is not None and (
            not request_id.strip() or len(request_id) > 128
        ):
            raise ValueError("request_id debe tener entre 1 y 128 caracteres.")
        if cwd is not None:
            working_directory = Path(cwd).expanduser().resolve()
            if not working_directory.is_dir():
                raise ValueError(f"El directorio de trabajo no existe: {cwd}")
            cwd = str(working_directory)

        with self._lock:
            if self._shutting_down:
                raise RuntimeError("BeanRunner se está cerrando y no acepta trabajos.")
            if request_id is not None and request_id in self._request_jobs:
                existing = self._request_jobs[request_id]
                if existing.command != valid_command or existing.cwd != cwd:
                    raise ValueError(
                        "request_id ya fue usado para otro comando o directorio."
                    )
                self._start_queued_jobs()
                self._record(
                    "duplicate_request",
                    existing,
                    {"request_id": request_id},
                )
                return existing

            queued_count = sum(
                1
                for job_id in self.queue
                if (queued_job := self.jobs.get(job_id)) is not None
                and queued_job.status == JobStatus.QUEUED
            )
            if queued_count >= self.max_queue_size:
                raise QueueCapacityError(
                    "Cola llena: capacidad máxima de "
                    f"{self.max_queue_size} trabajos en espera."
                )

            job = self.submitter.submit_job(valid_command)
            job.request_id = request_id
            job.cwd = cwd
            self.queue.append(job.id)
            if request_id is not None:
                self._request_jobs[request_id] = job
            try:
                if self.store is not None:
                    self.store.save(job)
            except StorageError:
                self.queue.remove(job.id)
                self.jobs.pop(job.id, None)
                if request_id is not None:
                    self._request_jobs.pop(request_id, None)
                raise
            try:
                self._record("job_accepted", job)
            except StorageError:
                self._start_queued_jobs()
                raise
            self._start_queued_jobs()
            return job

    def execute_job(self, job_id: str) -> Job | None:
        """Intenta iniciar trabajos en cola; se conserva por compatibilidad."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            self._start_queued_jobs()
            return job

    def get_job(self, job_id: str) -> Job | None:
        """Devuelve los metadatos o None si el ID no está registrado."""
        # La búsqueda concreta vive en manager, que comparte el registro jobs.
        return self.manager.get_job(job_id)

    def get_status(self, job_id: str) -> JobStatus | None:
        """Devuelve el estado actual o None si no existe ese ID."""
        job = self.get_job(job_id)
        return job.status if job is not None else None

    def list_jobs(self, status_filter: JobStatus | None = None) -> list[Job]:
        """Lista los trabajos registrados, opcionalmente filtrados por estado."""
        return self.manager.list_jobs(status_filter)

    def cancel_job(self, job_id: str) -> tuple[bool, str]:
        """Cancela un trabajo en cola o solicita detener su proceso hijo."""
        with self._lock:
            # manager cambia el estado o llama al executor; después se repone la cola.
            job = self.jobs.get(job_id)
            was_queued = job is not None and job.status == JobStatus.QUEUED
            result = self.manager.cancel_job(job_id)
            if job is not None and result[0]:
                if was_queued:
                    self._persist(job, "job_cancelled")
                else:
                    self._record("job_cancel_requested", job)
            elif not result[0]:
                if self.event_log is not None:
                    self.event_log.record(
                        "job_cancel_rejected",
                        job.id if job is not None else job_id,
                        {"reason": result[1]},
                    )
            self._start_queued_jobs()
            return result

    def shutdown(self) -> None:
        """Evita nuevos inicios, cancela la cola y termina procesos activos."""
        persistence_error: StorageError | None = None
        with self._lock:
            self._shutting_down = True
            # Lo pendiente no debe empezar durante el cierre: se marca cancelado.
            for job_id in tuple(self.queue):
                job = self.jobs.get(job_id)
                if job is not None and job.status == JobStatus.QUEUED:
                    job.transition_to(JobStatus.CANCELED)
                    try:
                        self._persist(job, "job_cancelled", {"reason": "shutdown"})
                    except StorageError as error:
                        persistence_error = persistence_error or error
            self.queue.clear()

            # Copiamos los trabajos activos para cancelarlos fuera del lock.
            running_jobs = [
                job for job in self.jobs.values() if job.status == JobStatus.RUNNING
            ]
            job_ids = list(self.jobs)

        for job in running_jobs:
            self.manager.cancel_job(job.id)
        self.executor.wait_for_jobs(job_ids)
        if persistence_error is not None:
            raise persistence_error

    def _start_queued_jobs(self) -> None:
        """Ocupa los espacios libres respetando el orden FIFO de la cola."""
        if self._shutting_down:
            return
        # Cada ejecución libera un slot; el callback vuelve a llenar ese espacio.
        while self.queue and self.executor.active_count < self.max_concurrency:
            job_id = self.queue.popleft()
            job = self.jobs.get(job_id)
            if job is None or job.status != JobStatus.QUEUED:
                continue
            started = self.executor.execute(
                job,
                self._on_job_finished,
                self._on_job_started,
            )
            if not started:
                self._persist(job, "job_failed", {"error": job.error_message})

    def _on_job_finished(self, _job: Job) -> None:
        """Reanuda la cola cuando el ejecutor termina de supervisar un trabajo."""
        with self._lock:
            event = (
                "job_cancelled"
                if _job.status == JobStatus.CANCELED
                else "job_finished"
            )
            self._persist(
                _job,
                event,
                {
                    "status": _job.status.value,
                    "exit_code": _job.exit_code,
                    "error": _job.error_message,
                },
            )
            self._start_queued_jobs()

    def _on_job_started(self, job: Job) -> None:
        self._persist(job, "job_started", {"pid": job.pid})

    def _persist(
        self,
        job: Job,
        event: str,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.store is not None:
            self.store.save(job)
        self._record(event, job, details)

    def _record(
        self,
        event: str,
        job: Job | None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.event_log is not None:
            self.event_log.record(event, job.id if job else None, details)

    def _recover_incomplete_jobs(self) -> None:
        for job in self.jobs.values():
            if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                continue
            previous_status = job.status.value
            job.error_message = (
                f"El servicio se reinició mientras el trabajo estaba {previous_status}; "
                "el proceso no se puede recuperar."
            )
            job.transition_to(JobStatus.FAILED)
            self._persist(
                job,
                "job_interrupted",
                {"previous_status": previous_status},
            )
