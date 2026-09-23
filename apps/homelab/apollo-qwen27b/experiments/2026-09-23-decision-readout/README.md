# Decision readout sidecar (S1/S2)

Serves the Decision Index wire format at `POST /v1/systemone` over the already
running Apollo vLLM engine. No new weights: it reads the served model's own
option-key log-probabilities and returns the typed distribution the frozen suite
expects.

Plan: `homelab/docs/plans/apollo-decision-model/one-pager.md` (slices S1/S2).
Contract: `decision_readout.feature`.

## Run

```sh
python3 decision_sidecar.py \
  --vllm-url http://127.0.0.1:11542 \
  --model qwen38-27b \
  --host 127.0.0.1 --port 11560
```

Env equivalents: `DECISION_VLLM_URL`, `DECISION_MODEL`, `DECISION_HOST`,
`DECISION_PORT`, `DECISION_TEMPERATURE` (default 1.0).

Health: `curl -fsS http://127.0.0.1:11560/healthz`.

## How it reads

The kit's non-chat prompt makes the model emit a newline before the answer, so
first-token scoring is unreliable. Instead the sidecar:

1. renders the kit's chat template (`enable_thinking=false`) into a context;
2. echoes the context once (`echo=true`, `max_tokens=0`, `logprobs=1`) to get
   its tokens;
3. echoes every `context + option_key` in one batched request and, per option,
   sums the log-probabilities of the tokens after the shared context prefix;
4. softmaxes those scores (`--temperature`).

So multi-token keys are scored over all their tokens, and no option is floored.
Each question costs two prefill requests.

## Check

```sh
python3 test_decision_sidecar.py
```

Starts a stub vLLM on an ephemeral port, so it needs no GPU and no model. Five
scenarios (DR-01..DR-05) are bound to runnable steps.

## S2 — kit acceptance

`run-s2-kit.sh` starts the engine, starts the sidecar, and runs the
authoritative reproduction kit (`apolinario/decision-index`, pinned revision)
with `--engine http --rows s2-rows.jsonl`. Result: 7/7 rows `ok`, all argmaxes
correct (receipt `results/s2b-kit-results.jsonl` on Apollo).

## Ceiling

A question's options must fit the model context; an over-capacity request is
refused with 422 (`Unsupported`), never truncated. There is no top-logprobs
window and no per-option floor anymore. Scoring on the pinned frozen suite
(DR-06) is still the next gate and is not claimed here.
