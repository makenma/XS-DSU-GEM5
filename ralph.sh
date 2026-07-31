#!/usr/bin/env bash
# Ralph autonomous loop adapted for OpenAI Codex CLI.
# Usage: ./ralph.sh [options] [max_iterations]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
PROMPT_FILE="$SCRIPT_DIR/scripts/ralph/CODEX.md"
TOOL="codex"
MODEL=""
SANDBOX_MODE="${RALPH_CODEX_SANDBOX:-workspace-write}"
MAX_ITERATIONS=10
CHECK_ONLY=false
DRY_RUN=false
UNSAFE=false

usage() {
  cat <<'EOF'
Usage: ./ralph.sh [options] [max_iterations]

Options:
  --tool codex             Compatibility option; Codex is the only supported tool
  --project DIR            Project containing prd.json (default: script directory)
  --model MODEL            Override the Codex model
  --sandbox MODE           read-only, workspace-write, or danger-full-access
                           (default: workspace-write)
  --unsafe                 Bypass Codex approvals and sandboxing
  --check                  Validate dependencies and project state, then exit
  --dry-run                Print the Codex command without invoking it
  -h, --help               Show this help

Environment:
  RALPH_CODEX_SANDBOX      Default sandbox mode when --sandbox is omitted
  RALPH_CODEX_SOURCE_HOME  Read-only Codex config/auth source
                           (default: $CODEX_HOME or ~/.codex)
  RALPH_CODEX_RUNTIME_HOME Writable Codex runtime home
                           (default: $TMPDIR/ralph-codex-$UID)
EOF
}

need_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    echo "Error: $1 requires a value." >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool)
      need_value "$@"
      TOOL="$2"
      shift 2
      ;;
    --tool=*)
      TOOL="${1#*=}"
      shift
      ;;
    --project)
      need_value "$@"
      PROJECT_DIR="$2"
      shift 2
      ;;
    --project=*)
      PROJECT_DIR="${1#*=}"
      shift
      ;;
    --model)
      need_value "$@"
      MODEL="$2"
      shift 2
      ;;
    --model=*)
      MODEL="${1#*=}"
      shift
      ;;
    --sandbox)
      need_value "$@"
      SANDBOX_MODE="$2"
      shift 2
      ;;
    --sandbox=*)
      SANDBOX_MODE="${1#*=}"
      shift
      ;;
    --unsafe)
      UNSAFE=true
      shift
      ;;
    --check)
      CHECK_ONLY=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        MAX_ITERATIONS="$1"
        shift
      else
        echo "Error: unknown argument '$1'." >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

if [[ "$TOOL" != "codex" ]]; then
  echo "Error: this project adapter supports only '--tool codex'." >&2
  exit 2
fi

if [[ ! "$MAX_ITERATIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: max_iterations must be a positive integer." >&2
  exit 2
fi

case "$SANDBOX_MODE" in
  read-only|workspace-write|danger-full-access) ;;
  *)
    echo "Error: invalid sandbox '$SANDBOX_MODE'." >&2
    exit 2
    ;;
esac

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Error: project directory does not exist: $PROJECT_DIR" >&2
  exit 2
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
PRD_FILE="$PROJECT_DIR/prd.json"
PROGRESS_FILE="$PROJECT_DIR/progress.txt"
STATE_DIR="$PROJECT_DIR/.ralph"
ARCHIVE_DIR="$STATE_DIR/archive"
LAST_BRANCH_FILE="$STATE_DIR/last-branch"
LAST_MESSAGE_FILE="$STATE_DIR/last-message.txt"
SOURCE_CODEX_DIR="${RALPH_CODEX_SOURCE_HOME:-${CODEX_HOME:-$HOME/.codex}}"
RUNTIME_BASE_DIR="${TMPDIR:-/tmp}"
RUNTIME_CODEX_DIR="${RALPH_CODEX_RUNTIME_HOME:-$RUNTIME_BASE_DIR/ralph-codex-$UID}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command not found: $1" >&2
    exit 127
  fi
}

require_command codex
require_command git
require_command python3

if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: project is not inside a Git work tree: $PROJECT_DIR" >&2
  exit 2
fi

GIT_DIR="$(git -C "$PROJECT_DIR" rev-parse --absolute-git-dir)"
GIT_COMMON_DIR="$(git -C "$PROJECT_DIR" rev-parse --git-common-dir)"
if [[ "$GIT_COMMON_DIR" != /* ]]; then
  GIT_COMMON_DIR="$PROJECT_DIR/$GIT_COMMON_DIR"
fi
GIT_COMMON_DIR="$(cd "$GIT_COMMON_DIR" && pwd)"

if [[ ! -r "$PRD_FILE" ]]; then
  echo "Error: missing readable PRD: $PRD_FILE" >&2
  exit 2
fi

if [[ ! -r "$PROMPT_FILE" ]]; then
  echo "Error: missing Codex prompt: $PROMPT_FILE" >&2
  exit 2
fi

if [[ ! -r "$SOURCE_CODEX_DIR/auth.json" ]]; then
  echo "Error: missing readable Codex authentication: $SOURCE_CODEX_DIR/auth.json" >&2
  exit 2
fi

# Codex 0.145+ persists app-server and SQLite state even for `exec --ephemeral`.
# Keep credentials/config readable from the user's installation while giving
# the autonomous child a private writable runtime home.
umask 077
mkdir -p "$RUNTIME_CODEX_DIR"
chmod 700 "$RUNTIME_CODEX_DIR"
for codex_file in auth.json config.toml models_cache.json installation_id; do
  if [[ -r "$SOURCE_CODEX_DIR/$codex_file" ]]; then
    install -m 600 "$SOURCE_CODEX_DIR/$codex_file" \
      "$RUNTIME_CODEX_DIR/$codex_file"
  fi
done

CURRENT_BRANCH="$(
  python3 -c 'import json, sys; value=json.load(open(sys.argv[1], encoding="utf-8")).get("branchName", ""); print(value)' "$PRD_FILE"
)"
INCOMPLETE_COUNT="$(
  python3 -c 'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(sum(not story.get("passes", False) for story in data.get("userStories", [])))' "$PRD_FILE"
)"

if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "Error: prd.json has no non-empty branchName." >&2
  exit 2
fi

CODEX_COMMAND=(
  env "CODEX_HOME=$RUNTIME_CODEX_DIR"
  codex exec
  --ephemeral
  --color never
  --output-last-message "$LAST_MESSAGE_FILE"
  -C "$PROJECT_DIR"
)
if [[ "$UNSAFE" == true ]]; then
  CODEX_COMMAND+=(--dangerously-bypass-approvals-and-sandbox)
else
  CODEX_COMMAND+=(--sandbox "$SANDBOX_MODE")
  CODEX_COMMAND+=(--add-dir "$GIT_DIR")
  if [[ "$GIT_COMMON_DIR" != "$GIT_DIR" ]]; then
    CODEX_COMMAND+=(--add-dir "$GIT_COMMON_DIR")
  fi
fi
if [[ -n "$MODEL" ]]; then
  CODEX_COMMAND+=(--model "$MODEL")
fi
CODEX_COMMAND+=(-)

echo "Ralph Codex adapter check passed"
echo "  Project: $PROJECT_DIR"
echo "  PRD branch: $CURRENT_BRANCH"
echo "  Incomplete stories: $INCOMPLETE_COUNT"
echo "  Max iterations: $MAX_ITERATIONS"
echo "  Sandbox: $([[ "$UNSAFE" == true ]] && echo bypassed || echo "$SANDBOX_MODE")"
echo "  Codex runtime: $RUNTIME_CODEX_DIR"

if [[ "$CHECK_ONLY" == true ]]; then
  exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
  printf '  Command:'
  printf ' %q' "${CODEX_COMMAND[@]}"
  printf ' < %q\n' "$PROMPT_FILE"
  exit 0
fi

mkdir -p "$STATE_DIR"

if [[ -f "$LAST_BRANCH_FILE" ]]; then
  LAST_BRANCH="$(<"$LAST_BRANCH_FILE")"
  if [[ -n "$LAST_BRANCH" && "$CURRENT_BRANCH" != "$LAST_BRANCH" ]]; then
    if [[ -f "$PROGRESS_FILE" ]] &&
       grep -Eq '^## .+ - US-[0-9]+' "$PROGRESS_FILE"; then
      DATE="$(date +%Y-%m-%d)"
      FOLDER_NAME="${LAST_BRANCH#ralph/}"
      FOLDER_NAME="${FOLDER_NAME//\//-}"
      ARCHIVE_FOLDER="$ARCHIVE_DIR/$DATE-$FOLDER_NAME"
      mkdir -p "$ARCHIVE_FOLDER"
      cp "$PRD_FILE" "$ARCHIVE_FOLDER/"
      cp "$PROGRESS_FILE" "$ARCHIVE_FOLDER/"
      echo "Archived previous run to: $ARCHIVE_FOLDER"
    fi
    {
      echo "# Ralph Progress Log"
      echo "Started: $(date --iso-8601=seconds)"
      echo "---"
    } > "$PROGRESS_FILE"
  fi
fi

printf '%s\n' "$CURRENT_BRANCH" > "$LAST_BRANCH_FILE"

if [[ ! -f "$PROGRESS_FILE" ]]; then
  {
    echo "# Ralph Progress Log"
    echo "Started: $(date --iso-8601=seconds)"
    echo "---"
  } > "$PROGRESS_FILE"
fi

echo "Starting Ralph with Codex"

for ((iteration = 1; iteration <= MAX_ITERATIONS; iteration++)); do
  echo
  echo "==============================================================="
  echo "  Ralph iteration $iteration of $MAX_ITERATIONS (codex)"
  echo "==============================================================="

  : > "$LAST_MESSAGE_FILE"
  set +e
  "${CODEX_COMMAND[@]}" < "$PROMPT_FILE"
  CODEX_STATUS=$?
  set -e

  if [[ "$CODEX_STATUS" -ne 0 ]]; then
    echo
    echo "Codex failed with status $CODEX_STATUS; stopping without consuming more iterations." >&2
    exit "$CODEX_STATUS"
  fi

  INCOMPLETE_COUNT="$(
    python3 -c 'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(sum(not story.get("passes", False) for story in data.get("userStories", [])))' "$PRD_FILE"
  )"

  if grep -Fxq '<promise>COMPLETE</promise>' "$LAST_MESSAGE_FILE" ||
     [[ "$INCOMPLETE_COUNT" -eq 0 ]]; then
    echo
    echo "Ralph completed all tasks at iteration $iteration."
    exit 0
  fi

  echo "Iteration $iteration complete; $INCOMPLETE_COUNT stories remain."
  sleep 2
done

echo
echo "Ralph reached max iterations ($MAX_ITERATIONS) with $INCOMPLETE_COUNT stories remaining."
echo "Check $PROGRESS_FILE for status."
exit 1
