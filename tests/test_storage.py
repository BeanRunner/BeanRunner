import json
import sys
import tempfile
import unittest
from pathlib import Path

from src.Job_Runner import JobRunner
from src.events import EventLog
from src.models import Job, JobStatus
from src.storage import JobStore


class JobStoreTests(unittest.TestCase):
    def test_round_trip_persists_metadata_and_separate_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = JobStore(temporary_directory)
            job = Job(
                command=["echo", "persisted"],
                request_id="request-1",
                cwd=temporary_directory,
            )
            job.transition_to(JobStatus.RUNNING)
            job.stdout = "standard output\n"
            job.stderr = "standard error\n"
            job.exit_code = 0
            job.transition_to(JobStatus.SUCCEEDED)
            store.save(job)

            restored = store.load()[job.id]

            self.assertEqual(restored.to_dict(), job.to_dict())
            self.assertEqual(restored.stdout, job.stdout)
            self.assertEqual(restored.stderr, job.stderr)
            job_dir = Path(temporary_directory) / "jobs" / job.id
            self.assertTrue((job_dir / "metadata.json").is_file())
            self.assertTrue((job_dir / "stdout.log").is_file())
            self.assertTrue((job_dir / "stderr.log").is_file())

    def test_restart_marks_unfinished_work_failed_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = JobStore(temporary_directory)
            job = Job(command=["sleep", "10"], request_id="request-2")
            job.transition_to(JobStatus.RUNNING)
            store.save(job)

            runner = JobRunner(data_dir=temporary_directory)
            restored = runner.get_job(job.id)

            self.assertIsNotNone(restored)
            self.assertEqual(restored.status, JobStatus.FAILED)
            self.assertIn("reinició", restored.error_message)
            self.assertEqual(store.load()[job.id].status, JobStatus.FAILED)
            events = EventLog(temporary_directory).recent()
            self.assertEqual(events[-1]["event"], "job_interrupted")
            self.assertEqual(events[-1]["job_id"], job.id)
            runner.shutdown()

    def test_metadata_is_persisted_through_queue_running_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(
                max_concurrency=1,
                data_dir=temporary_directory,
                max_queue_size=1,
            )
            try:
                running = runner.submit_job(
                    [sys.executable, "-c", "import time; time.sleep(30)"]
                )
                queued = runner.submit_job(
                    [sys.executable, "-c", "print('queued')"]
                )
                running_metadata_path = (
                    Path(temporary_directory)
                    / "jobs"
                    / running.id
                    / "metadata.json"
                )
                queued_metadata_path = (
                    Path(temporary_directory)
                    / "jobs"
                    / queued.id
                    / "metadata.json"
                )

                running_metadata = json.loads(
                    running_metadata_path.read_text(encoding="utf-8")
                )
                queued_metadata = json.loads(
                    queued_metadata_path.read_text(encoding="utf-8")
                )
                self.assertEqual(running_metadata["status"], "RUNNING")
                self.assertFalse(running_metadata["cancel_requested"])
                self.assertEqual(queued_metadata["status"], "QUEUED")

                canceled, message = runner.cancel_job(running.id)
                self.assertTrue(canceled, message)
                runner.executor.wait_for_jobs([running.id])

                running_metadata = json.loads(
                    running_metadata_path.read_text(encoding="utf-8")
                )
                self.assertEqual(running_metadata["status"], "CANCELED")
                self.assertTrue(running_metadata["cancel_requested"])
                self.assertIsNotNone(running_metadata["finished_at"])

                canceled, message = runner.cancel_job(queued.id)
                self.assertTrue(canceled, message)
                queued_metadata = json.loads(
                    queued_metadata_path.read_text(encoding="utf-8")
                )
                self.assertEqual(queued_metadata["status"], "CANCELED")
                self.assertIsNotNone(queued_metadata["finished_at"])
            finally:
                runner.shutdown()

    def test_corrupt_metadata_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = JobStore(temporary_directory)
            job_dir = Path(temporary_directory) / "jobs" / "corrupt"
            job_dir.mkdir()
            (job_dir / "metadata.json").write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "No se pudieron cargar"):
                store.load()


if __name__ == "__main__":
    unittest.main()
