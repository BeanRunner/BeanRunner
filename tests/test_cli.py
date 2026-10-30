import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import cli
from src.service import ServiceError


class FakeClient:
    def __init__(self, response=None, error=None, data_dir=None) -> None:
        self.response = response or {"ok": True}
        self.error = error
        self.data_dir = data_dir or Path(".")
        self.host = "127.0.0.1"
        self.port = 43827
        self.requests = []

    def request(self, operation, **payload):
        self.requests.append((operation, payload))
        if self.error is not None:
            raise self.error
        return self.response

    def _exchange(self, request):
        if self.error is not None:
            raise self.error
        return {"ok": True}


class CliTests(unittest.TestCase):
    def run_cli(self, arguments, client, output=None, errors=None):
        output = output or io.StringIO()
        errors = errors or io.StringIO()
        result = cli.main(
            arguments,
            output_stream=output,
            error_stream=errors,
            client_factory=lambda: client,
        )
        return result, output, errors

    def test_contextual_help_does_not_connect_to_service(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        result = cli.main(
            ["help", "cancel"],
            output_stream=output,
            error_stream=errors,
            client_factory=lambda: self.fail("help should not connect"),
        )

        self.assertEqual(result, 0)
        self.assertIn("bean cancel <job-id>", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_json_option_outputs_structured_job_list(self) -> None:
        client = FakeClient(
            {
                "jobs": [{"id": "job-1", "status": "RUNNING"}],
                "table": "JOB ID ...",
            }
        )
        result, output, errors = self.run_cli(["--json", "jobs"], client)

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), client.response["jobs"])
        self.assertEqual(errors.getvalue(), "")

    def test_global_flags_do_not_remove_program_arguments(self) -> None:
        client = FakeClient({"job": {"id": "job-1", "status": "QUEUED"}})
        result, output, _ = self.run_cli(
            ["--json", sys.executable, "-c", "print('--json')"],
            client,
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            client.requests[0][1]["command"],
            [sys.executable, "-c", "print('--json')"],
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            {"id": "job-1", "status": "QUEUED"},
        )

    def test_quiet_suppresses_success_but_not_errors(self) -> None:
        result, output, errors = self.run_cli(
            ["--quiet", "cancel", "job-1"],
            FakeClient({"message": "Cancelación solicitada"}),
        )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")

        result, output, errors = self.run_cli(
            ["--quiet", "status", "missing"],
            FakeClient(error=ServiceError("No se encontró el trabajo")),
        )
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("No se encontró el trabajo", errors.getvalue())

    def test_usage_errors_return_two_without_connecting(self) -> None:
        result, output, errors = self.run_cli(
            ["events", "0"],
            FakeClient(),
        )
        self.assertEqual(result, 2)
        self.assertIn("entre 1 y 500", errors.getvalue())
        self.assertEqual(output.getvalue(), "")

    def test_doctor_reports_directory_and_stopped_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            client = FakeClient(
                error=OSError("connection refused"),
                data_dir=Path(temporary_directory),
            )
            result, output, errors = self.run_cli(
                ["--json", "doctor"],
                client,
            )

        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(report["healthy"])
        checks = {check["check"]: check["status"] for check in report["checks"]}
        self.assertEqual(checks["data_dir"], "ok")
        self.assertEqual(checks["service"], "stopped")
        self.assertEqual(errors.getvalue(), "")

    def test_interactive_history_persists_and_can_be_listed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_stream = io.StringIO("help cancel\nhistory\nexit\n")
            output_stream = io.StringIO()
            error_stream = io.StringIO()
            with patch(
                "src.cli.default_data_dir",
                return_value=Path(temporary_directory),
            ), patch("src.cli._load_readline", return_value=None):
                result = cli._interactive(
                    input_stream,
                    output_stream,
                    error_stream,
                    client_factory=lambda: FakeClient(),
                )

            history_path = Path(temporary_directory) / "cli_history.jsonl"
            history = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(result, 0)
            self.assertEqual(history, ["help cancel", "history", "exit"])
            self.assertIn("help cancel", output_stream.getvalue())
            self.assertEqual(error_stream.getvalue(), "")

    def test_completion_offers_commands_and_job_statuses(self) -> None:
        class FakeReadline:
            def __init__(self) -> None:
                self.line_buffer = "jobs "
                self.completer = None

            def parse_and_bind(self, _command) -> None:
                pass

            def set_completer(self, completer) -> None:
                self.completer = completer

            def get_line_buffer(self) -> str:
                return self.line_buffer

        readline = FakeReadline()
        cli._configure_completion(readline)

        self.assertEqual(readline.completer("RUN", 0), "RUNNING")
        self.assertEqual(readline.completer("RUN", 1), None)
        readline.line_buffer = "he"
        self.assertEqual(readline.completer("he", 0), "help")


if __name__ == "__main__":
    unittest.main()
