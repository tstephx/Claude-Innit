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

Add to `~/.claude/mcp_servers.json`:

```json
{
  "claude-innit": {
    "command": "python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "/path/to/Claude-Innit"
  }
}
```
