"""Loopback client and automatic lifecycle management for the local service."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 43827
MAX_REQUEST_SIZE = 1024 * 1024


class ServiceError(RuntimeError):
    """Raised when the local BeanRunner service cannot serve a request."""


def default_data_dir() -> Path:
    override = os.environ.get("BEANRUNNER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base / "BeanRunner"
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "beanrunner"


class ServiceClient:
    """Sends one JSON-lines request per TCP connection to the loopback daemon."""

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.data_dir = (
            Path(data_dir).expanduser().resolve()
            if data_dir
            else default_data_dir()
        )
        self.host = host or os.environ.get("BEANRUNNER_HOST", DEFAULT_HOST)
        if self.host != DEFAULT_HOST:
            raise ValueError("El servicio solo permite conexiones locales por ahora.")
        if port is None:
            try:
                port = int(os.environ.get("BEANRUNNER_PORT", DEFAULT_PORT))
            except ValueError as error:
                raise ValueError("BEANRUNNER_PORT debe ser un puerto válido.") from error
        if not 1 <= port <= 65535:
            raise ValueError("El puerto debe estar entre 1 y 65535.")
        self.port = port

    def request(self, operation: str, **payload: Any) -> dict[str, Any]:
        request = {"operation": operation, **payload}
        if operation == "submit":
            request.setdefault("request_id", str(uuid4()))
        self._ensure_running()
        try:
            return self._exchange(request)
        except OSError:
            if operation != "submit":
                raise
            # A stable request ID makes the retry safe if the first response was lost.
            self._ensure_running()
            return self._exchange(request)

    def _ensure_running(self) -> None:
        try:
            self._exchange({"operation": "ping"})
            return
        except (OSError, ServiceError):
            self._start_service()

        deadline = time.monotonic() + 8.0
        last_error: OSError | ServiceError | None = None
        while time.monotonic() < deadline:
            try:
                self._exchange({"operation": "ping"})
                return
            except (OSError, ServiceError) as error:
                last_error = error
                time.sleep(0.05)
        raise ServiceError(
            "No se pudo iniciar o conectar con el servicio local. "
            f"Revisa {self.data_dir / 'daemon.log'}."
        ) from last_error

    def _start_service(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.data_dir / "daemon.log"
        command = [
            sys.executable,
            "-m",
            "src.server",
            "--data-dir",
            str(self.data_dir),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        try:
            with log_path.open("ab") as log_stream:
                options: dict[str, Any] = {
                    "stdin": subprocess.DEVNULL,
                    "stdout": log_stream,
                    "stderr": log_stream,
                    "close_fds": True,
                }
                if os.name == "nt":
                    options["creationflags"] = (
                        subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                    )
                else:
                    options["start_new_session"] = True
                subprocess.Popen(command, **options)
        except OSError as error:
            raise ServiceError(f"No se pudo iniciar BeanRunner: {error}") from error

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        encoded = (
            json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_REQUEST_SIZE:
            raise ServiceError("La solicitud excede el tamaño máximo permitido.")
        with socket.create_connection(
            (self.host, self.port),
            timeout=2.0,
        ) as connection:
            connection.settimeout(10.0)
            connection.sendall(encoded)
            with connection.makefile("rb") as stream:
                response_line = stream.readline()
        if not response_line:
            raise ServiceError("El servicio cerró la conexión sin responder.")
        try:
            response = json.loads(response_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(f"Respuesta inválida del servicio: {error}") from error
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise ServiceError(
                "El servicio devolvió una respuesta con formato inválido."
            )
        if not response["ok"]:
            raise ServiceError(str(response.get("error", "Error desconocido.")))
        return response
