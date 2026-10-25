# Runtime Selection Follow-up (#139)

## Failed live gates

PRs #141 and #142 passed CI and Deploy Verify, but deeper live checks found two selection gaps. Node-exporter succeeded: all three instance-level targets are up and master host metrics are present. Grafana health and provisioning are healthy. No backup/restore jobs were launched.

- **Velero:** Backup UIDs survived the initial observation, but ArgoCD pruned them again at 15:26:02Z and 15:27:16Z. The exclusion covered the application's DNS destination only. Controller apply logs identify `https://10.43.0.1:443`; the pinned gitops-engine `adb68bcaab73` filters API discovery using `config.Host` (`pkg/utils/kube/ctl.go:85`). Include both explicit local aliases, without widening groups/kinds/clusters to wildcards.
- **Custom alerts:** the live Prometheus selector requires `release=monitoring`; `cluster-health-alerts` had no such label. The corrected expressions existed in Kubernetes but were absent from Prometheus's runtime rules. Add the label; expect existing custom rules to become active as well as the repaired backup rules. Genuine alerts may fire.

## Changes and regression evidence

- Two new regression checks fail on the merged configuration: missing internal API endpoint and missing release selector label.
- Add the explicit endpoint alias and release label; update ownership expectations and run all script tests in the existing YAML CI job.
- Keep alert expressions, routes, images, Schedule management and pruning behavior for unrelated kinds unchanged.
- Verify monitoring Kustomize output and cached Helm rule-selector semantics. Do not infer runtime loading from a resource merely being Synced.

## Deployment gate and acceptance

The operator explicitly approved deployment. PR #143 merged as `fda91ec`; Deploy Verify run `38762337270` passed. The exact ConfigMap aliases and rule label are live. Stored backup recoverability remains unproven; no new backup or restore is authorized.

## Live evidence (2026-10-15, approximately 15:44 UTC)

- A 190-second resource watch observed both recreated Backup objects without deletion/recreation. Subsequent reads confirmed unchanged UIDs `860d4438-7a05-415e-9c96-7193bf7eca7c` and `08a56222-d50f-4b0c-b741-2301a1ab61e0`. No ArgoCD Backup prune records appeared after 15:41 UTC in the bounded log inspection.
- All 24 expected custom alert names are present in Prometheus runtime rules with evaluation health `ok`. An initial verifier accidentally included a similarly named chart rule group; checking against the repository's exact alert-name set corrected that diagnostic, without changing production rules.
- `VeleroBackupStale` is pending, and its expression reports approximately 39.06 days since last success. The surviving August Backup objects are `PartiallyFailed`. Ownership repair is not successful-backup or restore proof.
- All three exporter instances report `up=1`; master memory metrics are present. Grafana `/api/health` reports database `ok`; dashboard provisioning logs show completion without observed errors. All 17 ArgoCD Applications are Synced/Healthy.
- A bounded last-45-minute etcd sample contains one 138ms slow-apply warning, not the earlier multi-second fsync stalls. Storage improvement remains under observation; do not claim zero latency events.
- This repository exposes Deploy Verify, not workflows named Deploy Production or Infra Apply (auto). Both initial deployment runs (`38759860164`, `38760197207`) and follow-up deployment passed; deeper live checks, not pipeline green alone, found the initial faults.

## Rollback

Do not remove the endpoint protection without an equivalent safeguard. The rule label intentionally activates existing custom alerts; investigate genuine alerts rather than removing the label to suppress them. Stop for unexpected evaluation or service regressions.
