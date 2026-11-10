import sys
import tempfile
import threading
import time
import unittest

from src.Job_Runner import JobRunner
from src.server import BeanServer
from src.service import ServiceClient, ServiceError


class ServiceTests(unittest.TestCase):
    def test_json_lines_client_uses_persistent_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = JobRunner(
                data_dir=temporary_directory,
                max_concurrency=1,
                max_queue_size=1,
            )
            server = BeanServer(("127.0.0.1", 0), runner)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            client = ServiceClient(
                temporary_directory,
                host="127.0.0.1",
                port=server.server_address[1],
            )
            try:
                submission = client.request(
                    "submit",
                    command=[
                        sys.executable,
                        "-c",
                        "import os; print(os.getcwd()); print('network-ready')",
                    ],
                    request_id="protocol-test",
                    cwd=temporary_directory,
                )
                job_id = submission["job"]["id"]

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    job = client.request("status", job_id=job_id)["job"]
                    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELED"}:
                        break
                    time.sleep(0.01)

                self.assertEqual(job["status"], "SUCCEEDED")
                output = client.request("output", job_id=job_id)
                self.assertEqual(
                    output["stdout"],
                    f"{temporary_directory}\nnetwork-ready\n",
                )
                self.assertEqual(client.request("jobs")["jobs"][0]["id"], job_id)
                with self.assertRaises(ServiceError):
                    client.request("cancel", job_id="missing-job")
                events = client.request("events")["events"]
                self.assertTrue(
                    any(
                        event["event"] == "job_finished"
                        and event["job_id"] == job_id
                        for event in events
                    )
                )
                self.assertTrue(
                    any(
                        event["event"] == "request_rejected"
                        and event["job_id"] == "missing-job"
                        for event in events
                    )
                )

                running = client.request(
                    "submit",
                    command=[
                        sys.executable,
                        "-c",
                        "import time; time.sleep(30)",
                    ],
                    request_id="queue-capacity-running",
                )
                queued = client.request(
                    "submit",
                    command=[
                        sys.executable,
                        "-c",
                        "import time; time.sleep(30)",
                    ],
                    request_id="queue-capacity-queued",
                )
                self.assertEqual(running["job"]["status"], "RUNNING")
                self.assertEqual(queued["job"]["status"], "QUEUED")
                with self.assertRaisesRegex(ServiceError, "Cola llena"):
                    client.request(
                        "submit",
                        command=[sys.executable, "-c", "print('rejected')"],
                        request_id="queue-capacity-rejected",
                    )
                self.assertEqual(len(client.request("jobs")["jobs"]), 3)

                self.assertTrue(client.request("stop")["ok"])
                server_thread.join(timeout=3)
                self.assertFalse(server_thread.is_alive())
            finally:
                if server_thread.is_alive():
                    server.shutdown()
                    server_thread.join(timeout=3)
                server.server_close()
                runner.shutdown()


if __name__ == "__main__":
    unittest.main()
