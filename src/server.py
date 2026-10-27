"""Loopback JSON-lines service used by the installed `bean` command."""

from __future__ import annotations

import argparse
import json
import logging
import socketserver
import threading
from pathlib import Path
from typing import Any

from .Job_Runner import JobRunner
from .events import EventLog
from .models import JobStatus
from .service import DEFAULT_HOST, DEFAULT_PORT, MAX_REQUEST_SIZE


class BeanRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw_request = self.rfile.readline(MAX_REQUEST_SIZE + 1)
        if len(raw_request) > MAX_REQUEST_SIZE:
            self._record_rejection({}, ValueError("Solicitud demasiado grande."))
            self._write_response({"ok": False, "error": "Solicitud demasiado grande."})
            return
        request: dict[str, Any] = {}
        try:
            parsed_request = json.loads(raw_request.decode("utf-8"))
            if not isinstance(parsed_request, dict):
                raise ValueError("La solicitud debe ser un objeto JSON.")
            request = parsed_request
            response = self.bean_server.dispatch(request)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._record_rejection(request, error)
            response = {"ok": False, "error": f"Solicitud inválida: {error}"}
        except (OSError, RuntimeError) as error:
            self._record_rejection(request, error)
            response = {"ok": False, "error": str(error)}
        self._write_response(response)

    def _record_rejection(self, request: dict[str, Any], error: Exception) -> None:
        event_log = self.bean_server.job_runner.event_log
        if event_log is None:
            return
        job_id = request.get("job_id")
        event_log.record(
            "request_rejected",
            job_id if isinstance(job_id, str) else None,
            {
                "operation": request.get("operation"),
                "error": str(error),
            },
        )

    @property
    def bean_server(self) -> "BeanServer":
        if not isinstance(self.server, BeanServer):
            raise RuntimeError("El handler requiere una instancia BeanServer.")
        return self.server

    def _write_response(self, response: dict[str, Any]) -> None:
        encoded = (
            json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self.wfile.write(encoded)
        self.wfile.flush()


class BeanServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        runner: JobRunner | None,
    ) -> None:
        self.runner = runner
        self.stopping = threading.Event()
        super().__init__(address, BeanRequestHandler)

    @property
    def job_runner(self) -> JobRunner:
        if self.runner is None:
            raise RuntimeError("El servicio todavía no inicializó JobRunner.")
        return self.runner

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        runner = self.job_runner
        operation = request.get("operation")
        if not isinstance(operation, str):
            raise ValueError("Falta el campo operation.")
        if operation == "ping":
            return {"ok": True}
        if operation == "submit":
            command = request.get("command")
            if not isinstance(command, list) or not all(
                isinstance(argument, str) for argument in command
            ):
                raise ValueError("command debe ser una lista de argumentos de texto.")
            request_id = request.get("request_id")
            cwd = request.get("cwd")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("request_id debe ser texto no vacío.")
            if cwd is not None and not isinstance(cwd, str):
                raise ValueError("cwd debe ser texto.")
            job = runner.submit_job(command, request_id, cwd)
            return {"ok": True, "job": job.to_dict()}
        if operation == "jobs":
            status_name = request.get("status")
            if status_name is not None and not isinstance(status_name, str):
                raise ValueError("status debe ser texto.")
            try:
                status = JobStatus[status_name.upper()] if status_name else None
            except KeyError as error:
                valid = ", ".join(item.value for item in JobStatus)
                raise ValueError(f"Estado inválido; opciones: {valid}") from error
            jobs = runner.list_jobs(status)
            return {
                "ok": True,
                "jobs": [job.to_dict() for job in jobs],
                "table": runner.manager.format_table(jobs),
            }
        if operation == "status":
            job_id = self._required_string(request, "job_id")
            job = runner.get_job(job_id)
            if job is None:
                raise ValueError(f"No se encontró el trabajo {job_id}.")
            return {"ok": True, "job": job.to_dict()}
        if operation == "output":
            job_id = self._required_string(request, "job_id")
            job = runner.get_job(job_id)
            if job is None:
                raise ValueError(f"No se encontró el trabajo {job_id}.")
            return {"ok": True, "stdout": job.stdout, "stderr": job.stderr}
        if operation == "cancel":
            job_id = self._required_string(request, "job_id")
            success, message = runner.cancel_job(job_id)
            if not success:
                raise ValueError(message)
            return {"ok": True, "message": message}
        if operation == "events":
            limit_value = request.get("limit", 50)
            if not isinstance(limit_value, int) or isinstance(limit_value, bool):
                raise ValueError("limit debe ser un entero.")
            if limit_value > 500:
                raise ValueError("limit no puede exceder 500.")
            if runner.event_log is None:
                return {"ok": True, "events": []}
            return {"ok": True, "events": runner.event_log.recent(limit_value)}
        if operation == "stop":
            if runner.event_log is not None:
                runner.event_log.record("service_stop")
            threading.Thread(target=self._stop, daemon=True).start()
            return {"ok": True, "message": "Servicio local detenido."}
        raise ValueError(f"Operación desconocida: {operation}")

    @staticmethod
    def _required_string(request: dict[str, Any], key: str) -> str:
        value = request.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} debe ser texto no vacío.")
        return value

    def _stop(self) -> None:
        if not self.stopping.is_set():
            self.stopping.set()
            self.shutdown()


def serve(data_dir: Path, host: str, port: int) -> None:
    if host != "127.0.0.1":
        raise ValueError("El servicio solo puede escuchar en loopback por ahora.")
    server = BeanServer((host, port), None)
    runner: JobRunner | None = None
    try:
        event_log = EventLog(data_dir)
        runner = JobRunner(data_dir=data_dir)
        server.runner = runner
        event_log.record("service_started", details={"host": host, "port": port})
        with server:
            server.serve_forever(poll_interval=0.1)
    finally:
        if runner is not None:
            runner.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servicio local de BeanRunner")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    arguments = parser.parse_args(argv)
    log_path = arguments.data_dir / "daemon.log"
    arguments.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        encoding="utf-8",
    )
    try:
        serve(arguments.data_dir, arguments.host, arguments.port)
    except (OSError, ValueError, RuntimeError) as error:
        logging.exception("No se pudo iniciar el servicio")
        print(f"BeanRunner: no se pudo iniciar el servicio: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
