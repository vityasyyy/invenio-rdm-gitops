import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CLUSTER = ROOT / "k8s/dependencies/postgresql/resources/cluster.yaml"


def documents(path):
    return [document for document in yaml.safe_load_all(path.read_text()) if document]


class BackupContractTests(unittest.TestCase):
    def test_cnpg_schedule_is_six_field_daily(self):
        schedules = [document for document in documents(CLUSTER)
                     if document.get("kind") == "ScheduledBackup"]
        self.assertEqual(len(schedules), 1)
        fields = schedules[0]["spec"]["schedule"].split()
        self.assertEqual(len(fields), 6,
                         "CloudNativePG requires a seconds field; five fields run hourly")
        self.assertEqual(fields, ["0", "0", "2", "*", "*", "*"])
