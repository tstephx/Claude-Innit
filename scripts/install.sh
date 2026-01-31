#!/bin/bash
# scripts/install.sh - Install Claude Innit

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing Claude Innit..."

# Create virtual environment if needed
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
fi

# Install package
"$PROJECT_DIR/.venv/bin/pip" install -e "$PROJECT_DIR"

# Create data directory
mkdir -p "$PROJECT_DIR/data/memories"

echo "Claude Innit installed!"
echo ""
echo "Add to ~/.claude/mcp_servers.json:"
echo ""
cat << EOF
{
  "claude-innit": {
    "command": "$PROJECT_DIR/.venv/bin/python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "$PROJECT_DIR"
  }
}
EOF
