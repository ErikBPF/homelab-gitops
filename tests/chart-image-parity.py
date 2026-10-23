#!/usr/bin/env python3
"""Wrapper charts that pin an image must keep chart and image in step.

Renovate's shared preset disables Harbor image updates (the registry is not
reachable from GitHub runners), so a wrapper's Helm chart can move while its
pinned image stays behind. That is exactly the 4.13.4 -> 4.13.5 incident: the
chart passed flags the pinned binary rejected, and the CSI controller
crash-looped after auto-sync deployed it.

Each pairing declares the wrapper directory, the values key holding the image
tag, and the chart dependency whose version must match that tag.
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# directory -> (chart dependency name, values key, image key)
PAIRS = {
    "platform/csi-driver-nfs": ("csi-driver-nfs", "csi-driver-nfs", "nfs"),
}


def version_of(value):
    match = re.match(r"v?(\d+\.\d+\.\d+)", str(value))
    assert match, f"unparseable version: {value}"
    return match.group(1)


def versions(directory, dependency_name, values_key, image_key):
    wrapper = ROOT / directory
    chart = yaml.safe_load((wrapper / "Chart.yaml").read_text())
    values = yaml.safe_load((wrapper / "values.yaml").read_text())
    dependency = next(
        dep for dep in chart["dependencies"] if dep["name"] == dependency_name
    )
    tag = values[values_key]["image"][image_key]["tag"]
    return version_of(dependency["version"]), version_of(tag)


def main():
    # The comparison must be able to fail, and must read both shapes.
    assert version_of("v4.13.5@sha256:" + "0" * 64) == "4.13.5"
    assert version_of("4.13.4") == "4.13.4"

    failures = []
    for directory, (dependency, values_key, image_key) in PAIRS.items():
        chart_version, image_version = versions(directory, dependency, values_key, image_key)
        if chart_version != image_version:
            failures.append(
                f"{directory}: chart is {chart_version}, pinned image is {image_version}"
            )
    assert not failures, "chart/image parity broken:\n" + "\n".join(failures)
    print("chart-image parity OK")


if __name__ == "__main__":
    main()
