import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "k8s/infra/security"
FILE = "network-policies/recovery-db-allow.yaml"
DESTINATION = {
    "namespaceSelector": {
        "matchLabels": {"kubernetes.io/metadata.name": "recovery-db-20261019"}
    },
    "podSelector": {"matchLabels": {"cnpg.io/cluster": "postgres-recovery-drill"}},
}


class RecoveryNetworkTests(unittest.TestCase):
    def test_only_drill_minio_and_operator_status_connections_are_allowed(self):
        policies = list(yaml.safe_load_all((SECURITY / FILE).read_text()))
        self.assertEqual(len(policies), 2)
        self.assertEqual(
            {(p["metadata"]["namespace"], p["metadata"]["name"]) for p in policies},
            {("minio", "allow-recovery-db-read"),
             ("database", "allow-operator-recovery-status")},
        )
        by_namespace = {p["metadata"]["namespace"]: p for p in policies}
        self.assertEqual(by_namespace["minio"]["spec"], {
            "podSelector": {},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [DESTINATION],
                         "ports": [{"protocol": "TCP", "port": 9000}]}],
        })
        self.assertEqual(by_namespace["database"]["spec"], {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "cloudnative-pg"}},
            "policyTypes": ["Egress"],
            "egress": [{"to": [DESTINATION],
                        "ports": [{"protocol": "TCP", "port": 8000}]}],
        })
        resources = yaml.safe_load((SECURITY / "kustomization.yaml").read_text())["resources"]
        self.assertIn(FILE, resources)


if __name__ == "__main__":
    unittest.main()
