import json
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PIPFILE = ROOT / "docker/ugm/Pipfile"
LOCKFILE = ROOT / "docker/ugm/Pipfile.lock"
APP_CONFIG = ROOT / "k8s/apps/invenio/resources/app-config.yaml"


class ImageDependencyTests(unittest.TestCase):
    def test_invenio_image_has_the_s3_filesystem_extra(self):
        pipfile = tomllib.loads(PIPFILE.read_text())
        package = pipfile["packages"]["invenio-app-rdm"]
        self.assertIn("opensearch2", package["extras"])
        self.assertIn("s3", package["extras"])

    def test_lockfile_contains_the_s3_filesystem_backend(self):
        locked = json.loads(LOCKFILE.read_text())["default"]
        self.assertIn("invenio-s3", locked)
        self.assertIn("s3fs", locked)

    def test_application_uses_the_s3_storage_factory_and_minio_endpoint(self):
        config = yaml.safe_load(APP_CONFIG.read_text())["data"]
        self.assertEqual(config["INVENIO_FILES_REST_STORAGE_FACTORY"],
                         "invenio_s3.s3fs_storage_factory")
        self.assertEqual(config["INVENIO_S3_ENDPOINT_URL"],
                         "http://minio.minio.svc.cluster.local:9000")
