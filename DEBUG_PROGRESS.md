# InvenioRDM 500 Error — Debug Progress

**Goal:** Diagnose and fix the `POST /api/records?expand=1` → 500 error that blocks dataset uploads.

---

## What We Know About the Error

From the HAR file captured during the upload attempt:

| Field | Value |
|---|---|
| Endpoint | `POST /api/records?expand=1` |
| Status | `500 Internal Server Error` |
| `retry-after` | `40` (seconds until rate-limit window resets — informational, not the cause) |
| `x-ratelimit-remaining` | `997` (not rate-limited) |
| Request body | Includes `"files": {"enabled": true}` and `"rights": [{"id": "cc-by-4.0"}]` |

The `retry-after: 40` header appears on all responses from Flask-Limiter (not only errors), so it is **not** the cause of the 500.

---

## Infrastructure State

| Component | Status |
|---|---|
| InvenioRDM version | v13 (`invenio-app-rdm>=13.0.8`) |
| PostgreSQL | CloudNativePG cluster, healthy |
| OpenSearch | Single-node, healthy |
| Redis | Healthy (rate-limit, cache, broker all working) |
| MinIO (S3) | Endpoint `http://minio.minio.svc.cluster.local:9000`, buckets `invenio-rdm`, `invenio-rdm-uploads`, `invenio-rdm-backups` created by setup job |
| File storage | `invenio-s3>=3.0.2`, factory `invenio_s3.storage.s3boto3_storage_factory`, location `s3://invenio-rdm/files/` |
| DataCite / PIDs | Not configured — local PIDs only |
| ArgoCD | `invenio-bootstrap` app, `selfHeal: true` |
| Cluster | University Kubernetes (not the VPS at 145.79.15.69) |

---

## Merged PRs — Debug Infrastructure

| PR | Description | Status |
|---|---|---|
| #38 | Add temporary debug job (PostSync hook) | ✅ Merged |
| #39 | Rewrite debug job to write output to ConfigMap instead of stdout | ✅ Merged |
| #40 | Add `Role`/`RoleBinding` to ArgoCD project whitelist (prerequisite for RBAC) | ✅ Merged |
| #41 | Fix debug job: use `set +e` so ConfigMap write always runs (job was aborting early) | ✅ Merged |
| #42 | Add tracked placeholder ConfigMap so ArgoCD can serve it via resource API | ✅ Merged |
| #43 | Grant `argocd-server` SA pod-log access in `invenio` namespace | ✅ Merged |
| #44 | Also grant `argocd-application-controller` SA pod-log access | ✅ Merged |

---

## What We Have Tried and Why It Did Not Work

### 1. ArgoCD logs API → always returns empty (HTTP 200, 0 bytes)

**Root cause:** ArgoCD's logs API requires RBAC on `pods/log` for whichever SA proxies the request. We granted both `argocd-server` and `argocd-application-controller`. But the API still returns empty because the web container name is `web` (not `invenio-web`), AND ArgoCD's resource tree only shows directly-managed resources (Deployments, ConfigMaps, etc.) — not runtime pods. Without a pod name in the resource tree, the logs API can't find any pods to stream from.

**Status:** Blocked. Will continue investigating via the debug job.

### 2. Debug job → ConfigMap write always silently fails

**Root cause (hypothesis):** The debug job uses `curl` to POST/PATCH the Kubernetes API to write diagnostics to a ConfigMap. `curl` is almost certainly not installed in the `python:3.x-slim` base image that InvenioRDM uses. With `set +e`, the curl failure is silent and the job exits 0 (hookPhase: Succeeded) with an empty ConfigMap.

**Secondary issue:** Even if curl were available, `selfHeal: true` on `invenio-bootstrap` caused ArgoCD to immediately re-sync and revert the ConfigMap after the job wrote to it (before we could read it). We temporarily disabled selfHeal to confirm — the ConfigMap was still empty, confirming the curl-based write itself is the issue.

**Fix:** Rewrite the ConfigMap write using Python's `urllib.request` (stdlib, always available).

---

## Current Blocking Issue

The debug job runs but produces no readable output because the ConfigMap write uses `curl` which is not in the image. **Next PR will replace curl with Python urllib for the K8s API call.**

Once we have the diagnostic output, we will know the exact Python traceback from:
- Step 6a: create draft **without** files (expected to succeed)
- Step 6b: create draft **with** files (expected to show the traceback)

---

## Most Likely Root Cause Hypotheses

Listed in order of probability based on available evidence:

### Hypothesis A — `invenio-s3` config key mismatch (HIGH probability)
`app-config.yaml` sets `INVENIO_S3_ENDPOINT`. Flask strips the `INVENIO_` prefix → Flask config key `S3_ENDPOINT`. But `invenio-s3` v3 reads `S3_ENDPOINT_URL`. If that key is `None`, boto3 tries to reach AWS S3 instead of MinIO, and all file-related operations fail.

**Evidence for:** The config uses `INVENIO_S3_ENDPOINT` not `INVENIO_S3_ENDPOINT_URL`. The setup job bypasses this by using `os.environ['INVENIO_S3_ENDPOINT']` directly (not Flask config), which is why S3 buckets are created successfully.

**Evidence against:** Draft creation with `files.enabled: True` in InvenioRDM only creates a DB bucket record — it does NOT make an S3 API call. So this mismatch would only cause errors during actual file upload, not during draft creation.

**Verdict:** Needs to be fixed regardless, but may not be the 500 cause.

### Hypothesis B — OpenSearch index error during draft indexing (MEDIUM probability)
Draft creation triggers an OpenSearch write (synchronous in InvenioRDM v13 by default). If the RDM index mapping conflicts with existing data, or if the OpenSearch write times out or rejects the document, it throws a 500.

**To confirm:** Check OpenSearch indices and mapping from the debug output.

### Hypothesis C — Vocabulary not loaded / lookup failure (MEDIUM probability)
The request includes `rights: [{id: "cc-by-4.0"}]`. If the licenses vocabulary isn't properly indexed in OpenSearch (even if it's in PostgreSQL), the vocabulary resolution may throw an unexpected exception.

**To confirm:** Step 6 of debug job will reveal whether a minimal draft (no rights) succeeds.

### Hypothesis D — Custom code in the image (LOW probability)
The custom image at `ghcr.io/vityasyyy/invenio-rdm-custom:latest` may contain overrides that throw an exception.

---

## Debug Artifacts in Repo (Temporary)

These files must be removed once the root cause is found and fixed:

| File | Purpose |
|---|---|
| `k8s/apps/invenio/invenio-debug-job.yaml` | PostSync job that runs diagnostics |
| `k8s/apps/invenio/debug-rbac.yaml` | Grants `default` SA permission to write the ConfigMap |
| `k8s/apps/invenio/invenio-debug-output-cm.yaml` | Placeholder ConfigMap (tracked by ArgoCD) |
| `k8s/apps/invenio/argocd-log-reader.yaml` | Grants ArgoCD SAs pod-log access in `invenio` namespace |

The `Role`/`RoleBinding` entries added to `k8s/infra/argocd/projects/invenio-project.yaml` (PR #40) can stay permanently — they're useful for future debugging.

The `argocd-log-reader.yaml` Role/RoleBinding can also stay permanently as good practice for ArgoCD observability.

---

## Pending Actions

1. **[IMMEDIATE]** Merge PR (in progress) — fix debug job to use Python urllib instead of curl for ConfigMap write
2. **[AFTER #1 MERGES]** Trigger sync → read ConfigMap → get Python traceback from step 6b
3. **[AFTER ROOT CAUSE FOUND]** Apply targeted fix (e.g., rename config key, fix OpenSearch mapping, etc.)
4. **[AFTER FIX VERIFIED]** Remove debug artifacts: `invenio-debug-job.yaml`, `debug-rbac.yaml`, `invenio-debug-output-cm.yaml`
5. **[SECURITY]** Rotate ArgoCD admin password — it was exposed in session logs

---

## Security Note

The ArgoCD admin password `Toiletpaper123` appeared in session transcripts. **Rotate it as soon as the debugging session is complete.**
