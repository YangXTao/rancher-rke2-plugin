#!/usr/bin/env bash
set -Eeuo pipefail

run_dir=${1:?run directory is required}
container_name=${2:?container name is required}
container_image=${3:?container image is required}
container_strategy=${4:?container strategy is required}
software_root=${5:?software root is required}
workspace_root=${6:?workspace root is required}
mode=${7:?download mode is required}
terraform_version=${8:?terraform version is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ "$container_strategy" == reuse-or-create || "$container_strategy" == reuse || "$container_strategy" == create ]] || { echo "INVALID_CONTAINER_STRATEGY" >&2; exit 2; }
[[ -f "$run_dir/.runtime.env" ]] || { echo "RUNTIME_ENV_MISSING" >&2; exit 2; }
source "$run_dir/.runtime.env"
chmod 0600 "$run_dir/.runtime.env"

[[ -x "$run_dir/run-logged.sh" ]] || chmod 0755 "$run_dir/run-logged.sh"
[[ -x "$run_dir/check-ip-conflicts.py" ]] || chmod 0755 "$run_dir/check-ip-conflicts.py"
command -v docker >/dev/null 2>&1 || { echo "CONTROL_DOCKER_UNAVAILABLE" >&2; exit 3; }

if docker container inspect "$container_name" >/dev/null 2>&1; then
  owner=$(docker inspect --format '{{index .Config.Labels "io.codex.rancher-rke2-control"}}' "$container_name")
  [[ "$owner" == true ]] || { echo "CONTROL_CONTAINER_NAME_CONFLICT" >&2; exit 4; }
  if [[ "$container_strategy" == create ]]; then
    docker rm -f "$container_name"
  else
    docker start "$container_name" >/dev/null 2>&1 || true
  fi
fi
if ! docker container inspect "$container_name" >/dev/null 2>&1; then
  if [[ "$container_strategy" == reuse ]]; then
    echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2
    exit 4
  fi
  docker image inspect "$container_image" >/dev/null 2>&1 || {
    [[ "$mode" == online ]] || { echo "CONTROL_IMAGE_OFFLINE_MISSING" >&2; exit 10; }
    docker pull "$container_image"
  }
  docker run -d --name "$container_name" --label io.codex.rancher-rke2-control=true \
    --restart unless-stopped --network host --cap-add NET_RAW \
    -v "$software_root:/software" -v "$workspace_root:/data/rancher/automation" \
    -v /etc/localtime:/etc/localtime:ro "$container_image" sleep infinity >/dev/null
fi
test "$(docker inspect --format '{{.State.Running}}' "$container_name")" = true
docker exec -i "$container_name" bash -lc 'test -w /software && test -w /data/rancher/automation && test ! -S /var/run/docker.sock'

deps_command='set -Eeuo pipefail; export DEBIAN_FRONTEND=noninteractive; if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y --no-upgrade ca-certificates curl unzip python3 iputils-ping; else echo UNSUPPORTED_CONTROL_CONTAINER; exit 6; fi; terraform_version="'"$terraform_version"'"; if ! command -v terraform >/dev/null 2>&1 || ! terraform version | grep -Fq "v${terraform_version}"; then mkdir -p /software/terraform; archive=/software/terraform/terraform_${terraform_version}_linux_amd64.zip; if [[ ! -s "$archive" ]]; then [[ "'"$mode"'" == online ]] || { echo OFFLINE_FILE_MISSING:"$archive"; exit 10; }; curl --fail --location --retry 3 -o "$archive" "https://releases.hashicorp.com/terraform/${terraform_version}/terraform_${terraform_version}_linux_amd64.zip"; fi; tmp=$(mktemp -d); unzip -oq "$archive" -d "$tmp"; install -m 0755 "$tmp/terraform" /usr/local/bin/terraform; rm -rf "$tmp"; fi; terraform version'
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/install-control-dependencies.log" bash -lc "set -a; source '$run_dir/.runtime.env'; set +a; $deps_command"

ip_args=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["ips"]))' "$run_dir/vm-input.json")
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/ip-conflicts.log" bash -lc "python3 '$run_dir/check-ip-conflicts.py' $ip_args"

terraform_env="set -a; source '$run_dir/.runtime.env'; set +a; export TF_PLUGIN_CACHE_DIR=/software/terraform/plugin-cache; mkdir -p \"\$TF_PLUGIN_CACHE_DIR\"; terraform init"
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/terraform-init.log" bash -lc "cd '$run_dir' && $terraform_env"
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/terraform-apply.log" bash -lc "cd '$run_dir' && $terraform_env && terraform apply -auto-approve"
docker exec -i "$container_name" bash -lc "cd '$run_dir' && set -a && source '$run_dir/.runtime.env' && set +a && terraform output -json > '$run_dir/terraform-outputs.json'"
rm -f "$run_dir/.runtime.env"
echo "VM_EXECUTION_SUCCEEDED run_dir=$run_dir"
