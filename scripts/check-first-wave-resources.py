#!/usr/bin/env python3
import argparse
import pathlib
import sys

import yaml


def pod_spec(document):
    spec = document.get("spec", {})
    if document.get("kind") == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template", {}).get("spec")


def validate(document, source, forbid_gpu):
    errors = []
    name = document.get("metadata", {}).get("name", "<unnamed>")
    prefix = f"{source}:{document.get('kind', '<unknown>')}/{name}"

    spec = pod_spec(document)
    if spec is not None:
        for container in spec.get("initContainers", []) + spec.get("containers", []):
            resources = container.get("resources", {})
            for boundary in ("requests", "limits"):
                values = resources.get(boundary, {})
                for resource in ("cpu", "memory"):
                    if not values.get(resource):
                        errors.append(
                            f"{prefix}:{container.get('name', '<unnamed>')} missing {boundary}.{resource}"
                        )
                if forbid_gpu and any("gpu" in key.lower() for key in values):
                    errors.append(f"{prefix}:{container.get('name', '<unnamed>')} requests unavailable GPU")

    if document.get("kind") == "PersistentVolumeClaim":
        spec = document.get("spec", {})
        if not spec.get("storageClassName"):
            errors.append(f"{prefix} missing storageClassName")
        if not spec.get("resources", {}).get("requests", {}).get("storage"):
            errors.append(f"{prefix} missing requests.storage")

    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--forbid-gpu", action="store_true")
    parser.add_argument("manifests", nargs="+")
    args = parser.parse_args()

    errors = []
    for manifest in args.manifests:
        path = pathlib.Path(manifest)
        for document in yaml.safe_load_all(path.read_text()):
            if document:
                errors.extend(validate(document, path, args.forbid_gpu))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
