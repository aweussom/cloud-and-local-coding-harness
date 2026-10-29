# T-004: Measure subagent reload after 5 min idle

- [ ] Keep-alive is the server default, 5 min; Claude Code sends no `keep_alive`. One reload seen at about 20 s
      [OBSERVED 2026-10-29, Glimmer unload + qwen load].
- [ ] Measure qwen-only reload 3x. If it hurts, raise keep-alive in the 5090 Ollama GUI.
