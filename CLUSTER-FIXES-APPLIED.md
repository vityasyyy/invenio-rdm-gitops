# Cluster Health Fixes - Applied

This document describes the fixes applied to resolve the 5 degraded apps in the cluster.

## Summary of Changes

### 1. Cloudflared (Wave -4 → -1)
- **File**: `argocd/apps/cloudflared.yaml`
- **Change**: Moved sync wave from -4 to -1 to deploy AFTER sealed-secrets controller (wave -3)
- **Result**: The SealedSecret credentials can now be unsealed before cloudflared starts
- **Cleanup**: Removed orphan duplicate SealedSecret at `k8s/infra/cloudflared/cloudflared-credentials-secret.yaml`

### 2. ArgoCD Repo-Server Resource Limits
- **File**: `k8s/infra/argocd/patches/kustomize/security-context-repo.yaml`
- **Changes**:
  - Memory: 128Mi/512Mi → **256Mi/1Gi**
  - CPU: 100m/500m → **200m/1**
  - Added `readOnlyRootFilesystem: true`
  - Added emptyDir volumes for `/tmp` and `/home/argocd` (required for Git checkouts and SSH keys)
- **Result**: Repo-server can handle large repos and won't OOM

### 3. MinIO - Automated Bucket Creation
- **Files**:
  - `k8s/infra/minio/values.yaml` - Use existingSecret, disabled bucket hooks
  - `k8s/infra/minio/create-bucket-job.yaml` - New Job with proper security context
  - `argocd/apps/minio.yaml` - Added third source for bucket job
- **Changes**:
  - Removed hardcoded `rootUser`/`rootPassword`, now using `existingSecret: minio-credentials`
  - Set `buckets: []` to disable the Helm chart's post-install hook (blocked by PSA)
  - Created a custom Job with PSA-compatible security context to create the bucket
  - Added ArgoCD hook annotation for the Job
- **Result**: MinIO deploys successfully, bucket is created automatically post-deploy

### 4. Velero - File-Level Backup Only
- **Files**:
  - `k8s/infra/velero/values.yaml` - Disabled snapshots, removed VSL
  - `k8s/infra/velero/backup-schedule.yaml` - Use fs-backup, remove VSL ref
- **Changes**:
  - Set `snapshotsEnabled: false` (NFS storage doesn't support CSI snapshots)
  - Removed invalid `volumeSnapshotLocation` with `region: minio`
  - Backup schedule now uses `defaultVolumesToFsBackup: true`
- **Result**: Velero will back up using restic file-level backups to MinIO

### 5. Monitoring - Enhanced Network Policies
- **File**: `k8s/infra/security/network-policies/monitoring-allow.yaml`
- **Changes**:
  - Added `allow-monitoring-internal` - allows all pods in monitoring namespace to communicate
  - Added `allow-prometheus-egress` - allows Prometheus to scrape targets across all namespaces + DNS
  - Added `allow-grafana-egress` - allows Grafana to query Prometheus + DNS
- **File**: `k8s/infra/security/network-policies/default-deny.yaml`
- **Changes**: Added default-deny policies for `minio` and `velero` namespaces
- **Result**: Grafana is accessible, Prometheus can scrape all targets

### 6. Credential Generation Script
- **File**: `scripts/generate-sealed-secrets.sh`
- **Purpose**: Generates fresh credentials for MinIO, Grafana, Velero, and seals them using kubeseal
- **Note**: Cloudflared credentials still need to be obtained manually from Cloudflare

## Next Steps

### 1. Generate and Seal New Credentials

Run the credential generation script:

```bash
./scripts/generate-sealed-secrets.sh
```

This will:
- Generate secure random credentials for MinIO, Grafana, Velero
- Seal them using the cluster's sealed-secrets controller
- Save the sealed secrets to the appropriate locations
- Save unencrypted credentials to `secrets/` directory

**Important**: The unencrypted credentials in `secrets/` should be backed up securely and NOT committed to Git.

### 2. Handle Cloudflared Credentials

The cloudflared credentials need to be obtained from Cloudflare:

1. Log into https://dash.cloudflare.com
2. Navigate to Zero Trust > Networks > Tunnels
3. Create a new tunnel or get existing tunnel credentials
4. Update the sealed secret manually:

```bash
kubectl -n kube-system get secret cloudflared-credentials -o json | kubeseal -o yaml > k8s/infra/cloudflared/cloudflared-credentials-secret.yaml
```

Note: The cloudflared SealedSecret is at `external-lb/k8s/cloudflared-credentials-secret.yaml`.

### 3. Commit and Push Changes

```bash
git add .
git commit -m "Fix all 5 degraded apps: cloudflared wave, argocd resources, minio bucket job, velero fs-backup, monitoring netpols"
git push origin main
```

### 4. Monitor ArgoCD Sync

Watch the applications sync in ArgoCD UI or via CLI:

```bash
kubectl get applications -n argocd -w
```

Expected sync order:
1. Wave -5: security-policies
2. Wave -4: traefik
3. Wave -3: sealed-secrets, argocd-self
4. Wave -2: cert-manager
5. Wave -1: cloudflared
6. Wave 1: minio
7. Wave 2: velero
8. Wave 5: monitoring
9. Wave 6: monitoring-extras

### 5. Verify Deployments

After sync completes, run the verification script:

```bash
./scripts/verify-infra.sh
```

### 6. Verify Specific Components

**MinIO bucket created:**
```bash
kubectl get job -n minio create-velero-backup-bucket
kubectl exec -n minio <minio-pod> -- mc ls minio/
```

**Velero BSL available:**
```bash
kubectl get backupstoragelocation -n velero
kubectl get schedule -n velero
```

**Grafana accessible:**
```bash
curl -I https://grafana.vityasy.me
```

## Troubleshooting

### ArgoCD repo-server still failing
- Check pod logs: `kubectl logs -n argocd -l app.kubernetes.io/name=argocd-repo-server`
- Check OOM events: `kubectl describe pod -n argocd -l app.kubernetes.io/name=argocd-repo-server`

### MinIO bucket not created
- Check Job logs: `kubectl logs -n minio job/create-velero-backup-bucket`
- Verify secret exists: `kubectl get secret -n minio minio-credentials`

### Velero BSL not available
- Check Velero pod logs: `kubectl logs -n velero deployment/velero`
- Verify secret exists: `kubectl get secret -n velero velero-credentials`
- Check BSL status: `kubectl get backupstoragelocation -n velero -o yaml`

### Grafana still inaccessible
- Check network policies: `kubectl get netpol -n monitoring`
- Check IngressRoute: `kubectl get ingressroute -n monitoring`
- Check Grafana pod: `kubectl logs -n monitoring -l app.kubernetes.io/name=grafana`

## Sync Wave Dependency Map

```
Wave -5: security-policies (namespaces, PSA, netpols, quotas)
Wave -4: traefik (ingress controller)
Wave -3: sealed-secrets (credential unsealing), argocd-self (ArgoCD management)
Wave -2: cert-manager (certificates)
Wave -1: cloudflared (tunnel - needs sealed-secrets)
Wave  1: minio (storage - bucket job post-sync)
Wave  2: velero (backups - needs minio BSL)
Wave  5: monitoring (metrics stack)
Wave  6: monitoring-extras (ingress - needs monitoring healthy)
```
