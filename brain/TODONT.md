# The TODONT.md convention

A log of things that were tried and did **not** work, or that work but are not
worth doing. Its purpose is to stop attractive-but-unsuitable ideas being
reintroduced without materially new evidence.

This file is both the explanation and the template. Copy it into your repo as
`TODONT.md` (or `brain/TODONT.md`), header intact, if you do not already have one.

## Why it earns a file of its own

A docstring is the wrong home for a decision that spans the codebase — nobody
looks in `_parse_thing()` to find out why you are not using an ORM. And a
rejected approach has no natural function to attach to, which is exactly why it
gets re-proposed every six months.

The failure this prevents is specific: someone (a colleague, a coding harness,
you in March) proposes the sensible-sounding thing, nobody remembers it was
measured and rejected, and it gets built.

## Rules

- **Log the ones that sound obviously sensible.** Those are precisely the ones
  that come back. "Use embeddings for retrieval" and "cache the last known
  value" both sound like good engineering; both can be wrong for a specific
  reason that took a day to establish.
- **A verdict needs evidence, not an opinion.** A number, a failure mode, a
  reproduction. "It felt slow" is not an entry; "18% recall at top-10 against a
  heuristic prefilter's 100%" is.
- **Say what would change the decision.** Without that the file reads as dogma
  and gets ignored. With it, it is a standing invitation to bring better
  evidence.
- **Update, do not duplicate.** When a verdict narrows or reverses, amend the
  entry with `**Update <date>:** …`. The history of a reversal is worth as much
  as the original.
- **Read it before proposing anything structural.** That is the whole return on
  writing it.

## Entry template

```markdown
## Do Not <the thing>, <scoped how>

**Tested:** <date> with <versions, data, scale>.
   — or —
**Considered:** <date>. <what prompted it>

**Decision:** <the rule, stated so it can be followed without reading on>

### What Was Tried

<enough that someone could repeat it>

### Result

<the numbers, the table, the failure. This is the load-bearing section.>

### Why This Is Rejected

<the mechanism. Not "it was slow" but why it is slow, or why the approach
cannot work for this shape of problem.>

### Would Change This Decision

<the specific, measurable condition. "A model reaching >50% top-10 recall on
the same validation method", not "if things improve".>

### Reproducing the Test

<paths, commands, fixtures — or an honest note that the scripts were
throwaway and what the inputs were>
```

Not every entry needs every section. A "Considered, rejected on reasoning"
entry can be four lines. A tested-and-measured one earns the full shape.

## Worked example, abridged

From the project this toolkit came from — kept because it shows what "evidence,
not opinion" looks like in practice:

```markdown
## Do Not Use Embedding Retrieval for Alarm-to-Ticket Matching

**Tested:** 2026-04-30 with qwen3-embedding:4b (2560 dim) against the live
corpus: 99 tickets, 12 alarms, and the existing matcher's output.

**Decision:** Do not use embeddings, vector similarity, or RAG retrieval to
select the candidates an LLM may inspect.

### Result

Cumulative rank of the known-correct ticket, 11 matched alarms, four input
variants. Best variant reached 2/11 = 18% recall at top-10. The existing
heuristic prefilter delivered 11/11.

Replacing it would have silently removed ~80% of the correct answers before
the LLM ever saw them.

### Why This Is Rejected

This problem is dominated by operational identifier collision, not by broad
semantic similarity. Tickets describing site-wide incidents embed as "broad
outage" and consistently outrank the specific correct ticket. The one alarm
that embedded well used a unique server name — 1 of 11.

### Would Change This Decision

Retest only if the matching ground truth shifts from identifier occurrence
toward genuinely describing the same incident, OR a materially stronger model
demonstrates >50% top-10 recall on the same validation method. A better JSON
parser or a repair loop is not enough; the semantic metric must move.
```

Note what makes it useful: the number that would flip it is stated, so the
entry does not have to be argued with — it has to be beaten.

## Relationship to epistemic tags

They cover different scopes and the boundary is clean:

| | Where | For |
| --- | --- | --- |
| Epistemic tag | Inside a docstring | A claim about *this function* that the code cannot state |
| TODONT entry | Its own file | A decision about *the codebase* that has no function to live in |

An `[INFERRED]` tag that gets chased down and turns out to be a dead end is
often a TODONT entry waiting to be written.

---

# Entries

## Do Not Put Model Routing in ~/.claude or in Shell Env

**Tested:** 2026-10-29, Claude Code 2.1.312.

**Decision:** Routing for a probe or wrapper goes in a settings file passed with `--settings`. Subagents reach the
local model through the `haiku` alias (`ANTHROPIC_DEFAULT_HAIKU_MODEL=<local>`, `CLAUDE_CODE_SUBAGENT_MODEL=haiku`).
Built-in Explore is overridden by name with `--agents`.

### Result

`~/.claude/settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` for
everyday use. Settings env outranks shell env [INFERRED, not tested in isolation], so a wrapper that only sets env
would send every subagent to sonnet, which maps to the cloud model. `--settings` ranks above user settings
[DOCUMENTED code.claude.com/docs/en/settings] and routing worked: token use per model shows qwen doing the subagent
work [OBSERVED 2026-10-29, `results/agent-probe-B-hybrid.jsonl`]. Built-in Explore ignores
`CLAUDE_CODE_SUBAGENT_MODEL` [DOCUMENTED code.claude.com/docs/en/subagents].

### Would Change This Decision

The global settings stop forcing a subagent model, or frontmatter `model:` is shown to accept a raw Ollama name
like `qwen3.8:27b` (untested; the alias avoids the question).

## Do Not Configure the 5090's Ollama with setx

**Tested:** 2026-10-29, Ollama 0.40.13 desktop app on Windows (the 5090 box).

**Decision:** Context length goes in the Ollama GUI settings. Check what the server actually got in the
`server config` line of `%LOCALAPPDATA%\Ollama\server.log` on that box, never by assuming.

### Result

After `setx OLLAMA_CONTEXT_LENGTH 65536` (and the others) plus a tray restart, `server.log` showed
`OLLAMA_CONTEXT_LENGTH:32768 OLLAMA_KEEP_ALIVE:5m0s`. After the GUI change, `ollama ps` showed 65536.

### Would Change This Decision

A server log showing the `setx` values after a restart, for example if Ollama runs as a service instead.

## Do Not Pass Arguments with Spaces or Quotes through Start-Process -ArgumentList

**Tested:** 2026-10-29, PowerShell 7, `probe-agent.ps1`.

**Decision:** Quote every argument by hand. Pass JSON as a file (`claude --agents <file>` is allowed with `-p`).

### Result

All tasks ended in 0 s with `Error: Invalid --agents configuration: invalid JSON: JSON Parse error: Expected '}'`.
`Start-Process` joined the array with spaces and dropped the quotes.

### Would Change This Decision

Moving the launcher off `Start-Process`. It is used because `Start-Job` is refused under application-control
policy on some corporate machines (..\nollama scripts/agent-probe.ps1).

## Do Not Read VRAM from ollama ps

**Tested:** 2026-10-29, qwen3.8:27b at 64K context on the RTX 5090.

**Decision:** VRAM headroom comes from Task Manager (or `nvidia-smi`) on the 5090.

### Result

`ollama ps`: 17.6 GB. Task Manager: about 23.5 GB in use, with no display on the card. About 6 GB unreported.

### Would Change This Decision

`ollama ps` matching `nvidia-smi` within 1 GB on the same load.

## Do Not Run Probe Configs in Parallel

**Considered:** 2026-10-29.

**Decision:** One config after another.

### Why This Is Rejected

All configs share the workdir under `%TEMP%`, and the 5090 runs one Ollama slot (`OLLAMA_NUM_PARALLEL=1`), so
parallel runs corrupt each other's fixtures and measure queueing, not the model.

### Would Change This Decision

A per-config workdir plus config D (two slots) proven to fit in VRAM.

## Do Not Expect the Hybrid Setup to Offload on Small Repos

**Tested:** 2026-10-29, 5 probe tasks, glm-5.3:cloud main, qwen3.8:27b subagents.

**Decision:** Do not count on subagent offload to save cloud quota on small repos. Measure on a large repo before
claiming savings.

### Result

glm started a subagent in 1 task of 5, the one that told it to (`delegate`). In the other 4, qwen had zero token
use, so B behaved as A. Subagents are a repo-size optimisation.

### Would Change This Decision

Probe tasks on a large real repo where Explore gets used unprompted.
