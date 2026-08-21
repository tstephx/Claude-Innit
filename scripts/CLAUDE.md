# Vault Materialization Scripts
One-way sync from `data/memories/` → Obsidian vault. Both are idempotent and automated.

| Script | Trigger | Output |
|--------|---------|--------|
| `scripts/materialize_sessions.py` | `save_session` hook + cron 2:00AM | `Sessions/YYYY-MM-DD-{project}.md` — dedup via `memory_id:` frontmatter |
| `scripts/materialize_memories.py` | `remember` hook + cron 2:15AM | `Claude-Memory/personal-innit.md`, `Claude-Memory/{project}-innit.md` — dedup via `innit_fragment_count` |

Both scripts maintain `PROJECT_CARD_MAP` mapping project slugs → vault `_PROJECT_CARD` paths. **Update this map when new projects get a `_PROJECT_CARD`.** Env var `EXCLUDE_INDEX_PATTERNS` (colon-separated) controls which paths `vault_index` skips; defaults to `/rss-news/` and `/vault-rss-feeds/`.
