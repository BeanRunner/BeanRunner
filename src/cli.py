"""Command-line interface exposed as the globally installed `bean` command."""

import json
import os
import shlex
import sys
from collections.abc import Sequence
from uuid import uuid4

from .service import ServiceClient, ServiceError


HELP = """Uso:
  bean <programa> [argumentos...]     Envia el programa como trabajo.
  bean run <programa> [argumentos...] Ejecuta incluso nombres reservados.
  bean jobs [estado]                  Lista trabajos.
  bean status <job-id>                Consulta los metadatos de un trabajo.
  bean output <job-id>                Muestra stdout y stderr guardados.
  bean cancel <job-id>                Solicita cancelar un trabajo.
  bean events [limite]                Muestra eventos recientes.
  bean stop                           Detiene el servicio local.
  bean --interactive                  Abre la consola interactiva.

El servicio se inicia automáticamente y solo escucha en localhost.
Ejemplo: bean sleep 10
"""


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"--help", "-h", "help"}:
        print(HELP, end="")
        return 0
    if arguments[0] == "--interactive":
        return _interactive()

    client = ServiceClient()
    action = arguments[0].lower()
    try:
        if action in {"jobs", "list"}:
            if len(arguments) > 2:
                return _usage("bean jobs [estado]")
            response = client.request(
                "jobs",
                status=arguments[1] if len(arguments) == 2 else None,
            )
            print(response["table"])
            return 0
        if action == "status":
            if len(arguments) != 2:
                return _usage("bean status <job-id>")
            print(
                json.dumps(
                    client.request("status", job_id=arguments[1])["job"],
                    indent=2,
                )
            )
            return 0
        if action == "output":
            if len(arguments) != 2:
                return _usage("bean output <job-id>")
            response = client.request("output", job_id=arguments[1])
            print(f"STDOUT:\n{response['stdout']}", end="")
            print(f"STDERR:\n{response['stderr']}", end="")
            return 0
        if action == "cancel":
            if len(arguments) != 2:
                return _usage("bean cancel <job-id>")
            print(client.request("cancel", job_id=arguments[1])["message"])
            return 0
        if action == "events":
            if len(arguments) > 2:
                return _usage("bean events [limite]")
            try:
                limit = int(arguments[1]) if len(arguments) == 2 else 50
            except ValueError:
                return _usage("El límite debe ser un entero positivo.")
            response = client.request("events", limit=limit)
            print(json.dumps(response["events"], ensure_ascii=False, indent=2))
            return 0
        if action == "stop":
            if len(arguments) != 1:
                return _usage("bean stop")
            print(client.request("stop")["message"])
            return 0

        if action == "run":
            if len(arguments) < 2:
                return _usage("bean run <programa> [argumentos...]")
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
        print(f"Trabajo aceptado: {job['id']} (estado: {job['status']})")
        return 0
    except (ServiceError, OSError, ValueError) as error:
        print(f"BeanRunner: {error}", file=sys.stderr)
        return 1


def _interactive() -> int:
    client = ServiceClient()
    print("BeanRunner. Escribe 'help' para ver comandos; 'exit' para salir.")
    while True:
        try:
            line = input("bean> ")
        except EOFError:
            return 0
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            print(f"Comando mal formado: {error}")
            continue
        if not tokens:
            continue
        if tokens[0] in {"exit", "quit"}:
            return 0
        if tokens[0] in {"help", "--help", "-h"}:
            print(HELP, end="")
            continue
        result = main(tokens)
        if result:
            print(f"Operación fallida (código {result}).")


def _usage(message: str) -> int:
    print(f"Uso: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
