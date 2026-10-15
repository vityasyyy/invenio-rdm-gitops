#!/usr/bin/env python3
import sys
from pathlib import Path

import yaml


EXPECTED_EXCLUSION = {
    "apiGroups": ["velero.io"],
    "kinds": ["Backup", "Restore"],
    "clusters": ["https://kubernetes.default.svc"],
}


def validate(root):
    errors = []

    def load(relative_path):
        try:
            return yaml.safe_load((root / relative_path).read_text()) or {}
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"{relative_path}: {error}")
            return {}

    argocd = load("k8s/infra/argocd/patches/argocd-cm.yaml")
    if (
        argocd.get("kind") != "ConfigMap"
        or (argocd.get("metadata") or {}).get("name") != "argocd-cm"
    ):
        errors.append("ArgoCD settings must be in the argocd-cm ConfigMap")
    try:
        exclusions = yaml.safe_load(
            (argocd.get("data") or {}).get("resource.exclusions", "[]")
        ) or []
    except yaml.YAMLError as error:
        exclusions = []
        errors.append(f"argocd-cm resource.exclusions: {error}")
    if not isinstance(exclusions, list):
        exclusions = []
    if exclusions.count(EXPECTED_EXCLUSION) != 1:
        errors.append(
            "argocd-cm resource.exclusions must include exactly one local velero.io Backup/Restore rule"
        )
    for rule in exclusions:
        if not isinstance(rule, dict):
            errors.append("argocd-cm resource.exclusions entries must be mappings")
            continue
        groups = rule.get("apiGroups")
        kinds = rule.get("kinds")
        if any(
            not isinstance(patterns, list)
            or not patterns
            or any(
                not isinstance(pattern, str)
                or not pattern
                or any(token in pattern for token in "*?[")
                for pattern in patterns
            )
            for patterns in (groups, kinds)
        ):
            errors.append(
                "argocd-cm resource.exclusions must not wildcard apiGroups or kinds"
            )
            continue
        if (
            rule != EXPECTED_EXCLUSION
            and "velero.io" in groups
            and {"Backup", "Restore", "Schedule"}.intersection(kinds)
        ):
            errors.append(
                "argocd-cm may not exclude Velero Backup, Restore, or Schedule outside the scoped rule"
            )

    kustomization = load("k8s/infra/velero/kustomization.yaml")
    if "backup-schedule.yaml" not in (kustomization.get("resources") or []):
        errors.append("Velero kustomization must keep backup-schedule.yaml Git-managed")
    schedule = load("k8s/infra/velero/backup-schedule.yaml")
    if schedule.get("apiVersion") != "velero.io/v1" or schedule.get("kind") != "Schedule":
        errors.append("backup-schedule.yaml must remain a velero.io/v1 Schedule")

    return errors


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate(root)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} Velero ownership violation(s)")
        return 1
    print("OK: Velero Backup/Restore exclusions are scoped and Schedule remains Git-managed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
