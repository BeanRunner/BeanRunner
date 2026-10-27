import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.Job_Runner import JobRunner
from src.models import JobStatus


class IdempotencyTests(unittest.TestCase):
    def test_same_request_id_returns_the_same_job_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(data_dir=temporary_directory)
            command = [sys.executable, "-c", "import time; time.sleep(0.1)"]
            original = runner.submit_job(command, "stable-key", temporary_directory)
            repeated = runner.submit_job(command, "stable-key", temporary_directory)

            self.assertIs(repeated, original)
            self.assertEqual(len(runner.jobs), 1)

            deadline = time.monotonic() + 5
            while not original.is_terminal and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(original.status, JobStatus.SUCCEEDED)
            runner.shutdown()

            restarted = JobRunner(data_dir=temporary_directory)
            retried = restarted.submit_job(
                command,
                "stable-key",
                temporary_directory,
            )
            self.assertEqual(retried.id, original.id)
            self.assertEqual(len(restarted.jobs), 1)
            restarted.shutdown()

    def test_concurrent_same_key_creates_one_job_and_logs_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(data_dir=temporary_directory)
            command = [sys.executable, "-c", "import time; time.sleep(0.1)"]
            with ThreadPoolExecutor(max_workers=8) as executor:
                jobs = list(
                    executor.map(
                        lambda _: runner.submit_job(
                            command,
                            "concurrent-key",
                            temporary_directory,
                        ),
                        range(16),
                    )
                )

            self.assertEqual(len({job.id for job in jobs}), 1)
            self.assertEqual(len(runner.jobs), 1)
            duplicate_events = [
                event
                for event in runner.event_log.recent(100)
                if event["event"] == "duplicate_request"
            ]
            self.assertEqual(len(duplicate_events), 15)
            deadline = time.monotonic() + 5
            while not jobs[0].is_terminal and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(jobs[0].is_terminal)
            runner.shutdown()

    def test_reusing_request_id_for_different_command_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(data_dir=temporary_directory)
            first = runner.submit_job(
                [sys.executable, "-c", "print('first')"],
                "collision-key",
                temporary_directory,
            )

            with self.assertRaisesRegex(ValueError, "ya fue usado"):
                runner.submit_job(
                    [sys.executable, "-c", "print('different')"],
                    "collision-key",
                    temporary_directory,
                )
            runner.cancel_job(first.id)
            runner.shutdown()


if __name__ == "__main__":
    unittest.main()
