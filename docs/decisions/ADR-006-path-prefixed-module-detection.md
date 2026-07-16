---
status: active
tags: [project/claude-innit, format/adr]
type: project
created: '2026-07-16'
modified: '2026-07-16'
---

# ADR-006: Path-Prefixed Module Detection for Extra-Path Files

**Date:** 2026-03-16 | **Status:** Implemented | **Supersedes:** —

## Decision

`_detect_module()` in `claude_innit/tools/vault.py` uses the lowercased top-level folder name (no prefix) for files under `VAULT_ROOT`. For files under `EXTRA_INDEX_PATHS` (`_Lab`, `_Projects`), it instead uses a path-prefixed name — `lab/project-name` or `projects/project-name` — so that, e.g., a `scripts/` folder in `_Lab` and one in `_Projects` don't collide into a single "scripts" module. Each location has its own excluded-framework-dir list (vault: `daily`, `inbox`, `archive`, `claude-memory`; extra-paths: `ref`, `scripts`, `docs`, `shared`).

## Why

Before this change, 8,128 extra-path files showed as "unassigned" because `_detect_module()` returned `None` for anything outside `VAULT_ROOT`. Prefixing fixed ~88% of that in one change, kept the module map a flat dict (no API shape change for `vault_stats`), and avoided the specific collision case where the same folder name recurs across different project roots.

## Rejected Alternatives

- **Nested dict for module namespacing** (`{"lab": {"scripts": ...}}`) — rejected per the design's own interview notes in favor of a flat, prefixed dict: keeps `vault_stats` output shape unchanged and avoids a breaking API change.
- **Status inference for extra-path files from git activity** — explicitly deferred as out of scope; module detection alone resolved 88% of the "unassigned" problem, so the incremental win from status inference didn't justify the added surface.

## Where in Code

- `claude_innit/tools/vault.py` — `_detect_module()`, `_FRAMEWORK_DIRS`
- `docs/archive/2026-03-13-vault-module-detection-and-frontmatter-tagger.md` — "Interview Decisions" table, Task 1
- `CLAUDE.md` — "Module detection" bullet under Key Design Decisions
