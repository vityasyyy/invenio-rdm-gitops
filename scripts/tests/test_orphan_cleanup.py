import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "k8s/apps/invenio"
SCRIPT = APP / "resources/orphan-cleanup-configmap.yaml"
CRONJOB = APP / "resources/orphan-cleanup-cronjob.yaml"
KUSTOMIZATION = APP / "kustomization.yaml"
INVENIO_PROJECT = ROOT / "k8s/infra/argocd/resources/invenio-project.yaml"


class OrphanCleanupTests(unittest.TestCase):
    def test_cleanup_uses_single_object_deletes_and_the_db_reference_set(self):
        script = yaml.safe_load(SCRIPT.read_text())["data"]["cleanup.py"]
        self.assertIn("FileInstance.query.join", script)
        self.assertIn("ObjectVersion.file_id == FileInstance.id", script)
        self.assertIn("fs.rm_file", script)
        self.assertNotIn("fs.rm(", script)
        self.assertIn("clear_orphaned_files", script)
        self.assertIn("force_delete_check=lambda file: True", script)

    def test_cleanup_is_a_bounded_declarative_cronjob(self):
        job = yaml.safe_load(CRONJOB.read_text())
        self.assertEqual(job["spec"]["schedule"], "15 4 * * *")
        self.assertEqual(job["spec"]["concurrencyPolicy"], "Forbid")
        pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertGreater(job["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"], 0)
        self.assertIn("ttlSecondsAfterFinished", job["spec"]["jobTemplate"]["spec"])
        self.assertEqual(pod["containers"][0]["command"][-1],
                         "exec(open('/scripts/cleanup.py').read())")
        self.assertIn("invenio-app-config", [source["configMapRef"]["name"]
                                             for source in pod["containers"][0]["envFrom"]
                                             if "configMapRef" in source])

    def test_cleanup_resources_are_in_the_invenio_kustomization(self):
        resources = yaml.safe_load(KUSTOMIZATION.read_text())["resources"]
        self.assertIn("resources/orphan-cleanup-configmap.yaml", resources)
        self.assertIn("resources/orphan-cleanup-cronjob.yaml", resources)

    def test_invenio_project_permits_the_cleanup_cronjob(self):
        project = yaml.safe_load(INVENIO_PROJECT.read_text())
        self.assertIn({"group": "batch", "kind": "CronJob"},
                      project["spec"]["namespaceResourceWhitelist"])
