# Decision readout sidecar (S1/S2)

Serves the Decision Index wire format at `POST /v1/systemone` over the already
running Apollo vLLM engine, and a chat shim at `POST /v1/chat/completions` so
LiteLLM registers it as `qwen-jev`. No new weights: it reads the served model's
own answer-slot probabilities.

Plan: `homelab/docs/plans/apollo-decision-model/one-pager.md` (slices S1-S3).
Contract: `decision_readout.feature`.

## Run

```sh
python3 decision_sidecar.py \
  --vllm-url http://127.0.0.1:11542 \
  --model qwen38-27b-nvfp4 \
  --host 127.0.0.1 --port 11560
```

Env equivalents: `DECISION_VLLM_URL`, `DECISION_MODEL`, `DECISION_HOST`,
`DECISION_PORT`, `DECISION_TEMPERATURE` (default 1.0).

Health: `curl -fsS http://127.0.0.1:11560/healthz`.

## How it reads

Options are rendered with single-letter labels (`a)`, `b)`, `c)`, ...). One
`/v1/chat/completions` request per question asks for exactly one option letter;
the sidecar reads the letter distribution at the answer slot (`logprobs=true`,
`top_logprobs` at least the option count), ignores non-letter tokens, maps
letters back to option keys, floors letters the model did not rank, and softmaxes.
A decision is therefore one prefill, options are independent, and multi-token
keys cost nothing extra.

More than 26 options has no single-token label; those questions fall back to the
echo readout (echo the context, then echo each `context+key` and sum the key
token log-probabilities), chunked in batches of 16 to bound the prompt-logprob
memory that can crash the engine.

Measured on Apollo (1 question, 3 options, warm): lettered readout ~0.31 s direct
vs ~0.76 s for the old per-option echo readout; end-to-end through LiteLLM
~0.65 s. The remaining floor is the engine's ~0.25 s per request (MTP +
PIECEWISE cudagraphs + the LMCache connector) plus LiteLLM.

## Check

```sh
python3 test_decision_sidecar.py
```

Starts a stub vLLM on an ephemeral port, so it needs no GPU and no model; 9 tests.

## S2 - kit acceptance

`run-s2-kit.sh` starts the engine, the sidecar, then the authoritative
reproduction kit (`apolinario/decision-index`) with `--engine http --rows
s2-rows.jsonl`. Receipt `results/s2b-kit-results.jsonl` on Apollo.

## Ceiling

A question's options must fit the model context; an over-capacity request is
refused with 422 (`Unsupported`), never truncated. The echo fallback's memory
scales with total prompt tokens, so its batch size bounds it. Scoring on the
pinned frozen suite (DR-06) is still the next gate and is not claimed here.
