#!/usr/bin/env bash
# Portability regression guard.
#
# Fails if a tracked .claude/**, .mcp.json, or CLAUDE.md file hardcodes an
# absolute /Users/ path outside the allowlist below. A fresh clone at a
# different filesystem location must not require a tracked /Users/... path.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Path prefixes retained by explicit audit decision — do not add an entry
# here without recording the decision in the portability audit ledger.
ALLOWLIST=()

failures=0
while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    file="${hit%%:*}"
    rest="${hit#*:}"
    line_no="${rest%%:*}"
    content="${rest#*:}"

    allowed=0
    for prefix in "${ALLOWLIST[@]}"; do
        if [[ "$content" == *"$prefix"* ]]; then
            allowed=1
            break
        fi
    done
    [ "$allowed" -eq 1 ] && continue

    echo "PORTABILITY: $file:$line_no hardcodes an absolute /Users/ path:"
    echo "  $content"
    failures=$((failures + 1))
done < <(git grep -n '/Users/' -- '.claude/**' '.mcp.json' 'CLAUDE.md' 2>/dev/null || true)

if [ "$failures" -gt 0 ]; then
    echo ""
    echo "$failures portability violation(s) found in tracked .claude/**, .mcp.json, or CLAUDE.md."
    exit 1
fi

echo "Portability check passed: no disallowed /Users/ paths in tracked .claude/**, .mcp.json, or CLAUDE.md."
