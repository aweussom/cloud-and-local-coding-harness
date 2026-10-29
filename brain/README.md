# brain/ — working state

One file per item. **The directory is the state**; `git mv` is the transaction.
`ls brain/next/1-now` is the view; nothing here renders a merged list.

- `next/` — needs the owner. A decision, or someone chased.
- `todo/` — startable cold by anyone, no decision pending.
- `proposed/` — noticed, not acted on, **not trusted**. Read the tags.
- `done/` — finished *and* the reasoning outlives the work. Everything else finished is `git rm`'d; git holds it.
- `plans/` — longer plans. `TODONT.md` — rejected approaches with the evidence. **Read before proposing anything
  structural.**

Buckets are `1-now 2-soon 3-someday`, plus `waiting/`, `waiting-on-time/` and `decided/` under `next/` and
`doing/` under `todo/`. `waiting/` is blocked on a **person**: you chase them. `waiting-on-time/` is blocked on a
**date or an event**: you only check; the item's `**Waiting on:**` line names what and when.

Filename is the schema: `NNN-slug.md`. IDs are shared across `next/` and `todo/` and never reused; the next one
is one above the highest in `git log --all --diff-filter=A --name-only -- brain`. Age is
`git log -1 --format=%as -- <path>`, never mtime. Commits name the item: `N-001: ...`, `T-002: ...`.

A `proposed/` entry is a heading, `**Noticed:** date · **Status:** proposed, not trusted`, then **Claim**
(tagged), **Evidence**, **Suggested home**.
