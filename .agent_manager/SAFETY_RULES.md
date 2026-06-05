# Project Safety Rules

1. Do not modify `main` directly.
2. Do not auto-merge.
3. Do not auto-push.
4. Do not weaken validators.
5. Do not promote AI-generated data without approval artifacts.
6. Stop on blocker_count increase.
7. Stop on unexpected core writes.
8. Stop on schema drift.
9. Stop if changed files exceed objective scope.
10. Stop if secrets or credentials appear in logs or artifacts.

Manager and review agents are read-only.
The coder agent is write-capable only in a later isolated-worktree phase.
Deterministic validation remains the authority layer.
