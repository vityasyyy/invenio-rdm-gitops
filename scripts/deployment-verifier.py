#!/usr/bin/env python3
"""Verify that Argo CD converged the tracked Applications to the triggering commit."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/vityasyyy/invenio-rdm-gitops.git"
MOVING_REVISIONS = {"main", "master", "HEAD"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BAD_HEALTH = {"Degraded", "Missing", "Progressing", "Unknown", "Suspended"}
SAMPLES = 3
INTERVAL = 30
TIMEOUT = 900
REQUEST_TIMEOUT = 15


class ConfigurationError(Exception):
    pass


class ApiError(Exception):
    pass


class FatalApiError(Exception):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep authenticated requests on the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class AppReport:
    name: str
    revisions: list[str]
    expected: list[str]
    sync: str
    health: str
    operation: str
    hooks: list[str]
    diagnostics: list[str]
    issues: list[str]

    @property
    def clean(self) -> bool:
        return not self.issues


@dataclass
class SnapshotReport:
    apps: list[AppReport]
    issues: list[str]

    @property
    def clean(self) -> bool:
        return bool(self.apps) and not self.issues and all(app.clean for app in self.apps)


@dataclass
class VerificationResult:
    passed: bool
    report: SnapshotReport | None
    samples_taken: int
    streak: int
    elapsed: float
    message: str


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _app_name(app) -> str:
    return str(_mapping(_mapping(app).get("metadata")).get("name") or "")


def _metadata_name(path: Path) -> str:
    in_metadata = False
    metadata_indent = 0
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if not in_metadata:
            if stripped == "metadata:":
                in_metadata = True
                metadata_indent = indent
            continue
        if indent <= metadata_indent:
            break
        match = re.match(r"^\s+name:\s*(.+?)\s*$", raw)
        if match:
            name = match.group(1).split("#", 1)[0].strip().strip("'\"")
            if name:
                return name
    raise ConfigurationError(f"{path}: metadata.name not found")


def load_expected_inventory(root: Path) -> list[str]:
    apps_dir = root / "argocd" / "apps"
    app_paths = sorted(apps_dir.glob("*.yaml"))
    root_path = root / "argocd" / "root.yaml"
    if not app_paths:
        raise ConfigurationError(f"{apps_dir}: no tracked Application manifests found")
    if not root_path.is_file():
        raise ConfigurationError(f"{root_path}: root Application manifest not found")
    names = [_metadata_name(path) for path in app_paths] + [_metadata_name(root_path)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigurationError("duplicate Application names: " + ", ".join(duplicates))
    return sorted(names)


def _normalize_repo(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def _source_expectation(source: dict, release: str) -> tuple[str | None, str | None]:
    if source.get("chart"):
        target = str(source.get("targetRevision") or "")
        return (target, None) if target else (None, "chart source has no targetRevision")
    repo = str(source.get("repoURL") or "")
    if not repo:
        return None, "source has no repoURL"
    if _normalize_repo(repo) != _normalize_repo(REPO_URL):
        return None, f"external repository source {repo!r} cannot be verified"
    target = str(source.get("targetRevision") or "")
    if target in MOVING_REVISIONS:
        return release, None
    if SHA_RE.fullmatch(target.lower()):
        return target.lower(), None
    return None, f"repository source targetRevision {target!r} is not a commit"


def _expected_revisions(app: dict, release: str) -> list[tuple[str | None, str | None]]:
    spec = _mapping(app.get("spec"))
    sources = spec.get("sources")
    if isinstance(sources, list) and sources:
        return [_source_expectation(_mapping(source), release) for source in sources]
    source = spec.get("source")
    if not isinstance(source, dict):
        return [(None, "application has no spec.source")]
    return [_source_expectation(source, release)]


def _observed_revisions(app: dict) -> tuple[list[str], str | None]:
    spec = _mapping(app.get("spec"))
    sync = _mapping(_mapping(app.get("status")).get("sync"))
    sources = spec.get("sources")
    if isinstance(sources, list) and sources:
        revisions = sync.get("revisions")
        if not isinstance(revisions, list):
            return [], "multi-source application has no status.sync.revisions"
        return [str(revision) for revision in revisions], None
    revision = sync.get("revision")
    if not revision:
        return [], "application has no status.sync.revision"
    return [str(revision)], None


def _key(resource: dict) -> tuple[str, str, str, str]:
    return tuple(str(resource.get(field) or "") for field in ("group", "kind", "namespace", "name"))


def _label(resource: dict) -> str:
    kind = resource.get("kind") or "resource"
    name = resource.get("name") or "?"
    namespace = resource.get("namespace")
    return f"{kind}/{namespace}/{name}" if namespace else f"{kind}/{name}"


def _short(value, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tree_problem(tree) -> str | None:
    if tree is None:
        return None
    if not isinstance(tree, dict):
        return "resource tree payload is not an object"
    if not isinstance(tree.get("nodes"), list):
        return "resource tree payload has no nodes list"
    if not all(isinstance(node, dict) for node in tree["nodes"]):
        return "resource tree payload has malformed nodes"
    return None


def _operation_targets(operation_state: dict) -> list[str] | None:
    sync = _mapping(_mapping(operation_state.get("operation")).get("sync"))
    for value in (
        sync.get("revisions"),
        sync.get("revision"),
        _mapping(operation_state.get("syncResult")).get("revisions"),
        _mapping(operation_state.get("syncResult")).get("revision"),
    ):
        if isinstance(value, list) and value:
            return [str(revision) for revision in value]
        if isinstance(value, str) and value:
            return [value]
    return None


def _hook_state(
    app: dict, tree: dict | None, historical: bool
) -> tuple[list[str], list[str], list[str]]:
    status = _mapping(app.get("status"))
    operation = _mapping(status.get("operationState"))
    results = _mapping(operation.get("syncResult")).get("resources")
    hook_results = (
        [item for item in results if isinstance(item, dict) and item.get("hookType")]
        if isinstance(results, list)
        else []
    )

    health: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    hook_keys = {_key(result) for result in hook_results}
    resources = status.get("resources")
    for resource in resources if isinstance(resources, list) else []:
        if isinstance(resource, dict) and resource.get("hook"):
            key = _key(resource)
            hook_keys.add(key)
            value = str(_mapping(resource.get("health")).get("status") or "")
            if value:
                health[key] = (_label(resource), value)
    nodes = _mapping(tree).get("nodes")
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and _key(node) in hook_keys:
            value = str(_mapping(node.get("health")).get("status") or "")
            if value:
                health[_key(node)] = (_label(node), value)

    states: list[str] = []
    issues: list[str] = []
    notes: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()

    for result in hook_results:
        key = _key(result)
        seen.add(key)
        hook_type = result.get("hookType") or "Hook"
        label = _label(result)
        phase = str(result.get("hookPhase") or "")
        message = _short(result.get("message"))
        live = health.get(key, ("", ""))[1]
        states.append(f"{hook_type} {label}={phase or live or 'unknown'}")
        if historical:
            if phase not in ("Succeeded", ""):
                notes.append(f"historical hook {hook_type} {label} {phase}" + (f": {message}" if message else ""))
            elif not phase:
                notes.append(f"historical hook {hook_type} {label} phase unavailable")
            if live in ("Degraded", "Missing"):
                issues.append(f"hook {hook_type} {label} health {live}")
            elif live in BAD_HEALTH:
                notes.append(f"historical hook {hook_type} {label} health {live}")
            continue
        if phase in ("Failed", "Error"):
            issues.append(f"hook {hook_type} {label} {phase}" + (f": {message}" if message else ""))
        elif phase in ("Running", "Terminating") or (phase and phase != "Succeeded"):
            issues.append(f"hook {hook_type} {label} {phase or 'unknown'}")
        if live in BAD_HEALTH:
            issues.append(f"hook {hook_type} {label} health {live}")
        elif not live and phase != "Succeeded":
            issues.append(f"hook {hook_type} {label} health unavailable")

    for key, (label, live) in health.items():
        if key in seen:
            continue
        if live in ("Degraded", "Missing"):
            issues.append(f"hook resource {label} health {live}")
        elif live in BAD_HEALTH:
            if historical:
                notes.append(f"historical hook resource {label} health {live}")
            else:
                issues.append(f"hook resource {label} health {live}")

    return states, issues, notes


def evaluate_app(app: dict, tree: dict | None, release: str, tree_error: str | None = None) -> AppReport:
    name = _app_name(app) or "<unnamed>"
    issues: list[str] = []
    status = app.get("status")
    if not isinstance(status, dict):
        status = {}
        issues.append("application has no status")

    sync_object = _mapping(status.get("sync"))
    health_object = _mapping(status.get("health"))
    if not isinstance(status.get("sync"), dict) and "application has no status" not in issues:
        issues.append("sync status malformed")
    if not isinstance(status.get("health"), dict) and "application has no status" not in issues:
        issues.append("health status malformed")
    sync = str(sync_object.get("status") or "<missing>")
    health = str(health_object.get("status") or "<missing>")
    if sync != "Synced":
        issues.append(f"sync {sync}")
    if health != "Healthy":
        issues.append(f"health {health}")

    pairs = _expected_revisions(app, release)
    expected = [value or "-" for value, _ in pairs]
    observed, problem = _observed_revisions(app)
    if problem:
        issues.append(problem)
    if len(observed) != len(pairs):
        issues.append(f"observed {len(observed)} source revisions for {len(pairs)} sources")
    for index, (value, source_problem) in enumerate(pairs):
        if source_problem:
            issues.append(f"source {index}: {source_problem}")
        elif index < len(observed) and observed[index] != value:
            issues.append(f"source {index}: revision {observed[index] or '<none>'} != expected {value}")

    operation = "-"
    diagnostics: list[str] = []
    historical = False
    operation_state = status.get("operationState")
    if isinstance(operation_state, dict):
        phase = str(operation_state.get("phase") or "")
        operation = phase or "unknown"
        targets = _operation_targets(operation_state)
        expected_values = [value for value, _ in pairs]
        target_text = ", ".join(targets) if targets else "<unknown>"
        expected_text = ", ".join(value or "-" for value in expected_values)
        message = _short(operation_state.get("message"))
        if phase in ("Running", "Terminating"):
            issues.append(f"operation {phase}")
        elif phase in ("Failed", "Error"):
            historical = bool(targets and all(expected_values) and targets != expected_values)
            if historical:
                diagnostics.append(
                    f"historical operation {phase}"
                    + (f" finished {operation_state.get('finishedAt')}" if operation_state.get("finishedAt") else "")
                    + f" for target {target_text} (expected {expected_text}); not treated as a current failure"
                    + (f": {message}" if message else "")
                )
            else:
                issues.append(
                    f"operation {phase}"
                    + ("" if targets else "; target revision unavailable")
                    + (f": {message}" if message else "")
                )
        elif phase == "Succeeded":
            historical = bool(targets and all(expected_values) and targets != expected_values)
        elif not phase:
            if operation_state.get("finishedAt"):
                historical = True
                diagnostics.append("historical operation with no phase; not treated as a current failure")
            else:
                issues.append("operation phase missing")
        else:
            issues.append(f"operation phase {phase}")
    elif operation_state is not None:
        operation = "malformed"
        historical = True
        issues.append("operation state malformed")

    if app.get("operation"):
        operation = "Requested"
        issues.append("operation requested but not started")

    if tree_error:
        issues.append(f"resource tree unavailable: {tree_error}")
    else:
        tree_problem = _tree_problem(tree)
        if tree_problem:
            tree = None
            issues.append(f"resource tree unavailable: {tree_problem}")
    hooks, hook_issues, hook_notes = _hook_state(app, tree, historical)
    issues.extend(hook_issues)
    diagnostics.extend(hook_notes)
    return AppReport(name, observed, expected, sync, health, operation, hooks, diagnostics, issues)


def evaluate_snapshot(
    items, trees: dict[str, dict], expected_names: list[str], release: str,
    tree_errors: dict[str, str] | None = None,
) -> SnapshotReport:
    issues: list[str] = []
    apps: list[AppReport] = []
    tree_errors = tree_errors or {}
    if not expected_names:
        issues.append("expected application inventory is empty")
    if not isinstance(items, list):
        issues.append("ArgoCD application list is malformed: items is not a list")
        items = []
    elif not items:
        issues.append("ArgoCD application list is empty")

    by_name: dict[str, dict] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(f"ArgoCD application item {index} is malformed")
            continue
        name = _app_name(item)
        if not name:
            issues.append(f"ArgoCD application item {index} has no metadata.name")
            continue
        if name in by_name:
            issues.append(f"ArgoCD application list contains duplicate name {name}")
            continue
        by_name[name] = item

    missing = [name for name in expected_names if name not in by_name]
    if missing:
        issues.append("expected applications missing from ArgoCD API: " + ", ".join(missing))
    for name in expected_names:
        app = by_name.pop(name, None)
        if app is None:
            apps.append(AppReport(name, [], [], "<missing>", "<missing>", "-", [], [], ["application missing from ArgoCD API"]))
        else:
            apps.append(evaluate_app(app, trees.get(name), release, tree_errors.get(name)))
    return SnapshotReport(apps, issues)


def verify(
    client, expected_names: list[str], release: str, samples: int = SAMPLES,
    interval: int = INTERVAL, timeout: int = TIMEOUT, sleep=time.sleep, clock=time.monotonic,
) -> VerificationResult:
    started = clock()
    deadline = started + timeout
    streak = taken = 0
    report: SnapshotReport | None = None
    message = ""

    while True:
        elapsed = clock() - started
        if elapsed >= timeout:
            return VerificationResult(False, report, taken, streak, elapsed, f"timed out after {timeout:.0f}s")
        taken += 1
        try:
            report = client.snapshot(expected_names, release, deadline=deadline)
        except FatalApiError as exc:
            return VerificationResult(False, report, taken, streak, clock() - started, str(exc))
        except ApiError as exc:
            if report is None:
                report = SnapshotReport([], [])
            report.issues = [f"could not read ArgoCD API: {exc}"]

        elapsed = clock() - started
        if report.clean and elapsed >= timeout:
            report.issues.append(f"clean result arrived after the {timeout:.0f}s deadline")
        if report.clean:
            streak += 1
            message = f"{streak}/{samples} consecutive clean samples"
        else:
            streak = 0
            message = "deployment is not clean"

        print(f"sample {taken}: clean={str(report.clean).lower()} streak={streak}/{samples} elapsed={elapsed:.0f}s")
        for issue in report.issues:
            print(f"  ! {issue}")
        for app in report.apps:
            if not app.clean:
                print(f"  - {app.name}: {'; '.join(app.issues)}")
            for note in app.diagnostics:
                print(f"  note {app.name}: {note}")

        if streak >= samples:
            return VerificationResult(True, report, taken, streak, elapsed, message)
        remaining = timeout - elapsed
        if remaining <= 0:
            return VerificationResult(False, report, taken, streak, elapsed, f"timed out after {timeout:.0f}s")
        sleep(min(interval, remaining))


def normalize_server(value: str) -> str:
    server = (value or "").strip().rstrip("/")
    if not server:
        raise ConfigurationError("ARGOCD_SERVER is required")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", server):
        server = "https://" + server
    parsed = urllib.parse.urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigurationError("ARGOCD_SERVER must be an http(s) URL with a host")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ConfigurationError("ARGOCD_SERVER must use https")
    return server.rstrip("/")


class ArgoCDClient:
    def __init__(self, server: str, token: str, request_timeout: int = REQUEST_TIMEOUT):
        self.server = normalize_server(server)
        self.token = token
        self.request_timeout = request_timeout
        self.context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.context),
            _NoRedirectHandler(),
        )

    def _get(self, path: str, deadline: float | None = None):
        request_timeout = self.request_timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiError(f"verification deadline exceeded before {path}")
            request_timeout = min(request_timeout, remaining)
        request = urllib.request.Request(
            self.server + path,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=request_timeout) as response:
                status, body = getattr(response, "status", 200), response.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise FatalApiError(f"ArgoCD API returned HTTP {code} for {path}") from None
            raise ApiError(f"ArgoCD API returned HTTP {code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiError(f"ArgoCD API request failed for {path}: {getattr(exc, 'reason', exc)}") from None
        if status != 200:
            raise ApiError(f"ArgoCD API returned HTTP {status} for {path}")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(f"ArgoCD API returned malformed JSON for {path}") from None

    def snapshot(
        self, expected_names: list[str], release: str, deadline: float | None = None
    ) -> SnapshotReport:
        payload = self._get("/api/v1/applications", deadline)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return SnapshotReport([], ["ArgoCD application list is malformed: items is not a list"])

        names = []
        for item in items:
            name = _app_name(item)
            if name in expected_names and name not in names:
                names.append(name)

        trees: dict[str, dict] = {}
        tree_errors: dict[str, str] = {}
        for name in names:
            if deadline is not None and time.monotonic() >= deadline:
                tree_errors[name] = "verification deadline exceeded"
                continue
            path = f"/api/v1/applications/{urllib.parse.quote(name, safe='')}/resource-tree"
            try:
                tree = self._get(path, deadline)
            except ApiError as exc:
                tree_errors[name] = str(exc)
                continue
            problem = _tree_problem(tree)
            if tree is None:
                tree_errors[name] = "resource tree payload is empty"
            elif problem:
                tree_errors[name] = problem
            else:
                trees[name] = tree
        return evaluate_snapshot(items, trees, expected_names, release, tree_errors)


def _markdown(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_summary(result: VerificationResult, release: str) -> str:
    lines = [
        "## Deployment verification",
        "",
        f"- Triggering revision: `{release}`",
        f"- Result: **{'PASS' if result.passed else 'FAIL'}**",
        f"- Samples: {result.samples_taken} taken, {result.streak} consecutive clean at end",
        f"- Elapsed: {result.elapsed:.1f}s",
        f"- Detail: {result.message}",
    ]
    report = result.report
    if report is not None:
        if report.issues:
            lines.extend(["", "### Snapshot issues", ""])
            lines.extend(f"- {issue}" for issue in report.issues)
        if report.apps:
            lines.extend([
                "",
                "| Application | Revision | Expected | Sync | Health | Operation | Hooks | Diagnostics | Issues |",
                "|---|---|---|---|---|---|---|---|---|",
            ])
            for app in report.apps:
                cells = [
                    app.name, ", ".join(app.revisions) or "-", ", ".join(app.expected) or "-",
                    app.sync, app.health, app.operation, "; ".join(app.hooks) or "-",
                    "; ".join(app.diagnostics) or "-", "; ".join(app.issues) or "clean",
                ]
                lines.append("| " + " | ".join(_markdown(cell) for cell in cells) + " |")
        if any(
            issue.startswith("source ") and " != expected " in issue
            for app in report.apps
            for issue in app.issues
        ):
            lines.extend([
                "",
                "> Revision mismatch means ArgoCD did not report the triggering commit. "
                "If a newer push landed during this run, this run is superseded; the newer "
                "run must prove the newer revision. A mismatched or superseded run does not pass.",
            ])
    return "\n".join(lines) + "\n"


def write_summary(path: str | None, text: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        print(f"warning: could not write GitHub summary: {exc}", file=sys.stderr)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv:
        print(f"unknown argument: {argv[0]}", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    try:
        expected = load_expected_inventory(root)
        release = os.environ.get("GITHUB_SHA", "").strip().lower()
        if not SHA_RE.fullmatch(release):
            raise ConfigurationError("GITHUB_SHA must be a full 40-character commit SHA")
        token = os.environ.get("ARGOCD_AUTH_TOKEN", "")
        if not token:
            raise ConfigurationError("ARGOCD_AUTH_TOKEN is required")
        client = ArgoCDClient(os.environ.get("ARGOCD_SERVER", ""), token)
    except ConfigurationError as exc:
        print(f"deployment verification configuration error: {exc}", file=sys.stderr)
        return 1

    result = verify(client, expected, release)
    summary = render_summary(result, release)
    print(summary)
    write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), summary)
    if result.passed:
        print(f"deployment verification passed for {release}")
        return 0
    print(f"deployment verification failed for {release}: {result.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
