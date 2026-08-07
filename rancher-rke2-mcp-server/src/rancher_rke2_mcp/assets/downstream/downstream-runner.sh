#!/usr/bin/env bash
set -Eeuo pipefail

run_dir=${1:?run directory is required}
container_name=${2:?control container name is required}
container_image=${3:?control container image is required}
container_strategy=${4:?control container strategy is required}
software_root=${5:?software root is required}
workspace_root=${6:?workspace root is required}
mode=${7:?download mode is required}
rancher_version=${8:?rancher version is required}
rancher2_provider_version=${9:?rancher2 provider version is required}
rancher_api_url=${10:?rancher api url is required}
rancher_ca_source=${11:?rancher private-CA certificate source is required}
terraform_version=${12:?terraform version is required}
cluster_name=${13:?cluster name is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
chmod 0600 "$run_dir/.dependency.env"
trap 'rm -f "$run_dir/.dependency.env"' EXIT

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

# The Rancher run's private-CA certificate is a durable artifact of the same
# configuration; copy it into this run's protected secrets directory so every
# Rancher API client (token script and Terraform provider) trusts RancherLB.
docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/rancher-ca.log" \
  bash -lc "install -D -m 0600 '$rancher_ca_source' '$run_dir/secrets/rancher-ca.pem'"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/install-control-dependencies.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$2/scripts/install-control-dependencies.sh" --components downstream --mode "$3" --software-root /software --proxy-url "$HTTP_PROXY" --terraform-version "$4"' -- "$run_dir" "$run_dir" "$mode" "$terraform_version"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/provider-cache.log" \
  bash -lc 'set -a; source "$1/.dependency.env"; set +a; bash "$2/scripts/prepare-terraform-provider-cache.sh" --mode "$3" --source rancher/rancher2 --version "$4" --release-base-url https://github.com/rancher/terraform-provider-rancher2/releases/download --software-root /software --proxy-url "$HTTP_PROXY"' -- "$run_dir" "$run_dir" "$mode" "$rancher2_provider_version"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/rancher-api-token.log" \
  bash -lc "python3 '$run_dir/scripts/prepare-rancher-api-token.py' --api-url '$rancher_api_url' --rancher-version '$rancher_version' --admin-password-file '$run_dir/secrets/rancher-bootstrap-password' --ca-file '$run_dir/secrets/rancher-ca.pem' --output-directory '$run_dir/secrets/rancher-api-token' --desired-server-url '$rancher_api_url'"

terraform_env="set -a; source '$run_dir/.dependency.env'; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy; export SSL_CERT_FILE='$run_dir/secrets/rancher-ca.pem'; export TF_CLI_CONFIG_FILE=/software/terraform/provider-cache-config/rancher-rancher2.tfrc; export TF_PLUGIN_CACHE_DIR=/software/terraform/plugin-cache; mkdir -p \"\$TF_PLUGIN_CACHE_DIR\""
docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/terraform-init.log" \
  bash -lc "cd '$run_dir/terraform' && $terraform_env && terraform init"
docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/terraform-apply.log" \
  bash -lc "cd '$run_dir/terraform' && $terraform_env && bash '$run_dir/scripts/terraform-with-rancher-token.sh' '$run_dir/secrets/rancher-api-token/token-key' apply -auto-approve"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/save-registration-command.log" \
  bash -lc "cd '$run_dir/terraform' && python3 '$run_dir/scripts/save-downstream-registration-command.py' '$run_dir/terraform' '$run_dir/secrets/downstream-registration-command'"

docker exec -i "$container_name" bash "$run_dir/scripts/run-logged.sh" "$run_dir/downstream-register.log" \
  bash -lc "cd '$run_dir/ansible' && ansible-playbook playbooks/downstream-register.yml"

for log_name in install-control-dependencies.log provider-cache.log rancher-api-token.log terraform-init.log terraform-apply.log save-registration-command.log downstream-register.log; do
  [[ -s "$run_dir/$log_name" ]] || { echo "DOWNSTREAM_LOG_MISSING: $log_name" >&2; exit 5; }
done

cluster_id=""
docker exec -i "$container_name" bash -lc "cd '$run_dir/terraform' && terraform output -raw cluster_id 2>/dev/null" > "$run_dir/.cluster-id" || true
cluster_id=$(tr -d '\r\n' < "$run_dir/.cluster-id" 2>/dev/null || true)
rm -f "$run_dir/.cluster-id"

docker exec -i "$container_name" bash -lc "printf 'component: downstream\\nstate: SUCCEEDED\\ncluster_name: %s\\ncluster_id: %s\\nprovider_source: rancher/rancher2\\nprovider_version: %s\\nprovider_cache: /software/terraform\\n' '$cluster_name' '$cluster_id' '$rancher2_provider_version' > '$run_dir/checkpoint.yaml'"
rm -f "$run_dir/.dependency.env"
echo "DOWNSTREAM_SUCCEEDED run_dir=$run_dir"
