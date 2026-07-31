# Ralph Instructions for Codex

You are one fresh iteration in an autonomous coding loop. Work in the current
Git repository and complete exactly one PRD user story.

## Start-of-iteration checks

1. Read prd.json from the repository root.
2. Read progress.txt, starting with its Codebase Patterns section when present.
3. Read and obey every applicable AGENTS.md before editing files.
4. Inspect git status and record which changes already existed. They belong to
   the user: never overwrite, discard, or commit unrelated pre-existing work.
   The sole exception is a set of interrupted Ralph files explicitly identified
   in progress.txt for the selected story; review their diff, then continue and
   stage them as part of that same story.
5. Read branchName from prd.json. If needed, safely switch to or create that
   branch without discarding local changes.
6. Select the lowest-priority-number story whose passes field is false.

## Implement one story

1. Implement only the selected story and its acceptance criteria.
2. Follow existing code patterns and keep the change focused.
3. Run the relevant build, tests, linters, and formatting checks. Do not mark a
   story complete while any required check is failing.
4. If blocked, leave passes false and append the blocker plus attempted
   diagnostics to progress.txt. Do not fabricate success.
5. Do not push, open a PR, contact external services, or broaden scope unless
   the PRD explicitly requires it.

## Complete the iteration atomically

After the implementation and checks pass:

1. Set only the selected story's passes field to true in prd.json.
2. Append this entry to progress.txt:

   ## YYYY-MM-DD HH:MM - US-NNN
   - Story: title
   - What was implemented
   - Files changed
   - Checks run and their results
   - Learnings for future iterations
   ---

3. Add genuinely reusable codebase patterns near the top of progress.txt under
   a Codebase Patterns heading. Do not add story-specific trivia.
4. Update a nearby AGENTS.md only when a durable repository convention was
   discovered and the file is not intentionally ignored or user-owned.
5. Stage only files changed for this story plus prd.json and progress.txt. Use
   explicit paths; never use git add -A or git add .
6. Review the staged diff and commit it with the repository-required subject
   prefix and format: `mem-cache: [Story ID] - [Story Title]`. Use the full
   story title when it satisfies the repository hook. If the hook rejects that
   title only because the complete header exceeds its enforced length limit,
   retain `mem-cache: [Story ID] -` and use a clear, unambiguous concise title
   that fits the limit. Do not infer that `feat:` is accepted from existing
   history, and never bypass hooks with `--no-verify`.

7. Confirm the commit contains no unrelated pre-existing changes.

If every story in prd.json now has passes set to true, end the response with:

<promise>COMPLETE</promise>

Otherwise, end normally so the next fresh Codex iteration can continue.
