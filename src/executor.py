"""Inicia, supervisa y cancela los procesos hijos de los trabajos."""

import logging
import subprocess
import threading
import time
from collections.abc import Callable

from .models import Job, JobStatus


logger = logging.getLogger("JobExecutor")
# El runner entrega esta función para que el executor avise cuando termina un hijo.
CompletionCallback = Callable[[Job], None]
StartCallback = Callable[[Job], None]


class JobExecutor:
    """Inicia procesos hijos y recoge sus resultados sin bloquear la CLI.

    JobRunner llama a `execute`; cada proceso se supervisa en un hilo y,
    al finalizar, el callback permite al runner iniciar el siguiente trabajo.
    """

    def __init__(self) -> None:
        # Relaciona cada ID de trabajo con su proceso mientras está activo.
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.monitors: dict[str, threading.Thread] = {}
        # Protege el registro de procesos frente al hilo monitor y al hilo CLI.
        self._lock = threading.Lock()

    def execute(
        self,
        job: Job,
        on_complete: CompletionCallback | None = None,
        on_started: StartCallback | None = None,
    ) -> bool:
        """Inicia el proceso hijo y devuelve si el lanzamiento tuvo éxito."""
        try:
            # Se pasa una lista de argumentos y no se invoca un shell del sistema.
            process = subprocess.Popen(
                job.command,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as error:
            # Si el ejecutable no existe, el trabajo falla antes de tener PID.
            job.error_message = str(error)
            job.transition_to(JobStatus.FAILED)
            logger.error("No se pudo iniciar el trabajo %s: %s", job.id, error)
            return False

        job.pid = process.pid
        job.transition_to(JobStatus.RUNNING)

        # Registrar el proceso permite a cancel() localizarlo usando el ID del job.
        with self._lock:
            self.processes[job.id] = process

        if on_started is not None:
            try:
                on_started(job)
            except Exception:
                process.kill()
                process.communicate()
                self._close_process_pipes(process)
                with self._lock:
                    self.processes.pop(job.id, None)
                job.error_message = "No se pudo persistir el inicio del trabajo."
                job.transition_to(JobStatus.FAILED)
                raise

        # communicate() espera al hijo en este hilo aparte, dejando libre la CLI.
        monitor = threading.Thread(
            target=self._monitor_process,
            args=(job, process, on_complete),
            name=f"beanrunner-job-{job.id}",
        )
        with self._lock:
            self.monitors[job.id] = monitor
        monitor.start()
        return True

    def cancel(
        self,
        job: Job,
        grace_seconds: float = 1.0,
        kill_timeout_seconds: float = 1.0,
    ) -> tuple[bool, str]:
        """Escalate from terminate to kill when a process ignores cancellation."""
        if grace_seconds < 0 or kill_timeout_seconds < 0:
            raise ValueError("Los plazos de cancelación no pueden ser negativos.")

        with self._lock:
            # El ID conecta el objeto Job con el Popen creado por execute().
            process = self.processes.get(job.id)
            if process is None or process.poll() is not None:
                return False, "El proceso ya terminó o no está activo."

            # El monitor usa esta marca para diferenciar cancelación de fallo.
            job.cancel_requested = True
            try:
                process.terminate()
            except OSError as error:
                job.cancel_requested = False
                logger.error("No se pudo terminar el trabajo %s: %s", job.id, error)
                return False, f"No se pudo terminar el proceso: {error}"

        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(
                "El trabajo %s no terminó tras %.2f s; escalando a kill.",
                job.id,
                grace_seconds,
            )
            try:
                process.kill()
            except OSError as error:
                if process.poll() is None:
                    logger.error(
                        "No se pudo escalar la cancelación del trabajo %s: %s",
                        job.id,
                        error,
                    )
                    return False, f"No se pudo forzar la terminación: {error}"
            try:
                process.wait(timeout=kill_timeout_seconds)
            except subprocess.TimeoutExpired:
                logger.error(
                    "El trabajo %s sigue activo %.2f s después de kill.",
                    job.id,
                    kill_timeout_seconds,
                )
                return (
                    False,
                    "No se pudo confirmar la terminación forzada; "
                    "el proceso sigue bajo supervisión.",
                )
            except OSError as error:
                logger.exception(
                    "No se pudo confirmar la terminación del trabajo %s",
                    job.id,
                )
                return False, f"No se pudo confirmar la terminación: {error}"
        except OSError as error:
            logger.exception(
                "Falló la espera de cancelación del trabajo %s",
                job.id,
            )
            return False, f"No se pudo esperar la terminación: {error}"

        return True, "Se solicitó la terminación del proceso."

    def get_process(self, job_id: str) -> subprocess.Popen[str] | None:
        """Devuelve el proceso registrado para ese trabajo, si sigue activo."""
        with self._lock:
            return self.processes.get(job_id)

    def wait_for_jobs(
        self,
        job_ids: list[str],
        timeout: float | None = None,
    ) -> None:
        """Wait for monitor callbacks to finish persisting terminal job state."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self._lock:
            monitors = [
                self.monitors[job_id]
                for job_id in job_ids
                if job_id in self.monitors
            ]
        for monitor in monitors:
            if deadline is None:
                monitor.join()
            else:
                monitor.join(max(0.0, deadline - time.monotonic()))

    @property
    def active_count(self) -> int:
        """Cuenta los procesos hijos que todavía están bajo supervisión."""
        with self._lock:
            return len(self.processes)

    def _monitor_process(
        self,
        job: Job,
        process: subprocess.Popen[str],
        on_complete: CompletionCallback | None,
    ) -> None:
        """Espera al hijo, recoge su salida y actualiza el estado del trabajo."""
        try:
            # communicate espera el fin y recoge ambas salidas para evitar mezclarlas.
            stdout, stderr = process.communicate()
            # El resultado capturado se guarda en el Job que consultan runner y CLI.
            job.stdout = stdout
            job.stderr = stderr
            job.exit_code = process.returncode
            if job.cancel_requested:
                job.transition_to(JobStatus.CANCELED)
            elif process.returncode == 0:
                job.transition_to(JobStatus.SUCCEEDED)
            else:
                job.transition_to(JobStatus.FAILED)
        except OSError as error:
            job.error_message = str(error)
            if not job.is_terminal:
                job.transition_to(JobStatus.FAILED)
            logger.exception("Falló la recolección del resultado del trabajo %s", job.id)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            self._close_process_pipes(process)
            # Al retirar el proceso se libera un slot; el callback permite llenar la cola.
            with self._lock:
                self.processes.pop(job.id, None)
            try:
                if on_complete is not None:
                    on_complete(job)
            finally:
                with self._lock:
                    self.monitors.pop(job.id, None)

    @staticmethod
    def _close_process_pipes(process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
