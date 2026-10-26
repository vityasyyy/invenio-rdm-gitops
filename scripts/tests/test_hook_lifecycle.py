import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
HOOKS = [
    ROOT / "k8s/infra/minio/resources/create-bucket-job.yaml",
    ROOT / "k8s/apps/invenio/resources/invenio-setup-job.yaml",
    ROOT / "k8s/infra/monitoring/resources/monitoring-pipecheck.yaml",
]


class HookLifecycleTests(unittest.TestCase):
    def test_hook_jobs_have_bounded_lifecycles_and_cleanup(self):
        for path in HOOKS:
            job = yaml.safe_load(path.read_text())
            self.assertGreater(job["spec"].get("activeDeadlineSeconds", 0), 0, path)
            policy = job["metadata"]["annotations"]["argocd.argoproj.io/hook-delete-policy"]
            self.assertEqual(set(policy.split(",")),
                             {"BeforeHookCreation", "HookSucceeded", "HookFailed"}, path)
