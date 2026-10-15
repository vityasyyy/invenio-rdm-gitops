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

This follow-up needs explicit deployment approval. After merge: watch Deploy Verify, wait for the exact ConfigMap and rule-label changes, inspect runtime Prometheus rules and their evaluation health, and observe Backup resources across several Velero sync cycles. Reject success if ArgoCD emits new prune records or UIDs keep changing. Stored backup recoverability remains unproven; no new backup or restore is authorized.

## Rollback

Do not remove the endpoint protection without an equivalent safeguard. The rule label intentionally activates existing custom alerts; investigate genuine alerts rather than removing the label to suppress them. Stop for unexpected evaluation or service regressions.
