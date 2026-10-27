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
