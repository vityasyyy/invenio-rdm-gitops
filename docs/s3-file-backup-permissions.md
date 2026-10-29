# S3 file backup permissions

These are non-secret policy examples derived from
`k8s/infra/minio/resources/s3-file-backup-config.json`. The owner provisions the
destination bucket, users, policies and Secret privately. Do not commit
credentials, SealedSecret ciphertext for this workflow, or reusable root/application
keys.

The command uses explicit environment credentials only. It never falls back to
application, root, CNPG, Velero or the saved inventory key, and it does not create
buckets. The destination bucket must be private and separate from the source,
CNPG and Velero buckets.

## Backup writer

Source buckets: `invenio-rdm`, `invenio-rdm-uploads`.
Destination bucket: `invenio-rdm-file-backups`, owned prefix `snapshots/`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:GetBucketVersioning",
        "s3:GetLifecycleConfiguration",
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": [
        "arn:aws:s3:::invenio-rdm",
        "arn:aws:s3:::invenio-rdm-uploads"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": [
        "arn:aws:s3:::invenio-rdm/*",
        "arn:aws:s3:::invenio-rdm-uploads/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::invenio-rdm-file-backups"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": [
        "arn:aws:s3:::invenio-rdm-file-backups/snapshots/*"
      ]
    }
  ]
}
```

No `s3:DeleteObject` is needed for backup. Removing source versioning,
lifecycle or ACL access is not allowed to make a failed preflight pass; fix the
policy or stop.

## Restore reader and drill writer

The restore identity reads only the backup bucket and writes only a separately
provisioned drill bucket named `invenio-rdm-drill-*`. The command additionally
requires the destination prefix to start with `restore/`, refuses a non-empty
prefix, and never creates the bucket. Grant bucket-level `s3:ListBucket` and
`s3:GetBucketLocation` on `invenio-rdm-file-backups`, and object-level
`s3:GetObject` on `invenio-rdm-file-backups/snapshots/*`. On the drill bucket,
grant bucket-level `s3:ListBucket`/`s3:GetBucketLocation`, and object-level
`s3:GetObject` for read-back verification, `s3:PutObject` and
`s3:AbortMultipartUpload` on the drill bucket's `restore/*` prefix. Do not grant
`s3:DeleteObject` to the restore identity.

## Prune identity

Prune is a separate privileged operation and is not scheduled. It needs
bucket-level `s3:ListBucket` and object-level `s3:GetObject`/`s3:DeleteObject` on
`invenio-rdm-file-backups/snapshots/*`. It must not have source, CNPG or Velero
delete access. Keep this identity separate from backup and restore. Its exact
scope is reviewed before any destructive run.

The command preserves the latest verified point, requires a newer verified point
before deleting an older expired one, and reports partial/corrupt/unrecognized
prefixes for inspection. It does not delete source, CNPG or Velero prefixes.

## Kubernetes Secret

The suspended CronJob references `minio/s3-file-backup-credentials` with keys
`accessKeyId`, `secretAccessKey` and optional `sessionToken`, mapped only to
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN`. The Secret is
unprovisioned in Git and must be created privately before a manual or scheduled run.
