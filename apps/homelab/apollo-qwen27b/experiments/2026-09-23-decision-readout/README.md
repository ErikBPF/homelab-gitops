# Decision readout sidecar (S0-S2)

Serves the Decision Index wire format at `POST /v1/decide` over the already
running Apollo vLLM engine (the authoritative kit's `/v1/systemone` path is kept
as an alias), and a chat shim at `POST /v1/chat/completions` so LiteLLM registers
it as `qwen-jev`. No new weights: it reads the served model's own answer-slot
probabilities.

Plan: `homelab/docs/plans/apollo-decision-model/one-pager.md` (slices S0-S4).
Contract: `decision_readout.feature`.

## Run

```sh
python3 decision_sidecar.py \
  --vllm-url http://127.0.0.1:11542 \
  --model qwen38-27b-nvfp4 \
  --host 127.0.0.1 --port 11560
```

Env equivalents: `DECISION_VLLM_URL`, `DECISION_MODEL`, `DECISION_DESCRIPTOR`,
`DECISION_MODELS_DIR`, `DECISION_HOST`, `DECISION_PORT`, `DECISION_TEMPERATURE`
(default: the descriptor's `temperature`).

Health: `curl -fsS http://127.0.0.1:11560/healthz`.

## Model descriptors

The readout is per model, not per deployment: a descriptor under `models/`
configures it and one engine serves every model that shares the profile.

| Field | Meaning |
|---|---|
| `readout` | `auto` \| `lettered` \| `echo` |
| `letters` | single-token label alphabet (default `a-z`) |
| `temperature` | softmax temperature for the letter distribution |
| `enable_thinking` | chat-template toggle for the lettered request |
| `system_prompt`, `letter_system_prompt` | readout instructions |
| `chat_template` | raw completion prefill for the echo fallback (required for `echo`) |
| `engine_profile` | `full` (MTP + LMCache) or `lean`; read by `serve-litellm.sh` |

`--descriptor NAME` (or `--model NAME`) loads `models/NAME.json`; a missing file
uses built-in defaults. `qwen38-27b-nvfp4.json` is the first descriptor. Deploy
copies `models/` beside `decision_sidecar.py` in the trial dir so both the sidecar
and `serve-litellm.sh` (which reads `engine_profile`) see it; without it the
sidecar still runs on the built-in defaults.

`readout=auto` probes the model at startup: if a single option letter is not the
top token at the answer slot for a fixture, it forces the echo readout. An
`echo` readout with no `chat_template` is refused, never guessed.

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
memory that can crash the engine. The same echo path serves a model the probe
downgrades.

Identical `state`+`questions` are served from a bounded LRU cache keyed by model,
descriptor and readout route. Results are memoized per process only.

Measured on Apollo (1 question, 3 options, warm): lettered readout ~0.31 s direct
vs ~0.76 s for the old per-option echo readout; end-to-end through LiteLLM
~0.65 s. The remaining floor is the engine's ~0.25 s per request (MTP +
PIECEWISE cudagraphs + the LMCache connector) plus LiteLLM. The lean profile
(no MTP, no LMCache connector) measured 0.238 s first call, 1.7 ms cached repeat;
`--enforce-eager` / `--max-num-seqs 1` was worse (~0.27 s) and is rejected.

## Check

```sh
python3 test_decision_sidecar.py
```

Starts a stub vLLM on an ephemeral port, so it needs no GPU and no model; 18 tests.

## S2 - parallel serving

One full-profile engine serves both chat and decisions; the sidecar does not
serialize decisions (they run on the `ThreadingHTTPServer` and issue concurrent
upstream requests). `bench_parallel.py` measures it:

```sh
python3 bench_parallel.py --each 4 --json results/parallel.json
```

It fires N concurrent decisions and N concurrent chats after a one-of-each warm
baseline and reports p50/p95, wall time, throughput and `parallelism`
(sum of latencies / wall: ~1 serialized, ~2N batched). Vary the engine's
`MAX_NUM_SEQS` / `MAX_NUM_BATCHED_TOKENS` in `serve-litellm.sh` to tune.

Measured on Apollo (full profile, 0 failures in every run):

| run | in flight | decision p50 | chat p50 | wall | throughput | parallelism |
|---|---|---|---|---|---|---|
| `--max-num-seqs 2`, `--each 4` (`results/parallel.json`) | 8 | 2.86 s | 1.66 s | 3.10 s | 2.58/s | 5.59 of 8 |
| `--max-num-seqs 8`, `--each 4` (`results/parallel-numseq8-each4.json`) | 8 | 1.39 s | 1.10 s | 1.39 s | 5.74/s | 6.37 |
| `--max-num-seqs 8`, `--each 8` (`results/parallel-numseq8-each8.json`) | 16 | 2.55 s | 1.62 s | 3.08 s | 5.19/s | 10.49 of 16 |

Decisions are not serialized behind chat. At `--max-num-seqs 2` the engine batched only
2 at a time; raising it to 8 cut wall time at `--each 4` by 55% and doubled throughput,
and scaling held at 16 in flight. `--max-num-batched-tokens` stayed at 1727 (plenty for
8 short prompts); raising it is untried. `--max-num-seqs 16` is likewise untried.

## S2 - kit acceptance

`run-s2-kit.sh` starts the engine, the sidecar, then the authoritative
reproduction kit (`apolinario/decision-index`) with `--engine http --rows
s2-rows.jsonl`. Receipt `results/s2b-kit-results.jsonl` on Apollo.

## Ceiling

A question's options must fit the model context; an over-capacity request is
refused with 422 (`Unsupported`), never truncated. The echo fallback's memory
scales with total prompt tokens, so its batch size bounds it. Scoring on the
pinned frozen suite (DR-06) is still the next gate and is not claimed here.