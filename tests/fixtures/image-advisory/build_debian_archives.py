"""Generate trusted Debian package metadata fixtures; never pull or run images."""

import argparse
from pathlib import Path
from urllib.parse import quote

from build_archives import json_bytes, sha256, tar_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="New disposable fixture directory")
    output = parser.parse_args().output_dir
    output.mkdir(mode=0o700)
    fixtures = [("debian-vulnerable", "openssl", "3.0.9-1", "amd64", False),
                ("debian-control", "base-files", "12.4+deb12u12", "amd64", False),
                ("debian-mixed", "openssl", "3.0.9-1", "amd64", True),
                ("debian-epoch", "openssl", "2:3.0.9-1", "all", False)]
    for label, name, version, architecture, mixed in fixtures:
        files = {"etc/os-release": b'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n',
                 "etc/debian_version": b"12.0\n",
                 "var/lib/dpkg/status": (f"Package: {name}\nStatus: install ok installed\nPriority: optional\n"
                                         f"Section: utils\nInstalled-Size: 1\nMaintainer: Synthetic Fixture\n"
                                         f"Architecture: {architecture}\nVersion: {version}\n"
                                         "Description: synthetic local scanner metadata\n\n").encode()}
        epoch, bare = version.split(":", 1) if ":" in version else (None, version)
        purl = f"pkg:deb/debian/{name}@{quote(bare, safe='.-~')}?arch={architecture}&distro=debian-12.0"
        if epoch:
            purl += "&epoch=" + epoch
        components = [{"type": "operating-system", "name": "debian", "version": "12.0"},
                      {"type": "library", "name": name, "version": version, "purl": purl}]
        if mixed:
            files["app/node_modules/lodash/package.json"] = json_bytes({"name": "lodash", "version": "4.17.11"})
            components.append({"type": "library", "name": "lodash", "version": "4.17.11", "purl": "pkg:npm/lodash@4.17.11"})
        layer = tar_bytes(files)
        config = json_bytes({"architecture": "amd64", "os": "linux", "config": {},
                             "rootfs": {"type": "layers", "diff_ids": ["sha256:" + sha256(layer)]},
                             "history": [{"created_by": "synthetic local security fixture"}]})
        manifest = json_bytes([{"Config": sha256(config) + ".json", "RepoTags": ["synthetic-" + label + ":local"],
                                "Layers": [sha256(layer) + "/layer.tar"]}])
        raw = tar_bytes({"manifest.json": manifest, sha256(config) + ".json": config,
                         sha256(layer) + "/layer.tar": layer})
        (output / (label + ".docker.tar")).write_bytes(raw)
        (output / (label + ".cdx.json")).write_bytes(json_bytes({
            "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1, "components": components}))
    print("Four synthetic Debian archives and expected inventories created; no images pulled, loaded or executed.")


if __name__ == "__main__":
    main()
