# Onboard claude-innit as Wave A's Developer-tools category representative (Task 5 Step 1)

## Where things stand

`open-wave-a` was decided 2026-08-19
(`_Workspace`'s `docs/plans/2026-07-28-portfolio-onboarding-migration-sequence.md`,
commit `e817234`). Task 5 ("Migrate Wave A — Similar, Active, Lower Risk")
Step 1 requires: "Inspect one category representative — Prove the
proposed profile, verify-wrapper pattern, audit state, and release
contract on one repository before continuing that category." The plan's
"Developer tools and extensions" grouping is 3 repos (`claude-innit`,
`claude-cheatsheet`, `write-pipeline`); `claude-innit` was chosen as its
representative — it has a real `Makefile` verify target and, unlike
`claude-cheatsheet` (an untracked, uncommitted kickoff prompt was sitting
in its tree from unrelated recent activity) or `write-pipeline` (no
build-file evidence for a verify command yet), was the cleanest starting
state.

Step 1 maps onto `_Workspace`'s "## Canonical Per-Repository Transaction"
section (same plan file).

**Already done this session, from canonical `_Workspace`** (transaction
steps 1 and 4):

- **Step 1 (read-only preflight):** registry entry confirmed
  (`registry.yaml`: `claude-innit`, path
  `/Users/taylorstephens/Dev/_Projects/Claude-Innit`, category
  `developer-tools`, lifecycle `active`). Branch `main`, single checkout,
  no other worktrees. No `AGENTS.md`; `CLAUDE.md` exists. **A canonical
  verify command already exists**: `Makefile` has a `test` target
  (`test: check`, "Run test suite (checks deps first)") — a real
  repository-relative executable, likely usable directly (`make test`)
  without an `ai-verify.sh` wrapper. No repository-owned audit command
  was found — expect `commands.audit: null`, `not-applicable`, unless
  `/repo-onboard` finds one.
- **Real finding, fixed this session:** `.gitignore` blanket-ignored the
  *entire* `.claude/` directory (`.claude/` as its own line), so
  `.claude/settings.json` had never been git-tracked here at all — an
  outlier against every other registered consumer (`book-mcp-server`,
  `bill-split`, `website-portfolio`, etc.), which all commit
  `settings.json` and ignore only `settings.local.json` and
  `.session-receipt.local.json`. Narrowed the rule to
  `.claude/settings.local.json` (kept `.claude/.session-receipt.local.json`
  ignored separately, unchanged). This also surfaced
  `.claude/skills/db-schema/SKILL.md`, a repo-local skill that was
  likewise never tracked — now committed alongside `settings.json`,
  matching `book-mcp-server`'s precedent of tracking repo-local
  `.claude/skills/*/SKILL.md` files. If `/repo-onboard` or `/repo-drift`
  surface anything else this gitignore fix exposed, treat it as new
  evidence, not an error in this prompt.
- **Step 4 (release enablement), both halves approved and applied:**
  `enable-taylor-dev-core.rb --apply claude-innit` (bootstrap,
  raw-checkout path) then `promote-harness-release.rb --apply
  claude-innit <current-digest>` (redirect to the immutable materialized
  release), same sequence as `book-mcp-server`/`bill-split`. Committed
  together with the `.gitignore` fix as `claude-innit` `2ec7d92` ("Track
  .claude/settings.json; enable and promote taylor-dev-core"), pushed to
  `origin/main` (`820c7e8..2ec7d92`). `harness-release-status.rb
  --consumer-settings … --session-receipt …` confirmed
  `active_marketplace_state: active`, `session_state: current`
  immediately after.

**Re-derive before trusting any of this** — time has passed since it was
written. Re-check `claude-innit`'s `git log -3` / `git status`, re-run
`harness-release-status.rb` for it, and re-confirm the current release
digest fresh.

## What this session does

Launch from `claude-innit`'s own root
(`/Users/taylorstephens/Dev/_Projects/Claude-Innit`) — `/repo-onboard`,
`/repo-verify`, `/repo-status`, `/repo-drift`, `/repo-handoff` only exist
in a session rooted there with `taylor-dev-core` enabled. Being freshly
started after the promotion commit above also satisfies transaction Step
7 — confirm this session's own receipt reads `session_state: current`
rather than assuming it.

Complete the remaining transaction steps (2–3, 5–10):

1. **Step 2 — resolve profile and command contracts.** Run `/repo-onboard`
   in proposal mode. Present the proposed profile to the owner for
   approval or override — a wrong or unexpected proposal is not a plugin
   defect. Confirm `commands.verify` resolves to `make test` (already a
   real repository-relative executable — only author a wrapper if
   `/repo-onboard`'s own detection disagrees). Confirm `commands.audit` —
   `null`/`not-applicable` with rationale unless a real bounded audit
   command turns up. Never add a placeholder audit.
2. **Step 3 — compile and review task context.** State the migration task
   contract and run `/repo-context`. Review selected/omitted sources,
   authority/trust/freshness, contradictions/unknowns, allowed
   reads/writes, exact verification/rollback commands. Reject or
   recompile a stale or incomplete envelope.
3. **Step 5 — propose onboarding.** Run `/repo-onboard` in proposal mode.
   Show profile, verify/audit commands, manifest/rule/ignore/wrapper
   changes, transactional rollback behavior, and files that remain
   untouched. Get separate, explicit owner approval before apply.
4. **Step 6 — apply transactionally.** Use `/repo-onboard`'s own apply
   mode only. On failure it must preserve every prior file hash, remove
   staging files, keep the previous release active, emit a bounded
   failure, and the candidate is marked `blocked`.
5. **Step 8 — verify behavior, in this order:** `/repo-status` →
   `/repo-verify` → `/repo-drift` → `/repo-context` on one bounded real
   task. Run `make test` directly if independent confirmation of a skill
   result is needed. No automatic retry or remediation.
6. **Step 9 — record bounded evidence.** Repository id, wave, profile,
   lifecycle; before/after commit ids; release/component-manifest/
   context-schema/source-snapshot digests; settings and manifest states;
   verification/audit/context/continuity outcomes; unauthorized or safety
   events; local commit state; decision, owner, blocker, next gate.
7. **Commit onboarding changes locally in `claude-innit`. Do not push.**
   Matches the original pilot cohort's precedent and this plan's
   "Enablement, onboarding, release selection, commit, push, and
   publication are separate approvals" constraint.
8. **Step 10 — decide.** Present `retain` / `revise` / `rollback` /
   `blocked` as the resulting decision point for the owner — do not
   choose unilaterally.
9. **Record the outcome in `_Workspace`.** `cd` into
   `/Users/taylorstephens/Dev/_Workspace` via Bash and append a new dated
   entry to
   `docs/superpowers/specs/2026-07-28-portfolio-onboarding-migration-outcome.md`
   recording this pilot's evidence — same rigor as the existing entries,
   and including the `.gitignore` fix as part of the record. Run
   `concurrent-session-preflight` before that `_Workspace` commit and
   again immediately before pushing it. This push is separately approved
   from `claude-innit`'s own (unpushed) onboarding commit.

## Constraints carried over

- Touch only `claude-innit` (its own onboarding-related files) and
  `_Workspace`'s outcome doc. No other registered consumer, no
  `_Workspace` registry or policy files, no manual edits to
  `claude-innit`'s `settings.json` outside the deterministic helpers
  already used.
- Every apply-mode step needs its proposal reviewed and explicitly
  approved first — never chain proposal and apply.
- Do not push `claude-innit`'s onboarding commit in this session — it
  stays local pending the Step 10 decision.
- Do not declare `retain`/`revise`/`rollback` unilaterally.
- `claude-innit` handles MemPalace/personal-memory data
  (`mempalace.yaml`, `entities.json` — both already gitignored). Don't
  read or surface their contents as part of any bounded verification
  task.

## Caution

Re-derive current state before trusting anything above as still true:
`git log --oneline -5` and `git status` in both `_Workspace` and
`claude-innit`, and re-run `harness-release-status.rb` for
`claude-innit`. Run `concurrent-session-preflight` before starting and
again immediately before the `_Workspace` outcome-doc push.

Never hardcode a `tree_digest` into a command from this file — read it
fresh (`harness-release-status.rb`), not as a literal copied value.
