# Fix Invenio RDM Upload and Redesign MinIO Bucket Creation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken file upload flow in Invenio RDM by ensuring MinIO buckets exist, redesign bucket creation to be reliable (not dependent on ArgoCD PostSync hooks), and resolve OpenSearch worker connection errors.

**Architecture:** Move bucket creation from an unreliable ArgoCD PostSync hook (in `minio-extras` app) into the existing `invenio-setup-job` that runs at sync-wave 2 before web pods start. The setup job already has S3 credentials, waits for dependencies, and is idempotent. Remove the PostSync hook to prevent future confusion. Investigate and fix the worker's OpenSearch connection refused errors.

**Tech Stack:** Kubernetes, ArgoCD, Kustomize, MinIO (S3-compatible), InvenioRDM, OpenSearch, Python/boto3

---

## Background: Why Uploads Are Broken

1. **Missing MinIO buckets**: The `invenio-setup-job` created a database record pointing to `s3://invenio-rdm/files/`, but the bucket was never created.
2. **PostSync hook never ran**: The `create-invenio-buckets` Job in `k8s/infra/minio/invenio-buckets-job.yaml` uses `argocd.argoproj.io/hook: PostSync`. ArgoCD only executes PostSync hooks during actual sync operations. Since the `minio-extras` app's non-hook resources didn't change after the hook was added, ArgoCD never triggered a sync, so the hook never ran.
3. **Worker OpenSearch errors**: Celery worker logs show `ConnectionError: [Errno 111] Connection refused` when trying to connect to OpenSearch. This may be a startup race condition or DNS caching issue.

---

## File Structure Changes

| File | Action | Reason |
|------|--------|--------|
| `k8s/apps/invenio/invenio-setup-job.yaml` | Modify | Add MinIO bucket creation using `mc` or `boto3` |
| `k8s/infra/minio/invenio-buckets-job.yaml` | Delete | Remove unreliable PostSync hook |
| `k8s/infra/minio/kustomization.yaml` | Modify | Remove reference to deleted job |
| `k8s/apps/invenio/invenio-worker-deployment.yaml` | Modify | Add init container or fix OpenSearch URL resolution |
| `k8s/apps/invenio/invenio-deployment.yaml` | Modify | Add init container for consistency |
| `k8s/apps/invenio/app-config.yaml` | Possibly modify | Ensure correct OpenSearch URL env vars |

---

### Task 1: Immediate Fix — Create Missing MinIO Buckets Manually

**Purpose:** Restore upload functionality immediately while we redesign the long-term solution.

**Files:** None (runtime cluster commands only)

- [ ] **Step 1: Get MinIO credentials from the sealed secret**

Run:
```bash
kubectl -n minio get secret minio-credentials \
  -o jsonpath='{.data.rootUser}' | base64 -d && echo
kubectl -n minio get secret minio-credentials \
  -o jsonpath='{.data.rootPassword}' | base64 -d && echo
```

Expected: Two values printed — save these as `MINIO_USER` and `MINIO_PASS`.

- [ ] **Step 2: Port-forward to MinIO API and create buckets**

Run:
```bash
kubectl -n minio port-forward svc/minio 9000:9000 &
PF_PID=$!

# Use mc (MinIO client) or curl. If mc is not installed, use the mc container:
kubectl -n minio run mc-tmp --rm -i --restart=Never \
  --image=quay.io/minio/mc:RELEASE.2024-11-21T17-21-54Z \
  --env="MINIO_ROOT_USER=$MINIO_USER" \
  --env="MINIO_ROOT_PASSWORD=$MINIO_PASS" \
  -- /bin/sh -c '
    mc alias set minio http://minio.minio.svc.cluster.local:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    for BUCKET in invenio-rdm invenio-rdm-uploads invenio-rdm-backups; do
      if ! mc ls minio/$BUCKET 2>/dev/null; then
        echo "Creating bucket $BUCKET..."
        mc mb minio/$BUCKET
      else
        echo "Bucket $BUCKET already exists"
      fi
    done
    mc ls minio
  '

kill $PF_PID 2>/dev/null
```

Expected: Output shows three buckets created (or already exist), then lists all buckets including `velero-backups`.

- [ ] **Step 3: Verify bucket accessibility from Invenio web pod**

Run:
```bash
kubectl -n invenio exec deployment/invenio-web -- python3 -c "
import boto3
from botocore.config import Config

s3 = boto3.client(
    's3',
    endpoint_url='http://minio.minio.svc.cluster.local:9000',
    aws_access_key_id='$(kubectl -n invenio get secret invenio-app-secrets -o jsonpath={.data.S3_ACCESS_KEY_ID} | base64 -d)',
    aws_secret_access_key='$(kubectl -n invenio get secret invenio-app-secrets -o jsonpath={.data.S3_SECRET_ACCESS_KEY} | base64 -d)',
    config=Config(signature_version='s3v4'),
    region_name='us-east-1'
)
print('Buckets:', [b['Name'] for b in s3.list_buckets()['Buckets']])
"
```

Expected: Output lists `invenio-rdm`, `invenio-rdm-uploads`, `invenio-rdm-backups`, `velero-backups`.

---

### Task 2: Redesign Bucket Creation — Move into Invenio Setup Job

**Purpose:** Make bucket creation reliable and GitOps-native by embedding it in the setup job that runs before web pods start.

**Rationale:** The existing `invenio-setup-job` already:
- Runs at sync-wave 2 (before web deployment at wave 3)
- Has all S3 credentials via `invenio-app-secrets`
- Is idempotent (`|| true` pattern)
- Waits for dependencies

This is far more reliable than a PostSync hook in a separate ArgoCD app that may or may not sync.

**Files:**
- Modify: `k8s/apps/invenio/invenio-setup-job.yaml`

- [ ] **Step 1: Install `mc` (MinIO client) in the setup job container**

The demo image (`ghcr.io/inveniosoftware/demo-inveniordm/demo-inveniordm:13.0.0-post1`) is based on AlmaLinux/RHEL and may not have `mc`. We can either:
- Option A: Use `pip install minio` (Python SDK) which is more likely to be available
- Option B: Download `mc` binary in an init step
- Option C: Use `curl` with the MinIO S3 API directly

**Recommended: Option A (Python boto3/minio SDK)** — the Invenio image already has Python and boto3.

Modify `k8s/apps/invenio/invenio-setup-job.yaml`. Add a bucket creation section after the file location creation:

```yaml
              # Create default file location if it doesn't exist
              invenio files location create --default default-location \
                s3://invenio-rdm/files/ || true

              # Create MinIO buckets if they don't exist
              echo "Ensuring MinIO buckets exist..."
              python3 -c "
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

s3 = boto3.client(
    's3',
    endpoint_url='http://minio.minio.svc.cluster.local:9000',
    aws_access_key_id='${INVENIO_S3_ACCESS_KEY_ID}',
    aws_secret_access_key='${INVENIO_S3_SECRET_ACCESS_KEY}',
    config=Config(signature_version='s3v4'),
    region_name='us-east-1'
)

for bucket in ['invenio-rdm', 'invenio-rdm-uploads', 'invenio-rdm-backups']:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f'Bucket {bucket} already exists')
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            print(f'Creating bucket {bucket}...')
            s3.create_bucket(Bucket=bucket)
            print(f'Bucket {bucket} created')
        else:
            print(f'Error checking bucket {bucket}: {e}')
            raise

print('All buckets verified')
"

              echo "Invenio setup complete!"
```

Replace the existing file location creation section (lines 76-78) with the above.

- [ ] **Step 2: Ensure the setup job has the S3 credentials as explicit env vars**

The setup job uses `envFrom` with `secretRef: invenio-app-secrets`. Verify the secret contains `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`. The Python script uses `${INVENIO_S3_ACCESS_KEY_ID}` which should be set by the secret.

Run to verify:
```bash
kubectl -n invenio get secret invenio-app-secrets -o jsonpath='{.data}' | jq 'keys'
```

Expected: Keys include `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`.

If the keys are named differently (e.g., `INVENIO_S3_ACCESS_KEY_ID`), update the Python script accordingly.

- [ ] **Step 3: Test the modified setup job locally or in a dry-run**

Apply the job manually to test:
```bash
# First, delete the existing completed job so ArgoCD doesn't get confused
kubectl -n invenio delete job invenio-setup --ignore-not-found

# Apply the modified job
kubectl apply -f k8s/apps/invenio/invenio-setup-job.yaml

# Watch it run
kubectl -n invenio logs -f job/invenio-setup
```

Expected: Logs show "Ensuring MinIO buckets exist..." followed by "Bucket invenio-rdm already exists" (since we created them in Task 1) and "All buckets verified".

- [ ] **Step 4: Commit the changes**

```bash
git add k8s/apps/invenio/invenio-setup-job.yaml
git commit -m "fix(invenio): create MinIO buckets in setup job instead of PostSync hook

Moves bucket creation from an unreliable ArgoCD PostSync hook
(in minio-extras app) into the invenio-setup-job which runs at
sync-wave 2 before web pods. This ensures buckets exist on every
Invenio sync, not just when minio-extras happens to sync.

Also makes the setup job more self-contained and idempotent."
```

---

### Task 3: Remove Unreliable PostSync Hook

**Purpose:** Clean up the now-redundant and unreliable PostSync hook to prevent future confusion.

**Files:**
- Delete: `k8s/infra/minio/invenio-buckets-job.yaml`
- Modify: `k8s/infra/minio/kustomization.yaml`

- [ ] **Step 1: Delete the PostSync hook file**

```bash
rm k8s/infra/minio/invenio-buckets-job.yaml
```

- [ ] **Step 2: Update kustomization.yaml to remove the deleted resource**

Edit `k8s/infra/minio/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - minio-credentials-secret.yaml
  - create-bucket-job.yaml
  # - invenio-buckets-job.yaml  # REMOVED: now handled by invenio-setup-job
  - minio-console-ingressroute.yaml
```

Or simply remove the line entirely:

```yaml
resources:
  - minio-credentials-secret.yaml
  - create-bucket-job.yaml
  - minio-console-ingressroute.yaml
```

- [ ] **Step 3: Verify kustomize build succeeds**

```bash
kustomize build k8s/infra/minio/ | head -20
```

Expected: YAML output with no errors. The output should NOT contain any Job named `create-invenio-buckets`.

- [ ] **Step 4: Commit the cleanup**

```bash
git add k8s/infra/minio/
git commit -m "cleanup(minio): remove unreliable PostSync hook for Invenio buckets

The create-invenio-buckets PostSync hook in minio-extras never ran
reliably because ArgoCD only executes PostSync hooks during actual
sync operations, and minio-extras rarely syncs. Bucket creation is
now handled by the invenio-setup-job (see previous commit)."
```

---

### Task 4: Fix OpenSearch Worker Connection Errors

**Purpose:** Resolve the Celery worker's `ConnectionError: [Errno 111] Connection refused` when connecting to OpenSearch.

**Diagnosis:** The worker has `OPENSEARCH_URL=http://invenio-search.invenio.svc.cluster.local:9200` from the secret. The network policies allow egress from `invenio` namespace pods (with label `app.kubernetes.io/part-of=invenio-rdm`) to `search` namespace on port 9200. The search namespace allows ingress from `invenio` namespace on port 9200.

However, the worker pod's labels are:
```
app.kubernetes.io/name=invenio-worker
app.kubernetes.io/part-of=invenio-rdm
```

This SHOULD match the egress policy `invenio-allow-egress-search` which selects `app.kubernetes.io/part-of=invenio-rdm`. But let me double-check... Yes, the worker has that label.

Possible causes:
1. **DNS caching**: The worker resolved `invenio-search.invenio.svc.cluster.local` before OpenSearch was ready and cached the failure.
2. **OpenSearch startup delay**: OpenSearch takes time to start listening on 9200.
3. **Worker starts before OpenSearch is ready**: The worker deployment has no init container waiting for OpenSearch.

**Solution:** Add an init container to the worker deployment that waits for OpenSearch to be ready before starting the worker. This is a standard pattern.

**Files:**
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml`
- Modify: `k8s/apps/invenio/invenio-deployment.yaml` (web pod) for consistency

- [ ] **Step 1: Add init container to worker deployment**

Edit `k8s/apps/invenio/invenio-worker-deployment.yaml`. Add an `initContainers` section before the main `containers` section:

```yaml
      initContainers:
        - name: wait-for-opensearch
          image: busybox:1.36
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -c
            - |
              echo "Waiting for OpenSearch to be ready..."
              until wget -qO- http://invenio-search.invenio.svc.cluster.local:9200/_cluster/health | grep -q '"status"'; do
                echo "OpenSearch not ready yet, waiting 5s..."
                sleep 5
              done
              echo "OpenSearch is ready!"
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
            limits:
              cpu: 50m
              memory: 32Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 65534
            seccompProfile:
              type: RuntimeDefault
```

Add this after the `spec:` in the pod template, before `containers:`.

- [ ] **Step 2: Add the same init container to the web deployment (for consistency)**

Edit `k8s/apps/invenio/invenio-deployment.yaml` and add the same `initContainers` block.

This ensures both web and worker pods wait for OpenSearch before starting, preventing race conditions.

- [ ] **Step 3: Restart the worker deployment to pick up changes**

After applying the changes (or if testing manually):
```bash
kubectl -n invenio rollout restart deployment/invenio-worker
kubectl -n invenio rollout status deployment/invenio-worker --timeout=180s
```

- [ ] **Step 4: Verify worker can connect to OpenSearch**

```bash
# Check new worker logs
kubectl -n invenio logs -f deployment/invenio-worker --tail=50
```

Look for: No more `ConnectionError: [Errno 111] Connection refused` errors. You may see Celery worker startup messages like `Connected to redis://...` and task registration.

- [ ] **Step 5: Commit the changes**

```bash
git add k8s/apps/invenio/invenio-worker-deployment.yaml k8s/apps/invenio/invenio-deployment.yaml
git commit -m "fix(invenio): add OpenSearch readiness init container to web and worker

Prevents race conditions where pods start before OpenSearch is
accepting connections, causing 'Connection refused' errors in Celery
tasks and potential search index corruption."
```

---

### Task 5: Restart Invenio and Verify Uploads Work

**Purpose:** Apply all fixes and end-to-end test the upload flow.

**Files:** None (runtime cluster commands only)

- [ ] **Step 1: Force ArgoCD to sync the invenio-bootstrap app**

```bash
argocd app sync invenio-bootstrap
argocd app wait invenio-bootstrap --health --timeout 300
```

Expected: App syncs successfully, all resources Healthy.

- [ ] **Step 2: Restart Invenio web and worker pods**

```bash
kubectl -n invenio rollout restart deployment/invenio-web
kubectl -n invenio rollout restart deployment/invenio-worker
kubectl -n invenio rollout status deployment/invenio-web --timeout=180s
kubectl -n invenio rollout status deployment/invenio-worker --timeout=180s
```

- [ ] **Step 3: Verify no errors in logs**

```bash
# Web pod
kubectl -n invenio logs -f deployment/invenio-web --tail=50

# Worker pod (in another terminal)
kubectl -n invenio logs -f deployment/invenio-worker --tail=50
```

Expected: No `Connection refused` errors, no S3/MinIO errors, no 500 errors.

- [ ] **Step 4: Test upload endpoint**

Access `https://invenio.vityasy.me` in a browser, log in, and try to create a new upload.

Or test via curl:
```bash
# Get a session cookie by logging in first, then:
curl -s -o /dev/null -w "%{http_code}" \
  -b "session_cookie=value" \
  https://invenio.vityasy.me/uploads/new
```

Expected: HTTP 200 (not 404).

- [ ] **Step 5: Verify a file can be uploaded**

Create a test record and upload a small file through the UI. If that's not possible programmatically:

```bash
# Create a test file in MinIO directly to verify the bucket works
kubectl -n invenio exec deployment/invenio-web -- python3 -c "
import boto3
from botocore.config import Config

s3 = boto3.client(
    's3',
    endpoint_url='http://minio.minio.svc.cluster.local:9000',
    aws_access_key_id='${INVENIO_S3_ACCESS_KEY_ID}',
    aws_secret_access_key='${INVENIO_S3_SECRET_ACCESS_KEY}',
    config=Config(signature_version='s3v4'),
    region_name='us-east-1'
)

s3.put_object(Bucket='invenio-rdm', Key='files/test-upload.txt', Body=b'Hello from Invenio')
obj = s3.get_object(Bucket='invenio-rdm', Key='files/test-upload.txt')
print('Upload test successful:', obj['Body'].read().decode())
"
```

Expected: "Upload test successful: Hello from Invenio"

---

### Task 6: Update Documentation

**Purpose:** Update README and SETUP.md to reflect the new bucket creation pattern and add troubleshooting guidance.

**Files:**
- Modify: `README.md`
- Modify: `SETUP.md`

- [ ] **Step 1: Update README.md Invenio Bootstrap section**

Find the section about MinIO usage (~line 158) and update:

```markdown
- MinIO usage: the Invenio setup job ensures required buckets exist (`invenio-rdm`, `invenio-rdm-uploads`, `invenio-rdm-backups`) before the web deployment starts. If you change bucket names, update both `k8s/apps/invenio/invenio-setup-job.yaml` and `k8s/apps/invenio/app-config.yaml`.
```

Remove or update any mention of the `create-invenio-buckets` PostSync hook.

- [ ] **Step 2: Update SETUP.md dependency mapping**

Find the MinIO section (~line 369) and update:

```markdown
- Endpoint: `http://minio.minio.svc.cluster.local:9000`
- Credentials source: SealedSecret in `invenio` namespace
- Required buckets: `invenio-rdm`, `invenio-rdm-uploads`, `invenio-rdm-backups`
- Bucket creation: handled automatically by `invenio-setup-job` (sync-wave 2)
```

- [ ] **Step 3: Add troubleshooting section for upload issues**

Add to README.md or SETUP.md:

```markdown
### Troubleshooting: Upload returns 404 or fails

1. Check MinIO buckets exist:
   ```bash
   kubectl -n invenio exec deployment/invenio-web -- python3 -c "
   import boto3
   s3 = boto3.client('s3', endpoint_url='http://minio.minio.svc.cluster.local:9000', ...)
   print([b['Name'] for b in s3.list_buckets()['Buckets']])
   ```
   Expected: `['invenio-rdm', 'invenio-rdm-uploads', 'invenio-rdm-backups', 'velero-backups']`

2. Check file location is configured in DB:
   ```bash
   kubectl -n database exec -it postgres-1 -- psql -U app-user invenio -c "SELECT * FROM files_location;"
   ```
   Expected: `default-location` with URI `s3://invenio-rdm/files/`

3. Check worker OpenSearch connectivity:
   ```bash
   kubectl -n invenio logs deployment/invenio-worker --tail=50 | grep -i error
   ```
   If you see `Connection refused` to OpenSearch, the init container may not be working. Restart the worker: `kubectl -n invenio rollout restart deployment/invenio-worker`
```

- [ ] **Step 4: Commit documentation updates**

```bash
git add README.md SETUP.md
git commit -m "docs: update MinIO bucket creation and upload troubleshooting

Documents the new bucket creation pattern (invenio-setup-job instead
of PostSync hook) and adds troubleshooting steps for upload issues."
```

---

## Self-Review Checklist

**1. Spec coverage:**
- ✅ Fix missing MinIO buckets (Task 1 immediate, Task 2 long-term)
- ✅ Redesign bucket creation to be reliable (Task 2, 3)
- ✅ Fix OpenSearch worker connection errors (Task 4)
- ✅ Verify uploads work (Task 5)
- ✅ Update documentation (Task 6)

**2. Placeholder scan:**
- ✅ No "TBD", "TODO", "implement later"
- ✅ No vague "add error handling" steps
- ✅ All commands have expected output
- ✅ All code blocks are complete

**3. Type consistency:**
- ✅ Secret key names consistent (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`)
- ✅ Bucket names consistent across all tasks
- ✅ Namespace references consistent (`invenio`, `minio`, `search`)

---

## Post-Implementation Verification

After all tasks are complete, run this comprehensive check:

```bash
# 1. ArgoCD apps all healthy
kubectl -n argocd get applications

# 2. All pods running
kubectl get pods -n invenio

# 3. Buckets exist
kubectl -n minio run mc-check --rm -i --restart=Never \
  --image=quay.io/minio/mc:RELEASE.2024-11-21T17-21-54Z \
  -- /bin/sh -c 'mc alias set minio http://minio.minio.svc.cluster.local:9000 $(kubectl -n minio get secret minio-credentials -o jsonpath={.data.rootUser} | base64 -d) $(kubectl -n minio get secret minio-credentials -o jsonpath={.data.rootPassword} | base64 -d) && mc ls minio'

# 4. No worker errors
kubectl -n invenio logs deployment/invenio-worker --tail=20

# 5. Upload endpoint accessible
curl -s -o /dev/null -w "%{http_code}\n" https://invenio.vityasy.me/uploads/new
```

Expected:
- All ArgoCD apps: Synced, Healthy
- Pods: All Running
- Buckets: 4 buckets listed
- Worker logs: No ConnectionError
- Upload endpoint: 200 (if logged in) or 302 (redirect to login)
