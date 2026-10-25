import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.TestCase):
    def run_bootstrap(self, mode="", *args):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            kubectl = folder / "kubectl"
            kubectl.write_text('''#!/bin/bash
echo "$*" >> "$CALLS"
case "$*" in
  kustomize*)
    [ "$MODE" != render-failure ] || exit 1
    printf 'kind: CustomResourceDefinition\nmetadata: {name: applications.argoproj.io}\n---\nkind: AppProject\nmetadata: {name: infra}\n' ;;
  "get nodes --no-headers") echo "node Ready" ;;
  "get namespace argocd")
    [ "$MODE" = existing ] || exit 1 ;;
  "get namespace argocd --ignore-not-found -o name")
    [ "$MODE" != existing ] || echo namespace/argocd
    [ "$MODE" != denied ] || exit 1
    exit 0 ;;
  apply*)
    case "$3" in
      */crds.yaml)
        grep -q 'kind: CustomResourceDefinition' "$3" || exit 1
        if grep -q 'kind: AppProject' "$3"; then exit 1; fi ;;
      */resources.yaml)
        grep -q 'kind: AppProject' "$3" || exit 1
        if grep -q 'kind: CustomResourceDefinition' "$3"; then exit 1; fi ;;
    esac ;;
  wait*) [ "$MODE" != timeout ] || exit 1 ;;
esac
exit 0
''')
            kubectl.chmod(0o755)
            calls = folder / "calls"
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/bootstrap-infra.sh"), *args],
                env={**os.environ, "PATH": f"{folder}:{os.environ['PATH']}",
                     "CALLS": str(calls), "MODE": mode},
                input="n\n", capture_output=True, text=True, timeout=10,
            )
            return result, calls.read_text() if calls.exists() else ""

    def test_cold_start_applies_root_after_readiness(self):
        result, calls = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"apply -f {ROOT}/argocd/root.yaml", calls)
        self.assertLess(calls.index("wait"), calls.index("argocd/root.yaml"))
        self.assertLess(calls.index("/crds.yaml"), calls.index("condition=Established"))
        self.assertLess(calls.index("condition=Established"), calls.index("/resources.yaml"))
        self.assertNotIn("argocd/projects/", calls)
        self.assertNotIn("get secret", calls)
        self.assertNotIn("exec", calls)

    def test_existing_install_is_rejected_without_mutation(self):
        result, calls = self.run_bootstrap("existing")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("apply", calls)
        self.assertNotIn("delete", calls)
        self.assertNotIn("patch", calls)

    def test_namespace_lookup_failure_is_not_absence(self):
        result, calls = self.run_bootstrap("denied")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("apply", calls)

    def test_wait_failure_prevents_root_and_success(self):
        result, calls = self.run_bootstrap("timeout")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("argocd/root.yaml", calls)
        self.assertNotIn("Bootstrap Complete", result.stdout)

    def test_force_reset_is_no_longer_supported(self):
        result, calls = self.run_bootstrap("", "--force-reset")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "")

    def test_render_failure_prevents_mutation(self):
        result, calls = self.run_bootstrap("render-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("create namespace", calls)
        self.assertNotIn("apply", calls)
