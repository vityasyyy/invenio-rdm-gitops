import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "k8s/infra/security"
DRILL_POLICY = "network-policies/recovery-db-allow.yaml"
DRILL_TOKENS = (
    "recovery-canary-src-20261019",
    "recovery-canary-dst-20261019",
    "recovery-db-20261019",
    "postgres-recovery-drill",
)


class RecoveryNetworkCleanupTests(unittest.TestCase):
    def test_drill_network_policy_file_is_removed(self):
        self.assertFalse((SECURITY / DRILL_POLICY).exists())

    def test_security_kustomization_no_longer_references_drill_policy(self):
        resources = yaml.safe_load((SECURITY / "kustomization.yaml").read_text())["resources"]
        self.assertNotIn(DRILL_POLICY, resources)

    def test_no_committed_security_manifest_targets_drill_resources(self):
        for path in SECURITY.rglob("*.yaml"):
            text = path.read_text()
            for token in DRILL_TOKENS:
                self.assertNotIn(token, text, f"{path} still references {token}")


if __name__ == "__main__":
    unittest.main()
