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
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

INNIT_SESSIONS = Path.home() / "Dev/_Projects/Claude-Innit/data/memories/sessions"
VAULT_SESSIONS = Path.home() / "Dev/Obsidian-Second-Brain/Sessions"
VAULT_ROOT = Path.home() / "Dev/Obsidian-Second-Brain"
TODAY = date.today().isoformat()

# ── Project → _PROJECT_CARD path map ──────────────────────────────────────────
# Maps project slug (from innit metadata) to vault-relative path (no .md extension)
# Used to generate [[wikilinks]] in the related: field.

PROJECT_CARD_MAP = {
    "api-dashboard": "Projects/api-dashboard/_PROJECT_CARD",
    "book-ingestion": "Projects/mcp (category)/mcp-books (category)/book-ingestion-python/_PROJECT_CARD",
    "book-ingestion-python": "Projects/mcp (category)/mcp-books (category)/book-ingestion-python/_PROJECT_CARD",
    "book-mcp-server": "Projects/mcp (category)/mcp-books (category)/book-mcp-server/_PROJECT_CARD",
    "briefcase": "Projects/mcp (category)/briefcase/_PROJECT_CARD",
    "career-coach-mcp": "Projects/mcp (category)/career-coach-mcp/_PROJECT_CARD",
    "claude-cheatsheet": "Projects/claude-cheatsheet/_PROJECT_CARD",
    "claude-innit": "Projects/mcp (category)/Claude-Innit/_PROJECT_CARD",
    "claude-workspace": "Projects/claude-workspace/_PROJECT_CARD",
    "dev-scripts": "Projects/dev-scripts/_PROJECT_CARD",
    "document-intelligence": "Projects/taylor-work (category)/document-intelligence/_PROJECT_CARD",
    "fast-mail": "Projects/mcp (category)/fast-mail/_PROJECT_CARD",
    "my-mcp-portfolio": "Projects/mcp (category)/my-mcp-portfolio/_PROJECT_CARD",
    "obsidian-brain-framework": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "periodical-parser": "Projects/whatbox-server/periodical-parser/_PROJECT_CARD",
    "website-portfolio": "Projects/whatbox-server/website-portfolio/_PROJECT_CARD",
    "whatbox": "Projects/whatbox-server/whatbox/_PROJECT_CARD",
    "whatbox-portfolio-mcp": "Projects/mcp (category)/whatbox-portfolio-mcp/_PROJECT_CARD",
    # Projects with flat-path aliases
    "_lab": "Projects/lab-docs/_PROJECT_CARD",
    "obsidian-brain": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "obsidian-second-brain": "Projects/obsidian-brain-framework/_PROJECT_CARD",
    "dotfiles": "Projects/dotfiles/_PROJECT_CARD",
    "action-tracker": "Projects/action-tracker/_PROJECT_CARD",
    "claude-projects": "Projects/claude-projects/_PROJECT_CARD",
    "rss-news-server": "Projects/whatbox-server/whatbox/rss-news-server/_PROJECT_CARD",
    "morning-reader": "Projects/whatbox-server/whatbox/morning-reader/_PROJECT_CARD",
}

# ── Frontmatter parsing ────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (fields, body). Fields are raw strings; body is the content after ---."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    fields: dict = {}
    # Parse simple key: value and key:\n- item list
    current_key = None
    current_list: list[str] = []

    for line in raw.splitlines():
        list_match = re.match(r"^- (.+)$", line)
        kv_match = re.match(r"^(\w[\w-]*):\s*(.*)", line)

        if list_match and current_key:
            current_list.append(list_match.group(1).strip("'\""))
        elif kv_match:
            if current_key and current_list:
                fields[current_key] = current_list
                current_list = []
            current_key = kv_match.group(1)
            val = kv_match.group(2).strip().strip("'\"")
            if val:
                fields[current_key] = val
                current_key = None
            # else: expect list items on following lines
        else:
            if current_key and current_list:
                fields[current_key] = current_list
                current_list = []
            current_key = None

    if current_key and current_list:
        fields[current_key] = current_list

    return fields, body


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
    card_path = PROJECT_CARD_MAP.get(project, f"Projects/{project}/_PROJECT_CARD")
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
