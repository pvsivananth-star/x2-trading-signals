# AGENTS.md — Working Rules for x2-trading-signals

Instructions for AI coding agents (and humans) working in this repository.

## Scope and Authorization
- Agents may read the repo, create branches, edit files, and commit locally.
- Agents must NOT push, open pull requests, merge, force-push, or delete remote branches. The repository owner pushes and opens PRs.
- Agents must not rewrite history on shared branches (`main`, any branch that exists on the remote).

## Checkout
- Start from an up-to-date `main`: `git checkout main && git pull`.
- Create work branches from `main` using the prefix `feature/`, `fix/`, or `docs/`, e.g. `feature/regime-filter-tuning`.
- Never work directly on `main`.

## Check-in (Commits)
- Stage only files relevant to the task. Do not commit secrets, broker credentials, `.env` files, API keys, account numbers, or local logs/data dumps.
- Commit messages use the form `<type>: <short imperative summary>`, where type is one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- Keep commits focused: one logical change per commit.
- Run existing tests and linters before committing when they are present. Report any failures rather than committing silently broken code.

## Trading-Logic Safety
- Do not change risk parameters (per-trade risk %, session stops, correlation caps, pre-close cutoff, symbol tiering, capital allocation, probation/disabled markets) without an explicit instruction in the task. Flag any change to these in the commit message and in the summary.
- Do not enable markets or symbols that the README marks as disabled or on probation unless the task explicitly asks for it.
- Never hard-code live-trading credentials or enable live order routing as a side effect of a change.

## Documentation
- Keep `README.md` consistent with the behavior it describes. If code changes alter a documented rule, update the README in the same commit.

## Handoff
- At the end of a session, summarize: branch name, commits made, files changed, and any unverified assumptions.
- Leave the branch unpushed. The owner reviews, pushes, and opens the PR.
