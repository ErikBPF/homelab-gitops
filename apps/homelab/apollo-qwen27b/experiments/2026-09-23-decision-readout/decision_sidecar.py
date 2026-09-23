#!/usr/bin/env python3
"""Loopback decision-engine readout over a served vLLM model (S1).

Renders a Decision Index question into the served model's chat form, scores each
option key by the summed log-probability of its tokens at the answer slot, and
serves the typed distribution the frozen suite expects at POST /v1/systemone.

Scoring uses /v1/completions with echo=true (max_tokens=0): one request echoes the
context to learn its tokens, one batched request echoes context+key for every
option, and each key's tokens are isolated by longest-common-prefix and summed.
This reads every key's log-probability (no top-logprobs window) and handles
multi-token keys, which a single answer-slot logprob does not.

    python3 decision_sidecar.py --vllm-url http://127.0.0.1:11542 --model qwen38-27b

Config falls back to DECISION_VLLM_URL / DECISION_MODEL / DECISION_HOST /
DECISION_PORT / DECISION_TEMPERATURE.
"""

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYSTEM_PROMPT = (
    "You are a decision engine. Read the state, then answer the question by "
    "replying with exactly one option key from the list. Do not explain."
)

# Qwen3.8 ChatML with the thinking block pre-filled empty (enable_thinking=false),
# so the option key is the model's immediate next token after the prompt.
CHAT_TEMPLATE = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n{body}<|im_end|>\n"
    "<|im_start|>assistant\n<think>\n\n</think>\n\n"
)

CAPACITY_MARKERS = (
    "options per choice",
    "a choice needs at least two options",
    "a score takes 2 to 10 levels",
    "the canvas holds",
    "maximum context length",
    "maximum model length",
    "longer than the maximum model length",
    "context window",
    "too many tokens",
)


class Unsupported(ValueError):
    """Question the engine cannot answer, or a request the model rejected."""


CHAT_NOTICE = (
    "This model is a typed decision engine. Send a JSON object with 'state' and "
    "'questions' (Decision Index format) as the user message."
)


def decision_from_messages(messages):
    """Return (state, questions) parsed from the last user message, or None."""
    content = None
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            break
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("questions"), dict):
        return payload.get("state", ""), payload["questions"]
    return None


def text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_prompt(state, question):
    body = [
        "State:",
        text(state) if state not in ("", None, {}, []) else "(empty)",
        "",
        "Question:",
        text(question.get("instructions", "")),
        "",
        "Options:",
    ]
    criteria = question["criteria"] if question["type"] == "choice" else {"false": "False", "true": "True"}
    for key, description in criteria.items():
        body.append(f"{key}: {key if description is None else text(description)}")
    body += ["", "Reply with exactly one option key."]
    return "\n".join(body)


def validate(questions, response):
    """Kit-compatible check of a response against the questions it answers."""
    answers = response.get("answers", {})
    if set(answers) != set(questions):
        raise Unsupported("answer keys do not match question keys")
    for key, question in questions.items():
        answer = answers[key]
        if answer.get("type") != question["type"]:
            raise Unsupported(f"{key}: type mismatch")
        if question["type"] == "choice":
            if answer.get("choice") not in question["criteria"]:
                raise Unsupported(f"{key}: choice outside criteria")
            probabilities = answer.get("probabilities", {})
            if set(probabilities) != set(question["criteria"]):
                raise Unsupported(f"{key}: probability keys do not match criteria")
            if not all(isinstance(p, (int, float)) and math.isfinite(p) and 0.0 <= p <= 1.0 for p in probabilities.values()):
                raise Unsupported(f"{key}: probability out of range")
            if abs(sum(probabilities.values()) - 1.0) > 0.01:
                raise Unsupported(f"{key}: probabilities do not sum to 1")
        elif question["type"] == "noul":
            value = answer.get("noul")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise Unsupported(f"{key}: noul out of range")
        else:
            raise Unsupported(f"{key}: unsupported question type {question['type']!r}")


class Readout:
    def __init__(self, vllm_url, model, temperature=1.0, timeout=600):
        if temperature <= 0:
            raise ValueError("temperature must be > 0")
        self.vllm_url = vllm_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def _echo(self, prompt):
        body = {
            "model": self.model,
            "prompt": prompt,
            "echo": True,
            "max_tokens": 0,
            "temperature": 0,
            "logprobs": 1,
        }
        request = urllib.request.Request(
            self.vllm_url + "/v1/completions",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", "replace")
            if error.code in (400, 413, 422) and any(marker in message for marker in CAPACITY_MARKERS):
                raise Unsupported(message)
            raise

    def _option_scores(self, context, keys):
        context_tokens = self._echo(context)["choices"][0]["logprobs"]["tokens"]
        payload = self._echo([context + key for key in keys])
        scores = []
        for choice in payload["choices"]:
            tokens = choice["logprobs"]["tokens"]
            token_logprobs = choice["logprobs"]["token_logprobs"]
            shared = 0
            while shared < len(context_tokens) and shared < len(tokens) and tokens[shared] == context_tokens[shared]:
                shared += 1
            scores.append(sum(token_logprobs[shared:]))
        return scores, payload.get("usage") or {}

    def __call__(self, state, questions):
        answers = {}
        input_tokens = 0
        for key, question in questions.items():
            question_type = question.get("type")
            if question_type == "choice":
                keys = list(question["criteria"])
            elif question_type == "noul":
                keys = ["false", "true"]
            else:
                raise Unsupported(f"{key}: unsupported question type {question_type!r}")
            context = CHAT_TEMPLATE.format(system=SYSTEM_PROMPT, body=render_prompt(state, question))
            scores, usage = self._option_scores(context, keys)
            peak = max(scores)
            weights = [math.exp((score - peak) / self.temperature) for score in scores]
            total = sum(weights)
            probabilities = {option: weight / total for option, weight in zip(keys, weights)}
            if question_type == "choice":
                choice = max(keys, key=lambda option: probabilities[option])
                answers[key] = {"type": "choice", "choice": choice, "probabilities": probabilities}
            else:
                answers[key] = {"type": "noul", "noul": probabilities["true"]}
            input_tokens += usage.get("prompt_tokens") or 0
        response = {"model": self.model, "answers": answers, "usage": {"input_tokens": input_tokens}}
        try:
            validate(questions, response)
        except Unsupported as error:
            raise RuntimeError(f"readout self-check failed: {error}") from error
        return response


class Handler(BaseHTTPRequestHandler):
    readout = None

    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            self._chat_completions()
            return
        if self.path != "/v1/systemone":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            state = payload.get("state", "")
            questions = payload["questions"]
        except Exception as error:
            self._send(400, {"error": f"bad request: {error}"})
            return
        try:
            response = self.readout(state, questions)
        except Unsupported as error:
            self._send(422, {"error": str(error)})
            return
        except Exception as error:
            self._send(500, {"error": str(error)})
            return
        response["model"] = payload.get("model") or self.readout.model
        self._send(200, response)

    def _chat_completions(self):
        length = int(self.headers.get("content-length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = payload["messages"]
        except Exception as error:
            self._send(400, {"error": f"bad request: {error}"})
            return
        decision = decision_from_messages(messages)
        try:
            if decision is None:
                content = CHAT_NOTICE
                tokens = 0
            else:
                response = self.readout(*decision)
                content = json.dumps(response, ensure_ascii=False)
                tokens = response["usage"]["input_tokens"]
        except Unsupported as error:
            self._send(422, {"error": str(error)})
            return
        except Exception as error:
            self._send(500, {"error": str(error)})
            return
        completion = {
            "id": "chatcmpl-decision",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model") or self.readout.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens},
        }
        if payload.get("stream"):
            self._send_stream(completion)
            return
        self._send(200, completion)

    def _send_stream(self, completion):
        choice = completion["choices"][0]
        chunks = [
            {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": choice["message"]["content"]}, "finish_reason": None}],
            },
            {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": completion["usage"],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        data = body.encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        self.send_error(404)

    def log_message(self, *args):
        pass


def make_server(host, port, readout):
    handler = type("BoundHandler", (Handler,), {"readout": readout})
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vllm-url", default=os.environ.get("DECISION_VLLM_URL", "http://127.0.0.1:11542"))
    parser.add_argument("--model", default=os.environ.get("DECISION_MODEL", "qwen38-27b"))
    parser.add_argument("--host", default=os.environ.get("DECISION_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DECISION_PORT", "11560")))
    parser.add_argument("--temperature", type=float, default=float(os.environ.get("DECISION_TEMPERATURE", "1.0")))
    args = parser.parse_args()
    readout = Readout(vllm_url=args.vllm_url, model=args.model, temperature=args.temperature)
    server = make_server(args.host, args.port, readout)
    print(f"decision readout on http://{args.host}:{args.port}/v1/systemone -> {args.vllm_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
