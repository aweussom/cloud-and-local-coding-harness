# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An evaluation of Claude Code with a cloud main model (Ollama Cloud) and local models for subagents, or fully
offline. Local today: RTX 5090 Ollama at 100.98.33.88 (Windows box, changed by the owner by hand; only its HTTP
API is reachable from here). Later: Mac Studio M5 Max. Probes, a wrapper, measurements; not a product.
`README.md` has the runbook, `brain/plans/PLAN.md` the plan, `brain/TODONT.md` the traps already paid for.

## Working rules

**Read `STATUS.md` first**: what is true today. Read `brain/TODONT.md` before proposing anything structural.

- **Epistemic tags.** Every non-trivial claim in `STATUS.md`, `brain/` and docs carries `[DOCUMENTED <source>]`,
  `[OBSERVED <date>]`, `[INFERRED]` or `[GUESS]`. Updates keep or downgrade a tag; an upgrade needs cited
  evidence. Correct a claim where it stands, never by appending elsewhere.
- **Look it up, or say you did not.** Versions, CLI flags, API surfaces, vendor docs are looked up before they
  are written down. Anything from memory is tagged `[GUESS]`.
- **Findings without a mandate** go to `brain/proposed/`, one file each, not into code or docs.
- **Counts in prose** are generated, dated, or left out. `STATUS.md` is replaced, never appended to.
- **Sysadmin style.** Flat control flow, named intermediates, fail loud, log intention. No flow-wrapping
  helpers. Match the file you are in.

**Working state** lives in `brain/`, one file per item; `brain/README.md` has the rules. `next/` needs the owner,
`todo/` is work, `proposed/` is untrusted. The directory is the state, `git mv` the transaction. Commits name
the item: `N-001: ...`, `T-002: ...`.

**Docs:** every function gets a prose docstring in the same edit: what, `Why:`, `In:`/`Out:`. The convention is
`docs/DOCUMENTING-A-CODEBASE.md`; `check_docs.py` gates it, enforced lists in `docs-toolkit.json` grow only with
files already at 100 %.

## Git

- Pushing straight to `main` is the normal workflow.
