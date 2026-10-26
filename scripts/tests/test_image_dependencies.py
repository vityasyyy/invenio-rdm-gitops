import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PIPFILE = ROOT / "docker/ugm/Pipfile"
LOCKFILE = ROOT / "docker/ugm/Pipfile.lock"


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
