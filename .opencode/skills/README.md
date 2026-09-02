# OpenCode Engineering Skills

Skills are designed to be installed under `.opencode/skills/<skill-id>/SKILL.md`.

Included:

- `issue-to-pr-workflow` (repo-specific) — tiered GitHub issue templates (T1/T2/T3), branch naming `<prefix>/<issue-number>-<short-slug>`, PR + squash-merge discipline. **Mandatory for every change in this repo.**

Global skills (installed at `~/.config/opencode/skills/`, discovered automatically):

- engineering-core
- feature-workflow
- code-review
- git-pr-workflow
- parallel-worktrees
- production-reliability
- frontend-design
- herd / herdr (lead-agent orchestration)

`AGENTS.md` is optional but recommended for project repositories because it provides a short, always-visible routing layer to the skills.

For a global setup, copy the skill directories to:
`~/.config/opencode/skills/`

For a project setup, copy them to:
`.opencode/skills/`

OpenCode discovers skills from these locations and loads skill bodies on demand. A skill's `description` is what helps the agent decide when to load it; it does not mean the entire skill body is permanently in the prompt.
