import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "k8s/apps/invenio"
SETUP = APP / "resources/invenio-setup-job.yaml"
INITIALIZE = APP / "initialization/invenio-initialize-job.yaml"
MUTATIONS = (
    "alembic upgrade",
    "files location create",
    "roles create",
    "access allow",
    "index init",
    "custom-fields init",
    "rdm fixtures",
    "rdm-records fixtures",
    "queues declare",
    "rebuild-index",
    "db create",
)


def job_command(path):
    job = yaml.safe_load(path.read_text())
    return job["spec"]["template"]["spec"]["containers"][0]["command"][2]


class SetupLifecycleTests(unittest.TestCase):
    def test_routine_hook_is_read_only(self):
        command = job_command(SETUP)
        for mutation in MUTATIONS:
            self.assertNotIn(mutation, command)
        for check in ("alembic current", "files location list", "index list", "queues list"):
            self.assertIn(check, command)

    def test_routine_hook_fails_closed_without_broad_swallowing(self):
        command = job_command(SETUP)
        self.assertNotIn("|| true", command)
        self.assertNotIn("|| echo", command)
        self.assertIn("exit 1", command)

    def test_initialization_is_not_reachable_from_automated_sync(self):
        kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
        self.assertFalse(any("initialization/" in resource for resource in kustomization["resources"]))
        self.assertTrue(INITIALIZE.is_file())

    def test_manual_initialization_has_all_data_operations(self):
        command = job_command(INITIALIZE)
        for mutation in ("alembic upgrade", "files location create", "roles create",
                         "access allow", "index init", "queues declare", "rebuild-index"):
            self.assertIn(mutation, command)

    def test_manual_initialization_does_not_swallow_unexpected_errors(self):
        command = job_command(INITIALIZE)
        self.assertNotIn("|| true", command)
        self.assertNotIn("|| echo", command)
        self.assertNotIn("2>&1 ||", command)
        self.assertEqual(yaml.safe_load(INITIALIZE.read_text())["spec"]["backoffLimit"], 0)

    def test_readme_and_operations_document_the_lifecycle(self):
        operations = (ROOT / "docs/operations.md").read_text()
        self.assertIn("initialization", operations.lower())
        self.assertIn("invenio-initialize", operations)
