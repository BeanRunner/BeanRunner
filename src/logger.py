import os
from models import Job, JobStatus

def recover_jobs_after_restart(jobs_storage: dict):
    """
    Recuperación después de reiniciar (RF-13, RNF-10, RNF-31):
    Inspecciona los trabajos persistidos al arrancar el servicio.
    """
    for job_id, job in jobs_storage.items():
        # Si el trabajo estaba marcado como RUNNING al apagarse el daemon
        if job.status == JobStatus.RUNNING:
            # Comprobamos si el proceso todavía existe en el Sistema Operativo (Linux)
            process_exists = False
            if job.pid:
                try:
                    # Enviar signal 0 a un PID comprueba si existe sin matarlo
                    os.kill(job.pid, 0)
                    process_exists = True
                except OSError:
                    process_exists = False
            
            if process_exists:
                # Opcional: Podría reasociarse, pero por seguridad del RNF-10 se marca interrumpido
                job.status = JobStatus.INTERRUPTED
                job.error_message = "Daemon restarted while process was active."
            else:
                # El proceso ya no existe en el SO
                job.status = JobStatus.INTERRUPTED
                job.error_message = "Process lost due to service restart or crash."
            
            job.finished_at = datetime.utcnow().isoformat()