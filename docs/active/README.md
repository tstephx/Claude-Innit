---
status: active
tags: [project/claude-innit, format/reference]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# Active Work

Open items only. Per `CLAUDE.md`, files here older than 14 days are considered stale — flag and either resolve or move to `docs/archive/` (if executed) or drop.

**Last verified:** 2026-07-16

---

## In Progress

*(nothing currently in progress)*

Recently landed (2026-07-16, previously sat uncommitted in the working tree): the `insert_memory()` FTS5 rowid-stability fix (`fa4b4be`), the `materialize_common.py` extraction (`fdf9ac5`), and the MemPalace `.gitignore` entries (`84392e1`). Test suite verified before landing: 274 passed; the 7 `test_embeddings.py` failures are environmental (missing ML deps) and identical at the prior HEAD.

## GitHub Issues

`gh issue list --state all` returns zero issues for `tstephx/Claude-Innit` — there are no open (or closed) GitHub issues to track.

## docs/plans/

Empty (and gitignored per `.gitignore`: `docs/plans/`). No staged, unexecuted plans waiting to be archived.

## Deferred Ideas (documented, not scheduled)

Captured under "Future considerations (not in scope)" in an already-archived, fully-executed plan — not active work, but the closest thing to a documented backlog:

- **Status inference for extra-path files** from git activity (recent commits → `active`) — see [`docs/archive/2026-03-13-vault-module-detection-and-frontmatter-tagger.md`](../archive/2026-03-13-vault-module-detection-and-frontmatter-tagger.md#future-considerations-not-in-scope)
- **Tag inference from content** — deferred per Zettelkasten research favoring connections over categories — same source
- **Obsidian Bases / Dataview views** now that vault frontmatter properties are consistent — same source
- **`.vaultignore` file** if the hardcoded exclusion list in `vault.py` grows unwieldy — same source

---

## How to Use This Folder

1. Add a file per open initiative, or (for small items) a line here with a link to its source (issue, plan, or code location).
2. When an item ships, either delete its entry here or, if it produced a design worth preserving, move the writeup to `docs/archive/`.
3. Anything sitting here 14+ days unresolved gets flagged in review — either finish it or explicitly park it.

## See Also

- [`docs/decisions/`](../decisions/) — settled architectural decisions (ADRs)
- [`docs/archive/`](../archive/) — executed plans, read-only reference
- [`CLAUDE.md`](../../CLAUDE.md) — Documentation section, Change Protocol
