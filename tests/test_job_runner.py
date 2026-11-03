import subprocess
import sys
import tempfile
import time
import unittest

from src.Job_Runner import JobRunner, QueueCapacityError
from src.executor import JobExecutor
from src.models import Job, JobStatus
from src.storage import JobStore


class FakeUnresponsiveProcess:
    def __init__(self, *, stops_after_kill: bool) -> None:
        self.stops_after_kill = stops_after_kill
        self.killed = False
        self.returncode = None
        self.actions: list[str] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")
        self.killed = True

    def wait(self, timeout: float) -> int:
        self.actions.append("wait")
        if self.killed and self.stops_after_kill:
            self.returncode = -9
            return self.returncode
        raise subprocess.TimeoutExpired("blocked", timeout)


class JobRunnerTests(unittest.TestCase):
    def wait_for_completion(self, runner: JobRunner, job_id: str) -> JobStatus:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = runner.get_status(job_id)
            if status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED):
                return status
            time.sleep(0.01)
        self.fail(f"Job {job_id} did not finish within the timeout")

    def test_submission_uses_validated_shared_job_storage(self) -> None:
        runner = JobRunner()

        with self.assertRaisesRegex(ValueError, "RF-02"):
            runner.submit_job('echo "unfinished')

        job = runner.submit_job([sys.executable, "-c", "print('runner ok')"])
        self.assertEqual(runner.jobs, {job.id: job})
        self.assertNotIn(job.id, runner.queue)
        self.assertIn(job.status, (JobStatus.RUNNING, JobStatus.SUCCEEDED))

    def test_successful_process_records_output_and_exit_code(self) -> None:
        runner = JobRunner()
        job = runner.submit_job(
            [
                sys.executable,
                "-c",
                "import time; print('runner ok'); time.sleep(0.05)",
            ]
        )
        process = runner.executor.get_process(job.id)

        runner.execute_job(job.id)

        self.assertEqual(self.wait_for_completion(runner, job.id), JobStatus.SUCCEEDED)
        self.assertEqual(job.exit_code, 0)
        self.assertEqual(job.stdout, "runner ok\n")
        self.assertEqual(job.stderr, "")
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        runner.executor.wait_for_jobs([job.id])
        self.assertNotIn(job.id, runner.executor.monitors)
        self.assertIsNotNone(process)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_failed_process_records_nonzero_exit_code(self) -> None:
        runner = JobRunner()
        job = runner.submit_job([sys.executable, "-c", "raise SystemExit(7)"])

        runner.execute_job(job.id)

        self.assertEqual(self.wait_for_completion(runner, job.id), JobStatus.FAILED)
        self.assertEqual(job.exit_code, 7)

    def test_launch_failure_marks_job_failed_without_raising(self) -> None:
        runner = JobRunner()
        job = runner.submit_job(["__beanrunner_missing_executable__"])

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIsNotNone(job.error_message)
        self.assertIsNotNone(job.finished_at)

    def test_running_job_can_be_canceled_and_reaped(self) -> None:
        runner = JobRunner()
        job = runner.submit_job(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        self.assertEqual(job.status, JobStatus.RUNNING)
        process = runner.executor.get_process(job.id)

        canceled, message = runner.cancel_job(job.id)

        self.assertTrue(canceled, message)
        final_status = self.wait_for_completion(runner, job.id)
        runner.executor.wait_for_jobs([job.id])
        self.assertEqual(final_status, JobStatus.CANCELED)
        self.assertEqual(job.status, JobStatus.CANCELED)
        self.assertIsNotNone(job.exit_code)
        self.assertIsNotNone(job.finished_at)
        self.assertIsNone(runner.executor.get_process(job.id))
        self.assertNotIn(job.id, runner.executor.monitors)
        self.assertIsNotNone(process)
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_cancellation_escalates_to_kill_after_grace_period(self) -> None:
        executor = JobExecutor()
        job = Job(command=["blocked"])
        job.transition_to(JobStatus.RUNNING)
        process = FakeUnresponsiveProcess(stops_after_kill=True)
        executor.processes[job.id] = process

        canceled, message = executor.cancel(
            job,
            grace_seconds=0.01,
            kill_timeout_seconds=0.02,
        )

        self.assertTrue(canceled, message)
        self.assertEqual(process.actions, ["terminate", "wait", "kill", "wait"])
        self.assertTrue(job.cancel_requested)

    def test_cancellation_reports_when_kill_does_not_stop_process(self) -> None:
        executor = JobExecutor()
        job = Job(command=["blocked"])
        job.transition_to(JobStatus.RUNNING)
        process = FakeUnresponsiveProcess(stops_after_kill=False)
        executor.processes[job.id] = process

        canceled, message = executor.cancel(
            job,
            grace_seconds=0,
            kill_timeout_seconds=0,
        )

        self.assertFalse(canceled)
        self.assertIn("sigue bajo supervisión", message)
        self.assertEqual(process.actions, ["terminate", "wait", "kill", "wait"])
        self.assertTrue(job.cancel_requested)
        self.assertIs(executor.get_process(job.id), process)

    def test_unconfirmed_escalation_is_persisted_as_failure_to_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(data_dir=temporary_directory)
            job = Job(command=["blocked"])
            job.transition_to(JobStatus.RUNNING)
            runner.jobs[job.id] = job
            process = FakeUnresponsiveProcess(stops_after_kill=False)
            runner.executor.processes[job.id] = process

            try:
                canceled, message = runner.cancel_job(job.id)

                self.assertFalse(canceled)
                self.assertIn("sigue bajo supervisión", message)
                events = runner.event_log.recent()
                self.assertEqual(events[-1]["event"], "job_cancel_escalation_failed")
                self.assertEqual(events[-1]["job_id"], job.id)
                persisted = JobStore(temporary_directory).load()[job.id]
                self.assertTrue(persisted.cancel_requested)
                self.assertEqual(persisted.status, JobStatus.RUNNING)
            finally:
                runner.executor.processes.pop(job.id, None)
                runner.jobs.clear()
                runner.shutdown()

    def test_runner_starts_queued_jobs_when_a_slot_frees(self) -> None:
        runner = JobRunner(max_concurrency=1)
        first = runner.submit_job(
            [sys.executable, "-c", "import time; time.sleep(0.2)"]
        )
        second = runner.submit_job([sys.executable, "-c", "print('second')"])

        self.assertEqual(first.status, JobStatus.RUNNING)
        self.assertEqual(second.status, JobStatus.QUEUED)

        first_status = self.wait_for_completion(runner, first.id)
        second_status = self.wait_for_completion(runner, second.id)
        self.assertEqual(first_status, JobStatus.SUCCEEDED)
        self.assertEqual(second_status, JobStatus.SUCCEEDED)
        self.assertEqual(first.status, JobStatus.SUCCEEDED)
        self.assertEqual(second.status, JobStatus.SUCCEEDED)
        self.assertEqual(second.stdout, "second\n")

    def test_cancel_queued_job_does_not_start_it(self) -> None:
        runner = JobRunner(max_concurrency=1)
        first = runner.submit_job(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        queued = runner.submit_job([sys.executable, "-c", "print('should not run')"])

        canceled, message = runner.cancel_job(queued.id)

        self.assertTrue(canceled, message)
        self.assertEqual(queued.status, JobStatus.CANCELED)
        self.assertIsNone(queued.pid)
        self.assertTrue(runner.cancel_job(first.id)[0])
        self.assertEqual(
            self.wait_for_completion(runner, first.id),
            JobStatus.CANCELED,
        )

    def test_rejects_submission_when_waiting_queue_is_full(self) -> None:
        runner = JobRunner(max_concurrency=1, max_queue_size=1)
        first = runner.submit_job(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        queued = runner.submit_job(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            request_id="queued-request",
        )

        try:
            self.assertEqual(first.status, JobStatus.RUNNING)
            self.assertEqual(queued.status, JobStatus.QUEUED)
            self.assertIs(
                runner.submit_job(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    request_id="queued-request",
                ),
                queued,
            )
            job_count = len(runner.jobs)

            with self.assertRaisesRegex(QueueCapacityError, "Cola llena"):
                runner.submit_job([sys.executable, "-c", "print('rejected')"])

            self.assertEqual(len(runner.jobs), job_count)
            self.assertEqual(len(runner.queue), 1)
            self.assertEqual(queued.status, JobStatus.QUEUED)
        finally:
            runner.shutdown()


if __name__ == "__main__":
    unittest.main()
