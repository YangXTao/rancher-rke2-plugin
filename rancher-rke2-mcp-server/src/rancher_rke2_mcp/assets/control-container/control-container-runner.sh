#!/usr/bin/env bash
set -Eeuo pipefail

manager_dir=${1:?manager directory is required}
env_file="$manager_dir/.control-container.env"
password_file="$manager_dir/.registry-password"
trap 'rm -f "$env_file" "$password_file"' EXIT
[[ -f "$env_file" ]] || { echo "CONTROL_CONTAINER_ENV_MISSING" >&2; exit 2; }
chmod 0600 "$env_file"
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

runner="$manager_dir/run-logged.sh"
prepare="$manager_dir/prepare-control-container.sh"
chmod 0700 "$runner" "$prepare"

bash "$runner" "$manager_dir/install-docker.log" bash "$prepare" install-docker "$password_file"
bash "$runner" "$manager_dir/prepare-container.log" bash "$prepare" prepare-container "$password_file"
bash "$runner" "$manager_dir/validate-container.log" bash "$prepare" validate-container "$password_file"
