#!/usr/bin/env bash
set -Eeuo pipefail
run_dir=${1:?run directory is required}
container_name=${2:?container name is required}
container_image=${3:?container image is required}
container_strategy=${4:?container strategy is required}
software_root=${5:?software root is required}
workspace_root=${6:?workspace root is required}
mode=${7:?download mode is required}
[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
[[ -x "$run_dir/run-logged.sh" ]] || chmod 0755 "$run_dir/run-logged.sh"
[[ -x "$run_dir/install-control-dependencies.sh" ]] || chmod 0755 "$run_dir/install-control-dependencies.sh"
command -v docker >/dev/null 2>&1 || { echo "CONTROL_DOCKER_UNAVAILABLE" >&2; exit 3; }
docker container inspect "$container_name" >/dev/null 2>&1 || { echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2; exit 4; }
owner=$(docker inspect --format '{{index .Config.Labels "io.codex.rancher-rke2-control"}}' "$container_name")
[[ "$owner" == true ]] || { echo "CONTROL_CONTAINER_NAME_CONFLICT" >&2; exit 4; }
docker start "$container_name" >/dev/null 2>&1 || true
test "$(docker inspect --format '{{.State.Running}}' "$container_name")" = true
docker exec -i "$container_name" bash -lc 'test -w /software && test -w /data/rancher/automation && test ! -S /var/run/docker.sock'
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/install-control-dependencies.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$1/install-control-dependencies.sh" --components node-init --mode "$2" --software-root /software --proxy-url "$HTTP_PROXY"' -- "$run_dir" "$mode"
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/ansible-playbook.log" \
  bash -lc "cd '$run_dir/ansible' && ansible-playbook playbooks/node-init.yml"
docker exec -i "$container_name" bash -lc "printf 'component: node-init\\nstate: SUCCEEDED\\n' > '$run_dir/checkpoint.yaml'"
rm -f "$run_dir/.dependency.env"
echo "NODE_INIT_SUCCEEDED run_dir=$run_dir"
