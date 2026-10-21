import contextlib
import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ALLOY_RUNTIME_IMAGE = "grafana/alloy:v1.21.0"
AGED_ENTRY_DROP_REASON = "older_than_loki_reject_window"
VALUES_PATH = ROOT / "k8s/infra/alloy/values.yaml"
RBAC_PATH = ROOT / "k8s/infra/alloy/rbac.yaml"
KUSTOMIZATION_PATH = ROOT / "k8s/infra/alloy/kustomization.yaml"
MONITORING_VALUES_PATH = ROOT / "k8s/infra/monitoring/values.yaml"
MONITORING_API_NETPOL_PATH = ROOT / "k8s/infra/monitoring/networkpolicy-kube-api.yaml"
MONITORING_EGRESS_PATH = ROOT / "k8s/infra/security/monitoring-egress.yaml"
MONITORING_ALLOW_PATH = ROOT / "k8s/infra/security/network-policies/monitoring-allow.yaml"
PODMONITOR_PATH = ROOT / "k8s/infra/alloy/podmonitor.yaml"


def load_yaml(path):
    with path.open() as handle:
        return yaml.safe_load(handle)


def load_values():
    return load_yaml(VALUES_PATH)


def render_chart():
    chart_path = os.environ.get("ALLOY_CHART_PATH")
    if not chart_path:
        raise unittest.SkipTest("ALLOY_CHART_PATH is not set")
    return subprocess.run(
        [
            "helm",
            "template",
            "alloy",
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


def alloy_block(config, header):
    start = config.index(header)
    end = config.index("\n}", start) + 2
    return config[start:end]


class AlloyValuesTest(unittest.TestCase):
    def setUp(self):
        self.values = load_values()

    def test_single_recreate_deployment_on_workers(self):
        controller = self.values["controller"]
        self.assertEqual(controller["type"], "deployment")
        self.assertEqual(controller["replicas"], 1)
        self.assertEqual(controller["updateStrategy"], {"type": "Recreate"})
        self.assertFalse(controller["hostNetwork"])
        self.assertFalse(controller["hostPID"])
        expressions = controller["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"]
        self.assertIn(
            {"key": "node-role.kubernetes.io/control-plane", "operator": "DoesNotExist"},
            expressions,
        )

    def test_service_account_token_is_mounted_for_api_access(self):
        self.assertFalse(self.values["rbac"]["create"])
        self.assertTrue(self.values["serviceAccount"]["automountServiceAccountToken"])
        self.assertFalse(self.values["configReloader"]["enabled"])
        self.assertFalse(self.values["crds"]["create"])

    def test_no_host_mounts(self):
        mounts = self.values["alloy"]["mounts"]
        self.assertFalse(mounts["varlog"])
        self.assertFalse(mounts["dockercontainers"])
        self.assertEqual([mount["name"] for mount in mounts["extra"]], ["alloy-storage"])
        self.assertNotIn('"hostPath"', yaml.safe_dump(self.values))

    def test_container_is_hardened(self):
        security = self.values["alloy"]["securityContext"]
        self.assertFalse(security["allowPrivilegeEscalation"])
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertTrue(security["runAsNonRoot"])
        self.assertEqual(security["capabilities"]["drop"], ["ALL"])

    def test_ui_is_not_exposed(self):
        self.assertFalse(self.values["service"]["enabled"])
        self.assertFalse(self.values["serviceMonitor"]["enabled"])
        self.assertFalse(self.values["ingress"]["enabled"])

    def test_network_policy_allows_only_prometheus_metrics_dns_api_and_loki(self):
        policy = self.values["networkPolicy"]
        self.assertTrue(policy["enabled"])
        self.assertEqual(len(policy["ingress"]), 1)
        self.assertEqual(
            policy["ingress"][0]["from"],
            [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}],
        )
        self.assertEqual({port["port"] for port in policy["ingress"][0]["ports"]}, {12345})
        self.assertEqual(len(policy["egress"]), 3)
        self.assertEqual({port["port"] for port in policy["egress"][0]["ports"]}, {53})
        self.assertEqual({port["port"] for port in policy["egress"][1]["ports"]}, {443, 6443})
        self.assertEqual(
            policy["egress"][1]["to"],
            [{"ipBlock": {"cidr": "10.43.0.1/32"}}, {"ipBlock": {"cidr": "10.17.117.41/32"}}],
        )
        self.assertEqual({port["port"] for port in policy["egress"][2]["ports"]}, {3100})
        self.assertEqual(
            policy["egress"][2]["to"][0]["podSelector"]["matchLabels"],
            {"app.kubernetes.io/name": "loki", "app.kubernetes.io/component": "single-binary"},
        )

    def test_config_discovery_is_namespace_scoped(self):
        config = self.values["alloy"]["configMap"]["content"]
        self.assertNotRegex(config, r"(?m)^\s*#", "Alloy rejects YAML-style comments")
        self.assertIn('role = "pod"', config)
        self.assertIn('names = ["invenio"]', config)
        self.assertIn('regex         = "invenio"', config)
        self.assertIn('action        = "keep"', config)
        self.assertIn("targets    = discovery.relabel.pods.output", config)
        self.assertIn("loki.source.kubernetes", config)
        for label in ("namespace", "app", "pod", "container"):
            self.assertIn(f'target_label  = "{label}"', config)
        self.assertIn("loki.monitoring.svc.cluster.local:3100", config)

    def test_config_has_no_file_or_cri_pipeline(self):
        config = self.values["alloy"]["configMap"]["content"]
        self.assertNotIn("loki.source.file", config)
        self.assertNotIn("local.file_match", config)
        self.assertNotIn("file_match", config)
        self.assertNotIn("stage.cri", config)
        self.assertNotIn("filename", config)
        self.assertIn('values = ["instance", "job"]', config)

    def test_stale_entries_are_dropped_before_label_drop(self):
        config = self.values["alloy"]["configMap"]["content"]
        process = alloy_block(config, 'loki.process "invenio" {')
        self.assertIn('older_than          = "167h"', process)
        self.assertIn(f'drop_counter_reason = "{AGED_ENTRY_DROP_REASON}"', process)
        self.assertLess(process.index("stage.drop"), process.index("stage.label_drop"))
        self.assertNotIn("tail_from_end", config)


class AlloyRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("helm") is None:
            raise unittest.SkipTest("helm is not installed")
        cls.objects = objects(render_chart())
        cls.by_kind_name = {
            (obj.get("kind"), obj.get("metadata", {}).get("name")): obj
            for obj in cls.objects
        }

    def test_renders_one_recreate_deployment(self):
        workloads = {
            (kind, name)
            for kind, name in self.by_kind_name
            if kind in {"DaemonSet", "Deployment", "StatefulSet"}
        }
        self.assertEqual(workloads, {("Deployment", "alloy")})
        deployment = self.by_kind_name[("Deployment", "alloy")]["spec"]
        self.assertEqual(deployment["replicas"], 1)
        self.assertEqual(deployment["strategy"], {"type": "Recreate"})

    def test_pod_has_no_host_namespaces_privileges_or_mounts(self):
        pod = self.by_kind_name[("Deployment", "alloy")]["spec"]["template"]["spec"]
        self.assertNotIn("hostNetwork", pod)
        self.assertNotIn("hostPID", pod)
        container = pod["containers"][0]
        self.assertNotIn("privileged", container.get("securityContext", {}))
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertTrue(container["securityContext"]["runAsNonRoot"])
        self.assertNotIn("hostPort", container["ports"][0])
        self.assertNotIn("hostPath", yaml.safe_dump(pod["volumes"]))

    def test_runs_on_workers_only(self):
        pod = self.by_kind_name[("Deployment", "alloy")]["spec"]["template"]["spec"]
        terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
            "nodeSelectorTerms"
        ]
        self.assertEqual(
            terms[0]["matchExpressions"],
            [{"key": "node-role.kubernetes.io/control-plane", "operator": "DoesNotExist"}],
        )

    def test_service_account_token_is_mounted(self):
        service_account = self.by_kind_name[("ServiceAccount", "alloy")]
        self.assertTrue(service_account["automountServiceAccountToken"])
        rbac = {
            obj.get("kind")
            for obj in self.objects
            if obj.get("kind") in {"Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"}
        }
        self.assertEqual(rbac, set())
        self.assertNotIn(("Secret", "alloy"), self.by_kind_name)

    def test_no_service_ingress_or_service_monitor(self):
        for kind in ("Service", "Ingress", "ServiceMonitor"):
            with self.subTest(kind=kind):
                self.assertEqual([obj for obj in self.objects if obj.get("kind") == kind], [])

    def test_config_reloader_is_not_rendered(self):
        pod = self.by_kind_name[("Deployment", "alloy")]["spec"]["template"]["spec"]
        self.assertEqual([container["name"] for container in pod["containers"]], ["alloy"])

    def test_network_policy_scopes_ingress_and_egress(self):
        policy = self.by_kind_name[("NetworkPolicy", "alloy")]["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
        ingress = policy["ingress"]
        self.assertEqual(len(ingress), 1)
        self.assertEqual(
            ingress[0]["from"],
            [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}],
        )
        self.assertEqual({port["port"] for port in ingress[0]["ports"]}, {12345})
        self.assertEqual(len(policy["egress"]), 3)
        self.assertEqual({port["port"] for port in policy["egress"][0]["ports"]}, {53})
        self.assertEqual({port["port"] for port in policy["egress"][1]["ports"]}, {443, 6443})
        self.assertEqual(
            policy["egress"][1]["to"],
            [{"ipBlock": {"cidr": "10.43.0.1/32"}}, {"ipBlock": {"cidr": "10.17.117.41/32"}}],
        )
        self.assertEqual(
            policy["egress"][2]["to"][0]["podSelector"]["matchLabels"],
            {"app.kubernetes.io/name": "loki", "app.kubernetes.io/component": "single-binary"},
        )
        self.assertEqual({port["port"] for port in policy["egress"][2]["ports"]}, {3100})

    def test_rendered_config_is_api_based(self):
        config = self.by_kind_name[("ConfigMap", "alloy")]["data"]["config.alloy"]
        self.assertIn('names = ["invenio"]', config)
        self.assertIn("targets    = discovery.relabel.pods.output", config)
        self.assertNotIn("loki.source.file", config)
        self.assertNotIn("stage.cri", config)
        self.assertIn("http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push", config)


class AlloyBroadPolicyUnionTest(unittest.TestCase):
    @staticmethod
    def load_policy(path, name):
        with path.open() as handle:
            for doc in yaml.safe_load_all(handle):
                if doc and doc["metadata"]["name"] == name:
                    return doc
        raise AssertionError(f"{name} not found in {path}")

    @staticmethod
    def selects(selector, app_name):
        expression = selector["matchExpressions"][0]
        return app_name not in expression["values"]

    def test_broad_policies_exclude_alloy_but_cover_other_pods(self):
        policies = [
            self.load_policy(MONITORING_API_NETPOL_PATH, "allow-monitoring-kube-api"),
            self.load_policy(MONITORING_EGRESS_PATH, "allow-monitoring-egress-to-internet"),
            self.load_policy(MONITORING_ALLOW_PATH, "allow-monitoring-internal"),
        ]
        for policy in policies:
            with self.subTest(policy=policy["metadata"]["name"]):
                selector = policy["spec"]["podSelector"]
                self.assertEqual(
                    selector,
                    {
                        "matchExpressions": [
                            {
                                "key": "app.kubernetes.io/name",
                                "operator": "NotIn",
                                "values": ["alloy"],
                            }
                        ]
                    },
                )
                self.assertFalse(self.selects(selector, "alloy"))
                self.assertTrue(self.selects(selector, "invenio-web"))
                self.assertTrue(self.selects(selector, None))


class AlloyRBACTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with RBAC_PATH.open() as handle:
            cls.objects = [doc for doc in yaml.safe_load_all(handle) if doc]
        cls.by_kind = {obj["kind"]: obj for obj in cls.objects}

    def test_role_is_namespace_scoped_to_pods_and_logs(self):
        role = self.by_kind["Role"]
        self.assertEqual(role["metadata"]["namespace"], "invenio")
        rules = {tuple(rule["resources"]): set(rule["verbs"]) for rule in role["rules"]}
        self.assertEqual(rules[("pods",)], {"get", "list", "watch"})
        self.assertEqual(rules[("pods/log",)], {"get"})
        self.assertNotIn("secrets", yaml.safe_dump(role))

    def test_role_binding_targets_the_monitoring_service_account(self):
        binding = self.by_kind["RoleBinding"]
        self.assertEqual(binding["metadata"]["namespace"], "invenio")
        self.assertEqual(
            binding["subjects"],
            [{"kind": "ServiceAccount", "name": "alloy", "namespace": "monitoring"}],
        )
        self.assertEqual(binding["roleRef"]["kind"], "Role")
        self.assertEqual(binding["roleRef"]["name"], "alloy-log-reader")

    def test_no_cluster_scoped_rbac(self):
        kinds = {obj["kind"] for obj in self.objects}
        self.assertNotIn("ClusterRole", kinds)
        self.assertNotIn("ClusterRoleBinding", kinds)


class AlloyPodMonitorTest(unittest.TestCase):
    def test_scrapes_the_pod_metrics_port_without_a_service(self):
        monitor = load_yaml(PODMONITOR_PATH)
        self.assertEqual(monitor["kind"], "PodMonitor")
        self.assertEqual(monitor["metadata"]["namespace"], "monitoring")
        self.assertEqual(monitor["metadata"]["labels"], {"release": "monitoring"})
        self.assertEqual(
            monitor["spec"]["selector"]["matchLabels"],
            {"app.kubernetes.io/name": "alloy", "app.kubernetes.io/instance": "alloy"},
        )
        endpoint = monitor["spec"]["podMetricsEndpoints"][0]
        self.assertEqual(endpoint["port"], "http-metrics")
        self.assertEqual(endpoint["path"], "/metrics")
        self.assertEqual(
            endpoint["relabelings"],
            [{"action": "replace", "targetLabel": "job", "replacement": "alloy"}],
        )


class AlloyKustomizationTest(unittest.TestCase):
    def test_applies_rbac_and_pod_monitor(self):
        kustomization = load_yaml(KUSTOMIZATION_PATH)
        self.assertEqual(kustomization["resources"], ["podmonitor.yaml", "rbac.yaml"])


class GrafanaLokiDatasourceTest(unittest.TestCase):
    def test_values_provision_one_loki_datasource(self):
        values = load_yaml(MONITORING_VALUES_PATH)
        self.assertEqual(
            values["grafana"]["additionalDataSources"],
            [
                {
                    "name": "Loki",
                    "uid": "loki",
                    "type": "loki",
                    "access": "proxy",
                    "url": "http://loki.monitoring.svc.cluster.local:3100",
                    "isDefault": False,
                }
            ],
        )


class GrafanaLokiDatasourceRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chart_path = os.environ.get("MONITORING_CHART_PATH")
        if not chart_path:
            raise unittest.SkipTest("MONITORING_CHART_PATH is not set")
        manifest = subprocess.run(
            [
                "helm",
                "template",
                "monitoring",
                chart_path,
                "--namespace",
                "monitoring",
                "--values",
                str(MONITORING_VALUES_PATH),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        cls.datasources = []
        for obj in objects(manifest):
            for content in (obj.get("data") or {}).values():
                if not isinstance(content, str) or "type: loki" not in content:
                    continue
                cls.datasources.extend(yaml.safe_load(content).get("datasources", []))

    def test_rendered_datasource_targets_the_loki_service(self):
        loki = [source for source in self.datasources if source.get("type") == "loki"]
        self.assertEqual(len(loki), 1)
        self.assertEqual(loki[0]["uid"], "loki")
        self.assertEqual(loki[0]["url"], "http://loki.monitoring.svc.cluster.local:3100")
        self.assertFalse(loki[0]["isDefault"])


def docker(*args, input_text=None):
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=120,
    )
    return result.stdout + result.stderr


def runtime_harness_config():
    production = load_values()["alloy"]["configMap"]["content"]
    process = alloy_block(production, 'loki.process "invenio" {')
    process = process.replace(
        "forward_to = [loki.write.loki.receiver]",
        "forward_to = [loki.echo.captured.receiver]",
    )
    process = process.replace(
        "  stage.drop {",
        '''  stage.regex {
    expression = "^(?P<ts>[^ ]+) (?P<msg>.*)$"
  }

  stage.timestamp {
    source = "ts"
    format = "RFC3339"
  }

  stage.drop {''',
        1,
    )
    return f'''logging {{ level = "info" }}

loki.source.api "in" {{
  http {{
    listen_address = "0.0.0.0"
    listen_port    = 3101
  }}
  forward_to = [loki.process.invenio.receiver]
}}

{process}

loki.echo "captured" {{}}
'''


def published_port(name, container_port):
    return int(docker("port", name, f"{container_port}/tcp").strip().rsplit(":", 1)[1])


def wait_for_http(url, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return response.read().decode()
        except OSError:
            pass
        time.sleep(0.25)
    raise AssertionError(f"{url} did not become ready")


@contextlib.contextmanager
def running_alloy(config_text):
    name = f"alloy-runtime-test-{os.getpid()}"
    (ROOT / "rendered").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "rendered") as tmp:
        (Path(tmp) / "harness.alloy").write_text(config_text)
        docker("rm", "-f", name)
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::3101",
            "-p",
            "127.0.0.1::12345",
            "-v",
            f"{tmp}:/etc/alloy:ro",
            "--entrypoint",
            "/bin/alloy",
            ALLOY_RUNTIME_IMAGE,
            "run",
            "--stability.level=public-preview",
            "--server.http.listen-addr=0.0.0.0:12345",
            "--storage.path=/tmp/alloy",
            "/etc/alloy/harness.alloy",
        )
        try:
            push_url = f"http://127.0.0.1:{published_port(name, 3101)}/loki/api/v1/push"
            metrics_url = f"http://127.0.0.1:{published_port(name, 12345)}/metrics"
            wait_for_http(metrics_url)
            yield push_url, metrics_url, name
        finally:
            docker("rm", "-f", name)


def push_entries(url, entries):
    values = [
        [
            str(int(timestamp.timestamp() * 1_000_000_000)),
            f"{timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')} {marker}",
        ]
        for marker, timestamp in entries
    ]
    payload = {"streams": [{"stream": {"job": "runtime-test"}, "values": values}]}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


@unittest.skipUnless(
    os.environ.get("ALLOY_RUNTIME_TEST") == "1",
    "set ALLOY_RUNTIME_TEST=1 to run tests against the pinned Alloy runtime",
)
class AlloyRuntimeAgeDropTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker is not installed")
        inspect = subprocess.run(
            ["docker", "image", "inspect", ALLOY_RUNTIME_IMAGE],
            capture_output=True,
            text=True,
        )
        if inspect.returncode:
            raise unittest.SkipTest(
                f"{ALLOY_RUNTIME_IMAGE} is not available locally; pull it before running"
            )

    def test_pinned_runtime_accepts_complete_config(self):
        version = docker(
            "run",
            "--rm",
            "--entrypoint",
            "/bin/alloy",
            ALLOY_RUNTIME_IMAGE,
            "--version",
        )
        self.assertIn("v1.21.0", version)
        docker(
            "run",
            "--rm",
            "-i",
            "--entrypoint",
            "/bin/alloy",
            ALLOY_RUNTIME_IMAGE,
            "validate",
            "/dev/stdin",
            input_text=load_values()["alloy"]["configMap"]["content"],
        )

    def test_mixed_batch_drops_only_aged_entries(self):
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        entries = [
            ("alloy-runtime-aged-8d", now - datetime.timedelta(days=8)),
            ("alloy-runtime-aged-169h", now - datetime.timedelta(hours=169)),
            ("alloy-runtime-aged-168h", now - datetime.timedelta(hours=168)),
            ("alloy-runtime-current", now),
        ]
        with running_alloy(runtime_harness_config()) as (push_url, metrics_url, name):
            self.assertEqual(push_entries(push_url, entries), 204)
            deadline = time.monotonic() + 15
            logs = ""
            while time.monotonic() < deadline:
                logs = docker("logs", name)
                if "alloy-runtime-current" in logs:
                    break
                time.sleep(0.25)
            self.assertIn("alloy-runtime-current", logs)
            for marker, _ in entries[:-1]:
                self.assertNotIn(marker, logs)
            metrics = urllib.request.urlopen(metrics_url, timeout=5).read().decode()
            dropped = [
                line
                for line in metrics.splitlines()
                if line.startswith("loki_process_dropped_lines_total{")
                and f'reason="{AGED_ENTRY_DROP_REASON}"' in line
            ]
            self.assertEqual(len(dropped), 1)
            self.assertEqual(float(dropped[0].rsplit(" ", 1)[1]), 3)


if __name__ == "__main__":
    unittest.main()
