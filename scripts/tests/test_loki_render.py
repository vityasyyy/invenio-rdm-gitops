import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES_PATH = ROOT / "k8s/infra/loki/values.yaml"
NETWORK_POLICY_PATH = ROOT / "k8s/infra/monitoring/loki-networkpolicy.yaml"


def load_values():
    with VALUES_PATH.open() as handle:
        return yaml.safe_load(handle)


def render_chart():
    chart_path = os.environ.get("LOKI_CHART_PATH")
    if not chart_path:
        raise unittest.SkipTest("LOKI_CHART_PATH is not set")
    return subprocess.run(
        [
            "helm",
            "template",
            "loki",
            chart_path,
            "--namespace",
            "monitoring",
            "--values",
            str(VALUES_PATH),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def objects(manifest):
    return [doc for doc in yaml.safe_load_all(manifest) if doc]


class LokiValuesTest(unittest.TestCase):
    def setUp(self):
        self.values = load_values()

    def test_single_binary_mode_is_selected(self):
        self.assertEqual(self.values["deploymentMode"], "SingleBinary")
        self.assertEqual(self.values["singleBinary"]["replicas"], 1)

    def test_scalable_components_stay_scaled_to_zero(self):
        for component in ("read", "write", "backend"):
            with self.subTest(component=component):
                self.assertEqual(self.values[component]["replicas"], 0)

    def test_filesystem_storage_is_on_the_persistent_volume(self):
        filesystem = self.values["loki"]["storageConfig"]["filesystem"]
        self.assertEqual(filesystem["chunks_directory"], "/var/loki/chunks")
        self.assertEqual(filesystem["rules_directory"], "/var/loki/rules")

    def test_persistence_is_not_deleted_with_the_statefulset(self):
        persistence = self.values["singleBinary"]["persistence"]
        self.assertTrue(persistence["enabled"])
        self.assertFalse(persistence["enableStatefulSetAutoDeletePVC"])
        self.assertEqual(persistence["storageClass"], "btd-nfs")
        self.assertEqual(persistence["size"], "10Gi")

    def test_retention_is_bounded(self):
        self.assertEqual(self.values["loki"]["limits_config"]["retention_period"], "168h")
        compactor = self.values["loki"]["compactor"]
        self.assertTrue(compactor["retention_enabled"])
        self.assertEqual(compactor["delete_request_store"], "filesystem")

    def test_canary_stays_enabled_and_pushes_to_loki(self):
        canary = self.values["lokiCanary"]
        self.assertTrue(canary["enabled"])
        self.assertTrue(canary.get("push", True))

    def test_no_dead_promtail_configuration(self):
        self.assertNotIn("promtail", self.values)


class LokiRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("helm") is None:
            raise unittest.SkipTest("helm is not installed")
        manifest = render_chart()
        cls.objects = objects(manifest)
        cls.by_kind_name = {
            (obj.get("kind"), obj.get("metadata", {}).get("name")): obj
            for obj in cls.objects
        }
        cls.config = next(
            obj["data"]["config.yaml"]
            for obj in cls.objects
            if obj.get("kind") == "ConfigMap"
            and obj.get("metadata", {}).get("name") == "loki"
        )

    def test_renders_one_single_binary_statefulset(self):
        statefulset = self.by_kind_name[("StatefulSet", "loki")]
        self.assertEqual(statefulset["spec"]["replicas"], 1)
        claim = statefulset["spec"]["volumeClaimTemplates"][0]
        self.assertEqual(claim["spec"]["storageClassName"], "btd-nfs")
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "10Gi")
        self.assertNotIn("persistentVolumeClaimRetentionPolicy", statefulset["spec"])

    def test_renders_loki_service_on_the_canary_endpoint(self):
        service = self.by_kind_name[("Service", "loki")]
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        ports = {port["name"]: port["port"] for port in service["spec"]["ports"]}
        self.assertEqual(ports["http-metrics"], 3100)

    def test_canary_targets_the_single_binary_service(self):
        canary = self.by_kind_name[("DaemonSet", "loki-canary")]
        args = canary["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("-addr=loki.monitoring.svc.cluster.local.:3100", args)

    def test_no_scalable_or_gateway_workloads_are_rendered(self):
        workloads = {
            name
            for kind, name in self.by_kind_name
            if kind in {"Deployment", "StatefulSet", "DaemonSet"}
        }
        self.assertEqual(workloads, {"loki", "loki-chunks-cache", "loki-results-cache", "loki-canary"})

    def test_render_contains_retention_configuration(self):
        self.assertIn("retention_period: 168h", self.config)
        self.assertIn("retention_enabled: true", self.config)
        self.assertIn("delete_request_store: filesystem", self.config)


class LokiNetworkPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with NETWORK_POLICY_PATH.open() as handle:
            cls.policies = {
                doc["metadata"]["name"]: doc
                for doc in yaml.safe_load_all(handle)
                if doc
            }

    def test_base_policy_keeps_dns_only_egress(self):
        egress = self.policies["loki-allow-internal"]["spec"]["egress"]
        self.assertEqual(len(egress), 1)
        self.assertEqual({port["port"] for port in egress[0]["ports"]}, {53})

    def test_canary_can_push_to_the_single_binary(self):
        policy = self.policies["loki-canary-egress"]
        selector = policy["spec"]["podSelector"]["matchLabels"]
        self.assertEqual(selector["app.kubernetes.io/component"], "canary")
        egress = policy["spec"]["egress"]
        self.assertEqual(
            egress[0]["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"],
            "single-binary",
        )
        self.assertEqual({port["port"] for port in egress[0]["ports"]}, {3100})

    def test_single_binary_and_caches_are_scoped_to_memcached(self):
        egress = self.policies["loki-single-binary-egress"]["spec"]["egress"]
        self.assertEqual(
            egress[0]["to"][0]["podSelector"]["matchExpressions"][0]["values"],
            ["memcached-chunks-cache", "memcached-results-cache"],
        )
        self.assertEqual({port["port"] for port in egress[0]["ports"]}, {11211})

        ingress = self.policies["loki-cache-ingress"]["spec"]["ingress"]
        self.assertEqual(
            ingress[0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"],
            "single-binary",
        )
        self.assertEqual({port["port"] for port in ingress[0]["ports"]}, {11211})


if __name__ == "__main__":
    unittest.main()
