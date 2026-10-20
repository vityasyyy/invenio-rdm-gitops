import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCHEDULER = ROOT / "k8s/apps/invenio/invenio-scheduler-deployment.yaml"


class SchedulerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.deployment = yaml.safe_load(SCHEDULER.read_text())

    def test_rollout_cannot_run_two_beats_at_once(self):
        self.assertEqual(self.deployment["spec"]["replicas"], 1)
        self.assertEqual(
            self.deployment["spec"]["strategy"]["type"],
            "Recreate",
            "scheduler rollouts must stop the old beat before starting another",
        )

    def test_beat_command_stays_singleton_scheduler(self):
        args = self.deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(args[:4], ["celery", "-A", "invenio_app.celery", "beat"])
        self.assertIn("invenio_jobs.services.scheduler:RunScheduler", args)

    def test_liveness_budget_allows_slow_start_and_three_failed_checks(self):
        probe = self.deployment["spec"]["template"]["spec"]["containers"][0][
            "livenessProbe"
        ]
        self.assertEqual(probe["initialDelaySeconds"], 30)
        self.assertEqual(probe["periodSeconds"], 60)
        self.assertEqual(probe["failureThreshold"], 3)
        self.assertLessEqual(probe.get("timeoutSeconds", 1), probe["periodSeconds"])
        self.assertGreaterEqual(probe["periodSeconds"] * probe["failureThreshold"], 120)


if __name__ == "__main__":
    unittest.main()
