#!/usr/bin/env bash
set -uo pipefail

if (($# < 2)); then
  echo "Usage: run-logged.sh LOG_FILE COMMAND [ARG ...]" >&2
  exit 2
fi

log_file=$1
shift
mkdir -p "$(dirname "$log_file")"

{
  printf '\n===== START %s =====\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  printf 'COMMAND:'
  printf ' %q' "$@"
  printf '\n'
  "$@"
  command_rc=$?
  printf 'EXIT_CODE: %s\n' "$command_rc"
  printf '===== END %s =====\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  exit "$command_rc"
} 2>&1 | tee -a "$log_file"

pipeline_status=("${PIPESTATUS[@]}")
if ((pipeline_status[0] != 0)); then
  exit "${pipeline_status[0]}"
fi
if ((pipeline_status[1] != 0)); then
  exit "${pipeline_status[1]}"
fi
