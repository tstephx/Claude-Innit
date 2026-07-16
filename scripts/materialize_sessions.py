#!/usr/bin/env python3
"""
Materialize innit session memories into the Obsidian vault.

Reads sessions from data/memories/sessions/, checks which ones are already
in the vault via memory_id frontmatter, and creates missing vault notes.

Usage:
    python scripts/materialize_sessions.py [--dry-run] [--verbose]
"""

import argparse
import re
from datetime import date

from materialize_common import (
    INNIT_BASE,
    VAULT_SESSIONS,
    parse_frontmatter,
)

INNIT_SESSIONS = INNIT_BASE / "sessions"
TODAY = date.today().isoformat()


# ── Build dedup set ────────────────────────────────────────────────────────────


def build_materialized_set() -> set[str]:
    """Return set of memory_id values from all existing vault session notes."""
    materialized: set[str] = set()
    for f in VAULT_SESSIONS.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^memory_id:\s*(.+)$", text, re.MULTILINE)
        if m:
            materialized.add(m.group(1).strip())
    return materialized


# ── Generate vault filename ────────────────────────────────────────────────────


def vault_filename(session_date: str, project: str, taken: set[str]) -> str:
    """Generate a unique vault filename like YYYY-MM-DD-project.md."""
    slug = re.sub(r"[^a-z0-9-]", "-", project.lower()).strip("-")
    base = f"{session_date}-{slug}"
    name = f"{base}.md"
    if name not in taken:
        taken.add(name)
        return name
    i = 1
    while True:
        name = f"{base}-{i}.md"
        if name not in taken:
            taken.add(name)
            return name
        i += 1


# ── Generate vault note ────────────────────────────────────────────────────────


def build_vault_note(
    session_date: str,
    project: str,
    topics: list[str],
    memory_id: str,
    body: str,
) -> str:
    card_path = resolve_card_path(project)
    related_line = f'  - "[[{card_path}]]"'

    # Build topics list — project first, then additional topics (deduped)
    all_topics = [project] + [t for t in topics if t != project]
    topics_yaml = "\n".join(f"  - {t}" for t in all_topics)

    tags_yaml = f"  - sessions\n  - format/session\n  - project/{project}"

    return f"""---
created: {session_date}
modified: {TODAY}
type: reference
status: active
source: materialized
module: sessions
tags:
{tags_yaml}
topics:
{topics_yaml}
related:
{related_line}
session_project: {project}
memory_id: {memory_id}
confidence: developing
last_reviewed: ""
---

# Session: {session_date} ({project})

{body.strip()}

---
*Materialized from claude-innit on {TODAY}*
"""


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Materialize innit sessions into Obsidian vault"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing files"
    )
    parser.add_argument("--verbose", action="store_true", help="Show skipped files too")
    args = parser.parse_args()

    materialized = build_materialized_set()
    print(f"Found {len(materialized)} already-materialized sessions in vault.")

    # Existing vault filenames for conflict avoidance
    taken: set[str] = {f.name for f in VAULT_SESSIONS.glob("*.md")}

    session_files = sorted(INNIT_SESSIONS.glob("*.md"))
    skipped = created = 0

    for path in session_files:
        if path.name == "_index.md":
            continue

        memory_id = f"sessions/{path.name}"

        if memory_id in materialized:
            if args.verbose:
                print(f"  SKIP  {path.name}  (already materialized)")
            skipped += 1
            continue

        text = path.read_text(encoding="utf-8")
        fields, body = parse_frontmatter(text)

        session_date = fields.get("date", "")
        project = fields.get("project", "unknown")
        raw_topics = fields.get("topics", [])
        topics = raw_topics if isinstance(raw_topics, list) else [raw_topics]

        if not session_date:
            # Fall back: parse date from filename (YYYY-MM-DD-HHMMSS.md)
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", path.name)
            session_date = m.group(1) if m else "unknown"

        vault_name = vault_filename(session_date, project, taken)
        note = build_vault_note(session_date, project, topics, memory_id, body)

        dest = VAULT_SESSIONS / vault_name
        if args.dry_run:
            print(f"  CREATE {vault_name}  ← {path.name}  (project={project})")
        else:
            dest.write_text(note, encoding="utf-8")
            print(f"  Created {vault_name}")

        created += 1

    print(
        f"\nDone. Created: {created}  Skipped: {skipped}  Total innit sessions: {len(session_files)}"
    )
    if args.dry_run:
        print("(dry-run — no files written)")


if __name__ == "__main__":
    main()
