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

status=("${PIPESTATUS[@]}")
((status[0] == 0)) || exit "${status[0]}"
((status[1] == 0)) || exit "${status[1]}"
