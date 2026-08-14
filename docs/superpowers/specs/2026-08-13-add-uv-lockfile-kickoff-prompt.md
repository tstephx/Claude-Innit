# Add a uv.lock lockfile to Claude-Innit

## Where things stand

`pyproject.toml` (repo root) declares dependencies but there is no lockfile —
no `uv.lock`, `requirements.txt`, or equivalent. Current declared deps:

```toml
dependencies = [
    "mcp>=1.0.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
embeddings = [
    "torch>=2.2.0",
    "sentence-transformers>=2.2.0,<5.0",
    "numpy>=1.24.0,<2.0",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "hypothesis>=6.0",
    "numpy>=1.24.0,<2.0",
]
```

`requires-python = ">=3.11"`. No `.python-version` file present.

This matters because `embeddings` pulls in torch + sentence-transformers —
heavy, fast-moving packages where an unpinned resolve can silently drift
between machines/sessions. Several sibling repos in `~/Dev/_Projects/`
(`book-mcp-server`, `briefcase`, `career-coach-mcp`, `document-intelligence`,
`my-mcp-portfolio`, `book-ingestion-python`, `whatbox/rss-news-server`) all
use `pyproject.toml` + `uv.lock` as the house convention — this repo should
match that pattern rather than introduce a different one.

This was flagged during a dev-environment hygiene survey across
`~/Dev/_Projects/` (2026-08-13) as one of four repos with real dependency
packages but no lockfile.

## What this session does

1. Confirm `uv` is available (`uv --version`) — it's already installed at
   `~/.local/bin/uv` per this machine's existing setup.
2. Run `uv lock` in the repo root to generate `uv.lock` from the existing
   `pyproject.toml`. Do not add, remove, or change version constraints on
   any dependency — this is a "pin what's already declared" task, not a
   dependency review.
3. Lock **including** the `embeddings` and `dev` optional groups, not just
   the base `dependencies` list — those are the ones most worth pinning.
   Check `uv lock --help` for the right flag if `uv lock` alone doesn't
   cover extras/optional-dependencies by default in the installed version.
4. Run `uv sync` (or equivalent) and confirm the project still installs
   cleanly and `pytest` still passes with the locked versions.
5. Commit `uv.lock` (and only that file, plus this kickoff-prompt file if
   it's still uncommitted) with a clear message.

## Constraints carried over

- Don't upgrade any dependency version — lock what's currently resolvable
  against the existing constraints in `pyproject.toml`.
- Don't touch `[project.optional-dependencies].embeddings`'s version
  bounds even if a newer torch/sentence-transformers is available — that's
  a separate decision for the user to make deliberately, not something to
  bundle into a lockfile-creation task.
- If `uv lock` fails to resolve (version conflict, yanked package, etc.),
  stop and report the conflict rather than loosening constraints to force
  a resolve.

## Caution

Re-derive current state before trusting anything above as still true: run
`git log -5 --oneline` and `git status` in this repo, and check
`ListAgents` for any other concurrent session already working here. This
kickoff prompt was written on 2026-08-13 from a hygiene survey done the
same day — repo state may have moved since.
