import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCHEDULER = ROOT / "k8s/apps/invenio/resources/invenio-scheduler-deployment.yaml"


class SchedulerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.deployment = yaml.safe_load(SCHEDULER.read_text())

    def test_deployment_rollout_uses_recreate(self):
        self.assertEqual(self.deployment["spec"]["replicas"], 1)
        self.assertEqual(
            self.deployment["spec"].get("strategy", {}).get("type"),
            "Recreate",
            "scheduler Deployment rollouts must stop the old beat before starting another",
        )

    def test_beat_command_stays_singleton_scheduler(self):
        args = self.deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(args[:4], ["celery", "-A", "invenio_app.celery", "beat"])
        self.assertIn("invenio_jobs.services.scheduler:RunScheduler", args)


if __name__ == "__main__":
    unittest.main()
