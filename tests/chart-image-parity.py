#!/usr/bin/env python3
"""Wrapper charts that pin an image must keep chart and image in step.

Renovate's shared preset disables Harbor image updates (the registry is not
reachable from GitHub runners), so a wrapper's Helm chart can move while its
pinned image stays behind. That is exactly the csi-driver-nfs 4.13.4 -> 4.13.5
incident: the chart passed flags the pinned binary rejected and the controller
crash-looped once auto-sync deployed it.

Two invariants, chosen per wrapper:

- chart version == image version  (the chart version tracks the app version)
- chart appVersion == image version (the chart versions independently)

The appVersion check reads the vendored dependency that `helm dependency build`
leaves in `<wrapper>/charts/`, which `scripts/validate.sh` produces before it
runs this contract.
"""

import pathlib
import re
import tarfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# directory -> (chart dependency, values key, image key)
CHART_VERSION_PAIRS = {
    "platform/csi-driver-nfs": ("csi-driver-nfs", "csi-driver-nfs", "nfs"),
}

# directory -> (chart dependency, values key, image key)
APP_VERSION_PAIRS = {
    "platform/traefik": ("traefik", "traefik", "traefik"),
}


def version_of(value):
    match = re.match(r"v?(\d+\.\d+\.\d+)", str(value))
    assert match, f"unparseable version: {value}"
    return match.group(1)


def wrapper_chart(directory):
    wrapper = ROOT / directory
    chart = yaml.safe_load((wrapper / "Chart.yaml").read_text())
    values = yaml.safe_load((wrapper / "values.yaml").read_text())
    return wrapper, chart, values


def dependency(chart, name):
    return next(dep for dep in chart["dependencies"] if dep["name"] == name)


def image_tag(values, values_key, image_key):
    image = values[values_key]["image"]
    if "tag" in image:
        return image["tag"]
    return image[image_key]["tag"]


def vendored_app_version(wrapper, name, version):
    tarball = wrapper / "charts" / f"{name}-{version}.tgz"
    assert tarball.is_file(), (
        f"{tarball} missing — run `helm dependency build {wrapper.name}` first"
    )
    with tarfile.open(tarball) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith("Chart.yaml"))
        chart = yaml.safe_load(archive.extractfile(member))
    return chart["appVersion"]


def main():
    # The comparison must be able to fail, and must read both tag shapes.
    assert version_of("v4.13.5@sha256:" + "0" * 64) == "4.13.5"
    assert version_of("v3.7.14") == "3.7.14"

    failures = []
    for directory, (name, values_key, image_key) in CHART_VERSION_PAIRS.items():
        wrapper, chart, values = wrapper_chart(directory)
        chart_version = version_of(dependency(chart, name)["version"])
        pinned = version_of(image_tag(values, values_key, image_key))
        if chart_version != pinned:
            failures.append(
                f"{directory}: chart is {chart_version}, pinned image is {pinned}"
            )

    for directory, (name, values_key, image_key) in APP_VERSION_PAIRS.items():
        wrapper, chart, values = wrapper_chart(directory)
        version = str(dependency(chart, name)["version"])
        app_version = version_of(vendored_app_version(wrapper, name, version))
        pinned = version_of(image_tag(values, values_key, image_key))
        if app_version != pinned:
            failures.append(
                f"{directory}: chart {version} apps {app_version}, pinned image is {pinned}"
            )

    assert not failures, "chart/image parity broken:\n" + "\n".join(failures)
    print("chart-image parity OK")


if __name__ == "__main__":
    main()
