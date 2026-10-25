import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ManifestSourceTests(unittest.TestCase):
    def commands(self, app):
        spec = importlib.util.spec_from_file_location("manifest_sources", ROOT / "scripts/manifest_sources.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "argocd/apps").mkdir(parents=True)
            (root / "argocd/apps/example.yaml").write_text(yaml.safe_dump(app))
            (root / "values.yaml").write_text("replicas: 1\n")
            return module.render_commands(root)

    def app(self, helm):
        return {"metadata": {"name": "actual-app"}, "spec": {
            "destination": {"namespace": "database"}, "sources": [
                {"chart": "operator", "repoURL": "https://charts.example.org", "targetRevision": "1.2.3", "helm": helm},
                {"repoURL": "https://github.com/vityasyyy/invenio-rdm-gitops.git", "ref": "values"},
            ]}}

    def test_uses_application_release_namespace_version_and_values(self):
        commands = self.commands(self.app({"valueFiles": ["$values/values.yaml"]}))
        self.assertEqual(len(commands), 1)
        name, command = commands[0]
        self.assertEqual(name, "helm_actual-app_0.yaml")
        self.assertEqual(command[:4], ["helm", "template", "actual-app", "operator"])
        self.assertIn("database", command)
        self.assertIn("1.2.3", command)
        self.assertTrue(command[-1].endswith("/values.yaml"))

    def test_missing_values_file_fails_instead_of_rendering_defaults(self):
        with self.assertRaisesRegex(ValueError, "missing values"):
            self.commands(self.app({"valueFiles": ["$values/missing.yaml"]}))

    def test_inline_values_are_rejected_as_competing_configuration(self):
        with self.assertRaisesRegex(ValueError, "inline"):
            self.commands(self.app({"valuesObject": {"replicas": 3}}))

    def test_unknown_values_ref_is_not_treated_as_local(self):
        with self.assertRaisesRegex(ValueError, "values reference"):
            self.commands(self.app({"valueFiles": ["$other/values.yaml"]}))
