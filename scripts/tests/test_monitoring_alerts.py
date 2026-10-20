import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "k8s/infra/monitoring"
EXPECTED_RULES = {
    "app-slo": sorted({
        "InvenioWebReplicasUnavailable",
        "Invenio5xxRatioHigh",
        "TraefikServiceDown",
        "InvenioPodRestartHigh",
    }),
    "data-backups": sorted({
        "VeleroBackupFailed",
        "VeleroBackupMetricsMissing",
        "VeleroBackupNeverSucceeded",
        "VeleroBackupStale",
        "VeleroScheduledBackupFailed",
        "VeleroScheduledBackupOverdue",
        "PostgreSQLHighConnections",
        "CNPGReplicationLagHigh",
        "CNPGBackupStale",
        "CNPGArchivingDown",
        "VeleroPluginCrashlooping",
    }),
    "capacity": sorted({
        "HighMemoryUsage",
        "NodeDiskPressure",
        "InvenioPVCUsageHigh",
        "OOMKillsIncreasing",
        "PodNotReady",
        "PodCrashLoopingWithDown",
    }),
    "edge": sorted({"CloudflareTunnelDown", "TraefikHigh404Rate"}),
    "platform-self": sorted({
        "PrometheusTargetDown",
        "KubeStateMetricsDown",
        "AlertmanagerNotificationsFailing",
        "AlertmanagerConfigFailed",
    }),
}


def render(message, alerts, *, receiver="discord-critical"):
    if isinstance(alerts, dict):
        alerts = [alerts]
    rendered_alerts = []
    for index, alert in enumerate(alerts):
        if "annotations" not in alert:
            alert = {"annotations": alert}
        rendered_alerts.append(
            {
                "status": alert.get("status", "firing"),
                "labels": {
                    "alertname": f"SyntheticAlert{index}",
                    **alert.get("labels", {}),
                },
                "annotations": alert["annotations"],
                "startsAt": "2026-10-16T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "https://prometheus.example/graph",
                "fingerprint": f"synthetic-{index}",
            }
        )
    group_labels = rendered_alerts[0]["labels"]
    data = {
        "receiver": receiver,
        "status": (
            "firing"
            if any(alert["status"] == "firing" for alert in rendered_alerts)
            else "resolved"
        ),
        "alerts": rendered_alerts,
        "groupLabels": group_labels,
        "commonLabels": group_labels,
        "commonAnnotations": {},
        "externalURL": "https://prometheus.example",
        "version": "4",
        "groupKey": "synthetic",
        "truncatedAlerts": 0,
    }
    with tempfile.TemporaryDirectory() as directory:
        data_path = Path(directory) / "data.json"
        template_path = Path(directory) / "templates.tmpl"
        data_path.write_text(json.dumps(data))
        template_path.write_text('{{ define "noop" }}{{ end }}')
        result = subprocess.run(
            [
                "amtool", "template", "render",
                "--template.glob", str(template_path),
                "--template.text", message,
                "--template.data", str(data_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout.strip()


class MonitoringAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alerts = yaml.safe_load((MONITORING / "alerts.yaml").read_text())
        cls.receivers = yaml.safe_load((MONITORING / "discord-receivers.yaml").read_text())

    def discord_configs(self):
        return {
            receiver["name"]: receiver["discordConfigs"][0]
            for receiver in self.receivers["spec"]["receivers"]
            if receiver["name"] in {"discord-critical", "discord-warning"}
        }

    @unittest.skipUnless(shutil.which("amtool"), "amtool is not installed")
    def test_discord_titles_and_bodies_mark_firing_for_both_receivers(self):
        annotations = {
            "summary": "Synthetic summary",
            "description": "Synthetic description",
            "runbook_url": "https://docs.example/runbook",
            "dashboard": "https://grafana.example/dashboard",
        }
        for name, config in self.discord_configs().items():
            severity = name.removeprefix("discord-")
            alert = {
                "labels": {
                    "alertname": "VeleroScheduledBackupFailed",
                    "severity": severity,
                },
                "annotations": annotations,
            }
            header = f"[FIRING] [{severity.upper()}] VeleroScheduledBackupFailed"
            with self.subTest(receiver=name):
                self.assertEqual(
                    render(config["title"], [alert], receiver=name), header
                )
                self.assertEqual(
                    render(config["message"], [alert], receiver=name),
                    f"{header}\n"
                    "Synthetic summary\n"
                    "Synthetic description\n"
                    "Runbook: https://docs.example/runbook\n"
                    "Dashboard: https://grafana.example/dashboard",
                )

    @unittest.skipUnless(shutil.which("amtool"), "amtool is not installed")
    def test_discord_titles_and_bodies_mark_resolved_as_historical(self):
        annotations = {
            "summary": "Synthetic summary",
            "description": "Synthetic description",
            "runbook_url": "https://docs.example/runbook",
            "dashboard": "https://grafana.example/dashboard",
        }
        for name, config in self.discord_configs().items():
            severity = name.removeprefix("discord-")
            alert = {
                "status": "resolved",
                "labels": {
                    "alertname": "VeleroScheduledBackupFailed",
                    "severity": severity,
                },
                "annotations": annotations,
            }
            header = f"[RESOLVED] [{severity.upper()}] VeleroScheduledBackupFailed"
            with self.subTest(receiver=name):
                self.assertEqual(
                    render(config["title"], [alert], receiver=name), header
                )
                self.assertEqual(
                    render(config["message"], [alert], receiver=name),
                    f"{header}\n"
                    "Synthetic summary\n"
                    "Previous condition: Synthetic description\n"
                    "Runbook: https://docs.example/runbook\n"
                    "Dashboard: https://grafana.example/dashboard",
                )

    @unittest.skipUnless(shutil.which("amtool"), "amtool is not installed")
    def test_discord_mixed_group_marks_each_alert_status(self):
        for name, config in self.discord_configs().items():
            severity = name.removeprefix("discord-")
            labels = {
                "alertname": "VeleroScheduledBackupFailed",
                "severity": severity,
            }
            alerts = [
                {
                    "status": "firing",
                    "labels": labels,
                    "annotations": {
                        "summary": "Firing summary",
                        "description": "Current failure description",
                    },
                },
                {
                    "status": "resolved",
                    "labels": labels,
                    "annotations": {
                        "summary": "Resolved summary",
                        "description": "Previous failure description",
                    },
                },
            ]
            firing_header = (
                f"[FIRING] [{severity.upper()}] VeleroScheduledBackupFailed"
            )
            resolved_header = (
                f"[RESOLVED] [{severity.upper()}] VeleroScheduledBackupFailed"
            )
            with self.subTest(receiver=name):
                self.assertEqual(
                    render(config["title"], alerts, receiver=name), firing_header
                )
                self.assertEqual(
                    render(config["message"], alerts, receiver=name),
                    f"{firing_header}\n"
                    "Firing summary\n"
                    "Current failure description\n\n"
                    f"{resolved_header}\n"
                    "Resolved summary\n"
                    "Previous condition: Previous failure description",
                )

    def test_exact_rule_group_contract_and_resource_identity(self):
        source_groups = self.alerts["spec"]["groups"]
        self.assertEqual(len(source_groups), len(EXPECTED_RULES))
        self.assertEqual(
            len({group["name"] for group in source_groups}), len(source_groups)
        )
        groups = {
            group["name"]: sorted(
                rule.get("alert") or rule.get("record")
                for rule in group.get("rules", [])
            )
            for group in source_groups
        }
        self.assertEqual(groups, EXPECTED_RULES)

        metadata = self.alerts["metadata"]
        self.assertEqual(metadata["name"], "cluster-health-alerts")
        self.assertEqual(metadata["namespace"], "monitoring")
        self.assertEqual(metadata["labels"].get("release"), "monitoring")
        self.assertEqual(
            metadata["annotations"],
            {"argocd.argoproj.io/sync-options": "Replace=true"},
        )

    def test_required_backup_signals_use_velero_status_not_cr_creation(self):
        values = yaml.safe_load((MONITORING / "values.yaml").read_text())
        ksm = values["kube-state-metrics"]
        state = ksm["customResourceState"]
        self.assertTrue(state["enabled"])
        resources = {
            resource["groupVersionKind"]["kind"]: resource
            for resource in state["config"]["spec"]["resources"]
        }
        backup_metrics = {
            metric["name"] for metric in resources["Backup"]["metrics"]
        }
        schedule_metrics = {
            metric["name"] for metric in resources["Schedule"]["metrics"]
        }
        self.assertIn("velero_backup_completion_timestamp", backup_metrics)
        self.assertIn("velero_backup_start_timestamp", backup_metrics)
        self.assertIn("velero_schedule_status_last_backup_timestamp", schedule_metrics)

        rules = {
            rule["alert"]: rule["expr"]
            for group in self.alerts["spec"]["groups"]
            for rule in group.get("rules", [])
            if "alert" in rule
        }
        self.assertIn("velero_backup_completion_timestamp", rules["VeleroBackupStale"])
        self.assertNotIn(
            "velero_backup_creation_timestamp", rules["VeleroBackupStale"]
        )
        self.assertIn(
            "velero_backup_start_timestamp", rules["VeleroScheduledBackupFailed"]
        )
        self.assertIn(
            'backup=~"weekly-infra-backup-[0-9]{14}"',
            rules["VeleroScheduledBackupFailed"],
        )
        self.assertIn(
            'schedule="weekly-infra-backup"',
            rules["VeleroBackupMetricsMissing"],
        )

        extra_rules = ksm["rbac"]["extraRules"]
        self.assertTrue(
            any(
                "velero.io" in rule.get("apiGroups", [])
                and {"backups", "schedules"} <= set(rule.get("resources", []))
                for rule in extra_rules
            )
        )

    def test_kube_state_metrics_budget_covers_custom_resource_startup(self):
        values = yaml.safe_load((MONITORING / "values.yaml").read_text())
        resources = values["kube-state-metrics"]["resources"]

        def mebibytes(quantity):
            text = str(quantity)
            if text.endswith("Mi"):
                return float(text[:-2])
            if text.endswith("Gi"):
                return float(text[:-2]) * 1024
            raise AssertionError(f"unexpected memory quantity: {quantity!r}")

        # The 64Mi limit OOMKilled the Velero collector at startup (#139).
        self.assertEqual(mebibytes(resources["requests"]["memory"]), 64)
        self.assertGreaterEqual(mebibytes(resources["limits"]["memory"]), 128)

    def test_backup_dashboard_uses_completion_not_global_success(self):
        dashboards = [
            doc for doc in yaml.safe_load_all(
                (MONITORING / "grafana-dashboards.yaml").read_text()
            ) if doc
        ]
        exprs = [
            target["expr"]
            for doc in dashboards
            for data in (doc.get("data") or {}).values()
            for panel in json.loads(data)["panels"]
            for target in panel.get("targets", [])
            if "expr" in target
        ]
        self.assertTrue(
            any("velero_backup_completion_timestamp" in expr for expr in exprs)
        )
        self.assertFalse(
            any(
                "time() - velero_backup_last_successful_timestamp" in expr
                for expr in exprs
            )
        )

    @unittest.skipUnless(shutil.which("amtool"), "amtool is not installed")
    def test_discord_templates_render_lines_and_empty_annotation_fallbacks(self):
        expected_with_annotations = (
            "[FIRING] SyntheticAlert0\n"
            "Synthetic summary\nSynthetic description\n"
            "Runbook: https://docs.example/runbook\n"
            "Dashboard: https://grafana.example/dashboard"
        )
        expected_without_annotations = (
            "[FIRING] SyntheticAlert0\nNo description available."
        )
        annotations = self.receivers["spec"]["receivers"]
        discord_messages = [
            config["message"]
            for receiver in annotations
            if receiver["name"] in {"discord-critical", "discord-warning"}
            for config in receiver["discordConfigs"]
        ]
        self.assertEqual(len(discord_messages), 2)

        full = {
            "summary": "Synthetic summary",
            "description": "Synthetic description",
            "runbook_url": "https://docs.example/runbook",
            "dashboard": "https://grafana.example/dashboard",
        }
        for message in discord_messages:
            with self.subTest(message=message):
                self.assertEqual(render(message, full), expected_with_annotations)
                rendered = render(message, {})
                self.assertEqual(rendered, expected_without_annotations)
                self.assertNotIn(r"\n", rendered)

    @unittest.skipUnless(shutil.which("amtool"), "amtool is not installed")
    def test_grouped_alerts_have_a_blank_line_separator(self):
        annotations = [
            {"summary": "First summary", "description": "First description"},
            {"summary": "Second summary", "description": "Second description"},
        ]
        expected = (
            "[FIRING] SyntheticAlert0\n"
            "First summary\nFirst description\n\n"
            "[FIRING] SyntheticAlert1\n"
            "Second summary\nSecond description"
        )
        messages = [
            config["message"]
            for receiver in self.receivers["spec"]["receivers"]
            if receiver["name"] in {"discord-critical", "discord-warning"}
            for config in receiver["discordConfigs"]
        ]
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(render(message, annotations), expected)


if __name__ == "__main__":
    unittest.main()
