# Ralph Codex adapter

This project-local runner adapts the upstream snarktank/ralph loop to the
OpenAI Codex CLI. It reads prd.json and progress.txt from the selected project,
starts a fresh ephemeral codex exec process per iteration, and stops when all
stories pass or Codex emits the Ralph completion promise.

## Prerequisites

- An installed and authenticated codex CLI
- Git
- Python 3
- A valid prd.json in the project root

jq is not required by this adapter.

## Usage

From the GEM5 repository root:

    ./ralph.sh --check
    ./ralph.sh --dry-run 1
    ./ralph.sh 10

Optional arguments:

    ./ralph.sh --model <model> 10
    ./ralph.sh --sandbox workspace-write 10
    ./ralph.sh --project /absolute/path/to/project 10

The default workspace-write sandbox is recommended. It grants one additional
write root only for this project's resolved Git metadata directory, allowing
Ralph to create its branch and commit without opening the rest of the local
filesystem. The explicit --unsafe option maps to Codex's
dangerously-bypass-approvals-and-sandbox mode and should only be used when the
surrounding environment already provides isolation.

Runtime branch-tracking and archives live in the ignored .ralph directory.
Story history lives in progress.txt, and completed stories are marked directly
in prd.json.
