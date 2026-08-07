#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 TOKEN_FILE terraform-arguments..." >&2
  exit 2
fi

token_file=$1
shift
export TF_VAR_rancher_token_key
TF_VAR_rancher_token_key=$(tr -d '\r\n' < "$token_file")
exec terraform "$@"
