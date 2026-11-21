# Inkling 2 Mini as the local subagent model, RTX 5090

Evaluation of `thinkingmachines/Inkling-2-Mini` (released 2026-11-20) as a replacement for `qwen3.8:27b` in the
subagent seat, with the cloud main model unchanged (`glm-5.3:cloud`). Measured 2026-11-21 on the RTX 5090 box.
Not a migration. The Mac Studio (mlx-lm) is phase 2 and not in this round.

**Recommendation: do not switch on the RTX 5090.** Reasons and numbers at the end.

## Setup

- Inkling: SGLang docker image `lmsysorg/sglang:inkling-2-mini` [DOCUMENTED docs.sglang.io cookbook
  Inkling-2-Mini: Inkling is not in an SGLang release yet], RTX 5090 **Balanced** recipe from the cookbook's
  generator data (`python/sglang/cli/_commands/thinkingmachines--Inkling-2-Mini.json`, entry `rtx5090`/`balanced`):
  `--attention-backend fa4 --dtype bfloat16 --page-size 128 --context-length 65536 --reasoning-parser inkling
  --tool-call-parser inkling2`. Native NVFP4/MXFP8 weights, no GGUF. Driver 617.42, Docker 29.8.2.
  - Weights 18.47 GB, KV pool 71,936 full-attention tokens + 7,168 sliding-window, 2.2 GB GPU memory left
    [OBSERVED 2026-11-21, SGLang startup log]. The server warns "inkling2 quantization is not fully optimized yet".
- qwen3.8:27b: the tray Ollama 0.40.17 on the same box, the owner's setting of 131,072 context, 27.8 GB VRAM
  (nvidia-smi) [OBSERVED 2026-11-21].
- Harness: **OpenCode 1.18.30**, not Claude Code. Claude Code takes one `ANTHROPIC_BASE_URL`, so a cloud main model
  on Ollama plus a subagent on SGLang needs a routing proxy; OpenCode takes a provider per `baseURL` and a model per
  agent [DOCUMENTED opencode v1 docs, agents.mdx "Model"]. Both built-in subagents (`explore`, `general`) were
  pinned to the model under test. So this measures the model in the subagent seat, not Claude Code's Explore prompt.
  A Claude Code smoke test was planned only if Inkling won, and was not run.

## 0. Endpoint

SGLang serves Anthropic `POST /v1/messages` on every server, no flag; tool calls come back as `tool_use` blocks when
the server runs with `--tool-call-parser` [DOCUMENTED docs.sglang.io/docs/basic_usage/anthropic_api.md]. Verified:
`/v1/messages` with one tool returned `thinking` + `tool_use:get_weather{"city":"Bergen"}`, `stop_reason=tool_use`;
the OpenAI endpoint returned the same call with `finish_reason=tool_calls` [OBSERVED 2026-11-21,
`inkling-smoke.ps1` in the session scratchpad]. **Claude Code could talk to SGLang directly; no Ollama or
translation layer is needed for an all-Inkling setup.** The SGLang page also says Claude Code needs
`CLAUDE_CODE_ATTRIBUTION_HEADER=0` or it re-prefills the whole history every turn [DOCUMENTED same page]. The mixed
cloud+local setup is what needs a router.

## 1. Speed

`bench-speed.py`, one cold prompt per length (random nonce first, so no prefix cache), streamed, 2 runs each.
Prompt tokens are the server's own count; the two tokenizers differ, so the lengths are not identical.
Rows in `results/bench-speed-*.jsonl`. [OBSERVED 2026-11-21]

| Prompt | Inkling TTFT | Inkling decode | qwen3.8 TTFT | qwen3.8 decode |
|---|---|---|---|---|
| ~8K (8,638 / 8,040) | **0.6-0.7 s** | 152 tok/s | 2.4-2.5 s | 161 tok/s |
| ~16K (17,238 / 16,039) | **1.2 s** | 151 tok/s | 4.8-4.9 s | 159 tok/s |
| ~32K (34,438 / 32,038) | **2.5 s** | 141 tok/s | 10.2-10.4 s | 147-151 tok/s |

Inkling prefills about 4x faster and decodes at the same rate. Engine speed is not where Inkling loses (see 4).

## 2. Context

The 5090 recipe caps Inkling at 65,536 tokens [DOCUMENTED cookbook]. OpenCode compacts at the context limit minus
the output reserve, so the usable window in a subagent session was ~49K: peak context stopped at 47-49K in the
long tasks [OBSERVED]. **7 of 15 Inkling subagent sessions hit it and were compacted (1-5 times each)**: tuning 3/3,
routes 2/3, flags 2/3. All 7 still answered correctly. qwen at 131K was never compacted; its largest session
peaked at **63.5K** (tuning), which would not fit Inkling's window on this card [OBSERVED].

So 3 of 5 ordinary lookup tasks in a 7k-line repo outgrow Inkling's window on the 5090. What happens when they do:
OpenCode summarises and the subagent carries on; it costs time (see 4), not correctness, in this sample.

## 3. Constraints

Weakly tested. Each task said "Do not edit any file" and the fixture carries NoLlama's full `CLAUDE.md`; no tracked
file changed in 30 of 30 runs, either model [OBSERVED]. Whether OpenCode fed `CLAUDE.md` to the subagent is
[INFERRED], not checked. File:line references in the answers (median 4, both) are written by the main model, so they
say nothing about the subagent. A real test of many simultaneous instructions (the model's known weakness) needs
tasks whose instructions can be violated without editing files, and was not built in this round.

## 4. Head-to-head

`probe-subagent.ps1`: five read-only questions about a clone of NoLlama at `6ad999b` (`nollama.py` ~7k lines),
answers judged against ground truth computed from the clone (grep, git log), never against a transcript. 3 runs
per task per model. Medians from `summarize-subagent.ps1`. Thinking is estimated as reasoning characters / 4 for
both, because Ollama reports reasoning tokens as 0 [INFERRED estimate]. Context is input + cache read, because
Ollama reports only the uncached part as input [OBSERVED: input 493, cache.read 9292 on one step]. [OBSERVED 2026-11-21]

| Task | Inkling correct | qwen correct | Inkling wall (median, range) | qwen wall | Inkling thinking | qwen thinking |
|---|---|---|---|---|---|---|
| optimum (one lookup) | 3/3 | 3/3 | **28 s** (24-47) | 41 s (36-43) | 270 | 630 |
| history (git) | 3/3 | 3/3 | 23 s (21-26) | **20 s** (18-23) | 370 | 360 |
| routes (whole file) | 3/3 | 3/3 | 220 s (80-386) | **49 s** (40-82) | 13,000 | 1,600 |
| tuning (trace a call) | 3/3 * | 3/3 | 223 s (172-271) | **119 s** (102-142) | 9,200 | 3,200 |
| flags (cross-file diff) | **3/3** | 1/3 | 177 s (79-200) | 95 s (77-113) | 8,400 | 4,800 |

- **Correctness: Inkling 15/15, qwen 13/15.** qwen twice listed documented flags as undocumented (`--gpu-keepalive`,
  `README.md:154`; `--scan`, `docs/API.md:51`). \* One Inkling tuning run FAILED the first judge, which demanded the
  literal key `repetition_penalty_max`; the answer named `_apply_tuning`, the cap from `model-tuning.json`, and two
  more functions the ground truth did not know about. Re-judged PASS with the corrected judge, which both models
  were then scored by; the original verdict stays in `results/subagent-probe-inkling.jsonl`.
- **Tool-call errors: 0 for both** (Inkling 0/325, qwen 0/199 over the subagent sessions).
- **Time:** equal on single lookups, **1.9-4.5x slower on the three long tasks.** Inkling took 306 subagent steps in
  total against qwen's 111, thinks 3-8x more on long tasks, and compacts. With a 4x faster prefill, the time goes
  into behaviour, not the engine.

## 5. Prefix cache

The hypothesis was that, like Qwen3.8, the cache hits only at message boundaries because of the sliding window and
conv state. **Not what SGLang did.** Over all Inkling runs: 278 of 326 requests reused a cached prefix (85 %), and
86 % of prompt tokens came from cache. A cached request re-prefilled a median 1,353 tokens (p90 4,089, max 15,714);
qwen on Ollama re-prefilled a median 2,648 per cached step (p90 7,342) [OBSERVED 2026-11-21, SGLang
"Prefill batch" log lines, `#cached-token`; qwen from OpenCode's cache.read]. SGLang ran its default hybrid
checkpoint level 2 (checkpoints at prompt divergence and request ends) [DOCUMENTED
docs.sglang.io/docs/advanced_features/hybrid_cache_checkpoints.md]. Whether a hit can land mid-message was not
tested separately. SGLang does not report cached tokens to the client, so OpenCode shows cache read 0 for Inkling.

## 6. Reasoning loops

No task spun without finishing; no timeout in 30 runs. Inkling's worst session: routes run 2, 52 steps, ~25,800
thinking tokens (estimate), 375 s, 5 compactions, correct answer. Largest single-step thinking in Inkling sessions:
up to ~600 tokens on lookups, several thousand on the long tasks [OBSERVED]. Flag: **routes and tuning** are the
tasks where Inkling's thinking and step count run away; that is where the time goes.

## Recommendation: do not switch (on the RTX 5090)

- **For switching:** Inkling was more correct (15/15 vs 13/15), had 0 tool-call errors, and its engine is faster
  (prefill ~4x, decode equal). Prefix caching works.
- **Against, and decisive:** on the tasks that matter for a subagent (reading across a large file), it was 1.9-4.5x
  slower per task (median 220 s vs 49 s, 223 s vs 119 s, 177 s vs 95 s), and 3 of 5 tasks outgrew its window. The
  window is a property of this card: 32 GB holds the weights plus 72K tokens of KV, and nothing frees more.
  qwen's 131K covers the 63.5K the largest task needed.
- **What would change it:** a box where Inkling gets the window qwen has. The Mac Studio (128 GB) removes the
  64K cap; rerun `probe-subagent.ps1` there with `-SubContext 131072` and see whether the step count and thinking
  shrink when nothing gets compacted. If Inkling is then within ~1.5x of qwen's wall time, switching is worth it
  for the correctness. If it still takes 3x the steps, the slowness is the model, not the card.

## Reproduce

```powershell
# Inkling: SGLang container on the 5090 (stop the tray Ollama's qwen first), then
.\probe-subagent.ps1 -Config inkling -MainUrl http://<5090>:11434/v1 -SubUrl http://<5090>:30000/v1 -SubModelId thinkingmachines/Inkling-2-Mini
# qwen: stop the container, qwen3.8:27b in the tray Ollama
.\probe-subagent.ps1 -Config qwen -MainUrl http://<5090>:11434/v1 -SubUrl http://<5090>:11434/v1 -SubModelId qwen3.8:27b -SubContext 131072
.\summarize-subagent.ps1
python bench-speed.py --url http://<5090>:30000/v1 --model thinkingmachines/Inkling-2-Mini --label inkling --lengths 9850,19700,39400
python bench-speed.py --url http://<5090>:11434/v1 --model qwen3.8:27b --label qwen --lengths 9850,19700,39400
```
