"""Prepare the pinned public Alloy OCI archive in a new output directory."""

import argparse
import hashlib
import json
import signal
import ssl
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

INDEX = "sha256:89a5a320168387336b9ac3c850609c3ba7757eadd4ae5f1d819deb35e9854b18"
MANIFEST = "sha256:2fc2ae17ed3b712e37047724efd1cdf7a4124016c0b7522ac469dab0ddf8a8d7"
CONFIG = "sha256:a987a8dd42a1797dde3cd2badd869b2973f8ecb5868619c50e5f1f183ad94843"
ARCHIVE_SHA256 = "3570ce6b7c660cdf6988f57c4abb7044faef46f52e09f7cd65225a4cd130b4a7"
IMAGE_NAME = "docker.io/grafana/alloy:replica-r21"
ARCHIVE_NAME = "alloy-linux-amd64.oci.tar"
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def require(condition, message):
    if not condition:
        raise ValueError(message)


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        require(
            target.scheme == "https"
            and not target.username
            and not target.password
            and target.port in (None, 443)
            and any(
                target.hostname == host or (target.hostname or "").endswith("." + host)
                for host in ("docker.com", "docker.io", "cloudflarestorage.com")
            ),
            "Unexpected artifact redirect",
        )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Public CDN redirects do not need the registry's anonymous pull token.
        redirected.remove_header("Authorization")
        return redirected


def download(root):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=CA_BUNDLE)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        PublicRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )
    started = time.monotonic()

    def request(url, headers=None):
        require(time.monotonic() - started < 600, "Download deadline exceeded")
        return opener.open(
            urllib.request.Request(url, headers=headers or {}), timeout=45
        )

    query = urllib.parse.urlencode(
        {"service": "registry.docker.io", "scope": "repository:grafana/alloy:pull"}
    )
    with request("https://auth.docker.io/token?" + query) as response:
        token_response = response.read(1048577)
    require(len(token_response) <= 1048576, "Oversized token response")
    token = json.loads(token_response)["token"]
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": ", ".join(
            [
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ]
        ),
    }
    blobs = root / "layout/blobs/sha256"
    blobs.mkdir(parents=True)

    def fetch(kind, digest, size):
        target = blobs / digest.removeprefix("sha256:")
        partial = target.with_suffix(".partial")
        count = 0
        checksum = hashlib.sha256()
        url = "https://registry-1.docker.io/v2/grafana/alloy/" + kind + "/" + digest
        with request(url, headers) as response, partial.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                require(count <= size, "Artifact exceeds pinned size")
                require(time.monotonic() - started < 600, "Download deadline exceeded")
                output.write(chunk)
                checksum.update(chunk)
        require(count == size, "Artifact size mismatch")
        require("sha256:" + checksum.hexdigest() == digest, "Artifact digest mismatch")
        partial.rename(target)
        return target

    fetch("manifests", INDEX, 3117)
    manifest = json.loads(fetch("manifests", MANIFEST, 1622).read_bytes())
    require(manifest["config"]["digest"] == CONFIG, "Wrong config")
    require(len(manifest["layers"]) == 7, "Wrong layer count")
    require(
        sum(layer["size"] for layer in manifest["layers"]) == 223615801,
        "Wrong layer size",
    )
    for blob in [manifest["config"], *manifest["layers"]]:
        fetch("blobs", blob["digest"], blob["size"])


def write_archive(root):
    layout = root / "layout"
    blobs = layout / "blobs/sha256"

    def verify(digest, size=None):
        path = blobs / digest.removeprefix("sha256:")
        require(path.is_file() and not path.is_symlink(), "Unsafe blob")
        require(size is None or path.stat().st_size == size, "Blob size mismatch")
        with path.open("rb") as stream:
            require(
                "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == digest,
                "Blob digest mismatch",
            )
        return path

    index = json.loads(verify(INDEX, 3117).read_bytes())
    selected = [
        entry
        for entry in index["manifests"]
        if entry.get("platform", {}).get("os") == "linux"
        and entry.get("platform", {}).get("architecture") == "amd64"
    ]
    require(
        len(selected) == 1 and selected[0]["digest"] == MANIFEST,
        "Wrong platform mapping",
    )
    manifest = json.loads(verify(MANIFEST, selected[0]["size"]).read_bytes())
    require(manifest["config"]["digest"] == CONFIG, "Wrong config")
    config = json.loads(verify(CONFIG, manifest["config"]["size"]).read_bytes())
    require(
        config["os"] == "linux" and config["architecture"] == "amd64",
        "Wrong config platform",
    )
    require(len(manifest["layers"]) == 7, "Wrong layer count")
    for layer in manifest["layers"]:
        verify(layer["digest"], layer["size"])
    descriptor = dict(selected[0])
    descriptor["annotations"] = {"org.opencontainers.image.ref.name": IMAGE_NAME}
    (layout / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [descriptor],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    archive = root / ARCHIVE_NAME
    with tarfile.open(archive, "x", format=tarfile.USTAR_FORMAT) as output:
        for path in sorted(layout.rglob("*")):
            require(not path.is_symlink(), "Archive symlink forbidden")
            if path.is_dir():
                continue
            require(path.is_file(), "Archive contains a special file")
            info = tarfile.TarInfo(path.relative_to(layout).as_posix())
            info.size = path.stat().st_size
            info.mode = 0o644
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with path.open("rb") as stream:
                output.addfile(info, stream)
    with archive.open("rb") as stream:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest() == ARCHIVE_SHA256,
            "Archive checksum mismatch",
        )
    return archive


def deadline(_signal, _frame):
    raise TimeoutError("Image preparation deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_directory", type=Path, help="New directory; existing paths are rejected"
    )
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(600)
    try:
        args.output_directory.mkdir(mode=0o700)
        download(args.output_directory)
        archive = write_archive(args.output_directory)
        print(
            json.dumps(
                {
                    "archive": str(archive),
                    "sha256": ARCHIVE_SHA256,
                    "image": IMAGE_NAME,
                    "source_index": INDEX,
                    "platform_manifest": MANIFEST,
                    "platform": "linux/amd64",
                }
            )
        )
        return 0
    except Exception:
        # Network errors may contain signed redirect URLs; never echo them.
        print("ALLOY_IMAGE_PREPARATION_FAILED", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
