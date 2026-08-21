---
status: active
tags: [project/claude-innit, format/readme]
type: note
created: '2026-03-16'
modified: '2026-03-16'
related: ["[[Claude-Config/mcp-servers/claude-innit]]"]
---

# CLAUDE.md — Claude-Innit Memory System
<!-- project-name: claude-innit -->

**DO NOT scan directories on startup.** This is a focused MCP server project.

## Operational Rules
- Markdown in `data/memories/` is the source of truth. Do not hand-edit the SQLite DB except for debugging.
- Any change to memory markdown format, sync logic, or search routing MUST include test updates.
- Do not paste user memory content into chat; summarize and reference file paths.

## Project Purpose
MCP server giving Claude persistent memory across sessions. Three categories (personal, project, session), dual search (FTS5 + semantic), markdown-first storage.

## Data & Git Hygiene
- `data/innit.db` — gitignored (`*.db`)
- `data/memories/` — contains personal context. **Verify this is gitignored before committing.** Add `data/memories/sessions/` to `.gitignore` if not already excluded.
- For deployed use: store DB + memories outside repo and point server at that location.

## MCP Tools (15)

→ Full tool reference: [`ref/tools.md`](ref/tools.md)

## Key Design Decisions

→ Full architecture: [`ref/architecture.md`](ref/architecture.md) | Data model: [`ref/data-model.md`](ref/data-model.md)

## Commands
```bash
.venv/bin/python -m pytest tests/ -v    # all tests (307 total)
.venv/bin/python -m claude_innit.server # run MCP server
pip install -e ".[embeddings,dev]"      # dev install with all deps
pip install -e .                        # minimal install (no embeddings)
```

## Common Footguns

→ Full debugging guide, task procedures, and key file locations: [`ref/development.md`](ref/development.md)

## Change Protocol
1. `pytest tests/ -v` (before)
2. Change smallest surface area possible
3. `pytest tests/ -v` (after)
4. If schema or markdown format changed: call `admin_sync` + validate

## Documentation
- Reference docs: `ref/` — tools, architecture, data model, development guide
- ADRs: `docs/decisions/` — permanent, append-only decision log, numbered ADR-NNN; a changed decision gets a new ADR that supersedes the old one rather than editing it in place
- Active work: `docs/active/` — open items only; files >14 days old are stale, flag them
- Archive: `docs/archive/` — executed plans, read-only reference
- Plans: `docs/plans/` — legacy staging area, gitignored; do not add new plans here

## Git
- Branch: `main` — Style: `feat:`, `fix:`, `test:`, `refactor:`
- TDD: tests before implementation

---

*Last updated: 2026-07-16*
