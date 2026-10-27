import uuid
import unittest

from src.models import JobStatus
from src.submitter import JobSubmitter


class JobSubmitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = {}
        self.submitter = JobSubmitter(self.jobs)

    def test_submit_parses_command_and_registers_queued_job(self) -> None:
        job = self.submitter.submit_job("python3 -c \"print('hi')\"")

        self.assertEqual(job.command, ["python3", "-c", "print('hi')"])
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(self.jobs[job.id], job)
        self.assertEqual(str(uuid.UUID(job.id)), job.id)
        self.assertIsNotNone(job.to_dict()["created_at"])

    def test_submit_accepts_argument_list_and_generates_unique_ids(self) -> None:
        first_job = self.submitter.submit_job(["echo", "hello"])
        second_job = self.submitter.submit_job(["echo", "world"])

        self.assertEqual(first_job.command, ["echo", "hello"])
        self.assertNotEqual(first_job.id, second_job.id)

    def test_rejects_empty_and_malformed_commands(self) -> None:
        invalid_commands = (
            None,
            "",
            "   ",
            'echo "unfinished',
            [],
            [" ", ""],
            ["echo", 1],
            {"command": "echo"},
        )

        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "RF-02"):
                    self.submitter.submit_job(command)

        self.assertEqual(self.jobs, {})

    def test_valid_submission_succeeds_after_invalid_commands(self) -> None:
        invalid_commands = (
            "",
            "   ",
            'echo "unfinished',
            [" ", ""],
            ["echo", 1],
        )

        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    self.submitter.submit_job(command)
                self.assertEqual(self.jobs, {})

        job = self.submitter.submit_job(["echo", "still available"])

        self.assertEqual(job.command, ["echo", "still available"])
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(self.jobs, {job.id: job})


if __name__ == "__main__":
    unittest.main()
