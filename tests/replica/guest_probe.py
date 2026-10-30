"""Read a known replica API object without exposing its token or response body."""

import argparse
import http.client
import json
import os
from pathlib import Path
import ssl
import stat
from urllib.parse import urlsplit


def credentials(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise ValueError("unsafe kubeconfig")
        text = stream.read(65537)
    if len(text) > 65536:
        raise ValueError("oversized kubeconfig")
    config = json.loads(text)
    if config.get("apiVersion") != "v1" or config.get("kind") != "Config":
        raise ValueError("not a kubeconfig")
    if any(len(config[key]) != 1 for key in ("clusters", "contexts", "users")):
        raise ValueError("ambiguous identity")
    context = config["contexts"][0]
    cluster = config["clusters"][0]
    user = config["users"][0]
    if (config["current-context"] != context["name"]
            or context["context"]["cluster"] != cluster["name"]
            or context["context"]["user"] != user["name"]):
        raise ValueError("identity mismatch")
    connection = cluster["cluster"]
    if set(connection) != {"server", "certificate-authority"} or connection["server"] != "https://127.0.0.1:6443":
        raise ValueError("not the replica API")
    if set(user["user"]) != {"token"}:
        raise ValueError("unsupported authentication")
    token = user["user"]["token"]
    if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("invalid token")
    ca = connection["certificate-authority"]
    if not isinstance(ca, str) or not Path(ca).is_absolute():
        raise ValueError("invalid CA path")
    return token, ca


def probe(args):
    target = urlsplit(args.path)
    if (not args.path.startswith("/api/v1/") or target.scheme or target.netloc
            or target.fragment or any(ord(c) < 33 or ord(c) > 126 for c in args.path)):
        raise ValueError("invalid API path")
    if args.expect_status == 200:
        if args.json_pointer is not None:
            if not args.json_pointer.startswith("/") or not args.expect_value:
                raise ValueError("missing known JSON value")
        elif not args.body_contains:
            raise ValueError("missing known body content")
    elif args.json_pointer is not None or args.expect_value is not None or args.body_contains is not None:
        raise ValueError("positive check on denial")
    token, ca = credentials(args.kubeconfig)
    connection = http.client.HTTPSConnection("127.0.0.1", 6443, timeout=10,
                                              context=ssl.create_default_context(cafile=ca))
    try:
        # http.client neither loads proxy settings nor follows redirects.
        connection.request("GET", args.path, headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        body = response.read(1048577)
        if response.status != args.expect_status or len(body) > 1048576:
            raise ValueError("unexpected API response")
        if args.expect_status == 403:
            status = json.loads(body)
            if status.get("kind") != "Status" or status.get("reason") != "Forbidden" or status.get("code") != 403:
                raise ValueError("not a Kubernetes denial")
        elif args.json_pointer is not None:
            value = json.loads(body)
            for part in args.json_pointer[1:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
            if value != args.expect_value:
                raise ValueError("known object mismatch")
        elif args.body_contains not in body.decode("utf-8"):
            raise ValueError("known log mismatch")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--expect-status", type=int, choices=(200, 403), required=True)
    positive = parser.add_mutually_exclusive_group()
    positive.add_argument("--json-pointer")
    positive.add_argument("--body-contains")
    parser.add_argument("--expect-value")
    args = parser.parse_args()
    try:
        probe(args)
    except (OSError, ValueError, KeyError, TypeError, IndexError, http.client.HTTPException):
        # Exceptions and bodies may contain reflected credentials. Emit neither.
        print("ALLOY_REPLICA_API_FAILED", file=os.sys.stderr)
        return 1
    print(f"ALLOY_REPLICA_API_OK status={args.expect_status}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
