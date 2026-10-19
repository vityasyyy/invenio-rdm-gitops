import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/ci-validate-velero.py"
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


def write_fixture(
    root,
    exclusions,
    resources=("backup-schedule.yaml",),
    config_name="argocd-cm",
    chart_version="11.4.0",
    plugin_image="velero/velero-plugin-for-aws:v1.13.2",
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
        "resources": list(resources),
    }))
    (velero / "backup-schedule.yaml").write_text(yaml.safe_dump({
        "apiVersion": "velero.io/v1",
        "kind": "Schedule",
        "metadata": {"name": "weekly-infra-backup", "namespace": "velero"},
    }))
    (velero / "values.yaml").write_text(yaml.safe_dump({
        "initContainers": [{
            "name": "velero-plugin-for-aws",
            "image": plugin_image,
        }],
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


if __name__ == "__main__":
    unittest.main()
