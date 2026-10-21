import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deployment-verifier.py"

_spec = importlib.util.spec_from_file_location("deployment_verifier", SCRIPT)
deployment_verifier = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = deployment_verifier
_spec.loader.exec_module(deployment_verifier)

REPO = "https://github.com/vityasyyy/invenio-rdm-gitops.git"
RELEASE = "a" * 40
OLD = "b" * 40


def git_source(path="k8s/apps/invenio", target="main"):
    return {"repoURL": REPO, "targetRevision": target, "path": path}


def chart_source(chart="loki", version="6.24.0"):
    return {
        "chart": chart,
        "repoURL": "https://grafana.github.io/helm-charts",
        "targetRevision": version,
    }


def application(
    name,
    source=None,
    sources=None,
    revision=RELEASE,
    revisions=None,
    sync="Synced",
    health="Healthy",
    operation=None,
    resources=None,
):
    spec = {"source": source or git_source()}
    status = {
        "sync": {"status": sync, "revision": revision},
        "health": {"status": health},
    }
    if sources is not None:
        spec = {"sources": sources}
        status["sync"] = {"status": sync, "revisions": revisions or []}
    if operation is not None:
        status["operationState"] = operation
    if resources is not None:
        status["resources"] = resources
    return {"metadata": {"name": name}, "spec": spec, "status": status}


def evaluate(apps, trees=None, names=None):
    names = names or [app["metadata"]["name"] for app in apps]
    return deployment_verifier.evaluate_snapshot(apps, trees or {}, names, RELEASE)


def report_for(name):
    app = application(name)
    return evaluate([app])


class RevisionTests(unittest.TestCase):
    def test_old_revision_is_not_clean_even_when_synced_and_healthy(self):
        report = evaluate([application("demo", revision=OLD)])

        self.assertFalse(report.clean)
        self.assertIn("revision", " ".join(report.apps[0].issues).lower())

    def test_unrelated_revision_mismatch_is_reported_not_accepted(self):
        report = evaluate([application("demo", revision=OLD)])

        summary = deployment_verifier.render_summary(
            deployment_verifier.VerificationResult(
                passed=False,
                report=report,
                samples_taken=3,
                streak=0,
                elapsed=90,
                message="timeout",
            ),
            RELEASE,
        )
        self.assertIn(OLD, summary)
        self.assertIn("superseded", summary.lower())


class InventoryTests(unittest.TestCase):
    def test_empty_application_list_fails_for_expected_inventory(self):
        report = evaluate([], names=["demo"])

        self.assertFalse(report.clean)
        self.assertIn("missing", " ".join(report.issues).lower())

    def test_missing_expected_application_fails(self):
        report = evaluate([application("other")], names=["other", "demo"])

        self.assertFalse(report.clean)
        missing = [app for app in report.apps if app.name == "demo"][0]
        self.assertIn("missing", " ".join(missing.issues).lower())

    def test_expected_inventory_comes_from_tracked_metadata_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apps_dir = root / "argocd/apps"
            apps_dir.mkdir(parents=True)
            (apps_dir / "not-the-name.yaml").write_text(
                "apiVersion: argoproj.io/v1alpha1\n"
                "kind: Application\n"
                "metadata:\n"
                "  name: alpha\n"
                "spec:\n"
                "  source:\n"
                "    name: ignored\n"
            )
            (apps_dir / "beta.yaml").write_text(
                "metadata:\n"
                "  name: beta\n"
            )
            (root / "argocd/root.yaml").write_text(
                "metadata:\n"
                "  name: apps\n"
            )

            self.assertEqual(
                set(deployment_verifier.load_expected_inventory(root)),
                {"alpha", "beta", "apps"},
            )

    def test_empty_expected_inventory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(deployment_verifier.ConfigurationError):
                deployment_verifier.load_expected_inventory(Path(tmp))


class OperationTests(unittest.TestCase):
    def test_running_operation_is_not_clean(self):
        report = evaluate([
            application("demo", operation={"phase": "Running", "message": "syncing"})
        ])

        self.assertFalse(report.clean)
        self.assertIn("operation Running", report.apps[0].issues)

    def test_failed_operation_is_not_clean(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Failed",
                    "message": "sync failed: hook error",
                    "operation": {"sync": {"revision": RELEASE}},
                },
            )
        ])

        self.assertFalse(report.clean)
        issues = " ".join(report.apps[0].issues)
        self.assertIn("operation Failed", issues)
        self.assertIn("hook error", issues)

    def test_active_running_operation_waits_even_with_old_target(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Running",
                    "message": "syncing",
                    "operation": {"sync": {"revision": OLD}},
                },
            )
        ])

        self.assertFalse(report.clean)
        self.assertIn("operation Running", report.apps[0].issues)

    def test_stale_successful_operation_revision_is_clean(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Succeeded",
                    "message": "successfully synced (no more tasks)",
                    "finishedAt": "2026-10-20T07:48:26Z",
                    "operation": {"sync": {"revision": OLD}},
                },
            )
        ])

        self.assertTrue(report.clean, report.apps[0].issues)

    def test_stale_terminal_error_is_diagnostic_not_failure(self):
        report = evaluate([
            application(
                "minio",
                sources=[
                    chart_source("minio", "5.4.0"),
                    {"repoURL": REPO, "targetRevision": "main", "ref": "values"},
                ],
                revisions=["5.4.0", RELEASE],
                operation={
                    "phase": "Error",
                    "message": "ComparisonError: failed to get git client",
                    "finishedAt": "2026-09-28T03:21:00Z",
                    "operation": {
                        "sync": {"revisions": ["5.4.0", OLD]}
                    },
                    "syncResult": {
                        "revisions": ["5.4.0", OLD],
                        "resources": [
                            {
                                "hookType": "PostSync",
                                "hookPhase": "Running",
                                "kind": "Job",
                                "namespace": "minio",
                                "name": "minio-post-job",
                                "message": "job applied",
                            }
                        ],
                    },
                },
            )
        ])

        self.assertTrue(report.clean, report.apps[0].issues)
        notes = " ".join(report.apps[0].diagnostics)
        self.assertIn("historical operation Error", notes)
        self.assertIn(OLD, notes)
        self.assertIn("historical hook PostSync", notes)

    def test_stale_operation_does_not_hide_current_missing_hook(self):
        hook_key = {
            "group": "batch",
            "version": "v1",
            "kind": "Job",
            "namespace": "minio",
            "name": "minio-post-job",
        }
        report = evaluate(
            [
                application(
                    "minio",
                    operation={
                        "phase": "Error",
                        "finishedAt": "2026-09-28T03:21:00Z",
                        "operation": {"sync": {"revision": OLD}},
                    },
                    resources=[{**hook_key, "hook": True}],
                )
            ],
            trees={"minio": {"nodes": [{**hook_key, "health": {"status": "Missing"}}]}},
        )

        self.assertFalse(report.clean)
        self.assertIn("Missing", " ".join(report.apps[0].issues))

    def test_current_operation_error_is_not_clean(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Error",
                    "message": "ComparisonError: current state failed",
                    "finishedAt": "2026-10-21T00:00:00Z",
                    "operation": {"sync": {"revision": RELEASE}},
                },
            )
        ])

        self.assertFalse(report.clean)
        self.assertIn("operation Error", " ".join(report.apps[0].issues))

    def test_terminal_operation_without_target_fails_closed(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Error",
                    "message": "old error",
                    "finishedAt": "2026-09-28T03:21:00Z",
                },
            )
        ])

        self.assertFalse(report.clean)
        issues = " ".join(report.apps[0].issues)
        self.assertIn("operation Error", issues)
        self.assertIn("target revision unavailable", issues)


class HookTests(unittest.TestCase):
    def test_failed_postsync_hook_is_not_clean(self):
        report = evaluate([
            application(
                "demo",
                operation={
                    "phase": "Failed",
                    "operation": {"sync": {"revision": RELEASE}},
                    "syncResult": {
                        "resources": [
                            {
                                "hookType": "PostSync",
                                "hookPhase": "Failed",
                                "kind": "Job",
                                "namespace": "invenio",
                                "name": "setup",
                                "message": "backoff limit exceeded",
                            }
                        ]
                    },
                },
            )
        ])

        self.assertFalse(report.clean)
        self.assertIn("PostSync", " ".join(report.apps[0].issues))

    def test_missing_postsync_hook_health_is_not_clean(self):
        hook_key = {
            "group": "batch",
            "version": "v1",
            "kind": "Job",
            "namespace": "invenio",
            "name": "setup",
        }
        tree = {"nodes": [{**hook_key, "health": {"status": "Missing"}}]}
        report = evaluate(
            [
                application(
                    "demo",
                    operation={
                        "phase": "Succeeded",
                        "operation": {"sync": {"revision": RELEASE}},
                        "syncResult": {
                            "resources": [
                                {**hook_key, "hookType": "PostSync", "hookPhase": "Succeeded"}
                            ]
                        },
                    },
                    resources=[{**hook_key, "hook": True}],
                )
            ],
            trees={"demo": tree},
        )

        self.assertFalse(report.clean)
        self.assertIn("Missing", " ".join(report.apps[0].issues))

    def test_current_missing_hook_without_sync_result_is_not_clean(self):
        hook_key = {
            "group": "batch",
            "version": "v1",
            "kind": "Job",
            "namespace": "invenio",
            "name": "setup",
        }
        report = evaluate(
            [application("demo", resources=[{**hook_key, "hook": True}])],
            trees={"demo": {"nodes": [{**hook_key, "health": {"status": "Missing"}}]}},
        )

        self.assertFalse(report.clean)
        self.assertIn("Missing", " ".join(report.apps[0].issues))

    def test_non_hook_tree_node_is_not_treated_as_hook(self):
        tree = {
            "nodes": [
                {
                    "group": "apps",
                    "kind": "Deployment",
                    "namespace": "invenio",
                    "name": "invenio-web",
                    "health": {"status": "Missing"},
                }
            ]
        }
        report = evaluate([application("demo")], trees={"demo": tree})

        self.assertTrue(report.clean, report.apps[0].issues)

    def test_completed_deleted_postsync_hook_is_clean(self):
        hook_key = {
            "group": "batch",
            "version": "v1",
            "kind": "Job",
            "namespace": "invenio",
            "name": "setup",
        }
        report = evaluate(
            [
                application(
                    "demo",
                    operation={
                        "phase": "Succeeded",
                        "operation": {"sync": {"revision": RELEASE}},
                        "syncResult": {
                            "resources": [
                                {**hook_key, "hookType": "PostSync", "hookPhase": "Succeeded"}
                            ]
                        },
                    },
                    resources=[{**hook_key, "hook": True}],
                )
            ],
            trees={"demo": {"nodes": []}},
        )

        self.assertTrue(report.clean, report.apps[0].issues)


class ApiShapeTests(unittest.TestCase):
    def test_malformed_application_list_is_not_clean(self):
        report = deployment_verifier.evaluate_snapshot(
            {"items": "not-a-list"}, {}, ["demo"], RELEASE
        )

        self.assertFalse(report.clean)
        self.assertIn("malformed", " ".join(report.issues).lower())

    def test_application_without_status_is_not_clean(self):
        report = evaluate([{"metadata": {"name": "demo"}}])

        self.assertFalse(report.clean)
        self.assertIn("sync", " ".join(report.apps[0].issues).lower())

    def test_malformed_metadata_is_diagnosable_not_a_crash(self):
        report = evaluate(
            [{"metadata": "not-an-object", "spec": {}, "status": {}}],
            names=["demo"],
        )

        self.assertFalse(report.clean)
        self.assertIn("metadata.name", " ".join(report.issues))

    def test_malformed_resource_tree_is_not_clean(self):
        report = evaluate(
            [application("demo")],
            trees={"demo": {"nodes": "not-a-list"}},
        )

        self.assertFalse(report.clean)
        self.assertIn("resource tree", " ".join(report.apps[0].issues).lower())

    def test_resource_tree_with_malformed_node_is_not_clean(self):
        report = evaluate(
            [application("demo")],
            trees={"demo": {"nodes": [None]}},
        )

        self.assertFalse(report.clean)
        self.assertIn("resource tree", " ".join(report.apps[0].issues).lower())


class SourceRevisionTests(unittest.TestCase):
    def test_single_git_source_uses_triggering_revision(self):
        report = evaluate([application("demo", source=git_source())])

        self.assertTrue(report.clean, report.apps[0].issues)
        self.assertEqual(report.apps[0].expected, [RELEASE])

    def test_chart_source_uses_chart_version_not_commit(self):
        report = evaluate([
            application("demo", source=chart_source(version="6.24.0"), revision="6.24.0")
        ])

        self.assertTrue(report.clean, report.apps[0].issues)
        self.assertEqual(report.apps[0].expected, ["6.24.0"])

    def test_multi_source_keeps_chart_and_git_revisions_distinct(self):
        sources = [
            chart_source("alloy", "1.14.0"),
            {"repoURL": REPO, "targetRevision": "main", "ref": "values"},
            git_source("k8s/infra/alloy"),
        ]
        report = evaluate([
            application(
                "alloy",
                sources=sources,
                revisions=["1.14.0", RELEASE, RELEASE],
            )
        ])

        self.assertTrue(report.clean, report.apps[0].issues)
        self.assertEqual(report.apps[0].expected, ["1.14.0", RELEASE, RELEASE])

    def test_multi_source_git_revision_must_match_triggering_revision(self):
        sources = [
            chart_source("alloy", "1.14.0"),
            {"repoURL": REPO, "targetRevision": "main", "ref": "values"},
            git_source("k8s/infra/alloy"),
        ]
        report = evaluate([
            application(
                "alloy",
                sources=sources,
                revisions=["1.14.0", OLD, RELEASE],
            )
        ])

        self.assertFalse(report.clean)
        issues = " ".join(report.apps[0].issues)
        self.assertIn(OLD, issues)
        self.assertNotIn("1.14.0 !=", issues)


class SamplingTests(unittest.TestCase):
    class FakeClock:
        def __init__(self):
            self.now = 0
            self.sleeps = []

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    class SequenceClient:
        def __init__(self, reports):
            self.reports = list(reports)
            self.samples = 0

        def snapshot(self, expected_names, release):
            self.samples += 1
            return self.reports.pop(0)

    def test_three_consecutive_clean_samples_are_required_and_dirty_resets(self):
        clean = report_for("demo")
        dirty = evaluate([application("demo", revision=OLD)])
        clock = self.FakeClock()
        client = self.SequenceClient([clean, clean, dirty, clean, clean, clean])

        result = deployment_verifier.verify(
            client,
            ["demo"],
            RELEASE,
            samples=3,
            interval=30,
            timeout=900,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.samples_taken, 6)
        self.assertEqual(result.streak, 3)
        self.assertEqual(clock.sleeps, [30, 30, 30, 30, 30])

    def test_overall_timeout_bounds_polling(self):
        dirty = evaluate([application("demo", revision=OLD)])
        clock = self.FakeClock()
        client = self.SequenceClient([dirty] * 10)

        result = deployment_verifier.verify(
            client,
            ["demo"],
            RELEASE,
            samples=3,
            interval=30,
            timeout=75,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.passed)
        self.assertGreaterEqual(result.elapsed, 75)
        self.assertLessEqual(clock.now, 75)

    def test_clean_result_after_deadline_is_rejected(self):
        clock = self.FakeClock()
        clean = report_for("demo")

        class SlowClient:
            def snapshot(self, expected_names, release):
                clock.now += 901
                return clean

        result = deployment_verifier.verify(
            SlowClient(),
            ["demo"],
            RELEASE,
            samples=1,
            interval=30,
            timeout=900,
            sleep=clock.sleep,
            clock=clock,
        )

        self.assertFalse(result.passed)
        self.assertIn("deadline", " ".join(result.report.issues))


class SummaryTests(unittest.TestCase):
    def test_summary_reports_per_app_revision_sync_health_operation_and_hooks(self):
        report = report_for("demo")
        result = deployment_verifier.VerificationResult(
            passed=True,
            report=report,
            samples_taken=3,
            streak=3,
            elapsed=60,
            message="3 consecutive clean samples",
        )

        summary = deployment_verifier.render_summary(result, RELEASE)

        for field in ("Revision", "Sync", "Health", "Operation", "Hooks", "Diagnostics"):
            self.assertIn(field, summary)
        self.assertIn("demo", summary)
        self.assertIn(RELEASE, summary)


if __name__ == "__main__":
    unittest.main()
