# Documenting a codebase: the repeatable version

The playbook. Run it against a codebase and it produces documentation that
survives the next refactor, without you rediscovering the traps first.

Language-agnostic in structure. The worked examples are Python and
JavaScript, with Go included to show the extension point.

**About the examples.** They are real, from the sweep this was extracted
from: a three-country Flask + Leaflet operations dashboard (toll roads and
ferries — hence the NetXMS, AIS and ferry-quay vocabulary). Concrete beats
invented, so they are kept verbatim. Read `<domain thing>` as "the
equivalent in your codebase"; the shape of each lesson transfers.

Companion: [`../checker/check-docs.ps1`](../checker/check-docs.ps1), with the
project-specific bits marked `# CONFIGURE`.

## What it produced on the source project, in numbers

Measured by `check-docs.ps1 -Audit` before and after, on the source project:

| | Before | After |
| --- | --- | --- |
| Production Python (4 files, 165 defs) | 111 (67%) | **165 (100%)** |
| JavaScript (4 files, 199 functions) | 0 (0%) | **199 (100%)** |
| Whole repo incl. not-yet-scoped tooling | 172/519 (33%) | 423/520 (81%) |
| Logic diagrams | 0 | 14, covering 439 function references |

Cost: one focused session. The diagrams took roughly as long as the
docstrings, and both were dwarfed by *reading* the code — which is where
the value came from.

**What it found.** This is the part worth selling internally. Writing the
docs was not the only output; it surfaced things nobody knew:

- A retired subsystem (MUSE) still drawn as the live path in
  `ARCHITECTURE.md`, five weeks after it was switched off.
- An **unreachable code path**: a burst-debounce waiting on a signal that
  only a never-started thread ever set, so a poll documented as
  event-driven was in fact running on a flat timer.
- Two **permanently empty data buckets** in the Australia stub after a
  data swap narrowed it to one state — harmless today, silently wrong the
  moment a second state returns.
- A stale README claiming 218 OpenStreetMap gantries where the data had
  become 37 customer-supplied toll points.
- An inconsistent API field (`source: "muse.log+sqlite"` on one endpoint,
  `"netxms-direct+sqlite"` on its sibling), recorded as a known wart
  rather than fixed in a docs pass.
- Nine Mermaid parse errors in diagrams that had been read carefully and
  looked fine.

None of those needed a bug hunt. They fell out of having to write down
*why* each function exists.

## The five decisions to make first

Make these explicitly. Each one changes the work.

**1. Scope.** All files, or the code that runs in production first? Phased
is almost always right: it produces a defensible 100% on the code that
matters instead of a uniform 60% everywhere. Here: the three `app.py`
files, one cron script, and four `static/js/*.js` — with benchmark and
tooling scripts deferred and *named* as deferred.

**2. Docstring format.** Prose contract, or formal tags
(`@param`/`Args:`)? Choose tags only if something actually consumes them
— a docs site, `mypy --strict`, IDE contract checks. Otherwise prose, and
see [`TODONT.md`](TODONT.md) in this directory for the argument and a
template. The short version: tags
describe types, and types are the part a reader can already see.

**2b. Epistemic tags — decide this at the same time, not later.** Whether
docstrings mark which statements are *evidence* rather than phrasing. Say
yes. It is what makes the work survive the next rewrite, and retrofitting
it means re-reading everything. See
[Epistemic tagging](#epistemic-tagging--the-part-that-survives-a-rewrite)
below — it is the most important section in this document.

**3. Diagram home.** One place per diagram, or standalone files plus
reduced inline copies? One place. Copies drift and nothing detects it —
the repo this convention was ported from has a file asserting "same
diagram as" another that had already gained three nodes.

**4. Enforcement.** Written rule only, rule plus a checker, or plus a
hook? Rule plus a checker script, run by hand or by the coding harness.
A hook is available but nags mid-flow; add it later if the rule slips.

**5. The ratchet.** Critically: does the checker enforce everything from
day one, or only files that have reached 100%? **Only files at 100%.** A
checker that emits 300 known misses on its first run trains everyone to
ignore its output, and then it is worse than nothing.

## Order of operations

The order matters more than it looks.

### 1. Build the checker first, with an empty enforced list

Before writing a single docstring. Two reasons: it gives you a baseline
number to point at afterwards, and it becomes the instrument you use to
find the gaps instead of eyeballing files.

Run `-Audit` immediately and keep the output. That is your "before".

### 2. Draw the diagrams next

Counter-intuitive, but right. Diagramming a flow forces you to read every
function in it and to decide what each one is *for* — which is exactly
the input the docstrings need. Doing docstrings first means reading
everything twice.

It is also where the discoveries happen. Every finding in the list above
came from the diagram phase, because a diagram makes a dead path or a
missing edge visually obvious in a way that reading linearly does not.

### 3. Docstrings, file by file, adding each to the ratchet as it lands

One commit per file. Add the file to the enforced array in the *same*
commit that brings it to 100%, so the gate can never regress silently.

### 4. Write the rule down last

Only once the thing it describes exists. A convention document written
first describes an aspiration; written last, it describes the codebase.

## The docstring format

Identical in every language. One line of what, a `Why:`, then `In:`/`Out:`
in prose naming the edge case that will bite.

```python
def _msg_signature(msg):
    """Collapse counter drift in an alarm message so it dedups.

    Why: NetXMS re-fires 'Alarm changed:' with a ticking counter
    ('for 901 seconds' → 'for 954 seconds'); grouping on raw text
    inflates the active count 30+x per real alarm.

    In: raw message string, or None/''. Out: same string with every
    digit run replaced by 'N'; '' for falsy input (never None).
    """
```

```javascript
/**
 * Allow only http(s) URLs into an href, else "".
 *
 * Why escHtml is not enough: HTML-escaping does NOT neutralise a
 * `javascript:` URI — every character in one is escape-neutral, so the
 * value survives escaping intact and executes on click. Link targets here
 * come from operator-editable YAML, so they are untrusted input.
 *
 * In: anything. Out: the trimmed URL when it starts http:// or https://,
 * otherwise "" — callers render plain text instead of a link.
 */
```

### What makes a good `Why:`

The test: **could a competent reader recover this from the code?** If yes,
it does not belong. If no, it is the most valuable line in the file.

Good `Why:` content, all real examples from this sweep:

- The bug that caused the current shape — *"a literal `"` inside
  `data-sig=` closed the attribute early, so the lookup silently found
  nothing while the row still rendered correctly."*
- Why a default is what it is — *"Infinity, so an alarm with no usable
  timestamp sinks rather than sorting to the top as if brand new."*
- Why two similar functions must **not** be unified.
- What breaks if the call order changes — *"returns Infinity until both
  catalogues load, so running this first filters everything off the map
  with no error."*
- A decision and its date — *"operator decision 2026-06-10: no TTL,
  because a rarely-opened tab showing a departure as on-time five minutes
  after it was cancelled defeats its own purpose."*
- Rejected alternatives with their measurement.

Bad `Why:` content: restating the signature, restating the function name,
or "this is a helper function".

## Epistemic tagging — the part that survives a rewrite

**If you take one thing from this document, take this section.** Everything
else is process; this is the bit that stops the work being destroyed by the
next refactor.

A docstring carries two kinds of statement. One is derivable from the code
by reading it. The other is not — it came from a spec, a production run, or
domain knowledge, and **it cannot be recovered if it is lost**. A future
rewrite, human or model, cannot tell those apart unless you mark the
boundary.

### The tags

- `[DOCUMENTED]` — stated in a vendor spec, RFC, or stdlib docs. Name the
  source.
- `[OBSERVED]` — measured, or seen in a real run. Needs a date and enough
  provenance to re-run it.
- `[INFERRED]` — reasoned from code or evidence, not verified against
  reality. Say what would confirm it.
- `[GUESS]` — no basis. A placeholder asking to be checked or deleted.

### Do not tag what the code already says

**This is the important rule, not the definitions above.** The failure mode
of this convention is *over*-tagging. Marking `returns a list of ints` as
`[DOCUMENTED]` is syntactically fine and destroys the entire point: the
tags become decoration, and a diff touching them stops meaning anything.

The test: **could this line be reconstructed from the code alone?** If yes,
no tag. If it needs a spec, a run, or domain knowledge, tag it.

Most of a docstring needs no tag at all. When reviewing the first
regeneration, **check for over-tagging before you check for missing tags** —
that is the direction the error comes from.

### The rewrite contract

Write this into the agent/contributor doc, because it is the whole point:

- Untagged prose may be rewritten freely.
- **A tagged line is evidence, not phrasing.** Do not reword, replace, or
  drop it as part of an unrelated edit.
- If a tagged line must change because the code changed, change the tag with
  it and **say so explicitly in the summary** — never fold it into a diff
  that reads as a wording improvement.
- **Never promote a tag.** `[INFERRED]` becomes `[OBSERVED]` only when a run
  produced the number, and the date comes from that run.

### What a tag is worth: a worked example

A discriminator function in the source project carried a claim that an
identifier prefix maps 1:1 to one logical system. Tagged `[INFERRED]`, with the note that it was unverified.

Chasing that tag down took ten minutes and produced three outcomes:

1. The narrow claim was **true** — 10 systems, 32 quays, zero prefix
   collisions. It became `[DOCUMENTED]` with the file and the date.
2. The check surfaced a **different** hole one level down: 6 of 10 systems
   have more than two quays, so two quays sharing a prefix are not
   necessarily the two ends of one crossing. That stayed `[INFERRED]`, with
   the condition that would settle it.
3. Asking the domain expert turned the multi-leg case from "an anomaly to
   handle" into "the normal case, up to five-way" — which is a
   `[DOCUMENTED]` fact from the operator that **no amount of code reading
   would ever have produced**, and which stops a future rewrite "simplifying"
   the function to assume exactly two ends.

An untagged version of that sentence would have been silently rewritten by
the next person who found it wordy. That is the whole argument.

### Pair tags with tests where you can

A `[OBSERVED]` claim written only as prose is a test that never runs. If the
claim is checkable, extract it:

```
[OBSERVED 2026-07] First live run: 118 novel MMSIs, of which exactly the two
real catches — GISKOEY on Sulesund-Hareid and STADDA on Drag-Kjøpsvik — had
two quays in one prefix.
Covered by tests/test_discovery.py::test_serves_samband_* .
```

Here that meant adding tests that read the **real** data file rather than a
fixture, so they fail when the naming convention changes — which the
hand-written fixtures never would. The prose stays as the *why*; the test
carries the *assertion*.

### Exemptions, stated up front

- Test functions — the test name is the documentation.
- Package markers (`__init__.py` and equivalents).
- Single-expression one-liners whose name says everything
  (`const toRad = d => d * Math.PI / 180`). Encode this in the checker,
  or you will either write noise blocks or argue with false positives
  forever.

## Diagram conventions

Seven rules. The first is the one that pays off most.

1. **Node IDs are the real function names.** `_reconcile_alarm_snapshot`,
   not `RECON`. Every box in every diagram becomes greppable, and the
   diagram stops being a separate artifact you have to map onto the code.
2. **Solid edges = the normal path. Dashed = failure, degraded, dead, or
   scheduled-for-removal.** Being able to draw the failure paths is what
   makes these diagrams worth more than a call graph.
3. **A uniform `classDef` palette across every diagram** — here:
   `thread` / `route` / `store` / `upstream` / `browser` / `broken`. Per-file
   ad-hoc colours mean re-learning the key for each picture.
4. **ALL-CAPS annotation nodes carrying prose.** This is where "why" lives
   in a diagram. A node saying *"⚠ responded=False emits NO clears — a
   flaky timeout must not read as all-clear"* is the highest-value box on
   the page.
5. **A `%%` provenance header**: what it diagrams, the date, what it was
   built from, and the legend.
6. **Machine-readable coverage** — see below.
7. **One diagram per flow, in one place**, with an index table saying what
   each one *answers* (not what it contains).

### The `covers:` header — the only automated guard

```
%% covers: src/alarms.py:refresh_state,reconcile_snapshot
%% covers: static/js/app.js:fetchAlarms,renderAlarms
```

One line per source file, comma-separated function names. The checker then
does two things nothing else can:

- **Rename detection.** A renamed or deleted function makes its diagram go
  red immediately. This is the only *automatic* signal that a hand-drawn
  diagram has fallen behind.
- **Staleness pointing.** Given a diff, it names which diagrams to review
  — with `-Deep`, narrowed to diagrams whose covered *functions* were
  actually touched, not merely whose files were.

Be honest about the limit: it says a diagram *might* be stale. It cannot
say it *is*. A human still decides. If people rubber-stamp the output the
gate stops working — that is inherent, not a bug to fix.

## Adapting the script to another language

The checker needs one adapter per language. The contract is small:

```
Get-<Lang>Defs(relPath) -> list of records:
    File     relative path
    Line     1-based line of the definition
    EndLine  last line (only used by -Deep hunk overlap; approximate is fine)
    Name     the identifier
    Kind     'func' | 'class' | 'method'
    HasDoc   boolean
```

Then one line in `Get-DefsFor` dispatching on file extension.

**Use a real parser when the language ships one.** Python's `ast` is three
lines of shelled-out script and handles decorators, multi-line signatures
and nested classes that defeat any regex. Same for Ruby (`ripper`), or any
language with an accessible AST.

**Regex is acceptable when it is not.** The JavaScript adapter here is
deliberately crude because there is no parser available without adding an
npm dependency. It works because false positives are *preferred* to
silence: a flagged thing that needs no block either gets one anyway or
reveals a missing exemption rule.

Adapters proven here:

| Language | Approach | Doc convention detected |
| --- | --- | --- |
| Python | `ast` via `python -c` | `ast.get_docstring()` |
| JavaScript | regex, 3 patterns | preceding line ends `*/` |
| Go | regex on `^func` | preceding line starts `//` |

Sketches for common others — none of these are exotic:

- **TypeScript** — the JS adapter plus `: Type` in the signature regex;
  the doc convention is identical.
- **C#/Java** — regex on the modifier+return+name shape; look for `///` or
  `/** */` above, skipping annotations/attributes. The wrinkle is that
  attribute lines sit *between* the doc block and the declaration, so walk
  back past them.
- **Rust** — `^\s*(pub )?(async )?fn`, doc comments are `///` or `//!`.
  Note `//!` is inner and documents the *module*, not the next item.
- **Shell** — `^name()` or `^function name`, doc is `#` lines above. Worth
  doing: this repo's `build.sh`/`deploy-qa.sh` are load-bearing.

**Add a syntax gate if one is free.** `node --check` needs no packages and
catches the realistic failure of a documentation sweep — an unbalanced
`/** */`. Equivalents: `python -m py_compile`, `gofmt -e`, `bash -n`,
`tsc --noEmit`. Cheap, and it catches the one thing that would otherwise
only surface at runtime.

## Traps, all hit for real

**Mermaid**

- A **bare `%%` line** survives the comment stripper (it wants ≥1
  character after the `%%`) and then collides with the graph header. Write
  `%% ---`. This broke 7 of 8 diagrams on the first run and is invisible
  in the source.
- **`call` is a reserved node ID** (`click <id> call fn()`). So are `end`,
  `graph`, `subgraph`, `class`, `style`.
- `A -. .-> B` is **not** an unlabelled dotted edge. `A -.-> B` is.
- **Verify by parsing, never by eyeballing.** A manual pass over 14 files
  is a check nobody repeats.

**PowerShell** (if you use the template as-is)

- Splatting a **scalar** string passes it one *character* per argument.
  `@($existing | ForEach-Object {…})` — the `@()` is load-bearing when the
  pipeline might yield one item.
- The unary comma binds **looser than arithmetic**: `,@($a, $a + $n - 1)`
  parses as `(,@($a, $a + $n)) - 1` and dies with `op_Subtraction` on
  `Object[]`. Precompute, or use named fields.
- Windows caps a command line near 32k. Batch file lists. Exceeding it
  does *not* fail cleanly — you get *"StandardOutputEncoding is only
  supported when standard output is redirected"*, which points nowhere
  near the cause.
- Exclude `.venv` / `node_modules` / `site-packages` from recursive globs.
  Creating a virtualenv mid-task is what triggered the batching bug here.

**Process**

- Expect to fix the checker while using it. Two real bugs in this one
  surfaced only under real input.
- Verify frontend work **in a browser**, not just by reading. Driving the
  page and asserting on rendered DOM confirmed documented behaviour that
  no test suite covered: an area-collapse time window, a 220 ms panel
  swap, an escaping fix, three distinguishable failure states.
- When the checker flags something a change did not really invalidate, say
  so in the commit message. That closes the loop honestly instead of
  either ignoring the tool or making a pointless edit.

## Checklist

```
[ ] Decide: scope, format, EPISTEMIC TAGS, diagram home, enforcement, ratchet
[ ] Write the rewrite contract for tags into the agent/contributor doc EARLY
[ ] Write the checker with EMPTY enforced lists
[ ] Run -Audit; save the baseline number
[ ] Add a syntax gate if the language has a free one
[ ] Draw diagrams — one flow at a time, node IDs = function names
[ ]   add %% covers: headers as you go
[ ]   run the render/parse check after EVERY diagram, not at the end
[ ] Write the index table per diagram set
[ ] Docstrings, one file per commit, hot paths first
[ ]   tag only what the code cannot say; review the FIRST file for OVER-tagging
[ ]   extract any [OBSERVED] claim that is checkable into a real test
[ ]   add each file to the enforced list in the commit that hits 100%
[ ]   run the test suite; verify frontend in a browser
[ ] Write the convention into the repo's agent/contributor doc
[ ] Record rejected alternatives with their measurements
[ ] Fix any stale docs the sweep exposed — they will be there
```

## Honest limitations

- **The `covers:` guard is one-directional.** It catches code moving out
  from under a diagram. It cannot tell you a diagram was drawn wrong in
  the first place, or that a new flow needs one at all.
- **Hand-drawn diagrams need a human.** There is no generator here, on
  purpose: a generated call graph shows what calls what, which is the part
  you can already read. The value is in the failure paths and the
  annotations, and those cannot be derived.
- **The regex adapters will miss shapes.** Unusual JS function forms slip
  past. Silence from the checker is weaker evidence than a finding.
- **`-Render` needs a browser and one CDN fetch.** That is the cost of not
  taking an npm dependency on `mermaid-cli`. Fine here; may not be in a
  locked-down environment.
- **The ratchet only holds if someone extends it.** Files outside the
  enforced arrays are unchecked, and nothing prompts you to add them.
- **Two checker implementations will drift.** `tests/test_parity.py` in this
  toolkit runs both against a fixture and diffs the output, which turns that
  from a discovered failure into a caught one. Run it in CI if you have CI.

## See also

- [`CLAUDE-snippet.md`](CLAUDE-snippet.md) — the block to merge into your
  agent/contributor doc. The installer does this for you between markers.
- [`TODONT.md`](TODONT.md) — the rejected-approach log convention, and why a
  docstring is the wrong place for a decision that spans the codebase.
- [`../checker/PARITY.md`](../checker/PARITY.md) — how the two checker
  implementations are kept honest.
