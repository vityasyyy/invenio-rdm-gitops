import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class RuntimeSelectionTests(unittest.TestCase):
    def test_backup_exclusion_covers_both_local_api_endpoints(self):
        config = yaml.safe_load(
            (ROOT / "k8s/infra/argocd/patches/argocd-cm.yaml").read_text()
        )
        exclusions = yaml.safe_load(config["data"]["resource.exclusions"])
        for endpoint in (
            "https://kubernetes.default.svc",
            "https://10.43.0.1:443",
        ):
            for kind in ("Backup", "Restore"):
                self.assertTrue(any(
                    "velero.io" in rule["apiGroups"]
                    and kind in rule["kinds"]
                    and endpoint in rule["clusters"]
                    for rule in exclusions
                ), f"{kind} is not excluded for {endpoint}")

    def test_custom_rules_match_prometheus_release_selector(self):
        rules = yaml.safe_load(
            (ROOT / "k8s/infra/monitoring/alerts.yaml").read_text()
        )
        app = yaml.safe_load((ROOT / "argocd/apps/monitoring.yaml").read_text())
        self.assertEqual(
            rules["metadata"].get("labels", {}).get("release"),
            app["metadata"]["name"],
        )


if __name__ == "__main__":
    unittest.main()
