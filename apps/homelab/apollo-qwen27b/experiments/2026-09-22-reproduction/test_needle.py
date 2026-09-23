#!/usr/bin/env python3
"""Exercise the SSE parser without loading a model."""

import io
import runpy
import time
from pathlib import Path
from unittest.mock import patch

import bench_shared


read_stream = runpy.run_path(str(Path(__file__).with_name("needle.py")))["read_stream"]


def test_stream():
    events = io.BytesIO(
        b'data: {"choices":[{"delta":{"content":"BLUE-"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"ORBIT-7319"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":26039,"completion_tokens":8}}\n\n'
        b'data: [DONE]\n\n'
    )
    answer, usage, ttft = read_stream(events, time.monotonic())
    assert answer == "BLUE-ORBIT-7319"
    assert usage["prompt_tokens"] == 26039
    assert ttft >= 0

    completion = io.BytesIO(
        b'data: {"choices":[{"text":"Apollo ready"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":18,"completion_tokens":3}}\n\n'
        b'data: [DONE]\n\n'
    )
    answer, usage, _ = read_stream(completion, time.monotonic())
    assert answer == "Apollo ready" and usage["completion_tokens"] == 3

    completion.seek(0)
    with patch.object(bench_shared, "urlopen", return_value=completion):
        result = bench_shared.complete("http://localhost/", "qwen38-27b", "prompt", 3)
    assert result["prompt_tokens"] == 18
    assert result["completion_tokens"] == 3


if __name__ == "__main__":
    test_stream()
    print("STREAM_PARSER_OK")
