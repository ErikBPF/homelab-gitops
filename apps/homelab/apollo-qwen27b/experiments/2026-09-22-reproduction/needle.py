#!/usr/bin/env python3
"""Check long-context recall through a local vLLM chat endpoint."""

import argparse
import hashlib
import json
import time
from urllib.request import Request, urlopen


def read_stream(response, started):
    parts = []
    usage = None
    ttft = None
    for line in response:
        if not line.startswith(b"data: "):
            continue
        payload = line[6:].strip()
        if payload == b"[DONE]":
            break
        event = json.loads(payload)
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices", []):
            content = choice.get("delta", {}).get("content") or choice.get("text")
            if content:
                if ttft is None:
                    ttft = round(time.monotonic() - started, 3)
                parts.append(content)
    if not parts or usage is None:
        raise AssertionError("stream lacked output or usage")
    return "".join(parts), usage, ttft


def main():
    from transformers import AutoTokenizer

    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default="/model")
    parser.add_argument("--model-name", default="qwen38-27b")
    parser.add_argument("--url", default="http://127.0.0.1:11542/v1/chat/completions")
    parser.add_argument("--target-tokens", type=int, default=249000)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, trust_remote_code=True)
    line = "Archive log: the station crew inspected pumps, cables, and valves before dawn.\n"
    line_tokens = len(tokenizer.encode(line, add_special_tokens=False))
    repetitions = args.target_tokens // line_tokens
    marker = "BLUE-ORBIT-7319"
    prompt = (
        line * (repetitions // 2)
        + f"Archive verification passphrase: {marker}.\n"
        + line * (repetitions - repetitions // 2)
        + "What is the archive verification passphrase? Reply with the passphrase only."
    )
    token_count = len(tokenizer.encode(prompt, add_special_tokens=False))
    if not args.target_tokens <= token_count <= args.target_tokens + 1000:
        raise AssertionError(f"unexpected prompt length: {token_count}")

    request_body = {
        "model": args.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if args.stream:
        request_body.update(stream=True, stream_options={"include_usage": True})
    body = json.dumps(request_body).encode()
    started = time.monotonic()
    request = Request(args.url, body, {"Content-Type": "application/json"})
    with urlopen(request, timeout=900) as response:
        if args.stream:
            answer, usage, ttft = read_stream(response, started)
        else:
            result = json.load(response)
            answer = result["choices"][0]["message"]["content"] or ""
            usage = result["usage"]
            ttft = None
    elapsed = round(time.monotonic() - started, 3)
    if marker not in answer or usage["prompt_tokens"] < args.target_tokens:
        raise AssertionError("long-context recall or prompt length failed")
    print(json.dumps({
        "result": "NEEDLE_OK",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "elapsed_seconds": elapsed,
        "ttft_seconds": ttft,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
