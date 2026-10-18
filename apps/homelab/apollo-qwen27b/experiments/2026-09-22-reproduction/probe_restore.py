#!/usr/bin/env python3
"""Prove external LMCache restore of a long prefix.

Runs one cold needle request (stores the prefix in LMCache L1), resets the
native vLLM prefix cache, then repeats the identical request and asserts the
recall comes from LMCache (external hit tokens) rather than a native hit.

Prints one JSON object on stdout; exits 1 if any assertion fails.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request

METRICS = (
    "lmcache_mp_lookup_requested_tokens_total",
    "lmcache_mp_lookup_hit_tokens_total",
    "lmcache_mp_lookup_hit_l1_tokens_total",
    "lmcache_mp_lookup_hit_l2_tokens_total",
    "lmcache_mp_l1_memory_usage_bytes",
    "lmcache_mp_l1_usage_ratio",
    "lmcache_mp_num_chunks_loaded_total",
    "lmcache_mp_num_finished_stores_total",
    "lmcache_mp_num_finished_retrieves_total",
)


def fetch(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


def scrape(text, name):
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name):]
        if rest and not rest[0].isspace() and rest[0] != "{":
            continue
        total += float(line.rsplit(" ", 1)[1])
    return total


def snapshot(url):
    text = fetch(url)
    return {name: scrape(text, name) for name in METRICS}


def needle(args, phase):
    proc = subprocess.run(
        [sys.executable, "/trial/needle.py", "--model-dir", args.model_dir,
         "--model-name", args.model_name, "--url", args.url,
         "--target-tokens", str(args.target_tokens), "--stream"],
        capture_output=True, text=True, timeout=args.timeout,
    )
    if proc.returncode != 0:
        raise AssertionError(f"{phase} needle failed: {proc.stderr.strip()}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["result"] == "NEEDLE_OK", result
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-tokens", type=int, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:11542/v1/chat/completions")
    parser.add_argument("--reset-url", default="http://127.0.0.1:11542/reset_prefix_cache")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:11552/metrics")
    parser.add_argument("--model-dir", default="/model")
    parser.add_argument("--model-name", default="qwen38-27b")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    report = {"tag": args.tag, "target_tokens": args.target_tokens, "metrics": {}}
    before = snapshot(args.metrics_url)
    report["metrics"]["before"] = before

    report["cold"] = needle(args, "cold")
    after_cold = snapshot(args.metrics_url)
    report["metrics"]["after_cold"] = after_cold

    request = urllib.request.Request(args.reset_url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        report["reset_prefix_cache"] = response.status
        assert response.status == 200, response.status
    time.sleep(2)
    after_reset = snapshot(args.metrics_url)
    report["metrics"]["after_reset"] = after_reset

    report["restored"] = needle(args, "restored")
    after_restore = snapshot(args.metrics_url)
    report["metrics"]["after_restore"] = after_restore

    deltas = {name: after_restore[name] - after_reset[name] for name in METRICS}
    report["deltas"] = deltas
    report["restore_faster"] = report["restored"]["elapsed_seconds"] < report["cold"]["elapsed_seconds"]

    hit = deltas["lmcache_mp_lookup_hit_tokens_total"]
    assert hit >= 0.9 * args.target_tokens, f"external hit {hit} < 90% of {args.target_tokens}"
    assert deltas["lmcache_mp_lookup_hit_l1_tokens_total"] >= 0.9 * args.target_tokens, deltas
    assert deltas["lmcache_mp_num_finished_retrieves_total"] >= 2, "no retrieve finished on both GPUs"
    report["verdict"] = "LMCACHE_LONG_RESTORE_OK"
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
