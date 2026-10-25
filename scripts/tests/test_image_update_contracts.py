import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "k8s/apps/invenio"
APP_KUSTOMIZATION = APP / "kustomization.yaml"
IMAGE_PIN = APP / "image-pin/kustomization.yaml"
IMAGE = "ghcr.io/vityasyyy/invenio-ugm"


class ImageUpdateContractTests(unittest.TestCase):
    def test_git_has_one_image_digest_source(self):
        pin = yaml.safe_load(IMAGE_PIN.read_text())
        self.assertEqual(pin["kind"], "Component")
        self.assertEqual([(item["name"], item["digest"]) for item in pin["images"]],
                         [(IMAGE, "sha256:0f685be3fc05e4fb517a68c2508ac649ce40d9bf7af50c42d738aa93351a82c0")])
        kustomization = yaml.safe_load(APP_KUSTOMIZATION.read_text())
        self.assertIn("image-pin", kustomization["components"])
        self.assertNotIn("images", kustomization)

    def test_image_updater_targets_the_git_component_through_argo_writeback(self):
        updater = yaml.safe_load((ROOT / "k8s/infra/argocd-image-updater/resources/image-updater-cr.yaml").read_text())
        reference = updater["spec"]["applicationRefs"][0]
        self.assertEqual(reference["namePattern"], "invenio-bootstrap")
        image = reference["images"][0]
        self.assertEqual(image["imageName"], IMAGE + ":latest")
        self.assertEqual(image["commonUpdateSettings"]["updateStrategy"], "digest")
        self.assertEqual(image["manifestTargets"]["kustomize"]["name"], IMAGE)
        self.assertEqual(updater["spec"]["writeBackConfig"]["method"], "argocd")

    def test_application_has_no_static_override_competing_with_the_git_pin(self):
        application = yaml.safe_load((ROOT / "argocd/apps/invenio-bootstrap.yaml").read_text())
        source = application["spec"]["source"]
        self.assertEqual(source["path"], "k8s/apps/invenio")
        self.assertNotIn("kustomize", source)
        self.assertNotIn("sources", application["spec"])

    def test_application_spec_images_override_wins_over_the_shared_component(self):
        if not shutil.which("kustomize"):
            self.skipTest("kustomize is not installed")
        override = "sha256:" + "d" * 64
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "app"
            shutil.copytree(APP, copy)
            overlay = Path(directory) / "review-overlay"
            overlay.mkdir()
            (overlay / "kustomization.yaml").write_text(yaml.safe_dump({
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["../app"],
                "images": [{"name": IMAGE, "digest": override}],
            }))
            rendered = subprocess.check_output(
                ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(overlay)],
                text=True,
            )
        images = [line.split("image:", 1)[1].strip() for line in rendered.splitlines()
                  if "image:" in line and IMAGE in line]
        self.assertTrue(images)
        for image in images:
            self.assertEqual(image, IMAGE + "@" + override)
        pin_digest = yaml.safe_load(IMAGE_PIN.read_text())["images"][0]["digest"]
        self.assertNotIn(IMAGE + "@" + pin_digest, rendered)
