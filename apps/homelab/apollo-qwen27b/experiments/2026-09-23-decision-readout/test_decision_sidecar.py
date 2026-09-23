#!/usr/bin/env python3
"""Behavior binding for decision_readout.feature (S1 readout sidecar).

Runs the sidecar's /v1/systemone surface against a stub vLLM, so no GPU or
model is needed. The stub echoes a canned token/log-probability stream; the test
asserts the typed distribution the Decision Index reproduction kit expects.

    python3 test_decision_sidecar.py
"""

import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import decision_sidecar

CANNED = {
    "red": -0.1,
    "blue": -3.0,
    "true": -0.2,
    "false": -2.0,
    "new york city": -0.4,
}
DEFAULT = -8.0
CAPACITY_MARKER = "maximum context length"
CONTEXT_TOKENS = ["CTX"] * 3


def softmax_pair(a, b):
    top = max(a, b)
    ea, eb = math.exp(a - top), math.exp(b - top)
    return ea / (ea + eb)


class StubVLLM(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = body.get("prompt")
        prompts = prompt if isinstance(prompt, list) else [prompt]
        if any("HUGE" in p for p in prompts):
            self._send(422, {"error": f"This model's {CAPACITY_MARKER} is 200000 tokens"})
            return
        if isinstance(prompt, str):
            self.server.context = prompt
        choices = [self._choice(index, p) for index, p in enumerate(prompts)]
        self._send(200, {"choices": choices, "usage": {"prompt_tokens": 42, "completion_tokens": 0, "total_tokens": 42}})

    def _choice(self, index, prompt):
        tokens = list(CONTEXT_TOKENS)
        logprobs = [0.0] * len(CONTEXT_TOKENS)
        key = prompt[len(self.server.context):] if hasattr(self.server, "context") else ""
        if key:
            total = CANNED.get(key, DEFAULT)
            parts = key.split(" ")
            for part in parts:
                tokens.append(part)
                logprobs.append(total / len(parts))
        return {
            "index": index,
            "finish_reason": "length",
            "logprobs": {"tokens": tokens, "token_logprobs": logprobs, "top_logprobs": None},
        }

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class DecisionSidecarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), StubVLLM)
        cls.stub_thread = threading.Thread(target=cls.stub.serve_forever, daemon=True)
        cls.stub_thread.start()
        stub_url = f"http://127.0.0.1:{cls.stub.server_address[1]}"
        readout = decision_sidecar.Readout(vllm_url=stub_url, model="stub-model")
        cls.server = decision_sidecar.make_server("127.0.0.1", 0, readout)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.stub.shutdown()

    def systemone(self, state, questions):
        request = urllib.request.Request(
            self.base + "/v1/systemone",
            data=json.dumps({"model": "qwen38-27b", "state": state, "questions": questions}).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = json.loads(error.read())
            error.close()
            return error.code, body

    def test_choice_distribution_is_valid_and_correct(self):
        status, body = self.systemone(
            "The color is red.",
            {"color": {"type": "choice", "instructions": "Which color is named?", "criteria": {"red": "red", "blue": "blue"}}},
        )
        self.assertEqual(status, 200)
        answer = body["answers"]["color"]
        self.assertEqual(answer["type"], "choice")
        self.assertEqual(answer["choice"], "red")
        self.assertEqual(set(answer["probabilities"]), {"red", "blue"})
        self.assertAlmostEqual(sum(answer["probabilities"].values()), 1.0, places=9)
        self.assertGreater(answer["probabilities"]["red"], answer["probabilities"]["blue"])
        self.assertEqual(body["model"], "qwen38-27b")
        self.assertIn("usage", body)

    def test_noul_returns_a_probability(self):
        status, body = self.systemone(
            "The color is red.",
            {"color": {"type": "noul", "instructions": "Is the color red?"}},
        )
        self.assertEqual(status, 200)
        answer = body["answers"]["color"]
        self.assertEqual(answer["type"], "noul")
        self.assertTrue(0.0 <= answer["noul"] <= 1.0)
        self.assertGreater(answer["noul"], 0.5)

    def test_multitoken_option_key_is_scored_by_summed_tokens(self):
        status, body = self.systemone(
            "Which city?",
            {"city": {"type": "choice", "instructions": "Which city is named?", "criteria": {"new york city": "new york city", "boston": "boston"}}},
        )
        self.assertEqual(status, 200)
        answer = body["answers"]["city"]
        self.assertEqual(answer["choice"], "new york city")
        self.assertAlmostEqual(answer["probabilities"]["new york city"], softmax_pair(-0.4, DEFAULT), places=9)

    def test_capacity_rejection_passes_through_as_422(self):
        status, body = self.systemone(
            "HUGE state",
            {"color": {"type": "choice", "instructions": "Which color is named?", "criteria": {"red": "red", "blue": "blue"}}},
        )
        self.assertEqual(status, 422)
        self.assertIn(CAPACITY_MARKER, body["error"])

    def test_unknown_question_type_is_rejected(self):
        status, _ = self.systemone(
            "The color is red.",
            {"score": {"type": "score", "instructions": "Rate the color."}},
        )
        self.assertEqual(status, 422)

    def chat(self, content, stream=False):
        request = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps({"model": "qwen-jev", "messages": [{"role": "user", "content": content}], "stream": stream}).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = json.loads(error.read())
            error.close()
            return error.code, body

    def test_chat_completions_shims_a_decision_request(self):
        decision = json.dumps({
            "state": "The color is red.",
            "questions": {"color": {"type": "choice", "instructions": "Which color is named?", "criteria": {"red": "red", "blue": "blue"}}},
        })
        status, body = self.chat(decision)
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], "qwen-jev")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        payload = json.loads(body["choices"][0]["message"]["content"])
        self.assertEqual(payload["answers"]["color"]["choice"], "red")

    def test_chat_completions_explains_a_plain_message(self):
        status, body = self.chat("hello there")
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], decision_sidecar.CHAT_NOTICE)

    def test_chat_completions_streams_sse(self):
        decision = json.dumps({
            "state": "The color is red.",
            "questions": {"color": {"type": "choice", "instructions": "Which color is named?", "criteria": {"red": "red", "blue": "blue"}}},
        })
        request = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps({"model": "qwen-jev", "messages": [{"role": "user", "content": decision}], "stream": True}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            text = response.read().decode()
        self.assertIn("data: ", text)
        self.assertIn("[DONE]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
