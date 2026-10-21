# PROJECT_RULES

`PROJECT_RULES.json` stores only project-specific exceptions. The default rules are
bundled with the skill, so they remain independent of project, language, and agent.

The default set covers main protection, minimal changes, reuse, database changes,
scope, stability, tests, Git operations, data protection, temporaries, and decision
logging.

Commands are provider-neutral and read-only until a proposal is explicitly applied:

```text
rules list
rules add --id local-rule --title "Title" --text "Instruction"
rules modify --id local-rule --text "Updated instruction"
rules deactivate --id local-rule
rules delete --id local-rule
rules restore --id local-rule
```

Mutation commands return the normal proposal and approval hash. Persist them with
`apply --mode supervised` after reviewing the exact proposal. `start` and `resume`
load the effective rules and expose them as `project_rules`.

Before a new task, run `preflight --task "Task objective" --json`; `check-task` is
an alias. The result includes rules, exceptions, current decisions, checks, and
conflicts. `ready` permits continuation; `authorization_required` requires the
agent to report the reason and ask for authorization first. Codex and Claude Code
can invoke it through shell execution, but no per-prompt hook is guaranteed unless
the agent or environment provides that lifecycle mechanism.
