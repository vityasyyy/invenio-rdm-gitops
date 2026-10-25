import argparse
import json
import subprocess
from pathlib import Path

import yaml


REPOSITORY = "https://github.com/vityasyyy/invenio-rdm-gitops.git"


def render_commands(root, charts=None):
    commands = []
    for path in sorted((root / "argocd/apps").glob("*.yaml")):
        app = yaml.safe_load(path.read_text())
        name = app["metadata"]["name"]
        spec = app["spec"]
        sources = spec.get("sources", [spec.get("source", {})])
        refs = {source["ref"] for source in sources
                if source.get("repoURL") == REPOSITORY and "ref" in source}
        for index, source in enumerate(sources):
            if "chart" in source:
                helm = source.get("helm", {})
                if any(key in helm for key in ("values", "valuesObject", "parameters", "fileParameters")):
                    raise ValueError(f"{name}: inline Helm overrides are not supported; use authoritative valueFiles")
                chart = source["chart"]
                command = ["helm", "template", helm.get("releaseName", name), chart,
                           "--namespace", spec["destination"]["namespace"], "--include-crds"]
                if charts:
                    archive = charts / f"{chart}-{str(source['targetRevision']).lstrip('v')}.tgz"
                    if not archive.is_file():
                        raise ValueError(f"{name}: missing cached chart {archive}")
                    command[3] = str(archive)
                else:
                    command += ["--repo", source["repoURL"], "--version", str(source["targetRevision"])]
                for value in helm.get("valueFiles", []):
                    ref, separator, relative = value.partition("/")
                    if not separator or not ref.startswith("$") or ref[1:] not in refs:
                        raise ValueError(f"{name}: unsupported values reference {value}")
                    file = (root / relative).resolve()
                    if not file.is_relative_to(root.resolve()) or not file.is_file():
                        raise ValueError(f"{name}: missing values file {value}")
                    command += ["-f", str(file)]
                commands.append((f"helm_{name}_{index}.yaml", command))
            elif source.get("path"):
                if source.get("repoURL") != REPOSITORY:
                    raise ValueError(f"{name}: cannot render external Git source locally")
                directory = root / source["path"]
                if not (directory / "kustomization.yaml").is_file():
                    raise ValueError(f"{name}: missing kustomization at {directory}")
                commands.append((f"kustomize_{name}_{index}.yaml", ["kustomize", "build", str(directory)]))
    if not commands:
        raise ValueError("No Application render sources found")
    return commands


def main():
    parser = argparse.ArgumentParser(description="Render the sources selected by argocd/apps without configuration mirrors")
    parser.add_argument("--output", type=Path, default=Path("rendered"))
    parser.add_argument("--charts", type=Path, help="Offline directory of pinned chart-version.tgz archives")
    parser.add_argument("--list", action="store_true", help="Validate sources and print commands without rendering")
    args = parser.parse_args()
    commands = render_commands(Path(__file__).resolve().parents[1], args.charts)
    if args.list:
        print(json.dumps(commands, indent=2))
        return
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"Output directory must be empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, command in commands:
        print(f"Rendering {name}", flush=True)
        with (args.output / name).open("w") as output:
            subprocess.run(command, stdout=output, check=True, timeout=180)


if __name__ == "__main__":
    main()
