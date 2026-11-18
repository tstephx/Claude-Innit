# Shared claude-innit HTTP server (launchd)

One long-lived claude-innit process on `http://127.0.0.1:8767/mcp` serves every
Claude Code session, instead of one stdio process per session. Each stdio copy
loads its own embedding model and vault matrix (~585 MB each on this Mac), so
N sessions cost N × 585 MB. The shared server costs that once, plus a few MB per
connected session.

- `run-http-server.sh` — wrapper launchd runs. Starts
  `python -m claude_innit.server` with `CLAUDE_INNIT_TRANSPORT=http`, forwards
  SIGTERM, and logs every exit with its status, duration and last stderr line.
- `com.taylorstephens.claude-innit-http.plist` — LaunchAgent (RunAtLoad,
  KeepAlive, 30 s throttle). Paths point at the canonical checkout
  `~/Dev/_Projects/Claude-Innit`, not a worktree.
- Log: `~/Library/Logs/claude-innit/http-server.log`. Look for
  `[wrapper] server FAILED rc=…` lines.

Server settings (env vars, all optional):

| Var | Default | Notes |
|---|---|---|
| `CLAUDE_INNIT_TRANSPORT` | `stdio` | `http` selects the shared server (the wrapper sets it) |
| `CLAUDE_INNIT_PORT` | `8767` | |
| `CLAUDE_INNIT_HOST` | `127.0.0.1` | Only loopback values are accepted. The server has no auth |
| `CLAUDE_INNIT_SESSION_IDLE_TIMEOUT` | unset | Seconds before an idle MCP session is dropped. Unset means sessions live until the client disconnects |

## 1. Install the agent

Do this after the PR is merged and the canonical checkout is on the merged `main`.

```bash
cd ~/Dev/_Projects/Claude-Innit && git pull --ff-only
```

```bash
mkdir -p ~/Library/Logs/claude-innit
```

```bash
cp ~/Dev/_Projects/Claude-Innit/launchd/com.taylorstephens.claude-innit-http.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.taylorstephens.claude-innit-http.plist
```

Check it is up. A plain GET on `/mcp` without MCP headers returns an HTTP error
status; any HTTP response at all means the listener is alive:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8767/mcp
```

```bash
tail -n 20 ~/Library/Logs/claude-innit/http-server.log
```

## 2. Switch Claude Code to the shared server

There are two `claude-innit` entries in `~/.claude.json`: the global one under
`.mcpServers`, and a project-scoped one under
`.projects["/Users/taylorstephens/Dev/Obsidian-Second-Brain"].mcpServers`.
The project entry overrides the global one in that folder, so switch both.

Current (stdio) shape:

```json
"claude-innit": {
  "type": "stdio",
  "command": "/Users/taylorstephens/Dev/_Projects/Claude-Innit/.venv/bin/python",
  "args": ["-m", "claude_innit.server"],
  "env": {
    "VAULT_ROOT": "/Users/taylorstephens/Dev/Obsidian-Second-Brain",
    "EXTRA_INDEX_PATHS": "/Users/taylorstephens/Dev/_Projects"
  }
}
```

New (http) shape. `VAULT_ROOT` and `EXTRA_INDEX_PATHS` move into the plist:

```json
"claude-innit": {
  "type": "http",
  "url": "http://127.0.0.1:8767/mcp"
}
```

Back up, then rewrite both entries (Claude Code rewrites `~/.claude.json`
while running, so do this with as few sessions open as practical):

```bash
cp ~/.claude.json ~/.claude.json.bak-claude-innit-stdio
```

```bash
jq '.mcpServers["claude-innit"] = {"type":"http","url":"http://127.0.0.1:8767/mcp"} | .projects["/Users/taylorstephens/Dev/Obsidian-Second-Brain"].mcpServers["claude-innit"] = {"type":"http","url":"http://127.0.0.1:8767/mcp"}' ~/.claude.json > ~/.claude.json.new && mv ~/.claude.json.new ~/.claude.json
```

```bash
jq '.mcpServers["claude-innit"], .projects["/Users/taylorstephens/Dev/Obsidian-Second-Brain"].mcpServers["claude-innit"]' ~/.claude.json
```

New sessions connect to the shared server. Sessions that are already open keep
their stdio child until they end; nothing needs killing. In a new session,
`/mcp` should list `claude-innit` as connected over http.

## 3. Rollback

Either step can be undone on its own.

Config only (back to per-session stdio). Put the stdio shape back with jq
rather than restoring the backup file, because `~/.claude.json` holds other
state that has changed since the backup:

```bash
jq --argjson s "$(jq '.mcpServers["claude-innit"]' ~/.claude.json.bak-claude-innit-stdio)" --argjson p "$(jq '.projects["/Users/taylorstephens/Dev/Obsidian-Second-Brain"].mcpServers["claude-innit"]' ~/.claude.json.bak-claude-innit-stdio)" '.mcpServers["claude-innit"] = $s | .projects["/Users/taylorstephens/Dev/Obsidian-Second-Brain"].mcpServers["claude-innit"] = $p' ~/.claude.json > ~/.claude.json.new && mv ~/.claude.json.new ~/.claude.json
```

Stop and remove the agent:

```bash
launchctl bootout gui/$(id -u)/com.taylorstephens.claude-innit-http
```

```bash
rm ~/Library/LaunchAgents/com.taylorstephens.claude-innit-http.plist
```

The stdio path (`python -m claude_innit.server` with no
`CLAUDE_INNIT_TRANSPORT`) is unchanged, so rolling back the config is enough to
restore the old behaviour even with the agent still loaded.

## Trade-off

With the shared server, every session depends on one process. If it is down,
sessions show `claude-innit` as failed instead of starting a private copy.
KeepAlive restarts it within ~30 s; the log records why it stopped.
