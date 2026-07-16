---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-007: vault_tag as an MCP Tool with Two-Phase Preview/Apply

**Date:** 2026-03-16 | **Status:** Implemented | **Supersedes:** —

## Decision

Frontmatter tagging for untagged vault files is exposed as an MCP tool (`vault_tag`), not a one-off script, with a two-phase flow: a preview call groups untagged files by folder so Claude can propose status/tags/type in batches, then an apply call writes the fields — status, tags, type, created (from macOS `st_birthtime`), modified — honoring folder-level defaults with file-level overrides. `vault_tag` does not auto-trigger `vault_index` afterward; re-indexing is a separate, manual step.

## Why

An MCP tool can be called by Claude mid-session to tag newly created files, not just as a historical backfill script. Grouping the ~60 untagged files by folder for batch decisions was fast (~2 minutes) versus one-by-one triage. Folder-level defaults plus file-level overrides gives both bulk efficiency and per-file correctness. Keeping `vault_tag` and `vault_index` decoupled preserves each tool's independent testability.

## Rejected Alternatives

- **Standalone Python script** — can't be invoked by Claude during a live session; only useful for one-time backfill.
- **Single-phase auto-apply** — no review step before mutating ~60 files' frontmatter.
- **Auto re-index after every tag call** — would couple two tools that are otherwise independently testable and composable.

## Where in Code

- `claude_innit/tools/tag.py` — `vault_tag` two-phase preview/apply implementation
- `docs/archive/2026-03-13-vault-module-detection-and-frontmatter-tagger.md` — "Interview Decisions" table, Task 3
- `CLAUDE.md` — "Vault tagger" bullet under Key Design Decisions
