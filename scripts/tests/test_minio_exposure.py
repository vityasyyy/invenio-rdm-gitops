import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROUTE = ROOT / "k8s/infra/minio/resources/minio-s3-ingressroute.yaml"
POLICY = ROOT / "k8s/infra/security/resources/network-policies/minio-allow.yaml"
TRAEFIK_EGRESS = ROOT / "k8s/infra/security/resources/traefik-full-egress.yaml"
MINIO_VALUES = ROOT / "k8s/infra/minio/values.yaml"
BUCKET_JOB = ROOT / "k8s/infra/minio/resources/create-bucket-job.yaml"


def _ingress_rule_for(policy, namespace):
    return next(
        rule for rule in policy["spec"]["ingress"]
        if any(peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
            "kubernetes.io/metadata.name") == namespace
            for peer in rule["from"])
    )


def _egress_rule_for(policy, namespace):
    return next(
        rule for rule in policy["spec"]["egress"]
        if any(peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
            "kubernetes.io/metadata.name") == namespace
            for peer in rule["to"])
    )


class MinioExposureTests(unittest.TestCase):
    def test_public_route_is_scoped_to_the_invenio_bucket(self):
        route = yaml.safe_load(ROUTE.read_text())
        rules = route["spec"]["routes"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["match"],
                         "Host(`minio.vityasy.me`) && PathPrefix(`/invenio-rdm/`)")
        self.assertEqual(rules[0]["services"], [{"name": "minio", "port": 9000}])

    def test_minio_ingress_allows_traefik_on_both_ports(self):
        policies = [doc for doc in yaml.safe_load_all(POLICY.read_text()) if doc]
        policy = next(doc for doc in policies
                      if doc["metadata"]["name"] == "allow-minio-required-ingress")
        ports = {port["port"] for port in _ingress_rule_for(policy, "traefik")["ports"]}
        self.assertEqual(ports, {9000, 9001})

    def test_traefik_egress_allows_minio_s3(self):
        policy = yaml.safe_load(TRAEFIK_EGRESS.read_text())
        ports = {port["port"] for port in _egress_rule_for(policy, "minio")["ports"]}
        self.assertIn(9000, ports)

    def test_bucket_hook_uses_the_cached_minio_image(self):
        image = yaml.safe_load(MINIO_VALUES.read_text())["image"]
        expected = f"{image['repository']}:{image['tag']}"
        pod = yaml.safe_load(BUCKET_JOB.read_text())["spec"]["template"]["spec"]
        self.assertEqual(pod["containers"][0]["image"], expected)
        affinity = pod["affinity"]["podAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"]
        self.assertEqual(affinity[0]["labelSelector"]["matchLabels"]["app"], "minio")
