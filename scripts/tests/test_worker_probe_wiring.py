"""Wiring tests for the worker probe switch.

The probes must invoke the helper that ships in the image, must not build the
application, and must keep the reply budgets that define their failure timing.
The helper's own behaviour is covered in ``test_worker_probe_contracts.py``.
"""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "k8s/apps/invenio/resources/invenio-worker-deployment.yaml"

PROBE_NAMES = ("startupProbe", "livenessProbe", "readinessProbe")
PROBE_PATH = "/opt/invenio/var/instance/worker_healthcheck.py"
BUDGETS = {"startupProbe": "30", "livenessProbe": "30", "readinessProbe": "15"}


class WorkerProbeWiringTests(unittest.TestCase):
    def setUp(self):
        deployment = yaml.safe_load(WORKER.read_text())
        containers = deployment["spec"]["template"]["spec"]["containers"]
        self.container = next(c for c in containers if c["name"] == "worker")

    def command(self, name):
        return " ".join(self.container[name]["exec"]["command"])

    def test_probes_run_the_helper_without_importing_the_app(self):
        for name in PROBE_NAMES:
            command = self.command(name)
            with self.subTest(probe=name):
                self.assertIn(PROBE_PATH, command)
                self.assertNotIn("invenio_app.celery", command)
                self.assertNotIn(" -A", command)
                self.assertNotIn("inspect ping", command)

    def test_probe_reply_budgets_are_unchanged(self):
        budgets = {name: self.command(name).rsplit(" ", 1)[-1] for name in PROBE_NAMES}
        self.assertEqual(
            budgets,
            BUDGETS,
            "probe reply budgets are part of the health contract",
        )


if __name__ == "__main__":
    unittest.main()
