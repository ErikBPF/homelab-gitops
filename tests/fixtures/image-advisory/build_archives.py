"""Generate two deterministic Docker archives containing synthetic npm metadata."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(raw), 0o644, 0
            member.uid = member.gid = 0
            tar.addfile(member, io.BytesIO(raw))
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="New disposable fixture directory")
    output = parser.parse_args().output_dir
    output.mkdir(mode=0o700)
    for name, package, version in [("vulnerable", "lodash", "4.17.11"), ("control", "left-pad", "1.3.0")]:
        layer = tar_bytes({f"app/node_modules/{package}/package.json": json_bytes({"name": package, "version": version})})
        config = json_bytes({"architecture": "amd64", "os": "linux", "config": {},
                             "rootfs": {"type": "layers", "diff_ids": ["sha256:" + sha256(layer)]},
                             "history": [{"created_by": "synthetic local security fixture"}]})
        manifest = json_bytes([{"Config": sha256(config) + ".json", "RepoTags": ["synthetic-" + name + ":local"],
                                "Layers": [sha256(layer) + "/layer.tar"]}])
        raw = tar_bytes({"manifest.json": manifest, sha256(config) + ".json": config,
                         sha256(layer) + "/layer.tar": layer})
        (output / (name + ".docker.tar")).write_bytes(raw)
    print("Two synthetic archives created; no images pulled, loaded or executed.")


if __name__ == "__main__":
    main()
