# cloud-and-local-coding-harness

Can I run Claude Code with a cloud model as the main brain and my own GPU doing the subagent work? And later, with no cloud at all?

Today the cloud side is `glm-5.3:cloud` on an Ollama Cloud subscription. The local side is an RTX 5090 running Ollama on my tailnet, with `qwen3.8:27b`. A Mac Studio M5 Max with 128 GB is on order, delivery mid-November 2026. End goal is a fully offline setup on that box. Slow, hopefully good enough for daily use.

This is an evaluation. Probes, one wrapper, and the numbers. Not a product.

## What I found so far

Measured 2026-10-29, one run per task, so the times are noisy:

| Task | All cloud | Hybrid | qwen only |
|---|---|---|---|
| fix | 18 s | 14 s | 20 s |
| feature | 28 s | 25 s | 41 s |
| delegate | 18 s | 31 s | 36 s |
| hunt | 29 s | 33 s | 47 s |
| rename | 49 s | 24 s | 56 s |

All 15 passed. `qwen3.8:27b` drives Claude Code on its own, including a root-cause hunt across modules with a trap in it. The hybrid setup only sent work to the 5090 when the prompt asked for a subagent. On a small repo it behaves like all-cloud. `STATUS.md` has what is true today, `brain/TODONT.md` what did not work.

## How it works

Claude Code talks Anthropic `/v1/messages` to ONE base URL. Ollama serves that API, and a signed-in Ollama also proxies the `:cloud` models. So the 5090's Ollama serves both the cloud model and the local one, and Claude Code only ever talks to the 5090:

```
laptop: claude --settings ... --> 5090 Ollama :11434 --+-- qwen3.8:27b  -> local GPU
                                                       `-- glm-5.3:cloud -> Ollama Cloud
```

Subagents are routed through the `haiku` alias: `ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen3.8:27b` and `CLAUDE_CODE_SUBAGENT_MODEL=haiku`. The built-in Explore agent ignores that variable, so it is overridden by name with `--agents`. All of it goes in a `--settings` file, because my global `~/.claude/settings.json` forces subagents to sonnet and only `--settings` outranks it.

## Runbook

### Prerequisites

- Claude Code on the laptop (tested on 2.1.312), PowerShell 7.
- The 5090 box: Ollama (tested on 0.40.13), signed in with `ollama signin`, reachable on the tailnet at `100.98.33.88:11434`. Port 11434 has no auth, so keep it tailnet only.
- `OLLAMA_API_KEY` in the laptop's environment if you want quota numbers in the probe.

### 5090 setup (by hand, on that box)

1. `ollama pull qwen3.8:27b`
2. Ollama GUI settings: context length 64K. Do NOT use `setx` for this - the desktop app ignored it.
3. Restart Ollama from the tray, then check what the server actually got:

   ```powershell
   Select-String -Path "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'server config' | Select-Object -Last 1
   ```

4. Optional: NVIDIA Control Panel, "CUDA - Sysmem Fallback Policy" = "Prefer No Sysmem Fallback". A VRAM spill then fails loud instead of going quietly slow.

### Check from the laptop

```powershell
# Is it up, and what is loaded (context length, VRAM as Ollama sees it)
Invoke-RestMethod http://100.98.33.88:11434/api/version
(Invoke-RestMethod http://100.98.33.88:11434/api/ps).models | Select-Object name, context_length, size_vram

# Raw tool calls against one model, exit 1 on any miss
.\probe-toolcall.ps1 -Model qwen3.8:27b -Runs 5
```

`ollama ps` under-reports VRAM by about 6 GB. Task Manager on the 5090 is the truth.

### Use it

```powershell
.\claude-hybrid.ps1          # glm-5.3:cloud main, qwen3.8:27b subagents
```

Any `claude` argument passes through. Routing is in `hybrid.settings.json`.

### Run the probe

```powershell
.\probe-agent.ps1 -Config A-cloud  -MainModel glm-5.3:cloud -SubagentModel glm-5.3:cloud
.\probe-agent.ps1 -Config B-hybrid -MainModel glm-5.3:cloud -SubagentModel qwen3.8:27b
.\probe-agent.ps1 -Config L-local  -MainModel qwen3.8:27b   -SubagentModel qwen3.8:27b
```

Five tasks through `claude -p` against throwaway fixtures. Pass/fail is decided by running the original tests (plus a hidden one for `hunt`), never by reading the transcript. Each task records tool calls, tokens per model and the Ollama quota used. Rows go to `results/agent-probe-<config>.jsonl`, transcripts to `results/agent-probe/`. Run configs one after another: they share a workdir and the 5090 has one slot.

`-KeepWorkdir` leaves the fixture under `%TEMP%\claude-agent-probe*` so you can look at what the agent did.

## What is next

- Real tasks from my own backlog in the probe. The synthetic ones pass everywhere and no longer tell the configs apart.
- The Mac Studio: prefill speed first, then a bake-off between MLX servers that speak `/v1/messages`. `brain/plans/PLAN.md` phase 6.

## Layout

- `claude-hybrid.ps1`, `hybrid.settings.json`: the wrapper.
- `probe-toolcall.ps1`, `probe-agent.ps1`: the probes.
- `results/`: raw measurements, kept byte for byte, failed runs included.
- `brain/`: plan, decisions, what did not work, open items.
- `STATUS.md`: what is true today.
