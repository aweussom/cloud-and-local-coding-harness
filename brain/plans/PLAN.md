# PLAN: Claude Code with cloud main model and local RTX 5090 subagents

Written 2026-10-29. Nothing below has been executed yet.

## Goal

Run Claude Code with:

- main conversation on `glm-5.3:cloud` (Ollama cloud subscription)
- subagents and background tasks on `qwen3.8:27b` (local, RTX 5090)

Then decide, with numbers, whether the setup is worth keeping.

## Architecture

```
laptop: claude (wrapper script) --HTTP/Anthropic API--> 5090 Ollama :11434 (over Tailscale)
                                                         |-- qwen3.8:27b    -> local GPU
                                                         `-- glm-5.3:cloud  -> Ollama cloud
```

The laptop's Ollama is not involved. Claude Code accepts only one `ANTHROPIC_BASE_URL`, so the 5090's Ollama
serves both models. [DOCUMENTED: single base URL] [INFERRED: a signed-in remote Ollama proxies `:cloud` models
exactly like the local one does]

## Model choice

Pick `qwen3.8:27b`. `latest` has the same digest: 18 GB Q4_K_M, 256K context. [DOCUMENTED ollama.com/library/qwen3.8/tags]

| Model | Why / why not |
|---|---|
| `qwen3.8:27b` | Fastest of the three on a third-party 5090 run: 16 problems in 6.3 min, 14/16 solved. Vendor reports 61.7 on SWE-bench Pro. [DOCUMENTED, third-party/vendor] |
| `muse-glimmer` | Solved 15/16, but took 16.2 min. Speed drops from 76 to 20 tok/s with 40K tokens of context. Subagents read many files, so this rules it out. [DOCUMENTED, third-party] |
| `nemotron-3.5-bolt:latest` | Points to the 74B-A9B in an IQ2 quant (25 GB). Scores 21.4 on the Artificial Analysis Intelligence Index, against 34 for Qwen3.8. A 2-bit quant is a poor bet for strict tool calls. [DOCUMENTED tags/index] [INFERRED tool-call risk] |

Nobody has published tool-calling reliability numbers for any of these. Phase 4 measures it.

## Phase 1: 5090 server

The 5090 runs Windows. It is on the tailnet (address in `OLLAMA_5090_URL` on the laptop),
already listening there and signed in to Ollama cloud. Ollama 0.40.1, `muse-glimmer:latest` already pulled.
[OBSERVED 2026-10-29]

1. Upgrade Ollama to the latest release. The laptop runs 0.40.11. Match it or newer.
2. Pull the model and sign in:
   ```sh
   ollama pull qwen3.8:27b
   ollama signin
   ```
3. Expose Ollama on the tailnet and raise the context:
   ```powershell
   setx OLLAMA_HOST 0.0.0.0:11434
   setx OLLAMA_CONTEXT_LENGTH 65536
   setx OLLAMA_KEEP_ALIVE 30m
   setx OLLAMA_NUM_PARALLEL 1
   setx OLLAMA_MAX_LOADED_MODELS 1
   ```
   Quit Ollama from the tray and start it again. It reads user env vars at startup.
   [OBSERVED 2026-10-29: did not work. The server took context length from the Ollama GUI settings and ignored
   the `setx` values. Set context length in the GUI. Check what the server actually got in the `server config`
   line of `%LOCALAPPDATA%\Ollama\server.log`]
   The 64K context is a [GUESS]. Claude Code's system prompt plus tool definitions run to tens of thousands of
   tokens, and Ollama's default context would truncate it. 18 GB of weights plus a 64K KV cache should fit in 32 GB.
   Check with `nvidia-smi` in phase 2.

   Concurrency: Ollama never loads a second copy of the same model. Parallel requests share the one loaded model,
   with `OLLAMA_NUM_PARALLEL` slots. Each slot gets its own KV cache at the full context length, so VRAM grows
   with every slot. Requests beyond the slot count wait in a queue. Start at 1 slot. Phase 5 tests 2.
   [INFERRED from how Ollama's scheduler works. Verify with `ollama ps` and `nvidia-smi` under load]
4. Limit port 11434 to the tailnet (firewall or Tailscale ACL). It has no authentication.

## Phase 2: Verify the endpoint from the laptop

Set `$H = $env:OLLAMA_5090_URL`.

```powershell
Invoke-RestMethod "$H/api/version"
(Invoke-RestMethod "$H/api/tags").models.name     # expect qwen3.8:27b and the :cloud models
```

Test the Anthropic-compatible endpoint with both models:

```powershell
foreach ($m in 'glm-5.3:cloud','qwen3.8:27b') {
  $body = @{ model=$m; max_tokens=50; messages=@(@{role='user';content='Say OK'}) } | ConvertTo-Json -Depth 5
  Invoke-RestMethod "$H/v1/messages" -Method Post -ContentType 'application/json' `
    -Headers @{ 'x-api-key'='ollama'; 'anthropic-version'='2023-06-01' } -Body $body |
    Select-Object model, @{n='text';e={$_.content.text}}
}
```

On the 5090, `ollama ps` should show `qwen3.8:27b` at 100% GPU. If it shows a CPU/GPU split, lower `OLLAMA_CONTEXT_LENGTH`.

## Phase 3: Laptop configuration (isolated)

Do not put the env vars in this repo's `.claude/settings.json` or in `~/.claude/settings.json`. Either one would
reroute every normal Claude Code session. Launch through a wrapper instead.

`claude-hybrid.ps1`:

```powershell
param([Parameter(ValueFromRemainingArguments)] $Rest)
$env:ANTHROPIC_BASE_URL            = $env:OLLAMA_5090_URL
$env:ANTHROPIC_AUTH_TOKEN          = 'ollama'
$env:ANTHROPIC_API_KEY             = ''
$env:ANTHROPIC_MODEL               = 'glm-5.3:cloud'
$env:ANTHROPIC_DEFAULT_OPUS_MODEL  = 'glm-5.3:cloud'
$env:ANTHROPIC_DEFAULT_SONNET_MODEL= 'glm-5.3:cloud'
$env:ANTHROPIC_DEFAULT_HAIKU_MODEL = 'qwen3.8:27b'
$env:CLAUDE_CODE_SUBAGENT_MODEL    = 'haiku'
$env:CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = '2'
# Built-in Explore/Plan ignore CLAUDE_CODE_SUBAGENT_MODEL. Override Explore by name:
$agents = '{"Explore":{"description":"Read-only codebase search. Use for broad file/code lookups.","prompt":"You are a read-only search agent. Find and report; never edit.","tools":["Glob","Grep","Read","Bash"],"model":"haiku"}}'
claude --agents $agents @Rest
```

Design notes:

- Subagents are routed through the `haiku` alias, not the raw name `qwen3.8:27b`. The docs say frontmatter
  `model:` takes "the same values as `--model`" but don't confirm a non-Anthropic name with a colon. The alias
  avoids that question. [DOCUMENTED precedence, code.claude.com/docs/en/subagents]
- Model resolution order for a subagent: per-invocation param > frontmatter > `CLAUDE_CODE_SUBAGENT_MODEL` > main model. [DOCUMENTED]
- To pin every subagent to the 5090, Plan included, add `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (v2.1.257+;
  the laptop runs 2.1.312). Start without it and add it if Plan or ad-hoc calls end up on the cloud model.
- `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` caps how many subagents run at once (default 20, v2.1.217+). Past the
  cap, the spawn fails and Claude is told not to retry. Set to 2: one runs on the 5090, one waits in Ollama's
  queue. This keeps Claude from fanning out subagents that then time out waiting. The cap isn't enforced for
  ultracode/Workflow agents. [Source: GitHub issues and blogs. The official env-var reference doesn't list it.
  Check `claude --help` or the changelog]
- Forks run on the main model (cloud), so they don't load the 5090. Haiku-tier background calls (titles,
  summaries) do hit the 5090 and compete with subagents for the slot.
- Check before relying on it: confirm `--agents` exists in 2.1.312 with `claude --help`. If it doesn't, use
  `.claude/agents/Explore.md` inside a scratch test project, never in a real repo.
- Claude Code may show warnings about an unknown model or a non-Anthropic endpoint. Note them; don't chase them
  unless something breaks.

## Phase 4: Smoke tests

Run each test in a throwaway git repo with a small Python project.

| # | Test | Pass criterion |
|---|---|---|
| 1 | `.\claude-hybrid.ps1 -p "reply OK"` | Answers; 5090 log shows `glm-5.3:cloud` request |
| 2 | `.\claude-hybrid.ps1 --model haiku -p "list files with Glob"` | Valid tool call from Qwen |
| 3 | Interactive: "use the Explore agent to find where X is defined" | 5090 `ollama ps` shows qwen loaded; correct answer |
| 4 | Interactive: "spawn a general-purpose agent to write tests for module Y" | Subagent on qwen; tests run |
| 5 | `/model` shows `glm-5.3:cloud` | Main model unchanged after subagent use |

On the 5090, watch which model handles each request:

```sh
journalctl -u ollama -f | grep -E 'model|POST'
```

## Phase 5: Evaluation

Run the same 5 to 10 real tasks from one of my repos in three configs:

- A: all cloud (`ollama launch claude --model glm-5.3:cloud` against the 5090)
- B: hybrid (this plan)
- C: hybrid with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`
- D: B with `OLLAMA_NUM_PARALLEL=2` and `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=3`. Keep only if VRAM fits with no
  CPU spill and aggregate tok/s beats B.

Record per task:

- task completed y/n
- wall-clock time
- subagent tool-call failures (malformed JSON, wrong tool names, loops)
- 5090 tok/s (`ollama ps`, or `--verbose` on a direct run)
- context overflows or truncations
- cloud usage consumed (Ollama account page, before and after)

Decision rule:

- Keep B if it completes the same tasks as A with fewer than 1 tool-call failure in 10 subagent runs, and wall
  time is no more than 1.5 times A's.
- If the only gain is cloud quota and quota isn't the bottleneck, drop it.

Make the task set reusable. Keep it in `tasks/`: one file per task with a prompt, a fixed repo commit and a pass
check. A script runs them against any config and writes `results/<config>-<date>.json`. Phase 6 reruns it
unchanged.

## Phase 6: Mac Studio, fully offline (delivery mid-November 2026)

Hardware: M5 Max, 18-core CPU, 40-core GPU, 128 GB RAM, 512 GB SSD, 10 GbE.
[DOCUMENTED order spec]

Target layout:

```
laptop: claude --> router --+--> Mac Studio   (main model, big MoE, MLX)
                            `--> 5090 Ollama  (subagents, qwen3.8:27b)
```

Two hosts behind one `ANTHROPIC_BASE_URL` means the router from fallback 3 is no longer optional. If both
backends speak `/v1/messages`, it routes by `model` and passes the stream through. No translation.

What is known, from secondary sources only [DOCUMENTED, secondary]:

- 614 GB/s memory bandwidth on the 40-core GPU. Decode is bandwidth-bound.
- A Neural Accelerator in every GPU core. Reported prefill gains over M4 Max are 2.4-4x. One example: Qwen3-14B
  with 16K context takes about 8-10 s, against 30-40 s. Prefill is the agent bottleneck: Claude Code sends tens
  of thousands of tokens before the first user word.
- MLX uses the accelerators. llama.cpp-based Ollama did not as of June 2026. Ollama ships `-mlx` tags; how it
  does on M5 is unknown.
- `mlx-lm server` has an in-memory LRU prefix cache. Disk persistence (mlx-lm PR #1405) was closed unmerged.
- Several MLX-native servers speak Anthropic `/v1/messages` and have a persistent or SSD prefix cache, which is
  the NoLlama prewarm idea already built. All performance claims are their own; no independent benchmark found
  [DOCUMENTED, READMEs, 2026-10-29]:
  - vllm-mlx (github.com/waybarrios/vllm-mlx): continuous batching, paged KV, prefix cache, SSD tier,
    documented Claude Code support. v0.5.0, 2026-09-17.
  - Rapid-MLX (github.com/raullenchai/Rapid-MLX): focused on agent tool calls. Radix prefix cache saved to disk
    on shutdown. Claims Claude Code is tested against it each release.
  - oMLX (omlx.ai), mlx-serve (github.com/PhilipJohnBasile/mlx-serve): SSD-paged KV cache, Anthropic API.
- macOS caps GPU-wired memory at about 75% of RAM, roughly 96 GB. `sysctl iogpu.wired_limit_mb` raises it.
  [GUESS: still true on current macOS]

Steps:

1. Baseline the box. Raise `iogpu.wired_limit_mb` to about 112 GB, leaving the OS 16 GB. Install current Ollama.
2. Prefill test, first and decisive. Capture one real Claude Code first request with `ANTHROPIC_LOG=debug` or at
   the router, and replay it cold and warm. Record:
   - cold prefill seconds
   - warm, cached, prefill seconds for the next turn
   - decode tok/s
   Run the same model and the same captured request on every server in step 3.
3. Serving decision, by measurement. Not tied to Ollama.
   - Contestants: vllm-mlx, Rapid-MLX, and Ollama with its MLX engine as the baseline. Add oMLX only if the
     first two disappoint.
   - Pass criteria: cold prefill under 30 s, warm turns under 3 s, cache survives a server restart, zero
     malformed tool calls over the phase 5 task set. Decode tok/s breaks ties.
   - Check that the server uses the M5 Neural Accelerators. Compare its prefill speed against plain `mlx-lm`
     on the same model; a server that doesn't use them will be clearly slower.
   - If none passes: NoLlama with an MLX backend plus `/v1/messages`, porting the prewarm
     (`prewarm-8000.json`, ..\nollama docs/AGENTS.md). Last resort, because the off-the-shelf servers above
     already claim it.
   - Not mlx-lm + LiteLLM: two layers outside my control, and still no prewarm.
4. Model. Pick at delivery, not now. The candidate is a MoE with few active parameters that fits about 100 GB
   with 64K+ context. If a model good enough also fits the 5090's 32 GB with usable context, the 5090 wins on
   speed.
5. Router. Look at Ollama Herd first (ollamaherd.com/guides/claude-code). It puts several machines (Mac, NVIDIA
   Linux, Windows) behind one endpoint and sends each request to the machine that already has the model loaded,
   with the shortest queue [DOCUMENTED, project's own guide; untested here]. That is this step, ready-made, if
   it passes `/v1/messages` through untouched and the MLX server from step 3 can sit behind it. Otherwise write
   the passthrough: route `model` to a host, config in one file, log `file:line` per request with the chosen
   backend. Related, same alias trick as ours: claude-code-router + llama-swap on a Mac Studio
   (spicyneuron.substack.com, "A Mac Studio for Local AI - 6 Months Later").
6. Offline test. Unplug the network and set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. Check that Claude
   Code starts and works against the custom base URL with no login. [GUESS: it does] Web search and fetch will
   be gone. Note what else breaks.
7. Rerun the phase 5 task set against the cloud baseline, the hybrid setup and fully offline.
8. If cold prefill stays bad even with the cache, test a harness with a small system prompt (pi / oh-my-pi)
   before giving up on the model. The harness's prompt size is a knob too.

Done when: the offline config passes at least 80% of the tasks the cloud baseline passes, and its wall-clock
time is something I can live with. Written down in `STATUS.md` with the numbers.

## Fallbacks

In order of preference:

1. Subagent quality poor: try `qwen3.8:27b-q8_0` (30 GB) with `OLLAMA_CONTEXT_LENGTH=32768`. Less context, better
   weights. Check VRAM fit first.
2. Tool calls from Qwen are flaky: test `muse-glimmer` for subagents only, and accept the slowdown.
3. The 5090's Ollama won't proxy `:cloud` models, or cloud and local must be split: write a dumb router that
   reads `model` from the request body. `*:cloud` goes to ollama.com with my API key, everything else goes to the
   5090. Responses stream through untouched, with no protocol translation. Precondition, test with curl first:
   ollama.com accepts Anthropic `/v1/messages` with a bearer key [GUESS]. Before writing anything, try
   claude-code-router (CCR). It is a local Anthropic-speaking proxy on :3456 that routes by request type
   (default/think/background/longContext) and per subagent via a `<CCR-SUBAGENT-MODEL>` prompt tag. One
   unverified report: CCR v2.0.0 fails to route to Ollama Cloud with "provider not found". Test that first.
   Second choice: LiteLLM proxy. Write my own only as a pure passthrough. Leave Anthropic-to-OpenAI translation
   with tool calls to existing projects. `..\nollama` doesn't help here. It serves only OpenAI `/v1/chat/completions` and
   Ollama `/api/chat`, with no `/v1/messages` and no upstream forwarding. Reusable: Flask scaffolding only.
   [OBSERVED 2026-10-29, nollama.py routes]
4. Claude Code fights the non-Anthropic endpoint: try OpenCode. It supports a model per subagent [DOCUMENTED v2
   docs], but 2.0 is still a beta (`opencode2`, npm tag `next`). Prefer 1.x stable for the test.

## Output artifacts

- `STATUS.md`: what works today, replaced after each phase.
- `TODONT.md`: failed approaches, with numbers.
- `claude-hybrid.ps1`: the wrapper, committed with the hostname as a placeholder or read from env.
- `results/`: raw phase 5 measurements, one file per run.

## Open questions

- 5090 OS and Tailscale hostname.
- Whether Ollama cloud rate limits apply per account or per signed-in device. This matters if the laptop and the
  5090 both run cloud sessions.
