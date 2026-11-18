# Articulate

Second brain and study curator: a headless Python pipeline on the Mac that takes in Pocket, Canvas and the rest,
writes agent notes into the Obsidian vault, hands MieracleStudy its daily quiz material, and sends tasks to master-todo.

- Planning lives on GitHub Issues as a wayfinder map (label `wayfinder:map`). Decisions there override `docs/PLAN.md`.
- `docs/PLAN.md` is the original, tentative plan from a browser session.

## Wayfinding operations
- Map: an issue labelled `wayfinder:map`. Tickets: sub-issues of the map, labelled `wayfinder:<type>`.
- Blocking: GitHub's native issue dependencies ("blocked by").
- Frontier: open sub-issues of the map with no open blockers and no assignee.
- Claim: assign the ticket to `amierlorenz` before any work.
- Resolve: post the answer as a comment, close the issue, add a line to the map's Decisions so far.
- Out of scope: close the ticket as "not planned" and add a line under Out of scope.
- Subagents never touch GitHub or read credentials; they return text and the main session posts.

## Git workflow (pr mode)
<!-- git-workflow:pr — checked by the git-workflow plugin's guard hook; keep this marker -->
- Never commit to main, never merge a PR, never force-push. If a branch falls behind main, merge main into it.
- One branch per issue or feature: `feat/<issue#>-<slug>`, `fix/<issue#>-<slug>`.
- Conventional Commits 1.0.0, subject line only unless the why isn't obvious from the diff.
  Breaking changes: `!` after the type and a `BREAKING CHANGE:` footer.
- Run `git add` and `git commit -m "<subject>"` as separate commands, never chained.
- PR title is a conventional commit (it becomes the squash commit on main).
  Body: what, why, how tested, open questions, `Closes #N`.
- Review comments: fix what's clear, ask on the PR when ambiguous, reply to every thread.
