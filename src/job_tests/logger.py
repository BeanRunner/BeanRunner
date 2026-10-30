import logging
import sys

def setup_job_logger():
    logger = logging.getLogger("JobRunner")
    logger.setLevel(logging.INFO)
    
    # Evita duplicación de manejadores si se importa múltiples veces
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [JobRunner] [JobID: %(job_id)s] %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

# Adaptador para inyectar el Job ID en los logs fácilmente
class JobLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        job_id = self.extra.get("job_id", "GLOBAL")
        return f"[JobID: {job_id}] {msg}", kwargs