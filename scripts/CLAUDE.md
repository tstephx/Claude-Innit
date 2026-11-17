# Vault Materialization Scripts
One-way sync from `data/memories/` → Obsidian vault. Both are idempotent and automated.

| Script | Trigger | Output |
|--------|---------|--------|
| `scripts/materialize_sessions.py` | `save_session` hook + cron 2:00AM | `Sessions/YYYY-MM-DD-{project}.md` — dedup via `memory_id:` frontmatter |
| `scripts/materialize_memories.py` | `remember` hook + cron 2:15AM | `Claude-Memory/personal-innit.md`, `Claude-Memory/{project}-innit.md` — dedup via `innit_fragment_count` |

Both scripts maintain `PROJECT_CARD_MAP` mapping project slugs → vault `_PROJECT_CARD` paths. **Update this map when new projects get a `_PROJECT_CARD`.** `resolve_card_path` returns a path only if the card file exists under `VAULT_ROOT` (env, default `~/Dev/Obsidian-Second-Brain`); otherwise `related:` is omitted. `vault_index` skips the paths listed in `<VAULT_ROOT>/.index-exclude` (read by `VaultIndexer`, so the server and the vault's standalone index hook share it); env var `EXCLUDE_INDEX_PATTERNS` (colon-separated, unset by default) only adds patterns to that list.
