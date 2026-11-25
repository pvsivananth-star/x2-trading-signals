# AGENTS.md — Working Rules for x2-trading-signals

Instructions for AI coding agents (and humans) working in this repository.

## Scope and Authorization
- Agents may read the repo, edit files, commit locally, and push the current feature branch to its configured remote.
- Agents may push commits automatically without an additional user prompt, but must never force-push, delete remote branches, merge branches, or push directly to `main`.
- Agents must not rewrite history on shared branches (`main`, or any branch that already exists on the remote).
- Agents must not commit secrets, broker credentials, `.env` files, API keys, account numbers, local logs, or market-data dumps.

## Checkout and Branching
- Never work directly on `main`.
- When starting from `main`, update it first with `git checkout main && git pull --ff-only`, then create one task branch using the prefix `feature/`, `fix/`, or `docs/`, for example `feature/regime-filter-tuning`.
- Continue all work for the task on that same branch. Do not create additional branches unless the planned change is likely to break the core working model; if an additional isolation branch is necessary, record the reason in the handoff.
- If the current checkout is already a suitable task branch, continue on it instead of creating another branch; do not switch to a stale local base branch merely to recreate it.

## Implementation, Tests, Review, and Check-in
- Stage only files relevant to the task.
- For every code change, add or update unit tests covering the changed behavior. For documentation-only or non-executable configuration changes, explicitly record why unit tests are not applicable.
- Run the applicable unit tests, integration tests, and linters before committing. Do not commit while known applicable checks are failing.
- Perform a code review of the complete diff before committing. Fix valid findings and rerun the affected checks.
- Commit messages use the form `<type>: <short imperative summary>`, where type is one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- Keep commits focused: one logical change per commit.
- After checks pass and commits are created, push the current non-main branch automatically. If no upstream exists, use `git push --set-upstream <configured-remote> <current-branch>`; use ordinary pushes thereafter. Do not open or merge a pull request unless explicitly requested.

## Trading-Logic Safety
- Do not change risk parameters (per-trade risk %, session stops, correlation caps, pre-close cutoff, symbol tiering, capital allocation, probation/disabled markets) without an explicit instruction in the task. Flag any change to these in the commit message and in the summary.
- Do not enable markets or symbols that the README marks as disabled or on probation unless the task explicitly asks for it.
- Never hard-code live-trading credentials or enable live order routing as a side effect of a change.

## Documentation
- Keep `README.md` consistent with the behavior it describes. If code changes alter a documented rule, update the README in the same commit.

## Handoff
- At the end of a session, summarize: branch name, commits made, files changed, checks run, push outcome, and any unverified assumptions.
- The branch may be pushed automatically after review and checks pass. Do not open or merge a pull request unless explicitly requested.
