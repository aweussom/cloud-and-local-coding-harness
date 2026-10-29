---
name: document-codebase
description: Document an existing codebase so it stays documented — prose-contract docstrings on every function, Mermaid logic diagrams with machine-checked coverage, epistemic tags, and the check_docs.py ratchet. Use when asked to document a repo, add docstrings, draw architecture or logic diagrams, or set up the docs checker.
---

# Document a codebase

The decisions are in `DOCUMENTING-A-CODEBASE.md` — `conventions/` in the docs-toolkit
repo, `docs/` where it was installed. Read it once; this file is the order
and the rules that get skipped.

## Order — this order, for a reason

1. **Checker first, enforced lists empty.** Copy `check_docs.py` and
   `docs-toolkit.json` in if absent, empty every `enforced` list, run
   `python check_docs.py --audit` and **keep the number**. That is the baseline
   you quote at the end and the instrument you find gaps with.
2. **Diagrams before docstrings.** Drawing a flow is how you read every function
   in it; docstrings first means reading twice. Node IDs are real function
   names; dashed edges are failure/dead paths; ALL-CAPS nodes carry the *why*; a
   `%% covers:` header names what each diagram covers. `--render` after every
   diagram, not at the end.
3. **Docstrings, one file per commit, hot paths first.** Prose contract: one
   line of what, `Why:`, `In:`/`Out:` naming the edge case. No `@param`/`Args:`
   unless something consumes them. Add the file to `enforced` in the commit
   that brings it to 100 %, never before.
4. **The convention into the repo last.** Paste `conventions/CLAUDE-snippet.md`
   between its markers into `CLAUDE.md`. Then a `## <Language> specifics` section
   *after* the end marker for each language, and, if the code talks to a device or a remote system,
   `docs/<NAME>-BEHAVIOUR.md` for the dated facts about it that no function owns.

## Tags — the part a rewrite cannot recover

Tag only what the code does not say: `[DOCUMENTED]` (name the source),
`[OBSERVED <date>]`, `[INFERRED]` (say what would confirm it), `[GUESS]`. A
tagged line is evidence, not phrasing; updates preserve or downgrade, upgrades
need the run or the source. **Review your own output for over-tagging before
reporting done** — that is the direction the error comes from.

## Verify, then report

Run the repo's tests; docstring edits move nothing, prove it. Frontend work is
verified in a browser, not by reading. Report the before/after `--audit`
numbers and **what the sweep found** — stale docs, dead code, unreachable
branches — which is usually the most valuable output and the easiest to forget.
Say what is not covered and why. If the checker flags a diagram your change did
not really invalidate, say so in the commit message.

## Do not

Populate `enforced` before 100 %. Add a toolchain to a repo that has none — no
`package.json`, ruff or pydocstyle for the checker's sake. Where the repo already has
one, its documentation lint *is* the adapter: TypeScript and JavaScript repos with
ESLint take `conventions/eslintrc-jsdoc.js` (scope-aware, requires the `Why:`), and
`check_docs.py` keeps the coverage headers and the diagram staleness. Keep two copies of a diagram. Tag everything.
