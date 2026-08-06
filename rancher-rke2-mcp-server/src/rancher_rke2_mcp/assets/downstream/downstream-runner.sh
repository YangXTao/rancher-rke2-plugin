#!/usr/bin/env bash
set -Eeuo pipefail

run_dir=${1:?run directory is required}
container_name=${2:?control container name is required}
container_image=${3:?control container image is required}
container_strategy=${4:?control container strategy is required}
software_root=${5:?software root is required}
workspace_root=${6:?workspace root is required}
mode=${7:?download mode is required}
rancher_api_url=${8:?Rancher API URL is required}
rancher_version=${9:?Rancher version is required}
rancher_server_url=${10:?Rancher server-url is required}
rancher_ca_file=${11:?Rancher CA file is required}
rancher2_provider_version=${12:?rancher2 Provider version is required}
terraform_version=${13:?Terraform version is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
for executable in run-logged.sh install-control-dependencies.sh prepare-terraform-provider-cache.sh prepare-rancher-api-token.py save-downstream-registration-command.py terraform-with-rancher-token.sh; do
  [[ -x "$run_dir/scripts/$executable" ]] || chmod 0755 "$run_dir/scripts/$executable"
done
command -v docker >/dev/null 2>&1 || { echo "CONTROL_DOCKER_UNAVAILABLE" >&2; exit 3; }
docker container inspect "$container_name" >/dev/null 2>&1 || { echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2; exit 4; }
owner=$(docker inspect --format '{{index .Config.Labels "io.codex.rancher-rke2-control"}}' "$container_name")
[[ "$owner" == true ]] || { echo "CONTROL_CONTAINER_NAME_CONFLICT" >&2; exit 4; }
docker start "$container_name" >/dev/null 2>&1 || true
test "$(docker inspect --format '{{.State.Running}}' "$container_name")" = true
docker exec -i "$container_name" bash -lc 'test -w /software && test -w /data/rancher/automation && test ! -S /var/run/docker.sock'

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/install-control-dependencies.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$1/scripts/install-control-dependencies.sh" --components downstream --mode "$2" --software-root /software --proxy-url "$HTTP_PROXY" --terraform-version "$3"' -- "$run_dir" "$mode" "$terraform_version"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/provider-cache.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$1/scripts/prepare-terraform-provider-cache.sh" --mode "$2" --source rancher/rancher2 --version "$3" --release-base-url https://github.com/rancher/terraform-provider-rancher2/releases/download --software-root /software --proxy-url "$HTTP_PROXY"' -- "$run_dir" "$mode" "$rancher2_provider_version"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/rancher-api-token.log" \
  bash -lc 'python3 "$1/scripts/prepare-rancher-api-token.py" --api-url "$2" --rancher-version "$3" --admin-username admin --admin-password-file "$1/secrets/rancher-bootstrap-password" --ca-file "$4" --output-directory "$1/secrets/rancher-api-token" --token-api auto --desired-server-url "$5"' -- "$run_dir" "$rancher_api_url" "$rancher_version" "$rancher_ca_file" "$rancher_server_url"

terraform_env="export TF_CLI_CONFIG_FILE=/software/terraform/provider-cache-config/rancher-rancher2.tfrc; export TF_PLUGIN_CACHE_DIR=/software/terraform/plugin-cache; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy"
docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/terraform-init.log" \
  bash -lc "cd '$run_dir' && $terraform_env && terraform init"
docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/terraform-apply.log" \
  bash -lc "cd '$run_dir' && $terraform_env && bash '$run_dir/scripts/terraform-with-rancher-token.sh' '$run_dir/secrets/rancher-api-token/token-key' apply -auto-approve"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/save-registration-command.log" \
  bash -lc "cd '$run_dir' && $terraform_env && python3 '$run_dir/scripts/save-downstream-registration-command.py' '$run_dir' '$run_dir/secrets/downstream-registration-command'"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/downstream-register.log" \
  bash -lc "cd '$run_dir/ansible' && ansible-playbook playbooks/downstream-register.yml"

cluster_id=$(docker exec -i "$container_name" bash -lc "cd '$run_dir' && $terraform_env && terraform output -raw cluster_id")
docker exec -i "$container_name" bash -lc "printf 'component: downstream\\nstate: SUCCEEDED\\nprovider_source: rancher/rancher2\\nprovider_version: %s\\ncluster_id: %s\\n' '$rancher2_provider_version' '$cluster_id' > '$run_dir/checkpoint.yaml'"
rm -f "$run_dir/.dependency.env"
echo "DOWNSTREAM_SUCCEEDED run_dir=$run_dir"
