import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "k8s/infra/monitoring"


class ObservabilityContracts(unittest.TestCase):
    def test_grafana_egress_is_backend_and_port_scoped(self):
        policy = yaml.safe_load((MONITORING / "resources/grafana-backends-egress.yaml").read_text())
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], {"app.kubernetes.io/name": "grafana"})
        self.assertEqual(policy["spec"]["policyTypes"], ["Egress"])
        for rule, name, port in zip(policy["spec"]["egress"], ["loki", "alertmanager"], [3100, 9093], strict=True):
            self.assertEqual(rule["ports"], [{"port": port, "protocol": "TCP"}])
            self.assertEqual(rule["to"][0]["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "monitoring"})
            self.assertEqual(rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"], name)
        self.assertEqual(policy["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"], "single-binary")

    def test_grafana_backend_policy_is_rendered(self):
        kustomization = yaml.safe_load((MONITORING / "kustomization.yaml").read_text())
        self.assertIn("resources/grafana-backends-egress.yaml", kustomization["resources"])

    def test_opensearch_has_one_monitor_with_existing_name_and_filtered_service(self):
        values = yaml.safe_load((ROOT / "k8s/dependencies/opensearch/values.yaml").read_text())
        self.assertIs(values["serviceMonitor"]["enabled"], False)
        monitor = yaml.safe_load((ROOT / "k8s/dependencies/opensearch/resources/servicemonitor.yaml").read_text())
        self.assertEqual(monitor["metadata"]["name"], "opensearch-cluster-master-service-monitor")
        self.assertEqual(monitor["metadata"]["labels"]["release"], "monitoring")
        self.assertEqual(monitor["spec"]["endpoints"][0]["relabelings"], [{
            "sourceLabels": ["__meta_kubernetes_service_name"], "action": "keep", "regex": "opensearch-cluster-master",
        }])

    def test_alerts_use_real_traefik_and_cnpg_contracts(self):
        rules = yaml.safe_load((MONITORING / "resources/alerts.yaml").read_text())["spec"]["groups"]
        expressions = [rule["expr"] for group in rules for rule in group["rules"]]
        joined = "\n".join(expressions)
        self.assertIn("exported_service=~\"invenio-invenio-.*\"", joined)
        self.assertNotIn('service=~"invenio-web', joined)
        self.assertIn("cnpg_backends_total", joined)
        self.assertIn("cnpg_pg_settings_setting", joined)
        self.assertIn("cnpg_pg_replication_lag", joined)
        self.assertNotIn("cnpg_collection_", joined)
        self.assertIn("velero_backup_failure_total", joined)
        self.assertIn("velero_backup_partial_failure_total", joined)
        self.assertNotIn('{__name__=~"velero_backup_(partial_)?failure_total"', joined)
        self.assertIn("absent(cnpg_collector_last_available_backup_timestamp", joined)

    def test_dashboards_use_real_contracts_and_no_duplicate_opensearch_status(self):
        documents = [d for d in yaml.safe_load_all((MONITORING / "resources/grafana-dashboards.yaml").read_text()) if d]
        dashboards = [json.loads(blob) for document in documents for blob in document["data"].values()]
        expressions = []

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "expr":
                        expressions.append(item)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for dashboard in dashboards:
            walk(dashboard)
        joined = "\n".join(expressions)
        self.assertIn("exported_service=~", joined)
        self.assertIn("max(opensearch_cluster_status)", joined)
        self.assertIn("cnpg_backends_total", joined)
        self.assertNotIn("cnpg_collection_", joined)
        self.assertNotIn("service=~\\\"invenio-web", joined)
        self.assertIn("velero_backup_failure_total", joined)
        self.assertIn("velero_backup_partial_failure_total", joined)
        self.assertNotIn('{__name__=~"velero_backup_(partial_)?failure_total"', joined)
        self.assertIn("velero_backup_deletion_failure_total", joined)

    def test_logs_dashboard_uses_loki_with_stable_uid_and_filters(self):
        documents = [d for d in yaml.safe_load_all((MONITORING / "resources/grafana-dashboards.yaml").read_text()) if d]
        dashboards = [json.loads(blob) for document in documents for blob in document["data"].values()]
        logs = next(dashboard for dashboard in dashboards if dashboard.get("uid") == "invenio-logs")
        self.assertEqual(logs["title"], "04 Application Logs")
        self.assertEqual(logs["panels"][0]["datasource"], {"type": "loki", "uid": "loki"})
        self.assertEqual(logs["panels"][0]["targets"][0]["expr"], '{namespace=~"$namespace"} |~ "$search"')
        self.assertEqual({item["name"] for item in logs["templating"]["list"]}, {"namespace", "search"})
        self.assertTrue(any(link["url"] == "/d/overview-invenio-rdm" for link in logs["links"]))
        other_links = [link["url"] for dashboard in dashboards if dashboard["uid"] != "invenio-logs" for link in dashboard.get("links", [])]
        self.assertIn("/d/invenio-logs", other_links)
