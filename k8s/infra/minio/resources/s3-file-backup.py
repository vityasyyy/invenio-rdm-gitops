#!/usr/bin/env python3
"""Verified S3 file backups with isolated restore and protected pruning.

The command uses the SDK available in the runtime image. Importing this module
performs no credential discovery, network access or bucket operations.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


CONFIG_KEYS = {
    "endpoint",
    "region",
    "sources",
    "destination",
    "prefix",
    "retention_days",
}
PROTECTED_BUCKETS = frozenset({"invenio-rdm-backups", "velero-backups"})
KNOWN_SOURCE_BUCKETS = frozenset({"invenio-rdm", "invenio-rdm-uploads"})
SOURCE_BLOCKED_BUCKETS = PROTECTED_BUCKETS | {"invenio-rdm-file-backups"}
SAFE_ERROR_CODES = frozenset({
    "AccessDenied",
    "AccountProblem",
    "AuthorizationHeaderMalformed",
    "BadDigest",
    "BucketAlreadyExists",
    "BucketAlreadyOwnedByYou",
    "BucketNotEmpty",
    "EntityTooLarge",
    "ExpiredToken",
    "IncompleteBody",
    "InternalError",
    "InvalidAccessKeyId",
    "InvalidArgument",
    "InvalidBucketName",
    "InvalidPart",
    "InvalidPartOrder",
    "InvalidRequest",
    "InvalidSecurity",
    "KeyTooLongError",
    "MalformedXML",
    "MethodNotAllowed",
    "MissingContentLength",
    "NoSuchBucket",
    "NoSuchKey",
    "NoSuchLifecycleConfiguration",
    "NoSuchUpload",
    "NoSuchVersion",
    "NotFound",
    "NotImplemented",
    "OperationAborted",
    "PermanentRedirect",
    "PreconditionFailed",
    "RequestTimeTooSkewed",
    "RequestTimeout",
    "ServiceUnavailable",
    "SignatureDoesNotMatch",
    "SlowDown",
    "TemporaryRedirect",
    "Throttling",
    "TokenRefreshRequired",
    "XAmzContentMD5Mismatch",
    "XAmzContentSHA256Mismatch",
})
SNAPSHOT_FORMAT = "invenio-s3-snapshot-v1"
MARKER_FORMAT = "invenio-s3-snapshot-complete-v1"
MANIFEST_NAME = "manifest.json"
MARKER_NAME = "COMPLETE.json"
SNAPSHOT_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
DRILL_BUCKET_RE = re.compile(r"^invenio-rdm-drill-[a-z0-9][a-z0-9-]{0,44}$")
DRILL_PREFIX_ROOT = "restore/"
CHUNK_SIZE = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
UTC = timezone.utc


class BackupError(RuntimeError):
    """An operational failure with a safe, aggregate description."""

    def __init__(self, stage, reason):
        self.stage = stage
        self.reason = reason
        super().__init__(f"{stage}: {reason}")


def _invalid(message):
    raise ValueError(message)


def _validate_bucket(value, field):
    if not isinstance(value, str) or not value:
        _invalid(f"{field} must be a bucket name")
    if value in PROTECTED_BUCKETS:
        _invalid(f"{field} must not be a shared backup bucket")
    if not (3 <= len(value) <= 63):
        _invalid(f"{field} must be a bucket name")
    if not value[0].isalnum() or not value[-1].isalnum():
        _invalid(f"{field} must be a bucket name")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in value):
        _invalid(f"{field} must be a lowercase bucket name")


def _validate_prefix(value):
    if not isinstance(value, str) or not value:
        _invalid("prefix must be a non-empty owned prefix")
    if not value.endswith("/") or value.startswith("/") or "//" in value:
        _invalid("prefix must be a relative prefix ending in /")
    if any(part in ("", ".", "..") for part in value.split("/")[:-1]):
        _invalid("prefix must not traverse")


def validate_config(config):
    """Validate and copy the authoritative non-secret settings."""
    if not isinstance(config, dict):
        _invalid("config must be an object")
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        _invalid("config contains unknown settings")

    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        _invalid("endpoint must be an http(s) URL")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        _invalid("endpoint must be a plain http(s) URL without credentials")

    region = config.get("region")
    if not isinstance(region, str) or not region:
        _invalid("region must be a non-empty string")

    sources = config.get("sources")
    if (
        not isinstance(sources, list)
        or not sources
        or not all(isinstance(source, str) for source in sources)
    ):
        _invalid("sources must be a non-empty list of bucket names")
    if len(set(sources)) != len(sources):
        _invalid("sources must not contain duplicates")
    for source in sources:
        _validate_bucket(source, "sources")
        if source in SOURCE_BLOCKED_BUCKETS:
            _invalid("sources must not include a backup bucket")
    if not KNOWN_SOURCE_BUCKETS <= set(sources):
        _invalid("sources must include every known production source")

    destination = config.get("destination")
    _validate_bucket(destination, "destination")
    if destination in KNOWN_SOURCE_BUCKETS:
        _invalid("destination must not be a known production source")
    if destination in sources:
        _invalid("destination must not alias a source")

    prefix = config.get("prefix")
    _validate_prefix(prefix)

    retention_days = config.get("retention_days")
    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or not 1 <= retention_days <= 3650
    ):
        _invalid("retention_days must be an integer between 1 and 3650")

    return {
        "endpoint": endpoint,
        "region": region,
        "sources": list(sources),
        "destination": destination,
        "prefix": prefix,
        "retention_days": retention_days,
    }


def load_config(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return validate_config(json.load(handle))
    except OSError as exc:
        raise BackupError("config", "cannot read config") from exc
    except json.JSONDecodeError as exc:
        raise BackupError("config", "invalid JSON") from exc


def _utcnow():
    return datetime.now(UTC).replace(microsecond=0)


def _now(value):
    if value is None:
        return _utcnow()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(UTC).replace(microsecond=0)


def _iso(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("timestamp is malformed")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _snapshot_time(snapshot_id):
    try:
        return datetime.strptime(
            snapshot_id[:16], "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=UTC)
    except ValueError:
        raise BackupError("snapshot", "snapshot identity time is malformed") from None


def _new_snapshot_id(now):
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(6)}"


def _safe_error_code(code):
    return code if code in SAFE_ERROR_CODES else "ProviderError"


def _client_error_code(exc):
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    return _safe_error_code(str(error.get("Code") or "Unknown"))


def _call(stage, operation, **kwargs):
    try:
        return operation(**kwargs)
    except Exception as exc:  # noqa: BLE001 - classify SDK failures safely
        code = _client_error_code(exc)
        if code is None:
            raise
        raise BackupError(stage, code) from None


def _pages(stage, paginator, **kwargs):
    try:
        yield from paginator.paginate(**kwargs)
    except Exception as exc:  # noqa: BLE001 - classify SDK failures safely
        code = _client_error_code(exc)
        if code is None:
            raise
        raise BackupError(stage, code) from None


def _list_objects(stage, client, bucket):
    paginator = client.get_paginator("list_objects_v2")
    objects = []
    for page in _pages(stage, paginator, Bucket=bucket):
        for item in page.get("Contents", []):
            if not item.get("Key") or item.get("ETag") is None:
                raise BackupError(stage, "incomplete object listing")
            objects.append(item)
    return sorted(objects, key=lambda item: item["Key"])


def _object_identity(item):
    try:
        size = int(item["Size"])
        last_modified = _iso(item["LastModified"])
    except (KeyError, TypeError, ValueError):
        raise BackupError("inventory", "incomplete object identity") from None
    return (item.get("ETag"), size, last_modified)


def _object_map(objects):
    return {item["Key"]: _object_identity(item) for item in objects}


def _source_settings(client, bucket):
    versioning = _call(
        "settings", client.get_bucket_versioning, Bucket=bucket
    ).get("Status") or "unconfigured"
    try:
        lifecycle = _call(
            "settings", client.get_bucket_lifecycle_configuration, Bucket=bucket
        )
        rules = lifecycle.get("Rules") or []
    except BackupError as exc:
        if exc.reason != "NoSuchLifecycleConfiguration":
            raise
        rules = []
    if not isinstance(rules, list):
        raise BackupError("settings", "invalid lifecycle configuration")
    return {"versioning": versioning, "lifecycle_rules": len(rules)}


def _incomplete_uploads(client, bucket):
    uploads = []
    kwargs = {"Bucket": bucket}
    while True:
        response = _call("inventory", client.list_multipart_uploads, **kwargs)
        uploads.extend(response.get("Uploads") or [])
        if not response.get("IsTruncated"):
            break
        key_marker = response.get("NextKeyMarker")
        upload_marker = response.get("NextUploadIdMarker")
        if key_marker is None or upload_marker is None:
            raise BackupError("inventory", "incomplete upload pagination")
        kwargs.update(KeyMarker=key_marker, UploadIdMarker=upload_marker)

    part_count = 0
    part_bytes = 0
    for upload in uploads:
        if not upload.get("Key") or not upload.get("UploadId"):
            raise BackupError("inventory", "incomplete upload identity")
        part_kwargs = {
            "Bucket": bucket,
            "Key": upload["Key"],
            "UploadId": upload["UploadId"],
        }
        while True:
            response = _call("inventory", client.list_parts, **part_kwargs)
            for part in response.get("Parts") or []:
                try:
                    part_size = int(part.get("Size") or 0)
                except (TypeError, ValueError):
                    raise BackupError("inventory", "invalid part size") from None
                part_count += 1
                part_bytes += part_size
            if not response.get("IsTruncated"):
                break
            marker = response.get("NextPartNumberMarker")
            if marker is None:
                raise BackupError("inventory", "incomplete part pagination")
            part_kwargs["PartNumberMarker"] = marker
    return {"count": len(uploads), "parts": part_count, "bytes": part_bytes}


def _source_inventory(client, bucket):
    started = _utcnow()
    objects = _list_objects("inventory", client, bucket)
    settings = _source_settings(client, bucket)
    incomplete = _incomplete_uploads(client, bucket)
    completed = _utcnow()
    return {
        "bucket": bucket,
        "objects": objects,
        "settings": settings,
        "incomplete_uploads": incomplete,
        "list_started_at": _iso(started),
        "list_completed_at": _iso(completed),
    }


def _assert_stable(initial, rechecked):
    if initial["settings"] != rechecked["settings"]:
        raise BackupError(
            "inventory", f"source {initial['bucket']} settings changed during capture"
        )
    if _object_map(initial["objects"]) != _object_map(rechecked["objects"]):
        raise BackupError(
            "inventory", f"source {initial['bucket']} changed during capture"
        )


def _read_filled(body, amount):
    chunks = []
    remaining = amount
    while remaining > 0:
        chunk = body.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_limited(body, limit):
    chunks = []
    total = 0
    while True:
        chunk = _read_filled(body, min(CHUNK_SIZE, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise BackupError("read", "object exceeds the allowed manifest size")
    return b"".join(chunks)


def _hash_stream(body):
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = _read_filled(body, CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    return total, digest.hexdigest()


def _read_bytes(client, bucket, key, limit):
    response = _call("read", client.get_object, Bucket=bucket, Key=key)
    body = response.get("Body")
    if body is None:
        raise BackupError("read", "missing object body")
    content_length = response.get("ContentLength")
    try:
        too_large = content_length is not None and int(content_length) > limit
    except (TypeError, ValueError):
        raise BackupError("read", "invalid object size") from None
    if too_large:
        raise BackupError("read", "object exceeds the allowed manifest size")
    try:
        return _read_limited(body, limit)
    finally:
        body.close()


def _object_extra(response):
    extra = {}
    for name in (
        "ContentType",
        "CacheControl",
        "ContentDisposition",
        "ContentEncoding",
        "ContentLanguage",
    ):
        value = response.get(name)
        if value:
            extra[name] = value
    metadata = response.get("Metadata")
    if metadata:
        extra["Metadata"] = {str(key): str(value) for key, value in metadata.items()}
    return extra


def _verify_stream(client, bucket, key, expected_size, expected_sha256):
    response = _call("verify", client.get_object, Bucket=bucket, Key=key)
    body = response.get("Body")
    if body is None:
        raise BackupError("verify", "missing object body")
    try:
        content_length = response.get("ContentLength")
        try:
            size_mismatch = (
                content_length is not None
                and int(content_length) != expected_size
            )
        except (TypeError, ValueError):
            raise BackupError("verify", "invalid object size") from None
        if size_mismatch:
            raise BackupError("verify", "content mismatch")
        size, digest = _hash_stream(body)
    finally:
        body.close()
    if size != expected_size or digest != expected_sha256:
        raise BackupError("verify", "content mismatch")
    return response.get("ETag")


# The cached s3transfer 0.10.3 runtime was not inspected under the offline
# scope. A local 0.14.0 source check showed its non-seekable read path does not
# fill short reads, so this explicit path fills each 8 MiB part, hashes exactly
# the uploaded bytes, and keeps one part resident. The S3 10,000-part limit
# caps a supported object at 80,000 MiB (about 78 GiB).
def _safe_cleanup_reason(exc):
    if isinstance(exc, BackupError):
        return exc.reason
    return f"unexpected {type(exc).__name__}"


def _copy_stream(
    client,
    source_bucket,
    source_key,
    expected_size,
    source_etag,
    destination_bucket,
    destination_key,
):
    kwargs = {"Bucket": source_bucket, "Key": source_key}
    if source_etag:
        kwargs["IfMatch"] = source_etag
    response = _call("read", client.get_object, **kwargs)
    body = response.get("Body")
    if body is None:
        raise BackupError("read", "missing object body")

    digest = hashlib.sha256()
    total = 0
    upload_id = None
    completed = False
    try:
        content_length = response.get("ContentLength")
        try:
            size_mismatch = (
                content_length is not None and int(content_length) != expected_size
            )
        except (TypeError, ValueError):
            raise BackupError("read", "invalid object size") from None
        if size_mismatch:
            raise BackupError("read", "source size changed")
        extra = _object_extra(response)

        if expected_size == 0:
            if body.read(1):
                raise BackupError("read", "source size changed")
            _call(
                "write",
                client.put_object,
                Bucket=destination_bucket,
                Key=destination_key,
                Body=b"",
                ContentLength=0,
                **extra,
            )
        else:
            upload = _call(
                "write",
                client.create_multipart_upload,
                Bucket=destination_bucket,
                Key=destination_key,
                **extra,
            )
            upload_id = upload.get("UploadId")
            if not upload_id:
                raise BackupError("write", "missing upload identity")
            parts = []
            part_number = 1
            while True:
                chunk = _read_filled(body, CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise BackupError("read", "source size changed")
                digest.update(chunk)
                part = _call(
                    "write",
                    client.upload_part,
                    Bucket=destination_bucket,
                    Key=destination_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                    ContentLength=len(chunk),
                )
                if not part.get("ETag"):
                    raise BackupError("write", "missing part identity")
                parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                part_number += 1
            if total != expected_size:
                raise BackupError("read", "source size changed")
            _call(
                "write",
                client.complete_multipart_upload,
                Bucket=destination_bucket,
                Key=destination_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            completed = True
    except Exception as primary:
        cleanup_failure = None
        if upload_id and not completed:
            try:
                _call(
                    "cleanup",
                    client.abort_multipart_upload,
                    Bucket=destination_bucket,
                    Key=destination_key,
                    UploadId=upload_id,
                )
            except Exception as cleanup:  # noqa: BLE001 - report both failures
                cleanup_failure = _safe_cleanup_reason(cleanup)
        if cleanup_failure is not None:
            stage = primary.stage if isinstance(primary, BackupError) else "write"
            reason = (
                primary.reason
                if isinstance(primary, BackupError)
                else f"unexpected {type(primary).__name__}"
            )
            raise BackupError(
                stage, f"{reason}; cleanup failed: {cleanup_failure}"
            ) from None
        raise
    finally:
        body.close()

    destination_etag = _verify_stream(
        client,
        destination_bucket,
        destination_key,
        total,
        digest.hexdigest(),
    )
    return {
        "size": total,
        "sha256": digest.hexdigest(),
        "etag": destination_etag,
        "content_type": response.get("ContentType"),
        "metadata": dict(response.get("Metadata") or {}),
    }


def _prefix_has_objects(client, bucket, prefix, stage):
    response = _call(
        stage,
        client.list_objects_v2,
        Bucket=bucket,
        Prefix=prefix,
        MaxKeys=1,
    )
    return bool(response.get("Contents") or response.get("CommonPrefixes"))


def backup(client, config, now=None):
    config = validate_config(config)
    now = _now(now)
    started = _utcnow()
    snapshot_id = _new_snapshot_id(now)
    destination = config["destination"]
    snapshot_prefix = f"{config['prefix']}{snapshot_id}/"

    if _prefix_has_objects(client, destination, snapshot_prefix, "destination"):
        raise BackupError("destination", "snapshot prefix already exists")

    inventories = []
    for bucket in config["sources"]:
        inventory = _source_inventory(client, bucket)
        if inventory["settings"]["versioning"] != "unconfigured":
            raise BackupError(
                "settings",
                "source versioning must remain unconfigured for full coverage",
            )
        inventories.append(inventory)

    manifest_sources = []
    manifest_objects = []
    total_bytes = 0
    for inventory in inventories:
        bucket = inventory["bucket"]
        entries = []
        for item in inventory["objects"]:
            key = item["Key"]
            size = int(item["Size"])
            destination_key = f"{snapshot_prefix}objects/{bucket}/{key}"
            copied = _copy_stream(
                client,
                bucket,
                key,
                size,
                item.get("ETag"),
                destination,
                destination_key,
            )
            if copied["size"] != size:
                raise BackupError("read", "source size changed")
            entry = {
                "bucket": bucket,
                "key": key,
                "source_etag": item.get("ETag"),
                "source_last_modified": _iso(item["LastModified"]),
                "size": copied["size"],
                "sha256": copied["sha256"],
                "backup_key": destination_key,
                "backup_etag": copied["etag"],
                "content_type": copied["content_type"],
                "metadata": copied["metadata"],
                "acquired_at": _iso(_utcnow()),
            }
            entries.append(entry)
            manifest_objects.append(entry)
            total_bytes += copied["size"]
        manifest_sources.append(
            {
                "bucket": bucket,
                "settings": inventory["settings"],
                "objects": entries,
                "incomplete_uploads": inventory["incomplete_uploads"],
                "list_started_at": inventory["list_started_at"],
                "list_completed_at": inventory["list_completed_at"],
            }
        )

    copy_completed = _utcnow()
    for inventory, manifest_source in zip(inventories, manifest_sources):
        rechecked = _source_inventory(client, inventory["bucket"])
        _assert_stable(inventory, rechecked)
        manifest_source["settings_after"] = rechecked["settings"]
        manifest_source["incomplete_uploads_after"] = rechecked["incomplete_uploads"]
        manifest_source["recheck_started_at"] = rechecked["list_started_at"]
        manifest_source["recheck_completed_at"] = rechecked["list_completed_at"]

    incomplete = {"count": 0, "parts": 0, "bytes": 0}
    for manifest_source in manifest_sources:
        for field in incomplete:
            incomplete[field] += manifest_source["incomplete_uploads"][field]

    completed = _utcnow()
    manifest = {
        "format": SNAPSHOT_FORMAT,
        "snapshot_id": snapshot_id,
        "created_at": _iso(now),
        "atomic_snapshot": False,
        "consistency": "Sequential non-atomic acquisition; not a database/file snapshot.",
        "acquisition": {
            "started_at": _iso(started),
            "copy_completed_at": _iso(copy_completed),
            "completed_at": _iso(completed),
        },
        "source": manifest_sources,
        "object_count": len(manifest_objects),
        "byte_count": total_bytes,
        "objects": manifest_objects,
        "incomplete_uploads": incomplete,
    }
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_key = f"{snapshot_prefix}{MANIFEST_NAME}"
    _call(
        "manifest",
        client.put_object,
        Bucket=destination,
        Key=manifest_key,
        Body=manifest_bytes,
        ContentType="application/json",
        ContentLength=len(manifest_bytes),
    )
    if _read_bytes(client, destination, manifest_key, MAX_MANIFEST_BYTES) != manifest_bytes:
        raise BackupError("manifest", "manifest verification failed")

    marker = {
        "format": MARKER_FORMAT,
        "snapshot_id": snapshot_id,
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_sha256,
        "completed_at": _iso(completed),
    }
    marker_bytes = json.dumps(
        marker, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _call(
        "marker",
        client.put_object,
        Bucket=destination,
        Key=f"{snapshot_prefix}{MARKER_NAME}",
        Body=marker_bytes,
        ContentType="application/json",
        ContentLength=len(marker_bytes),
    )

    return {
        "status": "complete",
        "snapshot_id": snapshot_id,
        "objects": len(manifest_objects),
        "bytes": total_bytes,
        "incomplete_uploads": incomplete,
        "started_at": _iso(started),
        "completed_at": _iso(completed),
    }


def _validate_drill_target(config, destination_bucket, destination_prefix):
    if not isinstance(destination_bucket, str) or not DRILL_BUCKET_RE.fullmatch(
        destination_bucket
    ):
        raise ValueError("restore destination must use the invenio-rdm-drill- prefix")
    if (
        destination_bucket in config["sources"]
        or destination_bucket == config["destination"]
        or destination_bucket in PROTECTED_BUCKETS
    ):
        raise ValueError("restore destination must be an isolated drill bucket")
    if (
        not isinstance(destination_prefix, str)
        or not destination_prefix.startswith(DRILL_PREFIX_ROOT)
        or len(destination_prefix) <= len(DRILL_PREFIX_ROOT)
        or not destination_prefix.endswith("/")
        or "//" in destination_prefix
    ):
        raise ValueError("restore destination prefix must be under restore/")
    if any(
        part in ("", ".", "..")
        for part in destination_prefix.split("/")[:-1]
    ):
        raise ValueError("restore destination prefix must not traverse")


def _decode_json(raw, stage):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise BackupError(stage, "invalid JSON") from None
    if not isinstance(value, dict):
        raise BackupError(stage, "invalid JSON object")
    return value


def _read_json(client, bucket, key, stage):
    try:
        raw = _read_bytes(client, bucket, key, MAX_MANIFEST_BYTES)
    except BackupError as exc:
        if exc.reason in ("NoSuchKey", "NotFound"):
            raise BackupError(stage, "completion evidence is missing") from None
        raise
    return _decode_json(raw, stage)


def _validate_manifest(manifest, config, snapshot_id, snapshot_prefix):
    if manifest.get("format") != SNAPSHOT_FORMAT:
        raise BackupError("manifest", "unsupported snapshot format")
    if manifest.get("snapshot_id") != snapshot_id:
        raise BackupError("manifest", "snapshot identity does not match")
    objects = manifest.get("objects")
    if not isinstance(objects, list):
        raise BackupError("manifest", "object inventory is malformed")
    seen = set()
    byte_count = 0
    for item in objects:
        if not isinstance(item, dict):
            raise BackupError("manifest", "object entry is malformed")
        if item.get("bucket") not in config["sources"]:
            raise BackupError("manifest", "object source is outside the approved scope")
        if not isinstance(item.get("key"), str) or not item["key"]:
            raise BackupError("manifest", "object key is malformed")
        backup_key = item.get("backup_key")
        expected_backup_key = (
            f"{snapshot_prefix}objects/{item['bucket']}/{item['key']}"
        )
        if (
            not isinstance(backup_key, str)
            or backup_key != expected_backup_key
            or backup_key in seen
        ):
            raise BackupError("manifest", "backup key is outside the owned snapshot")
        seen.add(backup_key)
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BackupError("manifest", "object size is malformed")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BackupError("manifest", "object checksum is malformed")
        byte_count += size
    if len(objects) != manifest.get("object_count"):
        raise BackupError("manifest", "object count does not match")
    if byte_count != manifest.get("byte_count"):
        raise BackupError("manifest", "byte count does not match")


def _validate_marker(marker, snapshot_id, manifest_key, manifest_sha256):
    if marker.get("format") != MARKER_FORMAT:
        raise BackupError("snapshot", "unsupported completion format")
    if marker.get("snapshot_id") != snapshot_id:
        raise BackupError("snapshot", "completion identity does not match")
    if marker.get("manifest_key") != manifest_key:
        raise BackupError("snapshot", "completion manifest binding is invalid")
    if marker.get("manifest_sha256") != manifest_sha256:
        raise BackupError("snapshot", "completion manifest digest is invalid")
    try:
        _parse_iso(marker.get("completed_at"))
    except ValueError:
        raise BackupError("snapshot", "completion time is malformed") from None


def _load_snapshot(client, config, snapshot_id):
    destination = config["destination"]
    snapshot_prefix = f"{config['prefix']}{snapshot_id}/"
    manifest_key = f"{snapshot_prefix}{MANIFEST_NAME}"
    marker_key = f"{snapshot_prefix}{MARKER_NAME}"

    marker = _read_json(client, destination, marker_key, "snapshot")
    try:
        manifest_bytes = _read_bytes(
            client, destination, manifest_key, MAX_MANIFEST_BYTES
        )
    except BackupError as exc:
        if exc.reason in ("NoSuchKey", "NotFound"):
            raise BackupError("snapshot", "completion evidence is missing") from None
        raise
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    _validate_marker(marker, snapshot_id, manifest_key, manifest_sha256)
    manifest = _decode_json(manifest_bytes, "manifest")
    _validate_manifest(manifest, config, snapshot_id, snapshot_prefix)
    try:
        created_at = _parse_iso(manifest.get("created_at"))
    except ValueError:
        raise BackupError("manifest", "capture time is malformed") from None
    if created_at != _snapshot_time(snapshot_id):
        raise BackupError("manifest", "capture identity and time disagree")

    return {
        "snapshot_id": snapshot_id,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "snapshot_prefix": snapshot_prefix,
        "created_at": created_at,
    }


def _verify_snapshot_objects(client, config, point):
    for item in point["manifest"]["objects"]:
        _verify_stream(
            client,
            config["destination"],
            item["backup_key"],
            item["size"],
            item["sha256"],
        )


def restore(client, config, snapshot_id, destination_bucket, destination_prefix, confirmed=False):
    config = validate_config(config)
    if not confirmed:
        raise ValueError("restore requires explicit confirmation")
    if not isinstance(snapshot_id, str) or not SNAPSHOT_RE.fullmatch(snapshot_id):
        raise ValueError("snapshot identity is not owned by this command")
    _validate_drill_target(config, destination_bucket, destination_prefix)

    point = _load_snapshot(client, config, snapshot_id)
    _call("restore", client.head_bucket, Bucket=destination_bucket)
    if _prefix_has_objects(
        client, destination_bucket, destination_prefix, "restore"
    ):
        raise BackupError("restore", "destination prefix is not empty")

    total_bytes = 0
    for item in point["manifest"]["objects"]:
        destination_key = (
            f"{destination_prefix}{item['bucket']}/{item['key']}"
        )
        copied = _copy_stream(
            client,
            config["destination"],
            item["backup_key"],
            item["size"],
            None,
            destination_bucket,
            destination_key,
        )
        if copied["size"] != item["size"] or copied["sha256"] != item["sha256"]:
            raise BackupError("verify", "restored content does not match the manifest")
        total_bytes += copied["size"]
    return {
        "status": "complete",
        "snapshot_id": snapshot_id,
        "objects": len(point["manifest"]["objects"]),
        "bytes": total_bytes,
        "destination_bucket": destination_bucket,
        "destination_prefix": destination_prefix,
    }


def _snapshot_prefixes(client, bucket, prefix):
    paginator = client.get_paginator("list_objects_v2")
    snapshot_ids = []
    reported = []
    stray_objects = 0
    unrecognized_prefixes = 0
    for page in _pages(
        "prune", paginator, Bucket=bucket, Prefix=prefix, Delimiter="/"
    ):
        for common_prefix in page.get("CommonPrefixes") or []:
            value = common_prefix.get("Prefix") or ""
            if not value.startswith(prefix):
                continue
            snapshot_id = value[len(prefix):].rstrip("/")
            if SNAPSHOT_RE.fullmatch(snapshot_id):
                snapshot_ids.append(snapshot_id)
            else:
                unrecognized_prefixes += 1
        stray_objects += len(page.get("Contents") or [])
    if unrecognized_prefixes:
        reported.append(
            {
                "reason": "unrecognized prefixes",
                "count": unrecognized_prefixes,
            }
        )
    if stray_objects:
        reported.append(
            {"reason": f"{stray_objects} stray objects"}
        )
    return sorted(set(snapshot_ids)), reported


def _list_keys(client, bucket, prefix):
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in _pages("prune", paginator, Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents") or []:
            if item.get("Key"):
                keys.append(item["Key"])
    return sorted(keys)


def _delete_keys(client, bucket, keys):
    for offset in range(0, len(keys), 1000):
        batch = keys[offset:offset + 1000]
        if not batch:
            continue
        response = _call(
            "prune",
            client.delete_objects,
            Bucket=bucket,
            Delete={
                "Objects": [{"Key": key} for key in batch],
                "Quiet": False,
            },
        )
        for error in response.get("Errors") or []:
            code = _safe_error_code(str(error.get("Code") or "Unknown"))
            if code in ("NoSuchKey", "NotFound"):
                continue
            raise BackupError("prune", code)


def _expected_point_keys(point):
    return {
        f"{point['snapshot_prefix']}{MARKER_NAME}",
        f"{point['snapshot_prefix']}{MANIFEST_NAME}",
        *[item["backup_key"] for item in point["manifest"]["objects"]],
    }


def _delete_point(client, bucket, point):
    snapshot_prefix = point["snapshot_prefix"]
    _delete_keys(client, bucket, [f"{snapshot_prefix}{MARKER_NAME}"])
    _delete_keys(client, bucket, [f"{snapshot_prefix}{MANIFEST_NAME}"])
    _delete_keys(
        client,
        bucket,
        sorted(item["backup_key"] for item in point["manifest"]["objects"]),
    )


def prune(client, config, now=None, confirmed=False):
    config = validate_config(config)
    if not confirmed:
        raise ValueError("prune requires explicit confirmation")
    now = _now(now)
    destination = config["destination"]
    snapshot_ids, reported = _snapshot_prefixes(
        client, destination, config["prefix"]
    )

    complete = []
    for snapshot_id in snapshot_ids:
        try:
            point = _load_snapshot(client, config, snapshot_id)
            if point["created_at"] > now + timedelta(minutes=5):
                raise BackupError("snapshot", "capture time is in the future")
            complete.append(point)
        except BackupError as exc:
            if exc.stage not in ("snapshot", "manifest"):
                raise
            reported.append(
                {
                    "snapshot_id": snapshot_id,
                    "reason": f"{exc.stage}: {exc.reason}",
                }
            )

    for point in complete:
        _verify_snapshot_objects(client, config, point)

    for point in complete:
        actual_keys = set(
            _list_keys(client, destination, point["snapshot_prefix"])
        )
        if actual_keys != _expected_point_keys(point):
            reported.append(
                {
                    "snapshot_id": point["snapshot_id"],
                    "reason": "unexpected objects in owned snapshot",
                }
            )

    kept_all = sorted(point["snapshot_id"] for point in complete)
    if reported:
        return {
            "status": "attention",
            "deleted": [],
            "kept": kept_all,
            "reported": reported,
        }
    if not complete:
        return {
            "status": "ok",
            "deleted": [],
            "kept": [],
            "reported": [],
        }

    latest = max(
        complete,
        key=lambda point: (point["created_at"], point["snapshot_id"]),
    )
    cutoff = now - timedelta(days=config["retention_days"])
    deleted = []
    kept = []
    for point in sorted(
        complete,
        key=lambda item: (item["created_at"], item["snapshot_id"]),
    ):
        if point["snapshot_id"] == latest["snapshot_id"]:
            kept.append(point["snapshot_id"])
            continue
        strictly_newer = any(
            other["created_at"] > point["created_at"] for other in complete
        )
        if point["created_at"] < cutoff and strictly_newer:
            _delete_point(client, destination, point)
            deleted.append(point["snapshot_id"])
        else:
            kept.append(point["snapshot_id"])

    return {
        "status": "ok",
        "deleted": deleted,
        "kept": kept,
        "reported": [],
    }


def build_client(config):
    config = validate_config(config)
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    session_token = os.environ.get("AWS_SESSION_TOKEN") or None
    if not access_key or not secret_key:
        raise BackupError(
            "credentials",
            "explicit access key and secret are required; metadata providers are disabled",
        )
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        raise BackupError("credentials", "S3 SDK is unavailable") from None
    client_config = BotoConfig(
        connect_timeout=10,
        read_timeout=120,
        retries={"max_attempts": 3, "mode": "standard"},
        s3={"addressing_style": "path"},
        signature_version="s3v4",
        max_pool_connections=10,
    )
    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
    )
    return session.client(
        "s3",
        endpoint_url=config["endpoint"],
        region_name=config["region"],
        config=client_config,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="authoritative JSON settings")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("backup")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--snapshot-id", required=True)
    restore_parser.add_argument("--destination-bucket", required=True)
    restore_parser.add_argument("--destination-prefix", required=True)
    restore_parser.add_argument("--confirm", action="store_true")
    prune_parser = subparsers.add_parser("prune")
    prune_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        client = build_client(config)
        if args.mode == "backup":
            result = backup(client, config)
        elif args.mode == "restore":
            result = restore(
                client,
                config,
                args.snapshot_id,
                args.destination_bucket,
                args.destination_prefix,
                confirmed=args.confirm,
            )
        else:
            result = prune(client, config, confirmed=args.confirm)
    except (BackupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - never print opaque SDK payloads
        print(f"error: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 2 if result.get("status") == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
