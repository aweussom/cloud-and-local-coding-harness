# T-003: Config D, two Ollama slots

- [ ] 5090 GUI or env: `OLLAMA_NUM_PARALLEL=2`; wrapper: `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=3`.
- [ ] VRAM from Task Manager, not `ollama ps` (under-reports by about 6 GB [OBSERVED 2026-10-29]). No sysmem spill.
- [ ] Keep only if aggregate tok/s beats B (`brain/plans/PLAN.md` phase 5).
