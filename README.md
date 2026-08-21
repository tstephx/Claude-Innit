---
status: active
tags: [project/claude-innit, format/readme]
type: note
created: '2026-01-30'
modified: '2026-01-30'
---

# Claude Innit

Claude's persistent memory context system.

## Features

- Personal context (identity, preferences, workflows)
- Project context (per-project state and decisions)
- Session continuity (recent session summaries)
- Hybrid search (FTS5 + semantic vectors)

## Installation

```bash
pip install -e .
```

## Usage

Register as an MCP server in the consuming project's `.mcp.json`, under `mcpServers`:

```json
{
  "mcpServers": {
    "claude-innit": {
      "command": "claude-innit"
    }
  }
}
```

This relies on the `claude-innit` console script installed above being on `PATH` in the environment Claude Code launches servers from (an activated venv, or a global/pipx install).
