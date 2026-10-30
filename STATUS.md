# STATUS

Claude Code runs against the RTX 5090's Ollama with `glm-5.3:cloud` as main model and `qwen3.8:27b` for
subagents, or with qwen for everything. Three cheaper cloud main models pass too; `glm-5.3-flash` is the
everyday candidate at about a ninth of glm-5.3's token price. Every config passes every synthetic probe task, so
the probes no longer tell them apart on quality. The next step needs real tasks from the owner's backlog
(N-001). The fully offline target, the Mac Studio, is waiting on delivery (N-005).

## Verified

- 2026-10-29: the 5090 (Ollama 0.40.13) serves `glm-5.3:cloud` and `qwen3.8:27b` from one
  Anthropic `/v1/messages` endpoint. Raw tool calls: glm 3/3, qwen 5/5, muse-glimmer 3/3; qwen warm 0.4 s.
- 2026-10-29: `claude-hybrid.ps1` (Claude Code 2.1.312) routes Explore and general-purpose subagents to qwen.
  Token use per model proves it.
- 2026-10-29: `probe-agent.ps1`, 5 tasks x 3 configs, one run each, 15/15 PASS:

  | Task | A cloud | B hybrid | qwen only |
  |---|---|---|---|
  | fix | 18 s | 14 s | 20 s |
  | feature | 28 s | 25 s | 41 s |
  | delegate | 18 s | 31 s | 36 s |
  | hunt (multi-file root cause, trap, hidden test) | 29 s | 33 s | 47 s |
  | rename (multi-file, told to leave files alone) | 49 s | 24 s | 56 s |

  qwen root-causes `hunt` and follows the `rename` constraint. In B, glm started a subagent only when told to.
  On small repos B is effectively A. Run-to-run noise is large (A delegate 25 s, then 18 s). Raw rows in
  `results/agent-probe-*.jsonl`.
- 2026-10-30: cheaper Ollama Cloud models as main AND subagent model, same 5 tasks, one run each, 15/15 PASS.
  Prices per 1M tokens input/output [DOCUMENTED ollama.com/pricing, 2026-10-30]:

  | Task | glm-5.3-flash | minimax-m3.1-flash | mistral-large-4 |
  |---|---|---|---|
  | fix | 31 s | 30 s | 31 s |
  | feature | 39 s | 81 s | 59 s |
  | delegate | 28 s | 140 s | 48 s |
  | hunt | 45 s | 84 s | 47 s |
  | rename | 40 s | 113 s | 38 s |
  | session quota, 5 tasks | 0.80 % | 0.44 % | 2.33 % |
  | price in/out | $0.15 / $0.50 | $0.15 / $0.60 | $0.70 / $2.10 |

  glm-5.3 for reference: $1.40 / $4.40. minimax makes the most tool calls (up to 33 per task) and is 2-3x
  slower, but used the least quota, probably from its $0.015 cached input [INFERRED]. Quota is the session
  percent read before and after each task.

## Built, not proven

- Smoke test 5: `/model` unchanged after subagent use in an interactive session (T-002).

## In flight

Nothing.

## Known and accepted

- Keep-alive is the 5090 server default, 5 min. A subagent after idle pays a reload, about 20 s seen once (T-004).
- 8.5 GB of VRAM headroom with qwen at 64K context (Task Manager 23.5 of 32 GB). Config D waits on this (T-003).

## Blocked on whom

- Owner: pick real backlog tasks (N-001).
- Delivery: Mac Studio M5 Max, mid-November 2026 (N-005).

---
Replaced, never appended. History is `git log -- STATUS.md`.
