import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "k8s/apps/invenio"
INIT = APP / "initialization"
CONFIG_COMPONENT = APP / "config/kustomization.yaml"


def render(path):
    if shutil.which("kustomize"):
        command = ["kustomize", "build", str(path)]
    else:
        command = ["kubectl", "kustomize", str(path)]
    output = subprocess.check_output(command, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def config_map_refs(document):
    refs = []
    pod = document.get("spec", {}).get("template", {}).get("spec", {})
    for container in (pod.get("initContainers") or []) + (pod.get("containers") or []):
        for source in container.get("envFrom") or []:
            if "configMapRef" in source:
                refs.append(source["configMapRef"]["name"])
    return refs


class ConfigRolloutTests(unittest.TestCase):
    def test_config_is_a_hash_generating_component(self):
        component = yaml.safe_load(CONFIG_COMPONENT.read_text())
        self.assertEqual(component["kind"], "Component")
        generator = component["configMapGenerator"][0]
        self.assertEqual(generator["name"], "invenio-app-config")
        self.assertEqual(generator["envs"], ["app-config.env"])
        self.assertFalse((APP / "resources/app-config.yaml").exists())

    def test_app_and_initialization_share_the_hashed_config(self):
        app = render(APP)
        init = render(INIT)
        app_maps = [d for d in app if d.get("kind") == "ConfigMap"
                    and d["metadata"]["name"].startswith("invenio-app-config-")]
        init_maps = [d for d in init if d.get("kind") == "ConfigMap"
                     and d["metadata"]["name"].startswith("invenio-app-config-")]
        self.assertEqual(len(app_maps), 1)
        self.assertEqual(len(init_maps), 1)
        self.assertEqual(app_maps[0]["metadata"]["name"], init_maps[0]["metadata"]["name"])
        expected = app_maps[0]["metadata"]["name"]

        for document in app + init:
            for ref in config_map_refs(document):
                self.assertEqual(ref, expected, document.get("metadata", {}).get("name"))
