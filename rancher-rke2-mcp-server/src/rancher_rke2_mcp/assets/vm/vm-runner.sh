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
vsphere_provider_version=${9:?vSphere provider version is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ "$container_strategy" == reuse-or-create || "$container_strategy" == reuse || "$container_strategy" == create ]] || { echo "INVALID_CONTAINER_STRATEGY" >&2; exit 2; }
[[ -f "$run_dir/.runtime.env" ]] || { echo "RUNTIME_ENV_MISSING" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
chmod 0600 "$run_dir/.runtime.env" "$run_dir/.dependency.env"
trap 'rm -f "$run_dir/.runtime.env" "$run_dir/.dependency.env"' EXIT

[[ -x "$run_dir/run-logged.sh" ]] || chmod 0755 "$run_dir/run-logged.sh"
[[ -x "$run_dir/check-ip-conflicts.py" ]] || chmod 0755 "$run_dir/check-ip-conflicts.py"
[[ -x "$run_dir/install-control-dependencies.sh" ]] || chmod 0755 "$run_dir/install-control-dependencies.sh"
[[ -x "$run_dir/prepare-terraform-provider-cache.sh" ]] || chmod 0755 "$run_dir/prepare-terraform-provider-cache.sh"
[[ -x "$run_dir/verify-vm-state.py" ]] || chmod 0755 "$run_dir/verify-vm-state.py"
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

docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/install-control-dependencies.log" \
  bash "$run_dir/install-control-dependencies.sh" "$run_dir" "$mode" "$terraform_version"
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/provider-cache.log" \
  bash "$run_dir/prepare-terraform-provider-cache.sh" "$mode" "$vsphere_provider_version" /software "$run_dir"

ip_args=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["ips"]))' "$run_dir/vm-input.json")
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/ip-conflicts.log" bash -lc "python3 '$run_dir/check-ip-conflicts.py' $ip_args"

terraform_env="set -a; source '$run_dir/.runtime.env'; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy; export TF_CLI_CONFIG_FILE=/software/terraform/provider-cache-config/vmware-vsphere.tfrc; export TF_PLUGIN_CACHE_DIR=/software/terraform/plugin-cache; mkdir -p \"\$TF_PLUGIN_CACHE_DIR\""
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/terraform-init.log" bash -lc "cd '$run_dir' && $terraform_env && terraform init"
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/terraform-apply.log" bash -lc "cd '$run_dir' && $terraform_env && terraform apply -auto-approve"
expected_vm_count=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["ips"]))' "$run_dir/vm-input.json")
docker exec -i "$container_name" bash "$run_dir/run-logged.sh" "$run_dir/terraform-verify.log" \
  bash -lc "cd '$run_dir' && $terraform_env && terraform state list | python3 '$run_dir/verify-vm-state.py' --expected '$expected_vm_count'"
docker exec -i "$container_name" bash -lc "cd '$run_dir' && $terraform_env && terraform output -json > '$run_dir/terraform-outputs.json'"
echo "VM_EXECUTION_SUCCEEDED run_dir=$run_dir"
