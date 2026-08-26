# Restore the CLAUDE.md-reminder hook disabled by the gitleaks core.hooksPath rollout

## Where things stand

This is `Claude-Innit#3` (confirmed open, re-checked fresh 2026-08-26).
Filed from `workspace-control-plane`, where the audit that found this
issue happened — this is the only repo where the fix lands;
`workspace-control-plane` itself needs no further change here.

The fleet-wide gitleaks pre-commit rollout wired this repo via
`git config core.hooksPath .githooks` (commit `547f1d5`, "Add gitleaks
pre-commit secret-scanning gate"), and `.config/wt.toml` has a
`[[pre-start]]` step that re-runs that same command on every fresh
worktree. `core.hooksPath` is bare-repo-wide and replaces Git's hook
lookup entirely — it does not merge with the pre-existing `.git/hooks/*`
layer, it replaces it. This repo already had a `.git/hooks/pre-commit`
hook (a plain file, not a symlink) before this rollout landed — a
reminder that prints when a staged `.py` file changes, prompting to
update `CLAUDE.md`:

```bash
#!/bin/bash
# Reminder to update CLAUDE.md when structure changes
if git diff --cached --name-only | grep -qE '\.(py)$'; then
  echo ""
  echo "📝 Reminder: If you added tools or changed architecture,"
  echo "   consider updating CLAUDE.md (last updated: ...)"
  echo ""
fi
```

`.githooks/pre-commit` only contains the gitleaks scan (re-checked
fresh: still true as of this writing) — it does not include this
reminder logic. Since Git now resolves hooks exclusively from
`.githooks/`, **this reminder has not fired on any commit since
`547f1d5`, silently.** Lower severity than the sibling findings in the
same audit (`taylor-dev-core#121`, `dotfiles#125`): this hook is
advisory, not a security or test gate, so there's no correctness risk,
just a lost nudge.

This is the exact same bug class as `workspace-control-plane#118`, from
the same rollout: fixed there via a per-worktree indirection shim instead
of `core.hooksPath` — `workspace-control-plane` PR #121 (merged) is the
proven wiring pattern (`scripts/pre-commit-hook.sh`, mirroring that
repo's `scripts/pre-push-hook.sh`). This repo's fix is simpler: there's
only one hook to wire (no pre-existing pre-push hook competing for the
same shadowed-hooks-directory problem), so the fix is folding the
reminder logic into the one hook file rather than needing a
multi-hook shim.

## What this session does

1. Fold the CLAUDE.md-reminder logic (quoted above, from the pre-rollout
   `.git/hooks/pre-commit`) into `.githooks/pre-commit`, alongside the
   existing gitleaks scan — order doesn't matter functionally here since
   neither step depends on the other's output, but put the reminder after
   the gitleaks scan so a failed gitleaks scan (which exits non-zero and
   aborts the commit) doesn't get its exit status masked by a later
   echo-only step.
2. Update `.config/wt.toml`'s `[[pre-start]]` step's comment (if any) or
   any setup doc mentioning this hook to reflect that it now does both
   checks, so a future edit to one doesn't silently drop the other again.
3. Locally: confirm the current checkout's `.githooks/pre-commit` reflects
   the merged version (re-run `git config --get core.hooksPath` to
   confirm it's still `.githooks` — this repo does NOT need the
   `core.hooksPath`-to-symlink migration `taylor-dev-core`/`dotfiles` need,
   since there's no second, differently-named hook being shadowed here;
   don't over-apply that unrelated fix to this simpler case).
4. Smoke test before pushing: stage a dummy `.py` file change and confirm
   `git commit` shows both the CLAUDE.md reminder and the gitleaks scan
   output, then unstage/revert the dummy change before the real commit.

## Constraints carried over

- Don't touch `workspace-control-plane` or any other repo in this task —
  this fix is scoped to `Claude-Innit` alone.
- Don't migrate this repo off `core.hooksPath` to the symlink-shim
  pattern — unlike `taylor-dev-core`/`dotfiles`, there's no competing
  pre-existing hook of a different name here, so `core.hooksPath` is not
  itself the defect in this repo; the defect is only that the reminder
  logic got dropped when `.githooks/pre-commit` was created. Fold the
  logic in; don't rearchitect the wiring.
- Don't remove or weaken the gitleaks pre-commit scan while restoring the
  reminder — both must work together after this fix.

## Caution

Re-derive current state before trusting anything above: re-check
`git config --get core.hooksPath` and the content of `.githooks/pre-commit`
fresh (confirmed as described above on 2026-08-26), re-read
`Claude-Innit#3` fresh (`gh issue view 3 --repo tstephx/Claude-Innit`),
and re-read `workspace-control-plane` PR #121 for the general pattern
context even though this repo's fix doesn't need PR #121's multi-hook
shim itself. Run `.venv/bin/python -m pytest tests/ -v` (per
`CLAUDE.md:38`, 307 tests as of this writing) once against the current
`main` tip before creating a worktree, and fix or flag any pre-existing
failure first — though this fix touches only `.githooks/pre-commit`, not
Python source, so a clean baseline is expected. Run the
`concurrent-session-preflight` skill before starting and again
immediately before the final push.
