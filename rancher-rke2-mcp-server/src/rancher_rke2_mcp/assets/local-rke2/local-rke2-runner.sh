#!/usr/bin/env bash
set -Eeuo pipefail

run_dir=${1:?run directory is required}
container_name=${2:?container name is required}
container_image=${3:?container image is required}
container_strategy=${4:?container strategy is required}
software_root=${5:?software root is required}
workspace_root=${6:?workspace root is required}
mode=${7:?download mode is required}
rke2_version=${8:?RKE2 version is required}
shift 8
offline_images=("$@")

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
for executable in run-logged.sh install-control-dependencies.sh prepare-rke2-artifacts.sh; do
  [[ -x "$run_dir/$executable" ]] || chmod 0755 "$run_dir/$executable"
done
command -v docker >/dev/null 2>&1 || { echo "CONTROL_DOCKER_UNAVAILABLE" >&2; exit 3; }
docker container inspect "$container_name" >/dev/null 2>&1 || { echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2; exit 4; }
owner=$(docker inspect --format '{{index .Config.Labels "io.codex.rancher-rke2-control"}}' "$container_name")
[[ "$owner" == true ]] || { echo "CONTROL_CONTAINER_NAME_CONFLICT" >&2; exit 4; }
docker start "$container_name" >/dev/null 2>&1 || true
test "$(docker inspect --format '{{.State.Running}}' "$container_name")" = true
docker exec -i "$container_name" bash -lc 'test -w /software && test -w /data/rancher/automation && test ! -S /var/run/docker.sock'

docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/install-control-dependencies.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$1/install-control-dependencies.sh" --components local-rke2 --mode "$2" --software-root /software --proxy-url "$HTTP_PROXY"' -- "$run_dir" "$mode"

docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/prepare-rke2-artifacts.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; export DOWNLOAD_PROXY_URL="$HTTP_PROXY"; bash "$1/prepare-rke2-artifacts.sh" "$2" "$3" amd64 "$4/rke2" "${@:5}"' -- "$run_dir" "$mode" "$rke2_version" "$software_root" "${offline_images[@]}"

docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/ansible-inventory.log" \
  bash -lc "cd '$run_dir/ansible' && ansible-inventory --list > '$run_dir/ansible-inventory.json' && python3 '$run_dir/verify-inventory.py' '$run_dir/ansible-inventory.json' management_servers 3 '$rke2_version'"

docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/ansible-playbook.log" \
  bash -lc "cd '$run_dir/ansible' && ansible-playbook playbooks/local-rke2.yml"
docker exec -i "$container_name" bash -lc "printf 'component: local-rke2\\nstate: SUCCEEDED\\n' > '$run_dir/checkpoint.yaml'"
rm -f "$run_dir/.dependency.env"
echo "LOCAL_RKE2_SUCCEEDED run_dir=$run_dir"
