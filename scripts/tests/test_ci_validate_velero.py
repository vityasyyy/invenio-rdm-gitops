import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


import importlib.util
import json

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/ci-validate-velero.py"

_spec = importlib.util.spec_from_file_location("ci_validate_velero", VALIDATOR)
ci_validate_velero = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_validate_velero)

_pod_annotation = ci_validate_velero._pod_annotation
EXCLUSION = {
    "apiGroups": ["velero.io"],
    "kinds": ["Backup", "Restore"],
    "clusters": ["https://kubernetes.default.svc", "https://10.43.0.1:443"],
}
UNRELATED_EXCLUSION = {
    "apiGroups": ["batch"],
    "kinds": ["Job"],
    "clusters": ["*"],
}


DEFAULT_QUOTA = {
    "requests.cpu": "1",
    "requests.memory": "6Gi",
    "limits.cpu": "4",
    "limits.memory": "7Gi",
    "pods": "12",
}
DEFAULT_NODE_AGENT = {
    "cpuRequest": "100m",
    "cpuLimit": "250m",
    "memoryRequest": "1Gi",
    "memoryLimit": "1Gi",
}


def write_fixture(
    root,
    exclusions,
    resources=("backup-schedule.yaml",),
    config_name="argocd-cm",
    chart_version="11.4.0",
    plugin_image="velero/velero-plugin-for-aws:v1.13.2",
    quota=None,
    node_agent=None,
    namespaces=("monitoring",),
):
    root = Path(root)
    argocd = root / "k8s/infra/argocd/patches/argocd-cm.yaml"
    argocd.parent.mkdir(parents=True)
    argocd.write_text(yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": config_name},
        "data": {"resource.exclusions": yaml.safe_dump(exclusions)},
    }))

    app = root / "argocd/apps/velero.yaml"
    app.parent.mkdir(parents=True)
    app.write_text(yaml.safe_dump({
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "velero", "namespace": "argocd"},
        "spec": {"sources": [{
            "chart": "velero",
            "repoURL": "https://vmware-tanzu.github.io/helm-charts",
            "targetRevision": chart_version,
        }]},
    }))

    velero = root / "k8s/infra/velero"
    velero.mkdir(parents=True)
    (velero / "kustomization.yaml").write_text(yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [f"resources/{name}" for name in resources] + ["resources/node-agent-config.yaml"],
    }))
    (velero / "resources").mkdir()
    (velero / "resources/backup-schedule.yaml").write_text(yaml.safe_dump({
        "apiVersion": "velero.io/v1",
        "kind": "Schedule",
        "metadata": {"name": "weekly-infra-backup", "namespace": "velero"},
        "spec": {"template": {
            "defaultVolumesToFsBackup": False,
            "snapshotVolumes": False,
            "includedNamespaces": list(namespaces),
        }},
    }))
    (velero / "values.yaml").write_text(yaml.safe_dump({
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "initContainers": [{
            "name": "velero-plugin-for-aws",
            "image": plugin_image,
        }],
        "nodeAgent": {
            "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "250m", "memory": "256Mi"},
            },
            "extraArgs": ["--node-agent-configmap=node-agent-config"],
        },
        "configuration": {"repositoryMaintenanceJob": {"repositoryConfigData": {
            "name": "velero-repo-maintenance",
            "global": {
                "keepLatestMaintenanceJobs": 3,
                "podResources": {
                    "cpuRequest": "50m",
                    "cpuLimit": "250m",
                    "memoryRequest": "256Mi",
                    "memoryLimit": "256Mi",
                },
            },
        }}},
    }))
    (velero / "resources/node-agent-config.yaml").write_text(yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "node-agent-config", "namespace": "velero"},
        "data": {"node-agent-config.json": json.dumps({
            "podResources": dict(node_agent or DEFAULT_NODE_AGENT),
            "loadConcurrency": {"globalConfig": 1, "prepareQueueLength": 1},
        })},
    }))
    (velero / "resources/limit-range.yaml").write_text(yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": {"name": "velero-default-limits", "namespace": "velero"},
        "spec": {"limits": [{
            "default": {"cpu": "250m", "memory": "256Mi"},
            "defaultRequest": {"cpu": "50m", "memory": "64Mi"},
            "max": {"cpu": "1", "memory": "1Gi"},
            "min": {"cpu": "25m", "memory": "32Mi"},
            "type": "Container",
        }]},
    }))
    quota_path = root / "k8s/infra/security/resources/resource-quotas/velero-quota.yaml"
    quota_path.parent.mkdir(parents=True)
    quota_path.write_text(yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": "velero-quota", "namespace": "velero"},
        "spec": {"hard": dict(quota or DEFAULT_QUOTA)},
    }))

    monitoring = root / "k8s/infra/monitoring/values.yaml"
    monitoring.parent.mkdir(parents=True)
    monitoring.write_text(yaml.safe_dump({
        "grafana": {"podAnnotations": {"backup.velero.io/backup-volumes": "storage"}},
    }))

    for relative_path, kind, volumes in (
        ("k8s/apps/invenio/resources/invenio-deployment.yaml", "Deployment", "data,archive"),
        ("k8s/dependencies/redis/resources/redis.yaml", "Deployment", "data"),
    ):
        manifest = root / relative_path
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(yaml.safe_dump({
            "apiVersion": "apps/v1",
            "kind": kind,
            "metadata": {"name": "fixture"},
            "spec": {"template": {"metadata": {"annotations": {
                "backup.velero.io/backup-volumes": volumes,
            }}}},
        }))


def run_validator(root):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(root)], capture_output=True, text=True
    )


class VeleroValidatorTests(unittest.TestCase):
    def test_repository_uses_narrow_exclusions_and_keeps_schedule_managed(self):
        config = yaml.safe_load(
            (ROOT / "k8s/infra/argocd/patches/argocd-cm.yaml").read_text()
        )
        exclusions = yaml.safe_load(config["data"]["resource.exclusions"])
        self.assertEqual(exclusions, [EXCLUSION])
        result = run_validator(ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validator_rejects_global_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION, {
                "apiGroups": ["*"], "kinds": ["*"], "clusters": ["*"],
            }])
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resource.exclusions", result.stdout + result.stderr)

    def test_validator_allows_unrelated_narrow_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION, UNRELATED_EXCLUSION])
            result = run_validator(Path(directory))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validator_keeps_velero_schedule_discoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION, {
                "apiGroups": ["velero.io"],
                "kinds": ["Schedule"],
                "clusters": ["*"],
            }])
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Schedule", result.stdout + result.stderr)

    def test_validator_requires_argocd_configmap(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION], config_name="not-argocd-cm")
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("argocd-cm", result.stdout + result.stderr)

    def test_validator_rejects_unmanaged_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION], resources=())
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup-schedule.yaml", result.stdout + result.stderr)

    def test_validator_rejects_plugin_series_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION], plugin_image="velero/velero-plugin-for-aws:v1.11.1")
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1.13.x", result.stdout + result.stderr)

    def test_validator_rejects_unmapped_chart_version(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION], chart_version="12.0.0")
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compatibility mapping", result.stdout + result.stderr)

    def test_validator_rejects_unpinned_plugin_image(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(directory, [EXCLUSION], plugin_image="velero/velero-plugin-for-aws")
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be pinned", result.stdout + result.stderr)


class VeleroBackupScopeTests(unittest.TestCase):
    """The opt-in allowlist is explicit; scratch and self-backup volumes stay out."""

    def test_invenio_backs_up_application_volumes_only(self):
        value = _pod_annotation(
            ROOT / "k8s/apps/invenio/resources/invenio-deployment.yaml",
            "Deployment",
            "backup.velero.io/backup-volumes",
        )
        self.assertEqual({item.strip() for item in value.split(",")}, {"data", "archive"})

    def test_redis_backs_up_its_durable_volume(self):
        value = _pod_annotation(
            ROOT / "k8s/dependencies/redis/resources/redis.yaml",
            "Deployment",
            "backup.velero.io/backup-volumes",
        )
        self.assertEqual({item.strip() for item in value.split(",")}, {"data"})

    def test_grafana_opts_in_via_chart_pod_annotations(self):
        values = yaml.safe_load((ROOT / "k8s/infra/monitoring/values.yaml").read_text())
        annotations = values["grafana"]["podAnnotations"]
        self.assertEqual(annotations["backup.velero.io/backup-volumes"], "storage")

    def test_schedule_is_opt_in(self):
        schedule = yaml.safe_load(
            (ROOT / "k8s/infra/velero/resources/backup-schedule.yaml").read_text()
        )
        self.assertIs(schedule["spec"]["template"]["defaultVolumesToFsBackup"], False)

    def test_no_scratch_or_self_backup_opt_in(self):
        pattern = re.compile(r"^\s*backup\.velero\.io/backup-volumes\s*:")
        annotated = []
        for path in (ROOT / "k8s").rglob("*.yaml"):
            for line in path.read_text().splitlines():
                if not line.lstrip().startswith("#") and pattern.match(line):
                    annotated.append(path.relative_to(ROOT).as_posix())
                    break
        self.assertEqual(
            sorted(annotated),
            [
                "k8s/apps/invenio/resources/invenio-deployment.yaml",
                "k8s/dependencies/redis/resources/redis.yaml",
                "k8s/infra/monitoring/values.yaml",
            ],
        )

    def test_node_agent_config_is_wired_and_bounded(self):
        values = yaml.safe_load((ROOT / "k8s/infra/velero/values.yaml").read_text())
        self.assertIn(
            "--node-agent-configmap=node-agent-config",
            values["nodeAgent"]["extraArgs"],
        )
        self.assertEqual(
            values["nodeAgent"]["resources"],
            {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "250m", "memory": "256Mi"},
            },
        )
        kustomization = yaml.safe_load(
            (ROOT / "k8s/infra/velero/kustomization.yaml").read_text()
        )
        self.assertIn("resources/node-agent-config.yaml", kustomization["resources"])
        config = yaml.safe_load(
            (ROOT / "k8s/infra/velero/resources/node-agent-config.yaml").read_text()
        )
        settings = json.loads(config["data"]["node-agent-config.json"])
        self.assertEqual(settings["podResources"]["memoryLimit"], "1Gi")
        self.assertEqual(settings["podResources"]["memoryRequest"], "1Gi")
        self.assertEqual(settings["podResources"]["cpuLimit"], "250m")
        self.assertEqual(settings["loadConcurrency"]["globalConfig"], 1)
        self.assertEqual(settings["loadConcurrency"]["prepareQueueLength"], 1)

    def test_repository_maintenance_budget_is_pinned_in_values(self):
        values = yaml.safe_load((ROOT / "k8s/infra/velero/values.yaml").read_text())
        maintenance = values["configuration"]["repositoryMaintenanceJob"]
        config_data = maintenance["repositoryConfigData"]
        self.assertEqual(config_data["name"], "velero-repo-maintenance")
        global_config = config_data["global"]
        self.assertEqual(global_config["keepLatestMaintenanceJobs"], 3)
        pod_resources = global_config["podResources"]
        self.assertEqual(pod_resources["memoryRequest"], pod_resources["memoryLimit"])
        self.assertEqual(pod_resources["cpuLimit"], "250m")

    def test_schedule_opt_in_and_no_snapshots(self):
        schedule = yaml.safe_load(
            (ROOT / "k8s/infra/velero/resources/backup-schedule.yaml").read_text()
        )
        template = schedule["spec"]["template"]
        self.assertIs(template["defaultVolumesToFsBackup"], False)
        self.assertIs(template["snapshotVolumes"], False)

    def test_worst_case_budget_fits_quota(self):
        """Full simultaneous budget: rollout surge, 2 agents, 4 movers, 10 streams."""
        budget, hard = ci_validate_velero.resource_budget(ROOT)
        for key, value in budget.items():
            self.assertLessEqual(value, hard[key], key)
        self.assertEqual(budget["pods"], 18)
        self.assertEqual(budget["limits.cpu"], 5000)
        self.assertEqual(budget["limits.memory"], 8192)
        self.assertEqual(budget["requests.cpu"], 1100)
        self.assertEqual(budget["requests.memory"], 7040)

    def test_validator_rejects_quota_below_worst_case(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(
                directory,
                [EXCLUSION],
                quota={**DEFAULT_QUOTA, "limits.cpu": "2"},
            )
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("velero-quota limits.cpu", result.stdout + result.stderr)

    def test_validator_rejects_repository_growth_beyond_quota(self):
        two_stream_quota = {
            "requests.cpu": "700m",
            "requests.memory": "5248Mi",
            "limits.cpu": "3000m",
            "limits.memory": "6400Mi",
            "pods": "10",
        }
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(
                directory,
                [EXCLUSION],
                quota=two_stream_quota,
                namespaces=("monitoring", "invenio"),
            )
            result = run_validator(Path(directory))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(
                directory,
                [EXCLUSION],
                quota=two_stream_quota,
                namespaces=("monitoring", "invenio", "minio", "redis"),
            )
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("velero-quota", result.stdout + result.stderr)

    def test_validator_requires_memory_request_equal_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            write_fixture(
                directory,
                [EXCLUSION],
                node_agent={**DEFAULT_NODE_AGENT, "memoryRequest": "256Mi"},
            )
            result = run_validator(Path(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("memoryRequest", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
