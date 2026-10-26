import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROUTE = ROOT / "k8s/infra/minio/resources/minio-s3-ingressroute.yaml"
POLICY = ROOT / "k8s/infra/security/resources/network-policies/minio-allow.yaml"


class MinioExposureTests(unittest.TestCase):
    def test_public_route_is_scoped_to_the_invenio_bucket(self):
        route = yaml.safe_load(ROUTE.read_text())
        rules = route["spec"]["routes"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["match"],
                         "Host(`minio.vityasy.me`) && PathPrefix(`/invenio-rdm/`)")
        self.assertEqual(rules[0]["services"], [{"name": "minio", "port": 9000}])

    def test_traefik_may_reach_the_s3_api_port(self):
        policies = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
        policy = next(p for p in policies if p["metadata"]["name"] == "allow-minio-required-ingress")
        traefik_rule = next(
            rule for rule in policy["spec"]["ingress"]
            if any(peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name") == "traefik"
                for peer in rule["from"])
        )
        self.assertEqual({port["port"] for port in traefik_rule["ports"]}, {9000, 9001})
