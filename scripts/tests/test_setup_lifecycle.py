import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "k8s/apps/invenio"
SETUP = APP / "resources/invenio-setup-job.yaml"
INITIALIZE = APP / "initialization/invenio-initialize-job.yaml"
INIT_KUSTOMIZATION = APP / "initialization/kustomization.yaml"
IMAGE_PIN = APP / "image-pin/kustomization.yaml"
MUTATIONS = (
    "alembic upgrade",
    "files location create",
    "roles create",
    "access allow",
    "index init",
    "custom-fields init",
    "rdm fixtures",
    "rdm-records fixtures",
    "queues declare",
    "rebuild-index",
    "db create",
)


def container(path):
    return yaml.safe_load(path.read_text())["spec"]["template"]["spec"]["containers"][0]


def job_command(path):
    return container(path)["command"][2]


def env_refs(container_spec):
    refs = set()
    for source in container_spec.get("envFrom", []):
        if "configMapRef" in source:
            refs.add(("configMap", source["configMapRef"]["name"]))
        if "secretRef" in source:
            refs.add(("secret", source["secretRef"]["name"]))
    return refs


def alembic_block(command):
    match = re.search(
        r"invenio shell -c '\n([^']*migration_context[^']*)\n'",
        command,
        re.S,
    )
    if not match:
        raise AssertionError("Structured Alembic graph checker is missing")
    return match.group(1)


def run_alembic_block(block, current, heads):
    # Flask/Alembic are installed in the image, not the local test environment.
    setup = '''
import json, sys, types
current, heads = json.loads(sys.argv[1])
class Alembic:
    migration_context = types.SimpleNamespace(get_current_heads=lambda: current)
    def heads(self, resolve_dependencies=False):
        return [types.SimpleNamespace(revision=r) for r in
                (heads if resolve_dependencies else heads + ["dependency-only"])]
sys.modules["flask"] = types.SimpleNamespace(current_app=types.SimpleNamespace(
    extensions={"invenio-db": types.SimpleNamespace(alembic=Alembic())}))
'''
    return subprocess.run(
        ["python3", "-", json.dumps([current, heads])],
        input=setup + block,
        text=True,
        capture_output=True,
    )


class SetupLifecycleTests(unittest.TestCase):
    def test_routine_hook_is_read_only(self):
        command = job_command(SETUP)
        for mutation in MUTATIONS:
            self.assertNotIn(mutation, command)
        for check in ("migration_context.get_current_heads", "files location list", "index list", "queues list"):
            self.assertIn(check, command)

    def test_routine_hook_fails_closed_without_broad_swallowing(self):
        command = job_command(SETUP)
        self.assertNotIn("|| true", command)
        self.assertNotIn("|| echo", command)
        self.assertNotIn("2>/dev/null", command)
        self.assertIn("exit 1", command)

    def test_routine_hook_has_application_config_and_secret_parity(self):
        refs = env_refs(container(SETUP))
        self.assertIn(("configMap", "invenio-app-config"), refs)
        self.assertIn(("secret", "invenio-app-secrets"), refs)
        for name in ("invenio-deployment.yaml", "invenio-worker-deployment.yaml", "invenio-scheduler-deployment.yaml"):
            documents = [doc for doc in yaml.safe_load_all((APP / "resources" / name).read_text()) if doc]
            deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
            app_refs = env_refs(deployment["spec"]["template"]["spec"]["containers"][0])
            self.assertEqual(refs, app_refs, name)

    def test_routine_hook_mounts_the_application_volumes_read_only(self):
        job = yaml.safe_load(SETUP.read_text())
        pod = job["spec"]["template"]["spec"]
        self.assertEqual(
            {volume["name"]: volume["persistentVolumeClaim"]["claimName"] for volume in pod["volumes"]},
            {"data": "invenio-data", "archive": "invenio-archive"},
        )
        mounts = {mount["name"]: mount for mount in pod["containers"][0]["volumeMounts"]}
        self.assertEqual({name: mount["readOnly"] for name, mount in mounts.items()}, {"data": True, "archive": True})

    def test_initialization_is_not_reachable_from_automated_sync(self):
        kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
        self.assertFalse(any("initialization/" in resource for resource in kustomization["resources"]))
        self.assertTrue(INITIALIZE.is_file())

    def test_manual_initialization_has_all_data_operations(self):
        command = job_command(INITIALIZE)
        for mutation in ("alembic upgrade", "files location create", "roles create",
                         "access allow", "index init", "queues declare", "rebuild-index"):
            self.assertIn(mutation, command)

    def test_manual_initialization_does_not_swallow_unexpected_errors(self):
        command = job_command(INITIALIZE)
        self.assertNotIn("|| true", command)
        self.assertNotIn("|| echo", command)
        self.assertNotIn("2>/dev/null", command)
        self.assertNotIn("already exists|already present|duplicate", command)
        self.assertEqual(yaml.safe_load(INITIALIZE.read_text())["spec"]["backoffLimit"], 0)

    def test_manual_initialization_checks_reads_before_creation(self):
        command = job_command(INITIALIZE)
        self.assertIn('LOCATIONS="$(invenio files location list 2>&1)"', command)
        self.assertIn('INDICES="$(invenio index list 2>&1)"', command)
        self.assertIn('QUEUES="$(invenio queues list 2>&1)"', command)
        self.assertIn('ROLE_RESULT="$(invenio shell -c', command)
        self.assertIn('PERMISSION_RESULT="$(invenio shell -c', command)
        self.assertIn('ADMIN_RESULT="$(invenio shell -c', command)
        self.assertIn('custom-fields exists', command)

    def test_manual_initialization_uses_shared_image_component(self):
        component = yaml.safe_load(IMAGE_PIN.read_text())
        self.assertEqual(component["kind"], "Component")
        self.assertEqual(component["images"][0]["name"], "ghcr.io/vityasyyy/invenio-ugm")
        digest = component["images"][0]["digest"]
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")

        app_kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
        self.assertIn("image-pin", app_kustomization["components"])
        self.assertNotIn("images", app_kustomization)

        init_kustomization = yaml.safe_load(INIT_KUSTOMIZATION.read_text())
        self.assertIn("../image-pin", init_kustomization["components"])
        self.assertIn("invenio-initialize-job.yaml", init_kustomization["resources"])
        raw_image = container(INITIALIZE)["image"]
        self.assertIn("unpinned-render-with-kustomize", raw_image)
        self.assertNotIn(":latest", raw_image)

    def test_rendered_manual_job_uses_the_shared_digest_not_latest(self):
        if not shutil.which("kustomize"):
            self.skipTest("kustomize is not installed")
        digest = yaml.safe_load(IMAGE_PIN.read_text())["images"][0]["digest"]
        app_render = subprocess.check_output(["kustomize", "build", str(APP)], text=True)
        init_render = subprocess.check_output(["kustomize", "build", str(INITIALIZE.parent)], text=True)
        image = f"image: ghcr.io/vityasyyy/invenio-ugm@{digest}"
        self.assertIn(image, app_render)
        self.assertIn(image, init_render)
        self.assertNotIn("image: ghcr.io/vityasyyy/invenio-ugm:latest", init_render)

    def test_alembic_checker_passes_only_when_all_branches_are_at_heads(self):
        block = alembic_block(job_command(SETUP))
        at_head = run_alembic_block(block, ["abc"], ["abc"])
        self.assertEqual(at_head.returncode, 0, at_head.stderr)
        self.assertIn("at all 1 heads", at_head.stdout)

        behind = run_alembic_block(block, ["old"], ["new"])
        self.assertNotEqual(behind.returncode, 0)
        self.assertIn("behind=['old']", behind.stderr)
        self.assertIn("missing=['new']", behind.stderr)
        self.assertIn("targeted approved", behind.stderr)

        missing = run_alembic_block(block, ["abc"], ["abc", "def"])
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("missing=['def']", missing.stderr)

    def test_initialization_alembic_checker_uses_the_same_behavior(self):
        block = alembic_block(job_command(INITIALIZE))
        behind = run_alembic_block(block, ["old"], ["new"])
        self.assertNotEqual(behind.returncode, 0)
        self.assertIn("behind=['old']", behind.stderr)

    def test_alembic_checker_rejects_empty_or_unreadable_state(self):
        block = alembic_block(job_command(SETUP))
        for current, heads in (([], ["abc"]), (["abc"], []), (None, ["abc"])):
            with self.subTest(current=current, heads=heads):
                self.assertNotEqual(run_alembic_block(block, current, heads).returncode, 0)

    def test_missing_postconditions_recommend_targeted_remediation(self):
        command = job_command(SETUP)
        self.assertIn("targeted", command)
        self.assertIn("Do not run full initialization", command)
        self.assertNotIn("run the approved invenio-initialize Job", command)

    def test_readme_and_operations_document_the_lifecycle(self):
        operations = (ROOT / "docs/operations.md").read_text()
        self.assertIn("initialization", operations.lower())
        self.assertIn("invenio-initialize", operations)
        self.assertIn("targeted", operations.lower())
