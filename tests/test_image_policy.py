#!/usr/bin/env python3
"""Assert the ref-shape and timestamp helpers of the image policy."""

import importlib.util
import pathlib
from datetime import datetime, timezone

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check-image-policy.py"
spec = importlib.util.spec_from_file_location("image_policy", MODULE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

TAGGED = "ghcr.io/example/app:v1.2.3@sha256:" + "a" * 64
ZERO = "ghcr.io/example/app:v1.2.3@sha256:" + "0" * 64


def assert_ok(ref, where="apps/x/manifest.yaml"):
    assert policy.check(ref, where) == [], (ref, policy.check(ref, where))


def assert_bad(ref, needle, where="apps/x/manifest.yaml"):
    problems = policy.check(ref, where)
    assert problems and needle in problems[0], (ref, problems)


assert policy.split_ref(TAGGED) == ("ghcr.io/example/app", "v1.2.3", "sha256:" + "a" * 64)
assert policy.split_ref("ghcr.io/example/app@sha256:" + "a" * 64)[1] is None
assert_ok(TAGGED)
assert_bad("ghcr.io/example/app@sha256:" + "a" * 64, "missing tag")
assert_bad("ghcr.io/example/app:v1.2.3", "missing @sha256 digest")
assert_bad("ghcr.io/example/app:latest@sha256:" + "a" * 64, "rolling tag")
assert_bad(ZERO, "placeholder digest")
assert_ok(ZERO, where="apps/homelab/aster/deployment-server.yaml")

DIGEST_ONLY = "harbor.homelab.pastelariadev.com/dockerhub/library/traefik@sha256:" + "b" * 64
assert_bad(DIGEST_ONLY, "missing tag")
assert policy.check(DIGEST_ONLY, where="platform/traefik:x", allow_missing_tag=True) == []

now = datetime(2026, 11, 2, tzinfo=timezone.utc)
OWN_BUILD = "harbor.homelab.pastelariadev.com/library/airflow-dataplatform@sha256:" + "c" * 64
assert policy.age_check(OWN_BUILD, "apps/homelab/airflow:x", now) == []

assert policy.parse_created("2026-10-28T20:06:48.659631472Z") == datetime(
    2026, 10, 28, 20, 6, 48, 659631, tzinfo=timezone.utc
)
assert policy.parse_created("2026-10-28T20:06:48Z") == datetime(
    2026, 10, 28, 20, 6, 48, tzinfo=timezone.utc
)
assert policy.parse_created(None) is None

assert policy.registry("harbor.homelab.pastelariadev.com/dockerhub/library/postgres") == (
    "registry-1.docker.io",
    "library/postgres",
)
assert policy.registry("harbor.homelab.pastelariadev.com/library/cognee-homelab") == (None, None)
assert policy.registry("ghcr.io/berriai/litellm-database") == (
    "ghcr.io",
    "berriai/litellm-database",
)

print("image policy helpers OK")
