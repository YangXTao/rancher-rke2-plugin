#!/usr/bin/env bash
set -Eeuo pipefail

phase=${1:?phase is required}
password_file=${2:-}

required=(
  CONTROL_MODE CONTROL_SOFTWARE_ROOT CONTROL_WORKSPACE_ROOT CONTROL_CONTAINER_NAME
  CONTROL_CONTAINER_STRATEGY CONTROL_CONTAINER_IMAGE CONTROL_IMAGE_PULL_POLICY
  CONTROL_ALLOW_OFFLINE_REGISTRY_PULL CONTROL_DOCKER_VERSION CONTROL_DOCKER_ARCHIVE
  CONTROL_DOCKER_URL CONTROL_REGISTRY_HOST CONTROL_REGISTRY_INSECURE
)
for name in "${required[@]}"; do
  [[ -n "${!name+x}" ]] || { echo "CONTROL_CONFIG_MISSING: $name" >&2; exit 2; }
done
[[ $EUID -eq 0 ]] || { echo "CONTROL_DOCKER_ROOT_REQUIRED" >&2; exit 3; }
[[ "$CONTROL_MODE" == online || "$CONTROL_MODE" == offline ]] || exit 2
[[ "$CONTROL_CONTAINER_STRATEGY" == reuse-or-create ||
   "$CONTROL_CONTAINER_STRATEGY" == reuse ||
   "$CONTROL_CONTAINER_STRATEGY" == create ]] || exit 2

download_docker() {
  local partial="${CONTROL_DOCKER_ARCHIVE}.partial"
  rm -f "$partial"
  if command -v curl >/dev/null 2>&1; then
    args=(--fail --location --retry 3 --output "$partial")
    [[ -z "${CONTROL_PROXY_URL:-}" ]] || args+=(--proxy "$CONTROL_PROXY_URL")
    curl "${args[@]}" "$CONTROL_DOCKER_URL"
  elif command -v wget >/dev/null 2>&1; then
    HTTP_PROXY="${CONTROL_PROXY_URL:-}" HTTPS_PROXY="${CONTROL_PROXY_URL:-}" \
      wget --tries=3 --output-document="$partial" "$CONTROL_DOCKER_URL"
  else
    echo "CONTROL_DOCKER_DOWNLOADER_MISSING" >&2
    exit 5
  fi
  tar -tzf "$partial" | grep -x 'docker/docker' >/dev/null
  mv -f "$partial" "$CONTROL_DOCKER_ARCHIVE"
}

install_docker() {
  mkdir -p "$(dirname "$CONTROL_DOCKER_ARCHIVE")" "$CONTROL_SOFTWARE_ROOT/control-container/images" "$CONTROL_WORKSPACE_ROOT"
  if [[ ! -s "$CONTROL_DOCKER_ARCHIVE" ]]; then
    [[ "$CONTROL_MODE" == online ]] || { echo "CONTROL_DOCKER_OFFLINE_MISSING: $CONTROL_DOCKER_ARCHIVE" >&2; exit 4; }
    download_docker
  fi
  tar -tzf "$CONTROL_DOCKER_ARCHIVE" | grep -x 'docker/docker' >/dev/null || { echo "CONTROL_DOCKER_ARCHIVE_INVALID" >&2; exit 4; }
  current=
  command -v docker >/dev/null 2>&1 && current=$(docker --version | sed -n 's/^Docker version \([^,]*\),.*/\1/p')
  if [[ "$current" != "$CONTROL_DOCKER_VERSION" ]] || ! command -v dockerd >/dev/null 2>&1; then
    temp_dir=$(mktemp -d /tmp/control-docker.XXXXXX)
    tar -xzf "$CONTROL_DOCKER_ARCHIVE" -C "$temp_dir"
    for binary in "$temp_dir"/docker/*; do install -m 0755 "$binary" "/usr/local/bin/$(basename "$binary")"; done
    rm -rf "$temp_dir"
    install -d /etc/systemd/system
    printf '%s\n' '[Unit]' 'Description=Docker Application Container Engine' 'After=network-online.target' 'Wants=network-online.target' '' '[Service]' 'Type=notify' 'ExecStart=/usr/local/bin/dockerd' 'ExecReload=/bin/kill -s HUP $MAINPID' 'LimitNOFILE=infinity' 'LimitNPROC=infinity' 'LimitCORE=infinity' 'TasksMax=infinity' 'Delegate=yes' 'KillMode=process' 'Restart=always' 'RestartSec=2' '' '[Install]' 'WantedBy=multi-user.target' > /etc/systemd/system/docker.service
  fi
  if [[ "$CONTROL_REGISTRY_INSECURE" == true ]]; then
    install -d /etc/docker
    if [[ ! -s /etc/docker/daemon.json ]]; then
      printf '{"insecure-registries":["%s"]}\n' "$CONTROL_REGISTRY_HOST" > /etc/docker/daemon.json
    elif ! grep -Fq "\"$CONTROL_REGISTRY_HOST\"" /etc/docker/daemon.json; then
      echo "CONTROL_DOCKER_CONFIG_MERGE_REQUIRED: $CONTROL_REGISTRY_HOST" >&2
      exit 8
    fi
  fi
  systemctl daemon-reload
  systemctl enable --now docker.service
  docker version >/dev/null
}

image_registry() {
  local first=${CONTROL_CONTAINER_IMAGE%%/*}
  [[ "$first" == *.* || "$first" == *:* || "$first" == localhost ]] && printf '%s' "$first" || printf '%s' docker.io
}

prepare_image() {
  local have=false
  docker image inspect "$CONTROL_CONTAINER_IMAGE" >/dev/null 2>&1 && have=true
  if [[ -n "${CONTROL_IMAGE_ARCHIVE:-}" && -s "$CONTROL_IMAGE_ARCHIVE" ]]; then
    docker load -i "$CONTROL_IMAGE_ARCHIVE" >/dev/null
    docker image inspect "$CONTROL_CONTAINER_IMAGE" >/dev/null
    have=true
  fi
  [[ "$CONTROL_IMAGE_PULL_POLICY" != always && "$have" == true ]] && return
  [[ "$CONTROL_IMAGE_PULL_POLICY" != never ]] || { echo "CONTROL_IMAGE_MISSING" >&2; exit 6; }
  if [[ "$CONTROL_MODE" == offline ]]; then
    [[ "$CONTROL_ALLOW_OFFLINE_REGISTRY_PULL" == true && "$(image_registry)" == "$CONTROL_REGISTRY_HOST" ]] || { echo "CONTROL_IMAGE_OFFLINE_MISSING" >&2; exit 6; }
  fi
  docker_config=$(mktemp -d /tmp/control-docker-config.XXXXXX)
  export DOCKER_CONFIG="$docker_config"
  proxy_enabled=false
  cleanup_pull_environment() {
    rm -rf "$docker_config"
    if [[ "$proxy_enabled" == true ]]; then
      systemctl unset-environment HTTP_PROXY HTTPS_PROXY NO_PROXY
      systemctl restart docker.service
    fi
  }
  trap cleanup_pull_environment EXIT
  if [[ -n "${CONTROL_PROXY_URL:-}" ]]; then
    systemctl set-environment "HTTP_PROXY=$CONTROL_PROXY_URL" "HTTPS_PROXY=$CONTROL_PROXY_URL" "NO_PROXY=127.0.0.1,localhost,$CONTROL_REGISTRY_HOST,192.168.0.0/16,10.0.0.0/8"
    systemctl restart docker.service
    proxy_enabled=true
  fi
  if [[ -n "${CONTROL_REGISTRY_USERNAME:-}" ]]; then
    [[ -s "$password_file" ]] || { echo "CONTROL_REGISTRY_PASSWORD_MISSING" >&2; exit 6; }
    docker login "$CONTROL_REGISTRY_HOST" --username "$CONTROL_REGISTRY_USERNAME" --password-stdin < "$password_file" >/dev/null
  fi
  docker pull "$CONTROL_CONTAINER_IMAGE"
  cleanup_pull_environment
  trap - EXIT
}

container_compatible() {
  [[ "$(docker inspect --format '{{.Config.Image}}' "$CONTROL_CONTAINER_NAME")" == "$CONTROL_CONTAINER_IMAGE" ]] || return 1
  [[ "$(docker inspect --format '{{.Image}}' "$CONTROL_CONTAINER_NAME")" == "$(docker image inspect --format '{{.Id}}' "$CONTROL_CONTAINER_IMAGE")" ]] || return 1
  [[ "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$CONTROL_CONTAINER_NAME")" == host ]] || return 1
  [[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$CONTROL_CONTAINER_NAME")" == unless-stopped ]] || return 1
  [[ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/software"}}{{.Source}}{{end}}{{end}}' "$CONTROL_CONTAINER_NAME")" == "$CONTROL_SOFTWARE_ROOT" ]] || return 1
  [[ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data/rancher/automation"}}{{.Source}}{{end}}{{end}}' "$CONTROL_CONTAINER_NAME")" == "$CONTROL_WORKSPACE_ROOT" ]] || return 1
  [[ "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/localtime"}}{{.Source}}:{{.RW}}{{end}}{{end}}' "$CONTROL_CONTAINER_NAME")" == /etc/localtime:false ]] || return 1
  [[ -z "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}{{.Source}}{{end}}{{end}}' "$CONTROL_CONTAINER_NAME")" ]] || return 1
  [[ "$(docker inspect --format '{{json .HostConfig.CapAdd}}' "$CONTROL_CONTAINER_NAME")" == *NET_RAW* ]] || return 1
}

prepare_container() {
  prepare_image
  if docker container inspect "$CONTROL_CONTAINER_NAME" >/dev/null 2>&1; then
    [[ "$(docker inspect --format '{{index .Config.Labels "io.codex.rancher-rke2-control"}}' "$CONTROL_CONTAINER_NAME")" == true ]] || { echo "CONTROL_CONTAINER_NAME_CONFLICT" >&2; exit 7; }
    if [[ "$CONTROL_CONTAINER_STRATEGY" == create ]]; then
      docker rm -f "$CONTROL_CONTAINER_NAME" >/dev/null
    elif ! container_compatible; then
      [[ "$CONTROL_CONTAINER_STRATEGY" != reuse ]] || { echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2; exit 8; }
      docker rm -f "$CONTROL_CONTAINER_NAME" >/dev/null
    else
      docker start "$CONTROL_CONTAINER_NAME" >/dev/null
    fi
  fi
  if ! docker container inspect "$CONTROL_CONTAINER_NAME" >/dev/null 2>&1; then
    [[ "$CONTROL_CONTAINER_STRATEGY" != reuse ]] || { echo "CONTROL_CONTAINER_REUSE_UNAVAILABLE" >&2; exit 8; }
    docker run -d --name "$CONTROL_CONTAINER_NAME" --label io.codex.rancher-rke2-control=true --restart unless-stopped --network host --cap-add NET_RAW -v "$CONTROL_SOFTWARE_ROOT:/software" -v "$CONTROL_WORKSPACE_ROOT:/data/rancher/automation" -v /etc/localtime:/etc/localtime:ro "$CONTROL_CONTAINER_IMAGE" sleep infinity >/dev/null
  fi
}

validate_container() {
  container_compatible || { echo "CONTROL_CONTAINER_DEFINITION_INVALID" >&2; exit 8; }
  [[ "$(docker inspect --format '{{.State.Running}}' "$CONTROL_CONTAINER_NAME")" == true ]]
  docker exec -i "$CONTROL_CONTAINER_NAME" sh -lc 'test -w /software && test -w /data/rancher/automation && test ! -S /var/run/docker.sock'
  host_probe="$CONTROL_WORKSPACE_ROOT/.host-to-container-probe"
  container_probe="$CONTROL_WORKSPACE_ROOT/.container-to-host-probe"
  printf 'host\n' > "$host_probe"
  docker exec -i "$CONTROL_CONTAINER_NAME" sh -lc 'test "$(cat /data/rancher/automation/.host-to-container-probe)" = host; printf "container\n" >/data/rancher/automation/.container-to-host-probe'
  [[ "$(cat "$container_probe")" == container ]]
  rm -f "$host_probe" "$container_probe"
  echo "CONTROL_CONTAINER_READY: $CONTROL_CONTAINER_NAME"
}

case "$phase" in
  install-docker) install_docker ;;
  prepare-container) prepare_container ;;
  validate-container) validate_container ;;
  *) echo "UNKNOWN_CONTROL_CONTAINER_PHASE: $phase" >&2; exit 2 ;;
esac
