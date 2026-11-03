"""Command-line interface exposed as the globally installed `bean` command."""

import json
import os
import shlex
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from .service import ServiceClient, ServiceError, default_data_dir


COMMANDS = (
    "help",
    "jobs",
    "list",
    "status",
    "output",
    "cancel",
    "events",
    "stop",
    "doctor",
    "history",
    "exit",
    "quit",
)
STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED")
GLOBAL_OPTIONS = ("--json", "--quiet", "--no-color")
HELP = """Uso:
  bean [opciones] <comando> [argumentos...]
  bean [opciones] --interactive

Comandos:
  <programa> [args...]  Envia un programa como trabajo.
  run <programa> [...]  Ejecuta aunque el nombre coincida con un comando bean.
  jobs [estado]         Lista trabajos; estado es opcional.
  status <job-id>       Consulta metadatos de un trabajo.
  output <job-id>       Muestra stdout y stderr guardados.
  cancel <job-id>       Solicita cancelar un trabajo.
  events [limite]       Muestra eventos recientes (por defecto 50).
  doctor                Diagnostica configuración, almacenamiento y servicio.
  stop                  Detiene el servicio local.
  help [comando]        Muestra ayuda general o de un comando.

Opciones globales para comandos de una sola ejecución:
  --json                Emite resultados estructurados como JSON (antes del comando).
  --quiet               Suprime mensajes exitosos; los errores siguen visibles.
  --no-color            Desactiva colores (opciones antes del comando).
  --interactive         Abre la consola interactiva.

El servicio se inicia automáticamente y solo escucha en 127.0.0.1.
Ejemplo: bean --json jobs

En modo interactivo también están disponibles `history`, `exit` y `quit`.
"""

COMMAND_HELP = {
    "jobs": "Uso: bean jobs [estado]\nEstados: "
    + ", ".join(STATUSES)
    + "\nEjemplo: bean jobs RUNNING",
    "list": "Uso: bean list [estado]\nEs alias de `bean jobs [estado]`.",
    "status": "Uso: bean status <job-id>\nMuestra estado, PID, timestamps y código de salida.",
    "output": "Uso: bean output <job-id>\nMuestra por separado la salida stdout y stderr guardada.",
    "cancel": "Uso: bean cancel <job-id>\nCancela un trabajo en cola o solicita terminar el proceso activo.",
    "events": "Uso: bean events [limite]\nMuestra hasta 500 eventos recientes; por defecto, 50.",
    "stop": "Uso: bean stop\nDetiene el servicio local sin borrar el historial.",
    "doctor": "Uso: bean doctor\nRevisa Python, la carpeta de datos y la conexión al servicio.",
    "run": "Uso: bean run <programa> [argumentos...]\nEvita que un nombre reservado se interprete como comando bean.",
    "help": "Uso: bean help [comando]\nMuestra la ayuda general o la ayuda de un comando.",
    "--interactive": "Uso: bean --interactive\nInicia el modo interactivo con historial y autocompletado cuando están disponibles.",
}


def main(
    argv: Sequence[str] | None = None,
    *,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    client_factory: Callable[[], ServiceClient] = ServiceClient,
) -> int:
    return _run_command(
        argv,
        output_stream=output_stream,
        error_stream=error_stream,
        client_factory=client_factory,
    )


def _run_command(
    argv: Sequence[str] | None = None,
    *,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    client_factory: Callable[[], ServiceClient] = ServiceClient,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    output = output_stream or sys.stdout
    error_output = error_stream or sys.stderr
    json_output = False
    quiet = False
    no_color = "NO_COLOR" in os.environ
    while arguments and arguments[0] in GLOBAL_OPTIONS:
        option = arguments.pop(0)
        json_output = json_output or option == "--json"
        quiet = quiet or option == "--quiet"
        no_color = no_color or option == "--no-color"

    if not arguments:
        _write(HELP, output)
        return 0
    if arguments[0] in {"--help", "-h"}:
        _write(HELP, output)
        return 0
    if arguments[0] in {"--interactive", "interactive"}:
        return _interactive()
    if arguments[0] == "help":
        if len(arguments) > 2:
            return _usage("bean help [comando]", error_output)
        if len(arguments) == 1:
            _write(HELP, output)
            return 0
        command = arguments[1].lower()
        details = COMMAND_HELP.get(command)
        if details is None:
            return _usage(f"Comando desconocido: {command}", error_output)
        _write(details + "\n", output)
        return 0
    if len(arguments) > 1 and arguments[-1] in {"--help", "-h"}:
        details = COMMAND_HELP.get(arguments[0].lower())
        if details is None:
            _write(HELP, output)
        else:
            _write(details + "\n", output)
        return 0

    action = arguments[0].lower()
    try:
        client = client_factory()
        if action in {"jobs", "list"}:
            if len(arguments) > 2:
                return _usage("bean jobs [estado]", error_output)
            if len(arguments) == 2 and arguments[1].upper() not in STATUSES:
                return _usage(
                    f"Estado inválido. Usa uno de: {', '.join(STATUSES)}",
                    error_output,
                )
            response = client.request(
                "jobs",
                status=arguments[1] if len(arguments) == 2 else None,
            )
            _success(
                response["jobs"] if json_output else response["table"],
                output,
                quiet,
                json_output,
                color=not no_color,
            )
            return 0
        if action == "status":
            if len(arguments) != 2:
                return _usage("bean status <job-id>", error_output)
            response = client.request("status", job_id=arguments[1])
            _success(response["job"], output, quiet, json_output)
            return 0
        if action == "output":
            if len(arguments) != 2:
                return _usage("bean output <job-id>", error_output)
            response = client.request("output", job_id=arguments[1])
            data = {"stdout": response["stdout"], "stderr": response["stderr"]}
            human = f"STDOUT:\n{data['stdout']}STDERR:\n{data['stderr']}"
            _success(data if json_output else human, output, quiet, json_output)
            return 0
        if action == "cancel":
            if len(arguments) != 2:
                return _usage("bean cancel <job-id>", error_output)
            response = client.request("cancel", job_id=arguments[1])
            data = {"message": response["message"]}
            _success(data if json_output else data["message"], output, quiet, json_output)
            return 0
        if action == "events":
            if len(arguments) > 2:
                return _usage("bean events [limite]", error_output)
            try:
                limit = int(arguments[1]) if len(arguments) == 2 else 50
            except ValueError:
                return _usage("El límite debe ser un entero positivo.", error_output)
            if not 1 <= limit <= 500:
                return _usage("El límite debe estar entre 1 y 500.", error_output)
            response = client.request("events", limit=limit)
            _success(
                response["events"]
                if json_output
                else json.dumps(response["events"], ensure_ascii=False, indent=2),
                output,
                quiet,
                json_output,
            )
            return 0
        if action == "doctor":
            if len(arguments) != 1:
                return _usage("bean doctor", error_output)
            report, healthy = _doctor(client)
            human_report = "\n".join(
                f"{check['check']}: {check['status']} — {check['details']}"
                for check in report["checks"]
            )
            _success(
                report if json_output else human_report,
                output,
                quiet,
                json_output,
            )
            return 0 if healthy else 1
        if action == "stop":
            if len(arguments) != 1:
                return _usage("bean stop", error_output)
            response = client.request("stop")
            data = {"message": response["message"]}
            _success(data, output, quiet, json_output)
            return 0

        if action == "run":
            if len(arguments) < 2:
                return _usage("bean run <programa> [argumentos...]", error_output)
            command = arguments[1:]
        else:
            command = arguments
        response = client.request(
            "submit",
            command=command,
            cwd=os.getcwd(),
            request_id=str(uuid4()),
        )
        job = response["job"]
        _success(
            job if json_output else f"Trabajo aceptado: {job['id']} (estado: {job['status']})",
            output,
            quiet,
            json_output,
            color=not no_color,
        )
        return 0
    except (ServiceError, OSError, ValueError) as error:
        print(f"BeanRunner: {error}", file=error_output)
        return 1


def _success(
    value: Any,
    output: TextIO,
    quiet: bool,
    json_output: bool,
    *,
    color: bool = True,
) -> None:
    if quiet and not json_output:
        return
    if json_output:
        _write(json.dumps(value, ensure_ascii=False, indent=2) + "\n", output)
        return
    if isinstance(value, (dict, list)):
        _write(json.dumps(value, ensure_ascii=False, indent=2) + "\n", output)
        return
    text = str(value)
    if color and _supports_color(output):
        text = _color_statuses(text)
    _write(text + ("" if text.endswith("\n") else "\n"), output)


def _doctor(client: ServiceClient) -> tuple[dict[str, Any], bool]:
    data_dir = client.data_dir
    checks: list[dict[str, str]] = []
    healthy = True
    checks.append(
        {
            "check": "python",
            "status": "ok" if sys.version_info >= (3, 10) else "error",
            "details": f"Python {sys.version.split()[0]}",
        }
    )
    if sys.version_info < (3, 10):
        healthy = False

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=data_dir):
            pass
        checks.append(
            {"check": "data_dir", "status": "ok", "details": str(data_dir)}
        )
    except OSError as error:
        checks.append(
            {"check": "data_dir", "status": "error", "details": str(error)}
        )
        healthy = False

    checks.append(
        {
            "check": "service_config",
            "status": "ok",
            "details": f"{client.host}:{client.port}",
        }
    )
    try:
        client._exchange({"operation": "ping"})
    except (OSError, ServiceError) as error:
        checks.append(
            {
                "check": "service",
                "status": "stopped",
                "details": f"No responde; bean lo iniciará al enviar una operación ({error}).",
            }
        )
    else:
        checks.append(
            {"check": "service", "status": "ok", "details": "Servicio local disponible."}
        )
    return {"healthy": healthy, "checks": checks}, healthy


def _interactive(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    client_factory: Callable[[], ServiceClient] = ServiceClient,
) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    error_stream = error_stream or sys.stderr
    history_path = default_data_dir() / "cli_history.jsonl"
    history: list[str] = []
    readline_module = _load_readline()
    try:
        if history_path.is_file():
            history = [
                line
                for line in (
                    json.loads(entry)
                    for entry in history_path.read_text(encoding="utf-8").splitlines()
                )
                if isinstance(line, str)
            ][-500:]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(
            f"BeanRunner: no se pudo cargar el historial: {error}",
            file=error_stream,
        )
    if readline_module is not None:
        for line in history:
            readline_module.add_history(line)
        _configure_completion(readline_module)

    print(
        "BeanRunner. Escribe 'help' para ver comandos; 'exit' para salir.",
        file=output_stream,
    )
    try:
        client = client_factory()
    except (OSError, ServiceError, ValueError) as error:
        print(f"BeanRunner: {error}", file=error_stream)
        return 1
    while True:
        try:
            if input_stream is sys.stdin:
                line = input("bean> ")
            else:
                print("bean> ", end="", file=output_stream, flush=True)
                line = input_stream.readline()
                if not line:
                    break
                line = line.rstrip("\r\n")
        except EOFError:
            break
        except KeyboardInterrupt:
            print(file=output_stream)
            break
        if line.strip():
            history.append(line)
            if readline_module is not None and input_stream is not sys.stdin:
                readline_module.add_history(line)
            try:
                history_path.parent.mkdir(parents=True, exist_ok=True)
                history_path.write_text(
                    "".join(
                        json.dumps(item, ensure_ascii=False) + "\n"
                        for item in history[-500:]
                    ),
                    encoding="utf-8",
                )
                if os.name == "posix":
                    history_path.chmod(0o600)
            except OSError as error:
                print(
                    f"BeanRunner: no se pudo guardar el historial: {error}",
                    file=error_stream,
                )
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            print(f"Comando mal formado: {error}", file=output_stream)
            continue
        if not tokens:
            continue
        action = tokens[0].lower()
        if action in {"exit", "quit"}:
            break
        if action == "history":
            for index, entry in enumerate(
                history[-50:],
                start=max(1, len(history) - 49),
            ):
                print(f"{index:>3}  {entry}", file=output_stream)
            continue
        if action == "help" and len(tokens) == 1:
            print(HELP, end="", file=output_stream)
            continue
        if action in {"--help", "-h"}:
            print(HELP, end="", file=output_stream)
            continue
        if action == "help" and len(tokens) == 2:
            details = COMMAND_HELP.get(tokens[1].lower())
            print(details or f"Comando desconocido: {tokens[1]}", file=output_stream)
            continue
        if action == "doctor":
            report, _ = _doctor(client)
            print(json.dumps(report, ensure_ascii=False, indent=2), file=output_stream)
            continue
        _run_command(
            tokens,
            output_stream=output_stream,
            error_stream=error_stream,
            client_factory=client_factory,
        )
    return 0


def _load_readline() -> Any | None:
    try:
        import readline
    except ImportError:
        return None
    return readline


def _configure_completion(readline_module: Any) -> None:
    readline_module.parse_and_bind("tab: complete")

    def complete(text: str, state: int) -> str | None:
        try:
            line_buffer = readline_module.get_line_buffer()
            tokens = shlex.split(line_buffer)
        except ValueError:
            line_buffer = ""
            tokens = []
        if not tokens:
            candidates = COMMANDS
        elif len(tokens) == 1 and not line_buffer.endswith(" "):
            candidates = COMMANDS
        elif tokens[0].lower() in {"jobs", "list"}:
            candidates = STATUSES
        elif tokens[0].lower() == "help":
            candidates = COMMANDS
        else:
            candidates = ()
        matches = [item for item in candidates if item.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline_module.set_completer(complete)


def _supports_color(output: TextIO) -> bool:
    return (
        hasattr(output, "isatty")
        and output.isatty()
        and "NO_COLOR" not in os.environ
    )


def _color_statuses(text: str) -> str:
    colors = {
        "QUEUED": "\033[36m",
        "RUNNING": "\033[33m",
        "SUCCEEDED": "\033[32m",
        "FAILED": "\033[31m",
        "CANCELED": "\033[90m",
    }
    for status, color in colors.items():
        text = text.replace(status, f"{color}{status}\033[0m")
    return text


def _write(text: str, output: TextIO) -> None:
    output.write(text)


def _usage(message: str, error_output: TextIO = sys.stderr) -> int:
    print(f"Uso: {message}", file=error_output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
