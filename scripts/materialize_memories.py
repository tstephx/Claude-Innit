#!/usr/bin/env python3
"""
Materialize innit personal and project memories into the Obsidian vault's Claude-Memory/.

- personal/* → Claude-Memory/personal-innit.md
- project/*  → Claude-Memory/{project-slug}-innit.md (create or update if stale)

Usage:
    python scripts/materialize_memories.py [--dry-run] [--verbose]
"""

import argparse
import re
from datetime import date
from pathlib import Path

from materialize_common import (
    INNIT_BASE,
    VAULT_MEMORY,
    parse_frontmatter,
    slugify,
    strip_frontmatter_body,
)

TODAY = date.today().isoformat()


def existing_fragment_count(vault_file: Path) -> int:
    """Read innit_fragment_count from an existing vault innit file."""
    if not vault_file.exists():
        return 0
    m = re.search(
        r"^innit_fragment_count:\s*(\d+)", vault_file.read_text(), re.MULTILINE
    )
    return int(m.group(1)) if m else 0


# ── Note builders ──────────────────────────────────────────────────────────────


def build_project_note(project_slug: str, fragments: list[tuple[str, str]]) -> str:
    """
    Build a vault note for a project's innit memories.
    fragments: list of (filename, body_text)
    """
    card_path = resolve_card_path(project_slug)
    count = len(fragments)
    body_parts = "\n\n---\n\n".join(body for _, body in fragments)

    return f"""---
created: {TODAY}
modified: {TODAY}
type: reference
status: active
source: materialized
module: claude-memory
tags:
  - claude-memory
  - format/memory
  - project/{project_slug}
topics:
  - {project_slug}
related:
  - "[[{card_path}]]"
innit_project: {project_slug}
innit_fragment_count: {count}
confidence: developing
last_reviewed: ""
---

# Innit Memories: {project_slug}

{body_parts}

---
*Materialized from claude-innit ({count} fragments) on {TODAY}*
"""


def build_personal_note(fragments: list[tuple[str, str]]) -> str:
    """Build a vault note for all personal innit memories."""
    count = len(fragments)
    sections = []
    for filename, body in fragments:
        # Named files get a subheading; hash-named files get raw content
        name = Path(filename).stem
        if re.match(r"^[0-9a-f]{8}$", name):
            sections.append(body)
        else:
            sections.append(f"## {name.capitalize()}\n\n{body}")

    body_parts = "\n\n---\n\n".join(sections)

    return f"""---
created: {TODAY}
modified: {TODAY}
type: reference
status: active
source: materialized
module: claude-memory
tags:
  - claude-memory
  - format/memory
  - personal
topics:
  - personal
  - identity
  - preferences
innit_project: personal
innit_fragment_count: {count}
confidence: established
last_reviewed: ""
---

# Personal Innit Memories

{body_parts}

---
*Materialized from claude-innit ({count} fragments) on {TODAY}*
"""


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Materialize personal/project memories into vault"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing"
    )
    parser.add_argument("--verbose", action="store_true", help="Show skipped files too")
    args = parser.parse_args()

    created = updated = skipped = 0

    # ── Personal memories ──────────────────────────────────────────────────────
    personal_dir = INNIT_BASE / "personal"
    personal_files = sorted(personal_dir.glob("*.md"))
    personal_vault = VAULT_MEMORY / "personal-innit.md"

    fragments = [
        (f.name, strip_frontmatter_body(f.read_text(encoding="utf-8")))
        for f in personal_files
    ]
    existing_count = existing_fragment_count(personal_vault)

    if not personal_vault.exists():
        action = "CREATE"
    elif existing_count != len(fragments):
        action = "UPDATE"
    else:
        action = "SKIP"

    if action == "SKIP":
        if args.verbose:
            print(
                f"  SKIP  personal-innit.md  ({existing_count} fragments, up to date)"
            )
        skipped += 1
    else:
        note = build_personal_note(fragments)
        if args.dry_run:
            print(f"  {action}  personal-innit.md  ({len(fragments)} fragments)")
        else:
            personal_vault.write_text(note, encoding="utf-8")
            print(f"  {action}  personal-innit.md  ({len(fragments)} fragments)")
        if action == "CREATE":
            created += 1
        else:
            updated += 1

    # ── Project memories ───────────────────────────────────────────────────────
    project_dir = INNIT_BASE / "project"
    # Group files by normalized project slug
    project_groups: dict[str, list[tuple[str, str]]] = {}

    for f in sorted(project_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fields, body = parse_frontmatter(text)
        raw_project = fields.get("project", "unknown")
        slug = slugify(raw_project)
        if slug not in project_groups:
            project_groups[slug] = []
        project_groups[slug].append((f.name, body.strip()))

    for slug, fragments in sorted(project_groups.items()):
        vault_file = VAULT_MEMORY / f"{slug}-innit.md"
        existing_count = existing_fragment_count(vault_file)

        if not vault_file.exists():
            action = "CREATE"
        elif existing_count != len(fragments):
            action = "UPDATE"
        else:
            action = "SKIP"

        if action == "SKIP":
            if args.verbose:
                print(
                    f"  SKIP  {slug}-innit.md  ({existing_count} fragments, up to date)"
                )
            skipped += 1
            continue

        note = build_project_note(slug, fragments)
        if args.dry_run:
            print(
                f"  {action}  {slug}-innit.md  ({len(fragments)} fragments, was {existing_count})"
            )
        else:
            vault_file.write_text(note, encoding="utf-8")
            print(f"  {action}  {slug}-innit.md  ({len(fragments)} fragments)")
        if action == "CREATE":
            created += 1
        else:
            updated += 1

    print(f"\nDone. Created: {created}  Updated: {updated}  Skipped: {skipped}")
    if args.dry_run:
        print("(dry-run — no files written)")


if __name__ == "__main__":
    main()
