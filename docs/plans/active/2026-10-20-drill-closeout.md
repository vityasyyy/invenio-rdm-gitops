# Recovery drill closeout (#139)

Status: the three drill namespaces were deleted on 2026-10-20 after the owner's
approval. Three Retain PVs, their NFS backing directories, and the Velero
evidence remain. Physical storage removal and MinIO backend credential
revocation stay owner-gated; the Git network-policy removal is pending merge and
live verification.

## Scope

Cleanup covered only `recovery-canary-src-20261019`,
`recovery-canary-dst-20261019`, and `recovery-db-20261019`. No production
resource, namespace, backup, restore record, or PV was targeted.

## Inventory captured before deletion

| Namespace | Namespace UID | Contents |
|---|---|---|
| `recovery-canary-src-20261019` | `b61aa454-2330-48b7-b215-4d80f71e2812` | Pod `canary-reader`, PVC `canary-data`, NetworkPolicy `deny-all`, ResourceQuota `canary-budget` |
| `recovery-canary-dst-20261019` | `626ac378-ff3a-47d8-b7be-2740e72a7d15` | Pod `canary-reader`, PVC `canary-data`, NetworkPolicy `deny-all`, ResourceQuota `canary-budget` |
| `recovery-db-20261019` | `4b9539d2-5a89-4da8-ba78-b597ba532bef` | CNPG Cluster `postgres-recovery-drill`, primary Pod/PVC, three Services, NetworkPolicy `recovery-isolation`, ResourceQuota `recovery-budget`, operator-generated Secrets/Role/RoleBinding/PodDisruptionBudget, SealedSecret `recovery-minio-readonly` |

The native Cluster UID was `d7bbd71f-2e4e-47b0-b09a-a3426fd9f4ea`; its
operator-owned Pod, PVC, and Services carried controller owner references to
it. Canary objects had no owners. No ClusterRoleBinding or other cluster-scoped
resource referenced the drill namespaces, and no ArgoCD Application targeted
them.

### Drill volumes

| PVC | PVC UID | PV | PV UID |
|---|---|---|---|
| `recovery-canary-src-20261019/canary-data` | `a690ca58-427d-4648-a78e-4f719c4bb60c` | `pvc-a690ca58-427d-4648-a78e-4f719c4bb60c` | `68e98efe-00ba-4f82-9a17-7f299429ffd7` |
| `recovery-canary-dst-20261019/canary-data` | `7e593770-0399-4ef1-bb51-647430737faf` | `pvc-7e593770-0399-4ef1-bb51-647430737faf` | `4670abcc-1a75-4b3d-af6c-b84ca0c57d30` |
| `recovery-db-20261019/postgres-recovery-drill-1` | `e804d999-2378-4bdb-a5da-3b0daecee99b` | `pvc-e804d999-2378-4bdb-a5da-3b0daecee99b` | `dfebe444-7fad-43b5-8a11-17fdcf1c4fec` |

All three used distinct CSI handles on `10.17.117.48` under share
`/export/kube-btd` with subdirectories equal to their PV names.

## Executed cleanup

1. The lead deleted CNPG Cluster `postgres-recovery-drill` with foreground
   propagation; the operator removed its owned Pod, PVC, Services, and
   generated objects.
2. The lead deleted the three namespaces explicitly and confirmed all are
   absent.
3. The three PVs transitioned to `Released` and kept reclaim policy `Retain`
   and their original UIDs.
4. The two drill-only network policies were removed from Git. Live removal
   requires merge and ArgoCD sync; verification is pending. Production
   allowlists in `k8s/infra/security` are unchanged.

The Namespace Secret and SealedSecret `recovery-minio-readonly` disappeared
with the namespace, and Kubernetes RBAC/binding is gone. The two network rules
are removed from Git only; they remain live until the merge is synced.

## Preserved evidence

| Object | UID | State |
|---|---|---|
| Backup `recovery-canary-20261019` | `ecee259e-b3d4-4117-88f1-11074eb1336b` | Completed |
| Backup `recovery-production-20261019` | `ae9634ec-1f24-4bba-a91c-81870f36624d` | Completed |
| Backup `weekly-infra-backup-20261018030030` | `1b847231-2554-445c-8a8c-5c669a850b68` | PartiallyFailed |
| Restore `recovery-canary-20261019` | `603b2c62-fb03-4a57-aa46-ba26d5555db2` | Completed |
| Restore `recovery-canary-20261019-v2` | `9521b392-7bfe-4540-8b45-cad90110f544` | Completed |

Original production resources were not modified. The production database,
the 17 ArgoCD applications, and public HTTP reachability were confirmed healthy
after cleanup; the exact command output remains in the worker report.

## Remaining gates

- **NFS storage removal**: the three Released PVs still reference backing
  directories on the shared NFS export. Deleting them and removing those
  directories is a separately approved storage action. Do not force-remove PV
  finalizers or issue a broad recursive delete.
- **MinIO backend key revocation**: the drill-only identity behind
  `recovery-minio-readonly` was provisioned outside this repository; there is
  no credential manifest in Git to remove. The owner must revoke that identity
  and policy at the MinIO backend. No root credential or secret value was read.
- **PV object deletion**: requires the storage decision above; retained PVs
  are intentional evidence/residuals, not leaked in-use volumes.
- **Application consistency**: no new post-cleanup backup is required to prove
  quota cleanup. Prior canary and native recovery evidence remains retained and
  valid; seeded application-consistency proof remains open under #139.
- Monitoring ownership and `docs/plans/README.md` updates belong to the lead
  and the monitoring worker, not this change.

## Regression

`scripts/tests/test_recovery_network.py` asserts the drill policy file and its
kustomization entry are absent, and that no manifest under
`k8s/infra/security` references the drill namespaces or `postgres-recovery-drill`.
