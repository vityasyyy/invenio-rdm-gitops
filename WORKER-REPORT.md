# WORKER-REPORT — alert-quota (KubeMemoryQuotaOvercommit)

Branch: `agent/alert-quota`. **Not merged to main** (per constraints). No secrets touched.
No HPA/deployment/replica edits (owned by alert-hpa worker).

## What changed

Lowered `requests.memory` ResourceQuota hards (only that field; CPU/limits/LimitRanges untouched)
so the cluster-aggregate quota sum clears the `KubeMemoryQuotaOvercommit` >1.5x threshold:

- `k8s/apps/invenio/namespace-governance.yaml`: 12Gi → 7Gi
- `k8s/infra/security/resource-quotas/monitoring-quota.yaml`: 12Gi → 6Gi
- `k8s/apps/invenio-deps/postgresql/namespace.yaml`: 8Gi → 4Gi
- `k8s/apps/invenio-deps/opensearch/manifests/namespace.yaml`: 4Gi → 2Gi
- `k8s/apps/invenio-deps/redis/manifests/namespace.yaml`: 2Gi → 1Gi
- `k8s/infra/security/resource-quotas/minio-quota.yaml`: 4Gi → 2Gi
- `k8s/infra/security/resource-quotas/argocd-quota.yaml`: 4Gi → 3Gi
- velero (2Gi) + default (512Mi): kept by design (DR safety / already minimal)

Docs: created `docs/plans/active/2026-09-27-quota-overcommit.md` (audit table, decision log,
PromQL + kubectl gates, rollback, open questions).

Root cause (evidence): the alert is cluster-aggregate
`sum(hard requests.memory) / sum(node allocatable) > 1.5`, not per-namespace usage.
Before: 48.5Gi / 23.2Gi (3×7918Mi) = 2.09 → constantly firing. After: 27.5Gi / 23.2Gi = 1.19.
Per-namespace audit ranked all namespaces ≤27% of their own quota — no single firing namespace
exists, so none was guessed. The ~6am/6pm cadence is the warning-tier `repeatInterval: 12h`,
not a CronJob (Velero Sun 3am, CNPG daily 2am).

## Verification (this machine)

- `kustomize build` exit 0: `k8s/infra/security`, `k8s/apps/invenio`,
  `k8s/apps/invenio-deps/postgresql`, `k8s/apps/invenio-deps/opensearch/manifests`,
  `k8s/apps/invenio-deps/redis/manifests`. Rendered `requests.memory` confirmed:
  7Gi / 6Gi / 4Gi / 2Gi / 1Gi / 3Gi / 2Gi / 512Mi.
- `yamllint` clean on all touched quota files (exit 0).
- `kubectl cluster-info`: connection refused (10.17.104.130) — expected, no cluster access.

## What remains (open live gates for operator)

1. Sync + check aggregate ratio PromQL < 1.5 (query in plan doc §6).
2. `kubectl describe resourcequota -A` per-namespace fill check (expect <50% steady).
3. Confirm live `argocd-image-updater` (est. 128Mi) and CNPG container requests (est. ≤256Mi).
4. Watch one invenio rollout (surge fits 7Gi) + two 12h Discord windows for silence.
5. Coordinate with alert-hpa worker before any HPA max raise (7Gi fits 2→3 with 1.1Gi slack;
   do not lower invenio further without their sign-off).
6. Follow-ups NOT in this change set: Discord namespace rendering, ouroboros existence check.

Rollback: revert the quota commit(s); ArgoCD self-heals. Raising quotas is non-blocking.
