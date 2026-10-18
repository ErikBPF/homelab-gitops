#!/usr/bin/env python3
"""Measure identical literal prompts through the local completions API."""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
from urllib.request import Request, urlopen

from needle import read_stream


def complete(url, model, prompt, output_tokens):
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_tokens": output_tokens,
        "temperature": 0,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    started = time.monotonic()
    with urlopen(Request(url, body, {"Content-Type": "application/json"}), timeout=300) as response:
        answer, usage, ttft = read_stream(response, started)
    elapsed = round(time.monotonic() - started, 3)
    if usage["completion_tokens"] != output_tokens:
        raise AssertionError(f"expected {output_tokens} output tokens, got {usage['completion_tokens']}")
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "ttft_seconds": ttft,
        "elapsed_seconds": elapsed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", default="qwen38-27b")
    parser.add_argument("--url", default="http://127.0.0.1:11542/v1/completions")
    args = parser.parse_args()

    dataset = Path(args.dataset).read_bytes()
    samples = [json.loads(line) for line in dataset.splitlines()]
    if len(samples) != 4 or len({s["prompt"] for s in samples}) != 4:
        raise AssertionError("expected four distinct prompts")
    complete(args.url, args.model, "Warmup check: " + samples[0]["prompt"], 64)
    started = time.monotonic()
    results = []
    for sample in samples:
        result = complete(args.url, args.model, sample["prompt"], sample["output_tokens"])
        results.append(result)
        print(json.dumps({"sample": result}, sort_keys=True), flush=True)
    elapsed = time.monotonic() - started
    print(json.dumps({"summary": {
        "dataset_sha256": hashlib.sha256(dataset).hexdigest(),
        "completed": len(results),
        "input_tokens": sum(r["prompt_tokens"] for r in results),
        "output_tokens": sum(r["completion_tokens"] for r in results),
        "mean_ttft_ms": round(statistics.mean(r["ttft_seconds"] for r in results) * 1000, 2),
        "output_tokens_per_second": round(sum(r["completion_tokens"] for r in results) / elapsed, 2),
        "elapsed_seconds": round(elapsed, 3),
    }}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
