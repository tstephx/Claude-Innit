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

## In Progress (uncommitted working-tree changes)

As of this writing, `git status` on `main` shows uncommitted changes not yet folded into any plan doc:

- **Shared `materialize_common.py` extraction** — `scripts/materialize_memories.py` and `scripts/materialize_sessions.py` both duplicated `PROJECT_CARD_MAP`, `slugify()`, and `parse_frontmatter()`. A new `scripts/materialize_common.py` centralizes these; both scripts now import from it instead of duplicating ~100 lines each. Untracked file: `scripts/materialize_common.py`. Modified: `scripts/materialize_memories.py`, `scripts/materialize_sessions.py`.
- **`insert_memory()` rowid-stability fix** — `claude_innit/db/database.py` changes `insert_memory()` from `INSERT OR REPLACE` to an explicit `UPDATE`-if-exists-else-`INSERT`. `INSERT OR REPLACE` deletes and re-inserts the row with a new rowid, which leaves the FTS5 external-content table accumulating phantom `docsize` entries for stale rowids — eventually surfacing as "missing row N from content table" errors that silently return empty search results. The fix (documented in the method's own docstring) preserves rowid across updates.
- **`.gitignore` update** accompanies the above (untracked `scripts/materialize_common.py` addition).

None of this is committed yet — no commit message, no test run confirmed in this session. Next step: verify the FTS5 rowid fix against the existing `data/innit.db` (or a fresh one) and land both changes as separate commits per `CLAUDE.md`'s "smallest surface area" change protocol.

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
