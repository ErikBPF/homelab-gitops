#!/usr/bin/env python3
"""Small REST/OpenAPI tester for the registered `qwen-jev` decision model.

Exposes two endpoints: POST /decide forwards a Decision Index request to a
qwen-jev OpenAI chat endpoint and returns the parsed decision; POST /complete
runs a plain text chat completion through the same upstream. Swagger UI is
served at /docs and the machine-readable schema at /openapi.json.

    uvicorn qwen_jev_api:app --host 127.0.0.1 --port 8088
    # or: python3 qwen_jev_api.py

Config (env):
    QWEN_JEV_BASE_URL  default https://litellm.homelab.pastelariadev.com/v1
    QWEN_JEV_MODEL     default qwen-jev
    QWEN_JEV_API_KEY   default $LITELLM_API_KEY (empty -> no Authorization header)
    QWEN_JEV_TIMEOUT   default 600 seconds
    QWEN_JEV_MAX_TOKENS default 256 (thinking is on by default)
"""

import json
import os
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BASE_URL = os.environ.get("QWEN_JEV_BASE_URL", "https://litellm.homelab.pastelariadev.com/v1").rstrip("/")
MODEL = os.environ.get("QWEN_JEV_MODEL", "qwen-jev")
API_KEY = os.environ.get("QWEN_JEV_API_KEY") or os.environ.get("LITELLM_API_KEY") or ""
TIMEOUT = float(os.environ.get("QWEN_JEV_TIMEOUT", "600"))
MAX_TOKENS = int(os.environ.get("QWEN_JEV_MAX_TOKENS", "256"))
CLIENT = httpx.Client(timeout=TIMEOUT)

EXAMPLE = {
    "state": "The meeting is on Tuesday at 3pm in room B.",
    "questions": {
        "day": {
            "type": "choice",
            "instructions": "Which day is the meeting?",
            "criteria": {"monday": "Monday", "tuesday": "Tuesday", "friday": "Friday"},
        },
        "is_urgent": {"type": "noul", "instructions": "Is the meeting urgent?"},
    },
}


class Question(BaseModel):
    type: str = Field(..., description="'choice' (2-255 options) or 'noul'")
    instructions: Any = Field(None, description="What is being asked")
    criteria: Optional[dict[str, Optional[str]]] = Field(
        None, description="option key -> description; required for 'choice'"
    )


class Answer(BaseModel):
    type: str
    choice: Optional[str] = None
    probabilities: Optional[dict[str, float]] = None
    noul: Optional[float] = None


class DecisionRequest(BaseModel):
    state: Any = Field("", description="Context the engine reads")
    questions: dict[str, Question]

    model_config = {"json_schema_extra": {"examples": [EXAMPLE]}}


class DecisionResponse(BaseModel):
    model: str
    answers: dict[str, Answer]
    usage: Optional[dict[str, Any]] = None


class CompletionRequest(BaseModel):
    prompt: str = Field(..., description="User message for the upstream chat model")
    system: Optional[str] = Field(None, description="Optional system message")
    model: Optional[str] = Field(None, description="Override the upstream model (default qwen-jev)")
    max_tokens: Optional[int] = Field(None, description="Override the output token budget")
    temperature: float = Field(0.0, description="Sampling temperature; 0 is deterministic")

    model_config = {
        "json_schema_extra": {"examples": [{"prompt": "Say hello in one word.", "system": "Be terse."}]}
    }


class CompletionResponse(BaseModel):
    model: str
    content: str
    usage: Optional[dict[str, Any]] = None


app = FastAPI(
    title="qwen-jev decision tester",
    version="1.0.0",
    description=(
        "Forwards Decision Index requests to the registered `qwen-jev` model and "
        "returns typed decisions (choice + probability distribution, or noul)."
    ),
)


def _request(messages: list[dict[str, str]], model: str, max_tokens: int, temperature: float) -> dict:
    headers = {}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    try:
        response = CLIENT.post(
            BASE_URL + "/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
        )
    except httpx.HTTPError as error:
        raise HTTPException(502, f"upstream unreachable: {error}")
    if response.status_code >= 400:
        raise HTTPException(502, f"upstream {response.status_code}: {response.text[:500]}")
    return response.json()


def _content(payload: dict) -> str:
    content = (payload.get("choices") or [{}])[0].get("message", {}).get("content")
    if not content:
        raise HTTPException(502, "upstream returned empty content; raise max_tokens")
    return content


def _chat(content: str) -> str:
    return _content(_request([{"role": "user", "content": content}], MODEL, MAX_TOKENS, 0))


@app.get("/health")
def health():
    return {"status": "ok", "base_url": BASE_URL, "model": MODEL, "auth": bool(API_KEY)}


@app.post("/decide", response_model=DecisionResponse, summary="Answer typed decision questions")
def decide(request: DecisionRequest):
    payload = {
        "model": MODEL,
        "state": request.state,
        "questions": {
            key: question.model_dump(exclude_none=True) for key, question in request.questions.items()
        },
    }
    content = _chat(json.dumps(payload, ensure_ascii=False))
    try:
        return DecisionResponse.model_validate_json(content)
    except Exception as error:
        raise HTTPException(502, f"qwen-jev returned non-decision content: {content[:300]!r} ({error})")


@app.post("/complete", response_model=CompletionResponse, summary="Plain text chat completion")
def complete(request: CompletionRequest):
    messages = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.append({"role": "user", "content": request.prompt})
    payload = _request(messages, request.model or MODEL, request.max_tokens or MAX_TOKENS, request.temperature)
    return CompletionResponse(
        model=payload.get("model", request.model or MODEL),
        content=_content(payload),
        usage=payload.get("usage"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("QWEN_JEV_HOST", "127.0.0.1"), port=int(os.environ.get("QWEN_JEV_PORT", "8088")))
