import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "k8s/infra/monitoring"
MONITORING_ALLOW = ROOT / "k8s/infra/security/resources/network-policies/monitoring-allow.yaml"
ARGOCD_ALLOW = ROOT / "k8s/infra/security/resources/network-policies/argocd-allow.yaml"


def dashboards():
    documents = [d for d in yaml.safe_load_all(
        (MONITORING / "resources/grafana-dashboards.yaml").read_text()
    ) if d]
    return [json.loads(blob) for document in documents for blob in document["data"].values()]


def expressions(dashboard):
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "expr":
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(dashboard)
    return found


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

    def test_argocd_metrics_scrape_is_selected_and_allowed(self):
        monitor = yaml.safe_load(
            (MONITORING / "resources/argocd-servicemonitor.yaml").read_text()
        )
        self.assertEqual(monitor["kind"], "ServiceMonitor")
        self.assertEqual(monitor["metadata"]["namespace"], "monitoring")
        self.assertEqual(monitor["metadata"]["labels"], {"release": "monitoring"})
        self.assertEqual(
            monitor["spec"]["selector"]["matchLabels"],
            {
                "app.kubernetes.io/component": "metrics",
                "app.kubernetes.io/name": "argocd",
                "app.kubernetes.io/instance": "argocd-self",
            },
        )
        self.assertEqual(monitor["spec"]["namespaceSelector"]["matchNames"], ["argocd"])
        self.assertEqual(
            monitor["spec"]["endpoints"],
            [{
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
                "scrapeTimeout": "10s",
            }],
        )
        kustomization = yaml.safe_load((MONITORING / "kustomization.yaml").read_text())
        self.assertIn("resources/argocd-servicemonitor.yaml", kustomization["resources"])

        policies = {
            doc["metadata"]["name"]: doc
            for doc in yaml.safe_load_all(MONITORING_ALLOW.read_text())
            if doc
        }
        egress = policies["allow-prometheus-egress"]["spec"]["egress"][0]
        self.assertIn(8082, {port["port"] for port in egress["ports"]})

        argocd_policies = {
            doc["metadata"]["name"]: doc
            for doc in yaml.safe_load_all(ARGOCD_ALLOW.read_text())
            if doc
        }
        policy = argocd_policies["allow-monitoring-to-argocd-metrics"]
        self.assertEqual(
            policy["spec"]["podSelector"]["matchLabels"],
            {"app.kubernetes.io/name": "argocd-application-controller"},
        )
        self.assertEqual(policy["spec"]["policyTypes"], ["Ingress"])
        ingress = policy["spec"]["ingress"][0]
        self.assertEqual(
            ingress["from"],
            [{
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
                },
                "podSelector": {
                    "matchLabels": {"app.kubernetes.io/name": "prometheus"}
                },
            }],
        )
        self.assertEqual(ingress["ports"], [{"port": 8082, "protocol": "TCP"}])

    def test_gitops_dashboard_uses_real_argocd_and_kubernetes_metrics(self):
        dashboard = next(
            item for item in dashboards() if item.get("uid") == "gitops-reconciliation"
        )
        self.assertEqual(dashboard["title"], "05 GitOps & Reconciliation")
        description = dashboard.get("description", "")
        self.assertIn("R1-style consistency evidence", description)
        self.assertIn("R2/R3", description)
        joined = "\n".join(expressions(dashboard))
        for metric in (
            "argocd_app_info",
            "kube_deployment_status_replicas_available",
            "kube_horizontalpodautoscaler_status_current_replicas",
            "kube_configmap_info",
            "kube_job_status_succeeded",
            "kube_job_status_failed",
        ):
            self.assertIn(metric, joined)
        self.assertNotIn("argocd_app_operation", joined)

    def test_backup_dashboard_adds_restore_evidence_from_real_metrics(self):
        data = next(item for item in dashboards() if item["uid"] == "data-backups")
        joined = "\n".join(expressions(data))
        for metric in (
            "velero_restore_attempt_total",
            "velero_restore_success_total",
            "velero_restore_failed_total",
            "velero_restore_partial_failure_total",
        ):
            self.assertIn(metric, joined)

    def test_app_dashboard_adds_per_route_panels(self):
        app = next(item for item in dashboards() if item["uid"] == "invenio-app")
        joined = "\n".join(expressions(app))
        self.assertIn("sum by (exported_service)", joined)
        self.assertIn("histogram_quantile(0.95", joined)
        self.assertIn("traefik_service_request_duration_seconds_bucket", joined)
        self.assertIn('code=~"5.."', joined)

    def test_platform_dashboard_adds_capacity_panels(self):
        platform = next(
            item for item in dashboards() if item["uid"] == "platform-health"
        )
        joined = "\n".join(expressions(platform))
        self.assertIn('kube_pod_container_resource_requests{resource="cpu"', joined)
        self.assertIn('kube_node_status_allocatable{resource="cpu"', joined)
        self.assertIn('kube_pod_status_phase{phase="Pending"}', joined)
        self.assertIn("kubelet_volume_stats_available_bytes", joined)

    def test_logs_error_panel_is_time_bounded(self):
        logs = next(item for item in dashboards() if item["uid"] == "invenio-logs")
        self.assertEqual(
            logs["panels"][0]["targets"][0]["expr"],
            '{namespace=~"$namespace"} |~ "$search"',
        )
        bounded = [expr for expr in expressions(logs) if "count_over_time" in expr]
        self.assertTrue(bounded)
        for expr in bounded:
            self.assertIn("[5m]", expr)
            self.assertNotIn('namespace=~".*"', expr)
