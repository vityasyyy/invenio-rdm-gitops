# Monitoring Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task in this session. Steps use checkbox syntax for tracking.

**Goal:** Alert on missing, unsuccessful, and stale Velero backup metrics and restore node-exporter coverage on control-plane nodes.

**Architecture:** Prometheus tests will exercise the checked-in `PrometheusRule` after extracting its groups from the CR wrapper. Grafana will use the observed Velero metric. Node-exporter tolerations will be set under the pinned chart's `prometheus-node-exporter` dependency and verified by rendering the cached chart.

**Tech Stack:** PrometheusRule, `promtool test rules`, kube-prometheus-stack 69.6.0, Helm, Python/YAML.

## Global Constraints

- Change only `k8s/infra/monitoring/`, `scripts/ci-validate-monitoring.sh`, dedicated monitoring regression files, and this plan.
- Do not edit ArgoCD/Velero directories, shared CI workflows, or the plans index.
- No cluster changes, backup/restore jobs, secrets, SSH, network downloads, or deployment actions.
- Loki fault #140 remains separate and out of scope.
- Preserve alert routing and existing stale threshold; do not weaken checks.
- Keep `WORKER-REPORT.md` uncommitted; commit only scoped files with a Conventional Commit referencing #139.

## Files

- `k8s/infra/monitoring/alerts.yaml`: correct Velero metric; cover missing and zero success.
- `k8s/infra/monitoring/grafana-dashboards.yaml`: correct dead metric query.
- `k8s/infra/monitoring/values.yaml`: put control-plane taint toleration under the actual subchart key.
- `k8s/infra/monitoring/tests/backup-alerts.test.yaml`: synthetic metric and alert cases.
- `scripts/ci-validate-monitoring.sh`: run the synthetic rule tests against extracted production rules; pin the subchart toleration values.
- `docs/plans/active/2026-10-15-monitoring-repairs.md`: scope and verification record.

## Execution

- [x] Add Prometheus rule cases for absent metric with target up/down, zero success, fresh success, and stale success; run against current rules and record expected failure.
- [x] Correct the Velero rules and dashboard query; rerun the regression cases and verify alert timing/labels.
- [x] Correct node-exporter subchart tolerations; render cached chart 69.6.0 / dependency 4.44.1 and confirm the DaemonSet tolerates `CriticalAddonsOnly:NoExecute` while preserving `NoSchedule`.
- [ ] Run monitoring validator, promtool rule syntax/tests, YAML lint, relevant manifest render, and scoped diff review.
- [ ] Commit only assigned files; write root `WORKER-REPORT.md` uncommitted with SHA, files, exact checks, and gaps.

## Acceptance

- Missing metric alerts regardless of `up`; zero last-success timestamp alerts; fresh success stays quiet; success older than eight days alerts.
- Dashboard no longer queries `velero_backup_timestamp`.
- Cached chart render shows the node-exporter DaemonSet has the required control-plane `NoExecute` toleration.
- Existing alert routing and unrelated monitoring configuration remain unchanged.
- No deployment or cluster mutation occurs.

## Lead Review

Independent verification passed: production-rule regression tests (24 alerts), pre-fix rule failure reproduction, cached Helm chart render with both required tolerations, monitoring Kustomize render, YAML lint and shell syntax checks. The existing CI monitoring job invokes the updated validator, so no workflow/tool-install change is needed. Production approval is still required; node-exporter will add a pod on the master and may pull its existing image there. Expect the corrected stale-backup alert to expose the already-known backup gap, not prove that backups recovered. The lead interrupted a prolonged final-review loop and bounded the worker to final validation and reporting; no scope expansion occurred.

## Deployed Verification

The operator subsequently approved #142 and follow-up #143. All three exporter targets are up and all 24 custom rules now evaluate with health `ok`. Follow-up #143 added the missing `release=monitoring` selector label; valid expressions alone had not made the rules active. Grafana is healthy, and the stale-backup rule now exposes the actual approximately 39-day success gap. See `2026-10-15-runtime-selection.md`; no backup/restore run was performed.
