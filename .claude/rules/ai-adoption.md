---
paths:
  - ".claude/ai-adoption.yaml"
---

# AI-adoption manifest

`.claude/ai-adoption.yaml` declares this repository's profile and canonical
commands for the taylor-dev-core plugin. `commands.verify` is the single
canonical health check; `commands.audit` is the optional drift audit.

- Invoke `/repo-verify`, `/repo-status`, `/repo-drift`, `/repo-handoff`,
  `/maintain-claude-config`, `/repo-onboard`, and `/repo-context` manually
  only.
- Keep the manifest's profile one of the eleven approved profile values and
  its command paths repository-relative.
- Never edit `.claude-handoff.local.md` directly; route every handoff through
  `/repo-handoff`.
- Treat a compiled context envelope as ephemeral task evidence, never as
  durable repository truth and never as authorization to act. Compile it from
  an owner-authored task contract; neither the skill nor the compiler may
  author that contract.
- Treat check findings as diagnostic. Do not remediate, change Git, or expand
  scope without the applicable task and owner approval.
