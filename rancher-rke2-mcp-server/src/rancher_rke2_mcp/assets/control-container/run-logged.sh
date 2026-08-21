#!/usr/bin/env bash
set -Eeuo pipefail
log_file=${1:?log file is required}
shift
mkdir -p "$(dirname "$log_file")"
"$@" > >(tee -a "$log_file") 2> >(tee -a "$log_file" >&2)
