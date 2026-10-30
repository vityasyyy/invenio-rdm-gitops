import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "k8s/infra/minio/resources/s3-file-backup.py"
CONFIG = ROOT / "k8s/infra/minio/resources/s3-file-backup-config.json"
PERMISSIONS = ROOT / "docs/s3-file-backup-permissions.md"
OPERATIONS = ROOT / "docs/operations.md"
MINIO = ROOT / "k8s/infra/minio"
MONITORING = ROOT / "k8s/infra/monitoring"
CRONJOB = MINIO / "resources/s3-file-backup-cronjob.yaml"
ALERTS = MONITORING / "resources/s3-file-backup-alerts.yaml"
PINNED_RUNTIME = (
    "ghcr.io/cloudnative-pg/postgresql@sha256:"
    "99be063781d171d3971089b49c992706bdab9ccbd2b57cdf126c7542773aedfe"
)
NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def load_module():
    spec = importlib.util.spec_from_file_location("s3_file_backup", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_config(**overrides):
    config = {
        "endpoint": "http://minio.minio.svc.cluster.local:9000",
        "region": "us-east-1",
        "sources": ["invenio-rdm", "invenio-rdm-uploads"],
        "destination": "invenio-rdm-file-backups",
        "prefix": "snapshots/",
        "retention_days": 30,
    }
    config.update(overrides)
    return config


class FakeClientError(Exception):
    def __init__(self, code, message="synthetic client error"):
        self.response = {"Error": {"Code": code, "Message": message}}
        super().__init__(message)


class FakeBody:
    def __init__(self, data, read_limit):
        self._data = data
        self._position = 0
        self._read_limit = read_limit
        self.closed = False

    def read(self, amount=-1):
        if amount is None or amount < 0:
            amount = len(self._data) - self._position
        amount = min(amount, self._read_limit, len(self._data) - self._position)
        if amount <= 0:
            return b""
        start = self._position
        self._position += amount
        return self._data[start:self._position]

    def close(self):
        self.closed = True


class FakePaginator:
    def __init__(self, client, operation):
        self._client = client
        self._operation = operation

    def paginate(self, **kwargs):
        token = kwargs.pop("ContinuationToken", None)
        while True:
            page = self._client.list_objects_v2(
                ContinuationToken=token, **kwargs
            )
            yield page
            if not page.get("IsTruncated"):
                break
            token = page["NextContinuationToken"]


class FakeS3:
    def __init__(self, page_size=1000, read_limit=3):
        self.buckets = {}
        self.versioning = {}
        self.lifecycle = {}
        self.page_size = page_size
        self.read_limit = read_limit
        self.multipart = {}
        self.incomplete = {}
        self.calls = []
        self.put_order = []
        self.deleted = []
        self.aborted = []
        self.completed_parts = {}
        self.delete_errors = {}
        self.deny = set()
        self.deny_codes = {}
        self.bodies = []
        self.before_get = None
        self.before_list = None
        self.list_counts = {}
        self.clock = 0

    def create_bucket(self, name):
        self.buckets.setdefault(name, {})

    def put(self, bucket, key, data, content_type="application/octet-stream", metadata=None):
        self.clock += 1
        self.buckets.setdefault(bucket, {})[key] = {
            "body": data,
            "etag": f'"{hashlib.md5(data).hexdigest()}"',
            "last_modified": datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)
            + timedelta(seconds=self.clock),
            "content_type": content_type,
            "metadata": dict(metadata or {}),
            "sequence": self.clock,
        }
        return self.buckets[bucket][key]

    def add_incomplete(self, bucket, key, upload_id, part_sizes):
        self.incomplete.setdefault(bucket, {})[(key, upload_id)] = {
            "Key": key,
            "UploadId": upload_id,
            "parts": dict(enumerate(part_sizes, start=1)),
        }

    def _record(self, operation, bucket=None, key=None):
        self.calls.append((operation, bucket, key))
        if operation in self.deny_codes:
            raise FakeClientError(self.deny_codes[operation])
        if operation in self.deny:
            raise FakeClientError("AccessDenied")

    def get_paginator(self, operation):
        if operation != "list_objects_v2":
            raise AssertionError(f"unexpected paginator {operation}")
        return FakePaginator(self, operation)

    def list_objects_v2(
        self,
        Bucket,
        Prefix="",
        Delimiter=None,
        MaxKeys=1000,
        ContinuationToken=None,
        **kwargs,
    ):
        self._record("list_objects_v2", Bucket)
        self.list_counts[Bucket] = self.list_counts.get(Bucket, 0) + 1
        if self.before_list is not None:
            self.before_list(Bucket, self.list_counts[Bucket])

        entries = []
        common_prefixes = set()
        for key in sorted(self.buckets.get(Bucket, {})):
            if not key.startswith(Prefix):
                continue
            remainder = key[len(Prefix):]
            if Delimiter and Delimiter in remainder:
                common_prefixes.add(Prefix + remainder.split(Delimiter, 1)[0] + Delimiter)
            else:
                entries.append(("key", key))
        for common_prefix in common_prefixes:
            entries.append(("prefix", common_prefix))
        entries.sort(key=lambda item: item[1])

        start = int(ContinuationToken or 0)
        limit = min(MaxKeys or self.page_size, self.page_size)
        page_entries = entries[start:start + limit]
        page = {
            "Contents": [
                {
                    "Key": value,
                    "ETag": self.buckets[Bucket][value]["etag"],
                    "Size": len(self.buckets[Bucket][value]["body"]),
                    "LastModified": self.buckets[Bucket][value]["last_modified"],
                }
                for kind, value in page_entries
                if kind == "key"
            ],
            "CommonPrefixes": [
                {"Prefix": value}
                for kind, value in page_entries
                if kind == "prefix"
            ],
            "IsTruncated": start + limit < len(entries),
        }
        if page["IsTruncated"]:
            page["NextContinuationToken"] = str(start + limit)
        return page

    def get_bucket_versioning(self, Bucket):
        self._record("get_bucket_versioning", Bucket)
        return dict(self.versioning.get(Bucket, {}))

    def get_bucket_lifecycle_configuration(self, Bucket):
        self._record("get_bucket_lifecycle_configuration", Bucket)
        rules = self.lifecycle.get(Bucket)
        if rules is None:
            raise FakeClientError("NoSuchLifecycleConfiguration")
        return {"Rules": list(rules)}

    def get_object(self, Bucket, Key, IfMatch=None, **kwargs):
        self._record("get_object", Bucket, Key)
        if self.before_get is not None:
            self.before_get(Bucket, Key)
        item = self.buckets.get(Bucket, {}).get(Key)
        if item is None:
            raise FakeClientError("NoSuchKey")
        if IfMatch is not None and item["etag"] != IfMatch:
            raise FakeClientError("PreconditionFailed")
        body = FakeBody(item["body"], self.read_limit)
        self.bodies.append(body)
        return {
            "Body": body,
            "ContentLength": len(item["body"]),
            "ContentType": item["content_type"],
            "Metadata": dict(item["metadata"]),
            "ETag": item["etag"],
            "LastModified": item["last_modified"],
        }

    def head_bucket(self, Bucket):
        self._record("head_bucket", Bucket)
        if Bucket not in self.buckets:
            raise FakeClientError("NoSuchBucket")

    def put_object(
        self,
        Bucket,
        Key,
        Body,
        ContentType="application/octet-stream",
        Metadata=None,
        **kwargs,
    ):
        self._record("put_object", Bucket, Key)
        data = Body if isinstance(Body, (bytes, bytearray)) else Body.read()
        item = self.put(Bucket, Key, bytes(data), ContentType, Metadata)
        self.put_order.append(Key)
        return {"ETag": item["etag"]}

    def create_multipart_upload(
        self,
        Bucket,
        Key,
        ContentType="application/octet-stream",
        Metadata=None,
        **kwargs,
    ):
        self._record("create_multipart_upload", Bucket, Key)
        upload_id = f"upload-{len(self.multipart) + 1}"
        self.multipart[(Bucket, Key, upload_id)] = {
            "ContentType": ContentType,
            "Metadata": dict(Metadata or {}),
            "parts": {},
        }
        return {"UploadId": upload_id}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body, **kwargs):
        self._record("upload_part", Bucket, Key)
        data = Body if isinstance(Body, (bytes, bytearray)) else Body.read()
        self.multipart[(Bucket, Key, UploadId)]["parts"][PartNumber] = bytes(data)
        return {"ETag": f'"{hashlib.md5(bytes(data)).hexdigest()}"'}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self._record("complete_multipart_upload", Bucket, Key)
        upload = self.multipart.pop((Bucket, Key, UploadId))
        ordered = [upload["parts"][part["PartNumber"]] for part in MultipartUpload["Parts"]]
        self.completed_parts[(Bucket, Key)] = [len(part) for part in ordered]
        item = self.put(
            Bucket, Key, b"".join(ordered), upload["ContentType"], upload["Metadata"]
        )
        return {"ETag": item["etag"]}

    def abort_multipart_upload(self, Bucket, Key, UploadId, **kwargs):
        self._record("abort_multipart_upload", Bucket, Key)
        self.aborted.append((Bucket, Key, UploadId))
        self.multipart.pop((Bucket, Key, UploadId), None)
        return {}

    def delete_objects(self, Bucket, Delete):
        self._record("delete_objects", Bucket)
        deleted = []
        errors = []
        for entry in Delete["Objects"]:
            key = entry["Key"]
            if key in self.delete_errors:
                code = self.delete_errors[key]
                if code in ("NoSuchKey", "NotFound"):
                    self.buckets.get(Bucket, {}).pop(key, None)
                errors.append({"Key": key, "Code": code})
                continue
            self.buckets.get(Bucket, {}).pop(key, None)
            self.deleted.append((Bucket, key))
            deleted.append({"Key": key})
        return {"Deleted": deleted, "Errors": errors}

    def list_multipart_uploads(self, Bucket, KeyMarker=None, UploadIdMarker=None, MaxUploads=1000):
        self._record("list_multipart_uploads", Bucket)
        uploads = sorted(
            self.incomplete.get(Bucket, {}).values(),
            key=lambda item: (item["Key"], item["UploadId"]),
        )
        start = 0
        if KeyMarker is not None:
            for index, item in enumerate(uploads):
                if (item["Key"], item["UploadId"]) >= (KeyMarker, UploadIdMarker or ""):
                    start = index
                    break
            else:
                start = len(uploads)
        page = uploads[start:start + self.page_size]
        response = {
            "Uploads": [
                {"Key": item["Key"], "UploadId": item["UploadId"]}
                for item in page
            ],
            "IsTruncated": start + self.page_size < len(uploads),
        }
        if response["IsTruncated"]:
            response["NextKeyMarker"] = page[-1]["Key"]
            response["NextUploadIdMarker"] = page[-1]["UploadId"]
        return response

    def list_parts(self, Bucket, Key, UploadId, PartNumberMarker=None, MaxParts=1000):
        self._record("list_parts", Bucket, Key)
        upload = self.incomplete[Bucket][(Key, UploadId)]
        parts = sorted(upload["parts"])
        start = 0
        if PartNumberMarker is not None:
            start = next(
                (index for index, number in enumerate(parts)
                 if number > int(PartNumberMarker)),
                len(parts),
            )
        page = parts[start:start + self.page_size]
        response = {
            "Parts": [
                {"PartNumber": number, "Size": upload["parts"][number]}
                for number in page
            ],
            "IsTruncated": start + self.page_size < len(parts),
        }
        if response["IsTruncated"]:
            response["NextPartNumberMarker"] = page[-1]
        return response


class BackupConfigTests(unittest.TestCase):
    def test_backup_destination_cannot_alias_a_source(self):
        module = load_module()
        with self.assertRaises(ValueError):
            module.validate_config(valid_config(destination="invenio-rdm"))

    def test_rejects_shared_backup_store_buckets(self):
        module = load_module()
        for bucket in ("invenio-rdm-backups", "velero-backups"):
            with self.subTest(bucket=bucket):
                with self.assertRaises(ValueError):
                    module.validate_config(valid_config(destination=bucket))
                with self.assertRaises(ValueError):
                    module.validate_config(valid_config(sources=[bucket]))
        with self.assertRaises(ValueError):
            module.validate_config(valid_config(sources=["invenio-rdm-file-backups"]))

    def test_known_production_sources_are_never_destinations(self):
        module = load_module()
        for bucket in ("invenio-rdm", "invenio-rdm-uploads"):
            with self.subTest(bucket=bucket):
                with self.assertRaises(ValueError):
                    module.validate_config(valid_config(
                        sources=["invenio-rdm-uploads"]
                        if bucket == "invenio-rdm" else ["invenio-rdm"],
                        destination=bucket,
                    ))

    def test_known_production_sources_cannot_be_dropped(self):
        module = load_module()
        for sources in (
            ["invenio-rdm"],
            ["invenio-rdm-uploads"],
            ["invenio-rdm", "invenio-rdm-uploads", "another-bucket"],
        ):
            with self.subTest(sources=sources):
                if sources == ["invenio-rdm", "invenio-rdm-uploads", "another-bucket"]:
                    self.assertEqual(
                        module.validate_config(valid_config(sources=sources))["sources"],
                        sources,
                    )
                    continue
                with self.assertRaises(ValueError):
                    module.validate_config(valid_config(sources=sources))

    def test_rejects_a_prefix_outside_the_owned_snapshot_root(self):
        module = load_module()
        for prefix in ("", "snapshots", "/snapshots/", "snapshots//", "../snapshots/"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    module.validate_config(valid_config(prefix=prefix))

    def test_rejects_credentials_in_the_endpoint_and_unknown_settings(self):
        module = load_module()
        with self.assertRaises(ValueError):
            module.validate_config(valid_config(endpoint="http://key:secret@minio:9000"))
        with self.assertRaises(ValueError):
            module.validate_config(valid_config(unexpected="value"))

    def test_accepts_the_authoritative_settings_and_returns_a_copy(self):
        module = load_module()
        config = valid_config()
        validated = module.validate_config(config)
        self.assertEqual(validated, config)
        self.assertIsNot(validated, config)
        self.assertIsNot(validated["sources"], config["sources"])

    def test_repository_config_is_the_validated_source_of_truth(self):
        module = load_module()
        config = json.loads(CONFIG.read_text())
        self.assertEqual(module.validate_config(config), config)
        self.assertEqual(config["sources"], ["invenio-rdm", "invenio-rdm-uploads"])
        self.assertEqual(config["destination"], "invenio-rdm-file-backups")
        self.assertEqual(config["retention_days"], 30)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.client = FakeS3()
        self.config = valid_config()
        for bucket in self.config["sources"] + [self.config["destination"]]:
            self.client.create_bucket(bucket)

    def source(self, key, data, bucket="invenio-rdm", **kwargs):
        return self.client.put(bucket, key, data, **kwargs)

    def snapshot_keys(self, result, name):
        return f"{self.config['prefix']}{result['snapshot_id']}/{name}"

    def test_copies_every_completed_object_and_publishes_marker_last(self):
        self.source("files/seed.bin", b"seed-bytes", content_type="text/plain",
                    metadata={"origin": "seed"})
        self.source("tmp/completed-upload.bin", b"temporary", bucket="invenio-rdm-uploads")

        result = self.module.backup(self.client, self.config, now=NOW)

        self.assertEqual(result["status"], "complete")
        self.assertRegex(result["snapshot_id"], r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
        self.assertEqual(result["objects"], 2)
        self.assertEqual(result["bytes"], len(b"seed-bytes") + len(b"temporary"))
        self.assertNotIn("seed.bin", json.dumps(result))

        manifest_key = self.snapshot_keys(result, "manifest.json")
        marker_key = self.snapshot_keys(result, "COMPLETE.json")
        manifest_bytes = self.client.buckets[self.config["destination"]][manifest_key]["body"]
        marker = json.loads(self.client.buckets[self.config["destination"]][marker_key]["body"])
        manifest = json.loads(manifest_bytes)
        self.assertEqual(marker["manifest_sha256"], hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(marker["snapshot_id"], result["snapshot_id"])
        self.assertEqual(manifest["format"], "invenio-s3-snapshot-v1")
        self.assertEqual(self.client.put_order[-1], marker_key)
        self.assertEqual(manifest["object_count"], 2)
        self.assertEqual(
            {entry["key"] for entry in manifest["objects"]},
            {"files/seed.bin", "tmp/completed-upload.bin"},
        )
        for entry in manifest["objects"]:
            destination = self.client.buckets[self.config["destination"]][entry["backup_key"]]
            source = self.client.buckets[entry["bucket"]][entry["key"]]
            self.assertEqual(destination["body"], source["body"])
            self.assertEqual(entry["sha256"], hashlib.sha256(source["body"]).hexdigest())
        seed = next(entry for entry in manifest["objects"] if entry["key"] == "files/seed.bin")
        self.assertEqual(seed["content_type"], "text/plain")
        self.assertEqual(seed["metadata"], {"origin": "seed"})
        write_operations = {
            "put_object",
            "create_multipart_upload",
            "upload_part",
            "complete_multipart_upload",
            "abort_multipart_upload",
            "delete_objects",
        }
        self.assertFalse(any(
            bucket in self.config["sources"]
            for operation, bucket, key in self.client.calls
            if operation in write_operations
        ))

    def test_inventories_incomplete_uploads_without_aborting_them(self):
        self.client.add_incomplete("invenio-rdm", "files/interrupted.bin", "upload-a", [5, 7])
        self.client.add_incomplete("invenio-rdm-uploads", "tmp/interrupted.bin", "upload-b", [3])

        result = self.module.backup(self.client, self.config, now=NOW)
        manifest = json.loads(
            self.client.buckets[self.config["destination"]][
                self.snapshot_keys(result, "manifest.json")
            ]["body"]
        )

        by_bucket = {entry["bucket"]: entry for entry in manifest["source"]}
        self.assertEqual(by_bucket["invenio-rdm"]["incomplete_uploads"],
                         {"count": 1, "parts": 2, "bytes": 12})
        self.assertEqual(by_bucket["invenio-rdm-uploads"]["incomplete_uploads"],
                         {"count": 1, "parts": 1, "bytes": 3})
        self.assertEqual(manifest["incomplete_uploads"],
                         {"count": 2, "parts": 3, "bytes": 15})
        self.assertEqual(self.client.aborted, [])

    def test_paginates_listings_and_reads_objects_in_bounded_chunks(self):
        self.client.page_size = 1
        self.client.read_limit = 1
        for index in range(3):
            self.source(f"files/{index}.bin", bytes([index]) * (index + 1))

        result = self.module.backup(self.client, self.config, now=NOW)
        manifest = json.loads(
            self.client.buckets[self.config["destination"]][
                self.snapshot_keys(result, "manifest.json")
            ]["body"]
        )
        self.assertEqual(len(manifest["objects"]), 3)
        self.assertGreaterEqual(self.client.list_counts["invenio-rdm"], 3)

    def test_empty_sources_are_explicit_and_acquisition_bounds_are_recorded(self):
        result = self.module.backup(self.client, self.config, now=NOW)
        manifest = json.loads(
            self.client.buckets[self.config["destination"]][
                self.snapshot_keys(result, "manifest.json")
            ]["body"]
        )

        self.assertEqual(result["objects"], 0)
        for entry in manifest["source"]:
            self.assertEqual(entry["objects"], [])
            self.assertEqual(entry["incomplete_uploads"], {"count": 0, "parts": 0, "bytes": 0})
            self.assertIn("list_started_at", entry)
            self.assertIn("list_completed_at", entry)
        self.assertIn("started_at", manifest["acquisition"])
        self.assertIn("completed_at", manifest["acquisition"])
        self.assertFalse(manifest["atomic_snapshot"])

    def test_uses_multipart_for_large_objects_and_hashes_streamed_bytes(self):
        original_chunk = self.module.CHUNK_SIZE
        self.module.CHUNK_SIZE = 5
        try:
            data = b"0123456789ab"
            self.client.read_limit = 1
            self.source("files/large.bin", data)
            result = self.module.backup(self.client, self.config, now=NOW)
        finally:
            self.module.CHUNK_SIZE = original_chunk

        manifest = json.loads(
            self.client.buckets[self.config["destination"]][
                self.snapshot_keys(result, "manifest.json")
            ]["body"]
        )
        entry = manifest["objects"][0]
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(
            self.client.completed_parts[(self.config["destination"], entry["backup_key"])],
            [5, 5, 2],
        )

    def test_records_source_settings(self):
        self.client.lifecycle["invenio-rdm"] = [{"ID": "expire", "Status": "Enabled"}]
        result = self.module.backup(self.client, self.config, now=NOW)
        manifest = json.loads(
            self.client.buckets[self.config["destination"]][
                self.snapshot_keys(result, "manifest.json")
            ]["body"]
        )
        entry = next(item for item in manifest["source"] if item["bucket"] == "invenio-rdm")
        self.assertEqual(entry["settings"]["versioning"], "unconfigured")
        self.assertEqual(entry["settings"]["lifecycle_rules"], 1)

    def test_rejects_configured_source_versioning(self):
        self.client.versioning["invenio-rdm"] = {"Status": "Enabled"}
        with self.assertRaisesRegex(self.module.BackupError, "versioning"):
            self.module.backup(self.client, self.config, now=NOW)
        self.assertFalse(
            any(key.endswith("/COMPLETE.json")
                for key in self.client.buckets[self.config["destination"]])
        )

    def test_source_change_before_copy_fails_without_a_marker(self):
        self.source("files/seed.bin", b"before")

        def mutate(bucket, key):
            if bucket == "invenio-rdm" and key == "files/seed.bin":
                self.client.put(bucket, key, b"after")

        self.client.before_get = mutate
        with self.assertRaises(self.module.BackupError) as raised:
            self.module.backup(self.client, self.config, now=NOW)
        self.assertNotIn("files/seed.bin", str(raised.exception))
        self.assertFalse(
            any(key.endswith("/COMPLETE.json")
                for key in self.client.buckets[self.config["destination"]])
        )

    def test_source_change_during_capture_fails_without_a_marker(self):
        self.source("files/seed.bin", b"before")

        def mutate(bucket, count):
            if bucket == "invenio-rdm" and count == 2:
                self.client.put(bucket, "files/late.bin", b"late")

        self.client.before_list = mutate
        with self.assertRaisesRegex(self.module.BackupError, "changed during capture"):
            self.module.backup(self.client, self.config, now=NOW)
        self.assertFalse(
            any(key.endswith("/COMPLETE.json")
                for key in self.client.buckets[self.config["destination"]])
        )

    def test_readback_corruption_fails_without_a_marker(self):
        self.source("files/seed.bin", b"seed")

        def corrupt(bucket, key):
            if bucket == self.config["destination"] and key.endswith("objects/invenio-rdm/files/seed.bin"):
                self.client.buckets[bucket][key]["body"] = b"corrupt"

        self.client.before_get = corrupt
        with self.assertRaisesRegex(self.module.BackupError, "verify"):
            self.module.backup(self.client, self.config, now=NOW)
        self.assertFalse(
            any(key.endswith("/COMPLETE.json")
                for key in self.client.buckets[self.config["destination"]])
        )

    def test_denied_source_listing_is_reported_without_keys(self):
        self.client.deny.add("get_bucket_versioning")
        with self.assertRaises(self.module.BackupError) as raised:
            self.module.backup(self.client, self.config, now=NOW)
        self.assertIn("AccessDenied", str(raised.exception))

    def test_opaque_sdk_error_code_is_sanitized(self):
        self.client.deny_codes["get_bucket_versioning"] = (
            "SYNTHETIC_PRIVATE_PREFIX/child"
        )
        with self.assertRaises(self.module.BackupError) as raised:
            self.module.backup(self.client, self.config, now=NOW)
        self.assertNotIn("SYNTHETIC_PRIVATE_PREFIX", str(raised.exception))
        self.assertIn("ProviderError", str(raised.exception))

    def test_abort_cleanup_failure_is_visible_without_internal_ids(self):
        self.source("files/seed.bin", b"seed")
        self.client.deny.update({"upload_part", "abort_multipart_upload"})
        with self.assertRaises(self.module.BackupError) as raised:
            self.module.backup(self.client, self.config, now=NOW)
        message = str(raised.exception)
        self.assertIn("AccessDenied", message)
        self.assertIn("cleanup", message)
        self.assertNotIn("files/seed.bin", message)
        self.assertNotIn("upload-", message)

    def test_source_body_closes_when_size_validation_fails(self):
        self.source("files/seed.bin", b"12345678")

        def change_size(bucket, key):
            if bucket == "invenio-rdm" and key == "files/seed.bin":
                self.client.buckets[bucket][key]["body"] = b"1234567890"

        self.client.before_get = change_size
        with self.assertRaisesRegex(self.module.BackupError, "source size changed"):
            self.module.backup(self.client, self.config, now=NOW)
        self.assertTrue(self.client.bodies[-1].closed)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.client = FakeS3()
        self.config = valid_config()
        for bucket in self.config["sources"] + [self.config["destination"]]:
            self.client.create_bucket(bucket)
        self.client.put(
            "invenio-rdm", "files/seed.bin", b"seed-bytes",
            content_type="text/plain", metadata={"origin": "seed"},
        )
        self.result = self.module.backup(self.client, self.config, now=NOW)
        self.snapshot_id = self.result["snapshot_id"]
        self.drill_bucket = "invenio-rdm-drill-test"
        self.drill_prefix = f"restore/{self.snapshot_id}/"
        self.client.create_bucket(self.drill_bucket)

    def manifest_key(self, snapshot_id=None):
        return (
            f"{self.config['prefix']}{snapshot_id or self.snapshot_id}/manifest.json"
        )

    def marker_key(self, snapshot_id=None):
        return (
            f"{self.config['prefix']}{snapshot_id or self.snapshot_id}/COMPLETE.json"
        )

    def rewrite_manifest(self, mutate, snapshot_id=None):
        bucket = self.config["destination"]
        key = self.manifest_key(snapshot_id)
        manifest = json.loads(self.client.buckets[bucket][key]["body"])
        mutate(manifest)
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        self.client.put(bucket, key, data, "application/json")
        marker_key = self.marker_key(snapshot_id)
        marker = json.loads(self.client.buckets[bucket][marker_key]["body"])
        marker["manifest_sha256"] = hashlib.sha256(data).hexdigest()
        self.client.put(
            bucket, marker_key,
            json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        )

    def test_restore_writes_only_to_the_isolated_drill_scope(self):
        source_before = dict(self.client.buckets["invenio-rdm"])
        backup_before = dict(self.client.buckets[self.config["destination"]])
        self.client.put_order.clear()
        self.client.calls.clear()

        result = self.module.restore(
            self.client,
            self.config,
            self.snapshot_id,
            self.drill_bucket,
            self.drill_prefix,
            confirmed=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["objects"], 1)
        self.assertEqual(result["bytes"], len(b"seed-bytes"))
        restored_key = f"{self.drill_prefix}invenio-rdm/files/seed.bin"
        self.assertEqual(
            self.client.buckets[self.drill_bucket][restored_key]["body"], b"seed-bytes"
        )
        self.assertEqual(self.client.buckets["invenio-rdm"], source_before)
        self.assertEqual(self.client.buckets[self.config["destination"]], backup_before)
        write_buckets = {
            bucket for operation, bucket, key in self.client.calls
            if operation in {
                "put_object", "create_multipart_upload", "upload_part",
                "complete_multipart_upload", "delete_objects",
            }
        }
        self.assertEqual(write_buckets, {self.drill_bucket})

    def test_restore_requires_confirmation(self):
        with self.assertRaises(ValueError):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix,
            )

    def test_restore_rejects_targets_outside_the_drill_scope(self):
        for bucket in (
            "invenio-rdm", "invenio-rdm-uploads", self.config["destination"],
            "invenio-rdm-backups", "velero-backups", "some-other-bucket",
        ):
            with self.subTest(bucket=bucket):
                with self.assertRaises(ValueError):
                    self.module.restore(
                        self.client, self.config, self.snapshot_id,
                        bucket, self.drill_prefix, confirmed=True,
                    )
        for prefix in ("", "snapshots/", "restore", "restore/", "restore/../x/", "restore//x/"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    self.module.restore(
                        self.client, self.config, self.snapshot_id,
                        self.drill_bucket, prefix, confirmed=True,
                    )

    def test_restore_rejects_unowned_snapshot_identities(self):
        for snapshot_id in ("../snapshots", "20261029T120000Z-deadbeef", "x"):
            with self.subTest(snapshot_id=snapshot_id):
                with self.assertRaises(ValueError):
                    self.module.restore(
                        self.client, self.config, snapshot_id,
                        self.drill_bucket, self.drill_prefix, confirmed=True,
                    )

    def test_restore_rejects_missing_or_tampered_completion_evidence(self):
        bucket = self.config["destination"]
        del self.client.buckets[bucket][self.marker_key()]
        with self.assertRaises(self.module.BackupError):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_manifest_changed_after_the_marker(self):
        bucket = self.config["destination"]
        key = self.manifest_key()
        self.client.buckets[bucket][key]["body"] += b" "
        with self.assertRaisesRegex(self.module.BackupError, "manifest"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_tampered_marker(self):
        bucket = self.config["destination"]
        key = self.marker_key()
        marker = json.loads(self.client.buckets[bucket][key]["body"])
        marker["manifest_sha256"] = "0" * 64
        self.client.put(
            bucket, key,
            json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        )
        with self.assertRaisesRegex(self.module.BackupError, "snapshot"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_malformed_marker(self):
        bucket = self.config["destination"]
        self.client.put(
            bucket, self.marker_key(), b"{", "application/json"
        )
        with self.assertRaisesRegex(self.module.BackupError, "snapshot"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_malformed_manifest(self):
        bucket = self.config["destination"]
        self.client.put(
            bucket, self.manifest_key(), b"{", "application/json"
        )
        marker = json.loads(self.client.buckets[bucket][self.marker_key()]["body"])
        marker["manifest_sha256"] = hashlib.sha256(b"{").hexdigest()
        self.client.put(
            bucket, self.marker_key(),
            json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        )
        with self.assertRaisesRegex(self.module.BackupError, "manifest"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_corrupt_backup_bytes(self):
        bucket = self.config["destination"]
        manifest = json.loads(self.client.buckets[bucket][self.manifest_key()]["body"])
        self.client.buckets[bucket][manifest["objects"][0]["backup_key"]]["body"] = b"corrupt!!!"
        with self.assertRaisesRegex(self.module.BackupError, "verify"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_nonempty_drill_prefix(self):
        existing = f"{self.drill_prefix}existing"
        self.client.put(self.drill_bucket, existing, b"keep")
        with self.assertRaisesRegex(self.module.BackupError, "prefix"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )
        self.assertEqual(self.client.buckets[self.drill_bucket][existing]["body"], b"keep")

    def test_restore_does_not_create_a_missing_drill_bucket(self):
        with self.assertRaisesRegex(self.module.BackupError, "NoSuchBucket"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                "invenio-rdm-drill-missing", self.drill_prefix, confirmed=True,
            )
        self.assertNotIn("invenio-rdm-drill-missing", self.client.buckets)

    def test_restore_rejects_a_manifest_backup_key_outside_the_snapshot(self):
        def mutate(manifest):
            manifest["objects"][0]["backup_key"] = "another-bucket/key"
        self.rewrite_manifest(mutate)
        with self.assertRaisesRegex(self.module.BackupError, "manifest"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_a_manifest_backup_key_mismatch(self):
        def mutate(manifest):
            manifest["objects"][0]["backup_key"] = (
                manifest["source"][0]["objects"][0]["backup_key"] + ".other"
            )
        self.rewrite_manifest(mutate)
        with self.assertRaisesRegex(self.module.BackupError, "manifest"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )

    def test_restore_rejects_destination_readback_corruption(self):
        def corrupt(bucket, key):
            if bucket == self.drill_bucket and key.endswith("files/seed.bin"):
                self.client.buckets[bucket][key]["body"] = b"corrupt"

        self.client.before_get = corrupt
        with self.assertRaisesRegex(self.module.BackupError, "verify"):
            self.module.restore(
                self.client, self.config, self.snapshot_id,
                self.drill_bucket, self.drill_prefix, confirmed=True,
            )


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.client = FakeS3()
        self.config = valid_config()
        for bucket in self.config["sources"] + [self.config["destination"]]:
            self.client.create_bucket(bucket)

    def point(self, now):
        return self.module.backup(self.client, self.config, now=now)

    def snapshot_prefix(self, result):
        return f"{self.config['prefix']}{result['snapshot_id']}/"

    def manifest_key(self, result):
        return f"{self.snapshot_prefix(result)}manifest.json"

    def marker_key(self, result):
        return f"{self.snapshot_prefix(result)}COMPLETE.json"

    def rewrite_marker(self, result, **changes):
        bucket = self.config["destination"]
        key = self.marker_key(result)
        marker = json.loads(self.client.buckets[bucket][key]["body"])
        marker.update(changes)
        self.client.put(
            bucket, key,
            json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        )

    def test_prune_requires_confirmation(self):
        self.point(NOW - timedelta(days=40))
        with self.assertRaises(ValueError):
            self.module.prune(self.client, self.config, now=NOW)
        self.assertEqual(self.client.deleted, [])

    def test_prune_preserves_latest_and_removes_only_expired_verified_points(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        recent = self.point(NOW - timedelta(days=10))
        latest = self.point(NOW)
        bucket = self.config["destination"]
        old_manifest = json.loads(
            self.client.buckets[bucket][self.manifest_key(old)]["body"]
        )
        expected_old_keys = {
            self.marker_key(old),
            self.manifest_key(old),
            *[entry["backup_key"] for entry in old_manifest["objects"]],
        }

        self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        old_prefix = self.snapshot_prefix(old)
        self.assertFalse(any(key.startswith(old_prefix) for key in self.client.buckets[bucket]))
        self.assertIn(self.snapshot_prefix(recent) + "COMPLETE.json",
                      self.client.buckets[bucket])
        self.assertIn(self.snapshot_prefix(latest) + "COMPLETE.json",
                      self.client.buckets[bucket])
        self.assertEqual(
            {key for bucket_name, key in self.client.deleted}, expected_old_keys
        )
        self.assertTrue(all(bucket_name == bucket
                            for bucket_name, key in self.client.deleted))

    def test_prune_refuses_to_delete_when_latest_content_is_corrupt(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        latest = self.point(NOW)
        bucket = self.config["destination"]
        manifest = json.loads(
            self.client.buckets[bucket][self.manifest_key(latest)]["body"]
        )
        backup_key = manifest["objects"][0]["backup_key"]
        self.client.buckets[bucket][backup_key]["body"] = b"WXYZ"

        with self.assertRaisesRegex(self.module.BackupError, "verify"):
            self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertIn(self.marker_key(old), self.client.buckets[bucket])
        self.assertEqual(self.client.deleted, [])

    def test_prune_refuses_to_delete_on_denied_backup_read(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        self.client.deny.add("get_object")

        with self.assertRaisesRegex(self.module.BackupError, "AccessDenied"):
            self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertIn(self.marker_key(old), self.client.buckets[self.config["destination"]])
        self.assertEqual(self.client.deleted, [])

    def test_prune_preserves_unmanifested_extras_and_blocks_deletion(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        extra = self.snapshot_prefix(old) + "unmanifested-extra"
        self.client.put(bucket, extra, b"keep")

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertEqual(result["status"], "attention")
        self.assertIn(self.marker_key(old), self.client.buckets[bucket])
        self.assertIn(extra, self.client.buckets[bucket])
        self.assertEqual(self.client.deleted, [])

    def test_prune_sanitizes_unrecognized_prefixes(self):
        self.point(NOW)
        bucket = self.config["destination"]
        private_prefix = f"{self.config['prefix']}SYNTHETIC_PRIVATE_PREFIX/child"
        self.client.put(bucket, private_prefix, b"private")

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        rendered = json.dumps(result)
        self.assertNotIn("SYNTHETIC_PRIVATE_PREFIX", rendered)
        self.assertTrue(any(
            item.get("reason") == "unrecognized prefixes" and item.get("count") == 1
            for item in result["reported"]
        ))
        self.assertIn(private_prefix, self.client.buckets[bucket])

    def test_prune_incomplete_point_blocks_deletion(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=50))
        partial = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        del self.client.buckets[bucket][self.marker_key(partial)]

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertEqual(result["status"], "attention")
        self.assertIn(self.marker_key(old), self.client.buckets[bucket])
        self.assertEqual(self.client.deleted, [])

    def test_prune_preserves_the_latest_even_when_it_is_expired(self):
        only = self.point(NOW - timedelta(days=40))
        self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertIn(
            self.snapshot_prefix(only) + "COMPLETE.json",
            self.client.buckets[self.config["destination"]],
        )
        self.assertEqual(self.client.deleted, [])

    def test_prune_preserves_same_timestamp_peers_without_a_strictly_newer_success(self):
        first = self.point(NOW - timedelta(days=40))
        second = self.point(NOW - timedelta(days=40))

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        bucket = self.config["destination"]
        self.assertEqual(result["status"], "ok")
        for point in (first, second):
            self.assertIn(self.snapshot_prefix(point) + "COMPLETE.json", self.client.buckets[bucket])
        self.assertEqual(self.client.deleted, [])

    def test_prune_keeps_a_point_exactly_at_the_retention_boundary(self):
        boundary = self.point(NOW - timedelta(days=30))
        latest = self.point(NOW)
        self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertIn(
            self.snapshot_prefix(boundary) + "COMPLETE.json",
            self.client.buckets[self.config["destination"]],
        )
        self.assertIn(
            self.snapshot_prefix(latest) + "COMPLETE.json",
            self.client.buckets[self.config["destination"]],
        )

    def test_prune_reports_partial_and_unrecognized_points_without_deleting(self):
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        del self.client.buckets[bucket][self.marker_key(old)]
        partial_prefix = f"{self.config['prefix']}20261029T120000Z-abcdefabcdef/"
        self.client.put(bucket, partial_prefix + "objects/invenio-rdm/x", b"partial")
        stray = "not-a-snapshot/object"
        self.client.put(bucket, stray, b"stray")

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertEqual(result["status"], "attention")
        self.assertIn(partial_prefix + "objects/invenio-rdm/x", self.client.buckets[bucket])
        self.assertIn(stray, self.client.buckets[bucket])
        self.assertTrue(any(item["snapshot_id"] == old["snapshot_id"]
                            for item in result["reported"]))

    def test_prune_reports_future_and_malformed_capture_times(self):
        future = self.point(NOW - timedelta(days=40))
        manifest_key = self.manifest_key(future)
        bucket = self.config["destination"]
        manifest = json.loads(self.client.buckets[bucket][manifest_key]["body"])
        manifest["created_at"] = "2026-12-31T00:00:00Z"
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        self.client.put(bucket, manifest_key, data, "application/json")
        self.rewrite_marker(future, manifest_sha256=hashlib.sha256(data).hexdigest())
        self.point(NOW)

        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)

        self.assertTrue(any(item["snapshot_id"] == future["snapshot_id"]
                            for item in result["reported"]))
        self.assertIn(self.snapshot_prefix(future) + "COMPLETE.json",
                      self.client.buckets[bucket])

        manifest["created_at"] = "not-a-time"
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        self.client.put(bucket, manifest_key, data, "application/json")
        self.rewrite_marker(future, manifest_sha256=hashlib.sha256(data).hexdigest())
        result = self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertTrue(any(item["snapshot_id"] == future["snapshot_id"]
                            for item in result["reported"]))

    def test_prune_uses_manifest_capture_time_not_marker_time(self):
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        self.rewrite_marker(old, completed_at="2026-10-29T12:00:00Z")
        self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertFalse(any(
            key.startswith(self.snapshot_prefix(old))
            for key in self.client.buckets[self.config["destination"]]
        ))

    def test_prune_failure_is_not_reported_as_success(self):
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        self.client.delete_errors[self.manifest_key(old)] = "AccessDenied"
        with self.assertRaisesRegex(self.module.BackupError, "AccessDenied"):
            self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        # The marker was removed first so a failed point cannot remain certified.
        self.assertNotIn(self.marker_key(old), self.client.buckets[bucket])

    def test_prune_partial_object_deletion_is_not_reported_as_success(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        manifest = json.loads(self.client.buckets[bucket][self.manifest_key(old)]["body"])
        backup_key = manifest["objects"][0]["backup_key"]
        self.client.delete_errors[backup_key] = "AccessDenied"
        with self.assertRaisesRegex(self.module.BackupError, "AccessDenied"):
            self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertNotIn(self.marker_key(old), self.client.buckets[bucket])
        self.assertNotIn(self.manifest_key(old), self.client.buckets[bucket])
        self.assertIn(backup_key, self.client.buckets[bucket])

    def test_prune_calls_only_the_destination_bucket(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        source_before = dict(self.client.buckets["invenio-rdm"])
        self.client.calls.clear()
        self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertEqual(
            {bucket for operation, bucket, key in self.client.calls if bucket},
            {self.config["destination"]},
        )
        self.assertEqual(self.client.buckets["invenio-rdm"], source_before)

    def test_prune_is_idempotent_for_missing_objects(self):
        self.client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = self.point(NOW - timedelta(days=40))
        self.point(NOW)
        bucket = self.config["destination"]
        manifest = json.loads(
            self.client.buckets[bucket][self.manifest_key(old)]["body"]
        )
        backup_key = manifest["objects"][0]["backup_key"]
        self.client.delete_errors[backup_key] = "NoSuchKey"
        self.module.prune(self.client, self.config, now=NOW, confirmed=True)
        self.assertFalse(any(
            key.startswith(self.snapshot_prefix(old)) for key in self.client.buckets[bucket]
        ))


class CommandLineTests(unittest.TestCase):
    def test_build_client_requires_explicit_credentials(self):
        module = load_module()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(module.BackupError, "credentials"):
                module.build_client(valid_config())

    def test_main_prints_aggregate_status_without_object_keys(self):
        module = load_module()
        client = FakeS3()
        config = valid_config()
        for bucket in config["sources"] + [config["destination"]]:
            client.create_bucket(bucket)
        client.put("invenio-rdm", "files/seed.bin", b"seed-bytes")
        module.build_client = lambda ignored: client
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = module.main(["--config", str(config_path), "backup"])
        self.assertEqual(result, 0)
        self.assertNotIn("files/seed.bin", output.getvalue())

    def test_main_failure_does_not_echo_object_keys(self):
        module = load_module()
        client = FakeS3()
        config = valid_config()
        for bucket in config["sources"] + [config["destination"]]:
            client.create_bucket(bucket)
        client.put("invenio-rdm", "files/secret-name.bin", b"secret-bytes")
        client.deny.add("get_bucket_versioning")
        module.build_client = lambda ignored: client
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config))
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                result = module.main(["--config", str(config_path), "backup"])
        self.assertEqual(result, 1)
        self.assertIn("AccessDenied", error.getvalue())
        self.assertNotIn("secret-name.bin", error.getvalue())
        self.assertNotIn("secret-bytes", error.getvalue())

    def run_prune_main(self, module, client, config):
        module.build_client = lambda ignored: client
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config))
            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                result = module.main(
                    ["--config", str(config_path), "prune", "--confirm"]
                )
        return result, output.getvalue(), error.getvalue()

    def test_main_prune_returns_nonzero_for_attention(self):
        module = load_module()
        client = FakeS3()
        config = valid_config()
        for bucket in config["sources"] + [config["destination"]]:
            client.create_bucket(bucket)
        client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = module.backup(client, config, now=NOW - timedelta(days=50))
        partial = module.backup(client, config, now=NOW - timedelta(days=40))
        module.backup(client, config, now=NOW)
        bucket = config["destination"]
        del client.buckets[bucket][
            f"{config['prefix']}{partial['snapshot_id']}/COMPLETE.json"
        ]
        old_marker = f"{config['prefix']}{old['snapshot_id']}/COMPLETE.json"

        result, output, error = self.run_prune_main(module, client, config)

        self.assertEqual(result, 2, error)
        self.assertIn("attention", output)
        self.assertIn(old_marker, client.buckets[bucket])
        self.assertEqual(client.deleted, [])

    def test_main_prune_permission_failure_returns_nonzero_without_deletion(self):
        module = load_module()
        client = FakeS3()
        config = valid_config()
        for bucket in config["sources"] + [config["destination"]]:
            client.create_bucket(bucket)
        client.put("invenio-rdm", "files/seed.bin", b"seed")
        old = module.backup(client, config, now=NOW - timedelta(days=40))
        module.backup(client, config, now=NOW)
        bucket = config["destination"]
        old_marker = f"{config['prefix']}{old['snapshot_id']}/COMPLETE.json"
        client.deny.add("get_object")

        result, output, error = self.run_prune_main(module, client, config)

        self.assertEqual(result, 1)
        self.assertIn("AccessDenied", error)
        self.assertIn(old_marker, client.buckets[bucket])
        self.assertEqual(client.deleted, [])


def render(path):
    try:
        result = subprocess.run(
            ["kustomize", "build", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise unittest.SkipTest("kustomize is not installed")
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def find_document(documents, kind, name):
    return next(
        document for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    )


class PermissionsDocTests(unittest.TestCase):
    def policy(self):
        text = PERMISSIONS.read_text()
        block = text.split("```json", 1)[1].split("```", 1)[0]
        return json.loads(block), text

    def test_backup_writer_is_scoped_to_the_owned_snapshot_prefix(self):
        policy, _ = self.policy()
        bucket = next(
            item for item in policy["Statement"]
            if item["Resource"] == ["arn:aws:s3:::invenio-rdm-file-backups"]
        )
        self.assertTrue({"s3:ListBucket", "s3:GetBucketLocation"} <= set(bucket["Action"]))
        self.assertFalse({"s3:GetObject", "s3:PutObject"} & set(bucket["Action"]))

        objects = next(
            item for item in policy["Statement"]
            if item["Resource"] == ["arn:aws:s3:::invenio-rdm-file-backups/snapshots/*"]
        )
        self.assertTrue({
            "s3:GetObject",
            "s3:PutObject",
            "s3:AbortMultipartUpload",
        } <= set(objects["Action"]))

    def test_restore_drill_writer_is_scoped_to_the_restore_prefix(self):
        _, text = self.policy()
        section = text.split("## Restore reader", 1)[1].split(
            "## Prune identity", 1
        )[0]
        self.assertGreaterEqual(section.count("s3:GetObject"), 2)
        self.assertIn("s3:PutObject", section)
        self.assertIn("restore/*", section)
        self.assertIn("snapshots/*", section)


class ManifestWiringTests(unittest.TestCase):
    def test_minio_generator_owns_the_only_script_and_config_copy(self):
        kustomization = yaml.safe_load((MINIO / "kustomization.yaml").read_text())
        generators = [
            item for item in kustomization.get("configMapGenerator", [])
            if item["name"] == "s3-file-backup"
        ]
        self.assertEqual(len(generators), 1)
        self.assertEqual(
            set(generators[0]["files"]),
            {
                "resources/s3-file-backup.py",
                "resources/s3-file-backup-config.json",
            },
        )
        rendered = render(MINIO)
        configmaps = [
            document for document in rendered
            if document.get("kind") == "ConfigMap"
            and document["metadata"]["name"].startswith("s3-file-backup-")
        ]
        self.assertEqual(len(configmaps), 1)
        self.assertEqual(configmaps[0]["metadata"]["namespace"], "minio")
        data = configmaps[0]["data"]
        self.assertEqual(data["s3-file-backup.py"], SOURCE.read_text())
        self.assertEqual(data["s3-file-backup-config.json"], CONFIG.read_text())

    def test_rendered_cronjob_uses_the_generated_configmap_without_mirrors(self):
        app = yaml.safe_load(
            (ROOT / "argocd/apps/minio-extras.yaml").read_text()
        )
        self.assertEqual(app["spec"]["source"]["path"], "k8s/infra/minio")
        rendered = render(MINIO)
        configmap = next(
            document for document in rendered
            if document.get("kind") == "ConfigMap"
            and document["metadata"]["name"].startswith("s3-file-backup-")
        )
        cronjob = find_document(rendered, "CronJob", "invenio-rdm-file-backup")
        self.assertEqual(cronjob["spec"]["schedule"], "30 2 * * *")
        self.assertEqual(cronjob["spec"]["timeZone"], "Etc/UTC")
        self.assertFalse(cronjob["spec"]["suspend"])
        self.assertEqual(cronjob["spec"]["concurrencyPolicy"], "Forbid")
        self.assertEqual(cronjob["spec"]["startingDeadlineSeconds"], 300)
        self.assertEqual(cronjob["spec"]["successfulJobsHistoryLimit"], 3)
        self.assertEqual(cronjob["spec"]["failedJobsHistoryLimit"], 3)

        job = cronjob["spec"]["jobTemplate"]["spec"]
        self.assertEqual(job["activeDeadlineSeconds"], 900)
        self.assertEqual(job["backoffLimit"], 1)
        pod = job["template"]["spec"]
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertFalse(pod.get("automountServiceAccountToken", True))
        self.assertFalse(any("persistentVolumeClaim" in volume for volume in pod["volumes"]))
        config_volume = next(volume for volume in pod["volumes"] if volume["name"] == "backup")
        self.assertEqual(config_volume["configMap"]["name"], configmap["metadata"]["name"])

        container = pod["containers"][0]
        self.assertEqual(
            container["command"],
            [
                "python3",
                "/config/s3-file-backup.py",
                "--config",
                "/config/config.json",
                "backup",
            ],
        )
        self.assertEqual(
            container["resources"],
            {
                "requests": {"cpu": "100m", "memory": "64Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi"},
            },
        )
        environment = {item["name"]: item for item in container["env"]}
        self.assertEqual(
            environment["AWS_EC2_METADATA_DISABLED"]["value"], "true"
        )
        for variable, key in (
            ("AWS_ACCESS_KEY_ID", "accessKeyId"),
            ("AWS_SECRET_ACCESS_KEY", "secretAccessKey"),
        ):
            reference = environment[variable]["valueFrom"]["secretKeyRef"]
            self.assertEqual(reference["name"], "s3-file-backup-credentials")
            self.assertEqual(reference["key"], key)
        self.assertNotIn("envFrom", container)
        self.assertNotIn("minio-credentials", json.dumps(cronjob))
        self.assertNotIn("invenio-app-secrets", json.dumps(cronjob))
        self.assertNotIn("invenio-root", json.dumps(cronjob))

        self.assertEqual(container["image"], PINNED_RUNTIME)
        self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        cluster = next(
            document for document in yaml.safe_load_all(
                (ROOT / "k8s/dependencies/postgresql/resources/cluster.yaml").read_text()
            )
            if document and document.get("kind") == "Cluster"
        )
        self.assertEqual(
            cluster["spec"]["imageName"], "ghcr.io/cloudnative-pg/postgresql:16.4"
        )
        self.assertEqual(container["securityContext"]["runAsNonRoot"], True)
        self.assertEqual(container["securityContext"]["allowPrivilegeEscalation"], False)
        self.assertEqual(
            container["securityContext"]["capabilities"]["drop"], ["ALL"]
        )
        self.assertEqual(container["securityContext"]["readOnlyRootFilesystem"], True)

    def test_runtime_docs_record_the_pinned_digest_and_cache_gate(self):
        text = OPERATIONS.read_text()
        self.assertIn(PINNED_RUNTIME, text)
        self.assertIn("pullable", text)
        self.assertIn("cache", text)

    def test_monitoring_sources_include_the_s3_file_backup_rules(self):
        kustomization = yaml.safe_load((MONITORING / "kustomization.yaml").read_text())
        self.assertIn("resources/s3-file-backup-alerts.yaml", kustomization["resources"])
        rendered = render(MONITORING)
        rule = find_document(rendered, "PrometheusRule", "s3-file-backup-alerts")
        self.assertEqual(rule["metadata"]["namespace"], "monitoring")
        self.assertEqual(rule["metadata"]["labels"], {"release": "monitoring"})

    def test_alerts_are_suspended_aware_and_cover_the_required_failures(self):
        rule = yaml.safe_load(ALERTS.read_text())
        groups = rule["spec"]["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "file-backups")
        alerts = {item["alert"]: item for item in groups[0]["rules"] if "alert" in item}
        self.assertEqual(
            set(alerts),
            {
                "S3FileBackupJobFailed",
                "S3FileBackupNeverSucceeded",
                "S3FileBackupStale",
            },
        )
        for name, alert in alerts.items():
            with self.subTest(alert=name):
                expression = alert["expr"]
                self.assertIn('namespace="minio"', expression)
                self.assertIn('cronjob="invenio-rdm-file-backup"', expression)
                self.assertIn("kube_cronjob_spec_suspend", expression)
                self.assertIn("== 0", expression)
                self.assertIn("kube_job_owner", expression)
                self.assertIn('owner_kind="CronJob"', expression)
                self.assertIn('owner_name="invenio-rdm-file-backup"', expression)
                self.assertIn("for", alert)
                for field in ("summary", "description", "runbook_url", "dashboard"):
                    self.assertTrue(alert["annotations"].get(field))
        failed = alerts["S3FileBackupJobFailed"]["expr"]
        self.assertIn("kube_job_failed", failed)
        self.assertIn('condition="true"', failed)
        self.assertNotIn("kube_job_status_failed", failed)
        self.assertNotIn("kube_job_status_succeeded", failed)
        self.assertNotIn("kube_job_status_active", failed)
        self.assertNotIn("kube_job_status_completion_time", failed)
        self.assertIn('job_name=~"invenio-rdm-file-backup-.*"', failed)
        self.assertIn(
            "kube_job_status_completion_time",
            alerts["S3FileBackupNeverSucceeded"]["expr"],
        )
        self.assertIn("absent(", alerts["S3FileBackupNeverSucceeded"]["expr"])
        self.assertEqual(alerts["S3FileBackupNeverSucceeded"]["for"], "36h")
        self.assertIn("kube_job_status_completion_time", alerts["S3FileBackupStale"]["expr"])
        self.assertIn("36 * 3600", alerts["S3FileBackupStale"]["expr"])
        self.assertEqual(alerts["S3FileBackupStale"]["for"], "1h")

    @unittest.skipUnless(shutil.which("promtool"), "promtool is not installed")
    def test_alert_rules_syntax_and_suspended_scenarios(self):
        rule = yaml.safe_load(ALERTS.read_text())
        groups = rule["spec"]["groups"]
        alerts = {
            item["alert"]: item for item in groups[0]["rules"] if "alert" in item
        }
        job = "invenio-rdm-file-backup-1"
        other_job = "invenio-rdm-file-backup-other"
        suspend = (
            'kube_cronjob_spec_suspend{namespace="minio",'
            'cronjob="invenio-rdm-file-backup"}'
        )

        def job_series(metric, name, values):
            return {
                "series": (
                    f'{metric}{{namespace="minio",job_name="{name}"}}'
                ),
                "values": values,
            }

        def owner_series(name, owner_name):
            return {
                "series": (
                    f'kube_job_owner{{namespace="minio",job_name="{name}",'
                    f'owner_kind="CronJob",owner_name="{owner_name}"}}'
                ),
                "values": "1x50",
            }

        def condition_series(name, values):
            return {
                "series": (
                    f'kube_job_failed{{namespace="minio",job_name="{name}",'
                    f'condition="true"}}'
                ),
                "values": values,
            }

        def running_job(name, owner_name, terminal=True):
            return [
                job_series("kube_job_status_failed", name, "1x50"),
                job_series("kube_job_status_succeeded", name, "0x50"),
                job_series("kube_job_status_active", name, "0x50"),
                condition_series(name, "1x50" if terminal else "0x50"),
                owner_series(name, owner_name),
            ]

        def recent_job(name, owner_name, completion="169200"):
            return [
                job_series("kube_job_status_failed", name, "1x50"),
                job_series("kube_job_status_succeeded", name, "1x50"),
                job_series("kube_job_status_active", name, "0x50"),
                job_series("kube_job_status_completion_time", name, f"{completion}x50"),
                condition_series(name, "0x50"),
                owner_series(name, owner_name),
            ]

        scenarios = [
            {
                "name": "suspended",
                "series": [{"series": suspend, "values": "1x50"}]
                + running_job(job, "invenio-rdm-file-backup"),
                "eval": "48h",
                "expected": [],
            },
            {
                "name": "nonterminal_retry_gap",
                "series": [{"series": suspend, "values": "0x50"}]
                + running_job(job, "invenio-rdm-file-backup", terminal=False),
                "eval": "20m",
                "expected": [],
            },
            {
                "name": "success_after_retry",
                "series": [{"series": suspend, "values": "0x50"}]
                + recent_job(job, "invenio-rdm-file-backup"),
                "eval": "48h",
                "expected": [],
            },
            {
                "name": "success_completion_without_succeeded_gauge",
                "series": [
                    {"series": suspend, "values": "0x50"},
                    job_series("kube_job_status_failed", job, "1x50"),
                    job_series("kube_job_status_active", job, "0x50"),
                    job_series("kube_job_status_completion_time", job, "60x50"),
                    condition_series(job, "0x50"),
                    owner_series(job, "invenio-rdm-file-backup"),
                ],
                "eval": "20m",
                "expected": [],
            },
            {
                "name": "unrelated_terminal_job",
                "series": [{"series": suspend, "values": "0x50"}]
                + running_job(other_job, "another-cronjob"),
                "eval": "12h",
                "expected": [],
            },
            {
                "name": "unrelated_completion",
                "series": [{"series": suspend, "values": "0x50"}]
                + recent_job(other_job, "another-cronjob"),
                "eval": "48h",
                "expected": ["S3FileBackupNeverSucceeded"],
            },
            {
                "name": "terminal_condition",
                "series": [{"series": suspend, "values": "0x50"}]
                + running_job(job, "invenio-rdm-file-backup"),
                "eval": "12h",
                "expected": ["S3FileBackupJobFailed"],
            },
            {
                "name": "history_disappeared",
                "series": [{"series": suspend, "values": "0x50"}],
                "eval": "48h",
                "expected": ["S3FileBackupNeverSucceeded"],
            },
            {
                "name": "stale",
                "series": [
                    {"series": suspend, "values": "0x50"},
                    job_series("kube_job_status_completion_time", job, "0x50"),
                    owner_series(job, "invenio-rdm-file-backup"),
                ],
                "eval": "48h",
                "expected": ["S3FileBackupStale"],
            },
            {
                "name": "recent_success",
                "series": [
                    {"series": suspend, "values": "0x50"},
                    job_series("kube_job_status_completion_time", job, "169200x50"),
                    owner_series(job, "invenio-rdm-file-backup"),
                ],
                "eval": "48h",
                "expected": [],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            rule_path = Path(directory) / "rules.yaml"
            rule_path.write_text(yaml.safe_dump({"groups": groups}, sort_keys=False))
            for scenario in scenarios:
                with self.subTest(scenario=scenario["name"]):
                    test_path = Path(directory) / f"{scenario['name']}.yaml"
                    test_path.write_text(
                        yaml.safe_dump(
                            {
                                "rule_files": [str(rule_path)],
                                "evaluation_interval": "1h",
                                "tests": [
                                    {
                                        "interval": "1h",
                                        "input_series": scenario["series"],
                                        "alert_rule_test": [
                                            {
                                                "eval_time": scenario["eval"],
                                                "alertname": alert_name,
                                                "exp_alerts": (
                                                    [
                                                        {
                                                            "exp_labels": {
                                                                "severity": (
                                                                    "critical"
                                                                    if alert_name
                                                                    in (
                                                                        "S3FileBackupJobFailed",
                                                                        "S3FileBackupNeverSucceeded",
                                                                    )
                                                                    else "warning"
                                                                ),
                                                                **(
                                                                    {
                                                                        "job_name": job,
                                                                        "namespace": "minio",
                                                                    }
                                                                    if alert_name
                                                                    == "S3FileBackupJobFailed"
                                                                    else {}
                                                                ),
                                                            },
                                                            "exp_annotations": alerts[
                                                                alert_name
                                                            ]["annotations"],
                                                        }
                                                    ]
                                                    if alert_name in scenario["expected"]
                                                    else []
                                                ),
                                            }
                                            for alert_name in (
                                                "S3FileBackupJobFailed",
                                                "S3FileBackupNeverSucceeded",
                                                "S3FileBackupStale",
                                            )
                                        ],
                                    }
                                ],
                            },
                            sort_keys=False,
                        )
                    )
                    result = subprocess.run(
                        ["promtool", "test", "rules", str(test_path)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
