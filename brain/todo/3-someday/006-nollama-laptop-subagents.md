# T-006: Laptop iGPU as subagent host via NoLlama

Not expected to be good. The point: a user whose only hardware is a laptop is better off with a slow local model
than with none.

**Checked:** ..\nollama docs/BENCHMARKS.md (140V, Qwen3-Coder-30B-A3B: decode 15.7 tok/s, cold prefill
260 tok/s, cached-prefix TTFT 1.9 s [OBSERVED 2026-10-07/08 in nollama]); its STATUS.md (only Qwen3-Coder-30B-A3B
passed the agent probe); this repo's `probe-agent.ps1`. NoLlama has prefix caching and startup prewarm
(docs/AGENTS.md). It does not serve Anthropic `/v1/messages` [OBSERVED 2026-10-29, nollama.py routes].

- [ ] Endpoint: Claude Code needs `/v1/messages`. Either add it to NoLlama or put a translating proxy in front.
      Decide which before building (check NoLlama's git log first).
- [ ] Capture one subagent's first request (about 17k tokens [OBSERVED 2026-10-29, Explore test]) from a probe run.
- [ ] Replay it cold and warm against NoLlama on a 32 GB laptop (iGPU at 87%). Expect about 65 s cold
      [INFERRED: 17k / 260 tok/s]. The question is whether prewarm brings the warm case near 2 s.
- [ ] If yes: `probe-agent.ps1` B config with the laptop as subagent host. Record pass rate and wall time.
- [ ] Result, good or bad, into `brain/TODONT.md` or `STATUS.md`.
