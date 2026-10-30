import shlex
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src.Job_Runner import JobRunner
from src.main import interactive_loop, main as application_main, wait_for_job
from src.models import JobStatus
from src.storage import JobStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MainTests(unittest.TestCase):
    def test_entry_point_reports_successful_startup(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.main"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "[BeanRunner] Servicio JobRunner inicializado correctamente.\n"
            "Para trabajar localmente, ejecuta: python -m src.main --interactive\n",
        )
        self.assertEqual(result.stderr, "")

    def test_interactive_entry_uses_shared_cli(self) -> None:
        with patch("src.cli._interactive", return_value=0) as shared_cli:
            result = application_main(["--interactive"])

        self.assertEqual(result, 0)
        shared_cli.assert_called_once_with()

    def test_demo_preserves_job_submission_examples(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "--demo"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[PASS] Asignado ID único:", result.stdout)
        self.assertIn("[RECHAZADO] Error detectado (RF-02):", result.stdout)

    def test_interactive_mode_recovers_from_invalid_command_and_accepts_job(self) -> None:
        runner = JobRunner()
        python_command = shlex.join(
            [sys.executable, "-c", "print('cli funciona')"]
        )
        commands = StringIO(
            'submit echo "comillas incompletas\n'
            f"submit {python_command}\n"
            "list\n"
            "exit\n"
        )
        output = StringIO()

        result = interactive_loop(commands, output, runner)

        self.assertEqual(result, 0)
        self.assertIn("comando mal formado", output.getvalue())
        self.assertIn("Trabajo aceptado:", output.getvalue())
        self.assertIn("JOB ID", output.getvalue())
        self.assertEqual(len(runner.jobs), 1)
        job = next(iter(runner.jobs.values()))
        self.assertTrue(wait_for_job(runner, job.id))
        self.assertIn(
            job.status,
            (JobStatus.SUCCEEDED, JobStatus.CANCELED),
        )

    def test_interactive_mode_queries_job_and_reports_unknown_ids(self) -> None:
        runner = JobRunner()
        job = runner.submit_job([sys.executable, "-c", "print('query output')"])
        self.assertTrue(wait_for_job(runner, job.id))

        output = StringIO()
        commands = StringIO(
            f"status {job.id}\n"
            "status missing-id\n"
            f"output {job.id}\n"
            "output missing-id\n"
            "list INVALID\n"
            "exit\n"
        )
        interactive_loop(commands, output, runner)

        text = output.getvalue()
        self.assertIn('"status": "SUCCEEDED"', text)
        self.assertIn("query output", text)
        self.assertIn("No se encontró el trabajo missing-id", text)
        self.assertIn("Estado inválido", text)

    def test_interactive_mode_persists_jobs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            python_command = shlex.join(
                [
                    sys.executable,
                    "-c",
                    "import time; print('persisted cli', flush=True); time.sleep(0.1)",
                ]
            )
            commands = StringIO(f"submit {python_command}\nexit\n")
            output = StringIO()
            with patch(
                "src.main.default_data_dir",
                return_value=Path(temporary_directory),
            ):
                result = interactive_loop(commands, output)

            self.assertEqual(result, 0)
            jobs = JobStore(temporary_directory).load()
            self.assertEqual(len(jobs), 1)
            job = next(iter(jobs.values()))
            self.assertIn(
                job.status,
                (JobStatus.SUCCEEDED, JobStatus.CANCELED),
            )


if __name__ == "__main__":
    unittest.main()
