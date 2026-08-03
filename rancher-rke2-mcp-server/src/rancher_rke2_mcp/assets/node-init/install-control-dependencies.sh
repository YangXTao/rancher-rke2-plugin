#!/usr/bin/env bash
set -Eeuo pipefail

COMPONENTS=""
PROXY_URL=""
TERRAFORM_VERSION=""
# Paramiko connection support required by the password-based inventories is
# available through Ansible Core 2.20.x; 2.21 removes that plugin.
ANSIBLE_CORE_VERSION="2.20.7"
HELM_VERSION=""
KUBECTL_VERSION=""
SOFTWARE_ROOT=/software
MODE=online

usage() {
  printf '%s\n' \
    'Install missing automation-control-host dependencies.' \
    '' \
    'Usage:' \
    '  install-control-dependencies.sh --components <list> [options]' \
    '' \
    'Components: vm,node-init,local-rke2,rancher,downstream' \
    'Options:' \
    '  --mode online|offline' \
    '  --proxy-url URL' \
    '  --terraform-version VERSION' \
    '  --ansible-core-version VERSION' \
    '  --helm-version VERSION' \
    '  --kubectl-version VERSION' \
    '  --software-root DIRECTORY'
}

while (($#)); do
  case "$1" in
    --components) COMPONENTS="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --proxy-url) PROXY_URL="$2"; shift 2 ;;
    --terraform-version) TERRAFORM_VERSION="$2"; shift 2 ;;
    --ansible-core-version) ANSIBLE_CORE_VERSION="$2"; shift 2 ;;
    --helm-version) HELM_VERSION="$2"; shift 2 ;;
    --kubectl-version) KUBECTL_VERSION="$2"; shift 2 ;;
    --software-root) SOFTWARE_ROOT="${2%/}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$COMPONENTS" ]] || { echo "--components is required" >&2; exit 2; }
[[ "$MODE" == online || "$MODE" == offline ]] || { echo "--mode must be online or offline" >&2; exit 2; }

if ((EUID == 0)); then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  echo "Root or sudo is required to install control-host dependencies." >&2
  exit 1
fi

run_root() {
  "${SUDO[@]}" "$@"
}

has_component() {
  case ",$COMPONENTS," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) echo "Unsupported control-host architecture: $ARCH_RAW" >&2; exit 1 ;;
esac

if command -v apt-get >/dev/null 2>&1; then
  PACKAGE_MANAGER=apt
elif command -v dnf >/dev/null 2>&1; then
  PACKAGE_MANAGER=dnf
elif command -v yum >/dev/null 2>&1; then
  PACKAGE_MANAGER=yum
else
  PACKAGE_MANAGER=unavailable
fi

protect_readonly_localtime() {
  [[ "$PACKAGE_MANAGER" == apt ]] || return 0
  command -v apt-mark >/dev/null 2>&1 || return 0
  command -v dpkg-query >/dev/null 2>&1 || return 0
  dpkg-query -W -f='${Status}' tzdata 2>/dev/null |
    grep -F 'install ok installed' >/dev/null || return 0
  # tzdata's post-install script replaces /etc/localtime.  That path is a
  # deliberate read-only bind mount from the control host, so never upgrade
  # tzdata while installing unrelated tools inside the control container.
  run_root apt-mark hold tzdata >/dev/null
}

protect_readonly_localtime

apt_install_base_packages() {
  local proxy_opts=("$@")
  local packages=(
    ca-certificates curl wget unzip tar gzip jq openssl openssh-client git
    iputils-ping python3 python3-pip python3-venv
  )
  if run_root env DEBIAN_FRONTEND=noninteractive apt-get "${proxy_opts[@]}" \
    install -y --no-upgrade "${packages[@]}"; then
    protect_readonly_localtime
    return 0
  fi
  if grep -Fq ' /etc/localtime ' /proc/self/mountinfo &&
     run_root dpkg --audit 2>&1 | grep -F 'tzdata' >/dev/null &&
     [[ -f /var/lib/dpkg/info/tzdata.postinst ]]; then
    local postinst=/var/lib/dpkg/info/tzdata.postinst
    local backup="${postinst}.rancher-control-backup"
    run_root cp -a "$postinst" "$backup"
    printf '#!/bin/sh\nexit 0\n' | run_root tee "$postinst" >/dev/null
    run_root chmod 0755 "$postinst"
    if ! run_root env DEBIAN_FRONTEND=noninteractive dpkg --configure -a; then
      run_root mv -f "$backup" "$postinst"
      return 1
    fi
    run_root mv -f "$backup" "$postinst"
    protect_readonly_localtime
    run_root env DEBIAN_FRONTEND=noninteractive apt-get "${proxy_opts[@]}" \
      install -f -y --no-upgrade
    return 0
  fi
  return 1
}

package_install() {
  [[ "$MODE" == online ]] || { echo 'OFFLINE_CONTROL_DEPENDENCY_MISSING: base OS package' >&2; exit 10; }
  [[ "$PACKAGE_MANAGER" != unavailable ]] || { echo 'Supported package manager not found: apt-get, dnf, or yum is required.' >&2; exit 1; }
  if [[ "$PACKAGE_MANAGER" == apt ]]; then
    local proxy_opts=()
    if [[ -n "$PROXY_URL" ]]; then
      proxy_opts+=("-o" "Acquire::http::Proxy=$PROXY_URL")
      proxy_opts+=("-o" "Acquire::https::Proxy=$PROXY_URL")
    fi
    run_root apt-get "${proxy_opts[@]}" update
    # The control container bind-mounts /etc/localtime read-only.  Avoid a base
    # image upgrade (notably tzdata) while installing missing control tools, or
    # dpkg can attempt to replace that mount and leave the package database broken.
    apt_install_base_packages "${proxy_opts[@]}"
  else
    local proxy_opts=()
    [[ -n "$PROXY_URL" ]] && proxy_opts+=("--setopt=proxy=$PROXY_URL")
    run_root "$PACKAGE_MANAGER" -y "${proxy_opts[@]}" makecache
    run_root "$PACKAGE_MANAGER" -y "${proxy_opts[@]}" install \
      ca-certificates curl wget unzip tar gzip jq openssl openssh-clients git \
      iputils python3 python3-pip
  fi
}

for command_name in curl wget unzip tar jq openssl ssh python3 ping; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    if [[ "$MODE" == offline ]]; then
      echo "OFFLINE_CONTROL_DEPENDENCY_MISSING: $command_name" >&2
      exit 10
    fi
    package_install
    break
  fi
done

TMP_DIR="$(mktemp -d /tmp/rancher-control-deps.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
run_root mkdir -p "$SOFTWARE_ROOT/control-dependencies"

curl_fetch() {
  local url="$1"
  local output="$2"
  local args=(--fail --location --silent --show-error --retry 3 --connect-timeout 20)
  [[ -n "$PROXY_URL" ]] && args+=(--proxy "$PROXY_URL")
  curl "${args[@]}" "$url" --output "$output"
}

install_terraform() {
  local version="${TERRAFORM_VERSION#v}"
  [[ -n "$version" ]] || { echo "Terraform version is required." >&2; exit 1; }
  if command -v terraform >/dev/null 2>&1 && terraform version | grep -F "v$version" >/dev/null; then
    return
  fi
  local archive_name="terraform_${version}_linux_${ARCH}.zip"
  local stored_archive="$SOFTWARE_ROOT/terraform/$archive_name"
  local archive="$TMP_DIR/terraform.zip"
  run_root mkdir -p "$SOFTWARE_ROOT/terraform"
  if [[ "$MODE" == online ]]; then
    curl_fetch "https://releases.hashicorp.com/terraform/$version/$archive_name" "$archive"
    run_root install -m 0644 "$archive" "$stored_archive"
  else
    [[ -s "$stored_archive" ]] || { echo "OFFLINE_FILE_MISSING: $stored_archive" >&2; exit 10; }
    archive="$stored_archive"
  fi
  unzip -oq "$archive" -d "$TMP_DIR/terraform"
  run_root install -m 0755 "$TMP_DIR/terraform/terraform" /usr/local/bin/terraform
  terraform version | grep -F "v$version" >/dev/null
}

install_ansible() {
  local venv=/opt/rancher-automation/ansible-venv
  local install_required=true
  if [[ -x "$venv/bin/ansible-playbook" ]] && "$venv/bin/python" -c 'import paramiko' >/dev/null 2>&1; then
    if [[ -z "$ANSIBLE_CORE_VERSION" ]] || "$venv/bin/ansible-playbook" --version | grep -F "core $ANSIBLE_CORE_VERSION" >/dev/null; then
      install_required=false
    fi
  fi
  if [[ ! -x "$venv/bin/ansible-playbook" ]]; then
    run_root mkdir -p "$(dirname "$venv")"
    if ! run_root python3 -m venv "$venv"; then
      [[ "$MODE" == online ]] || { echo 'OFFLINE_CONTROL_DEPENDENCY_MISSING: python3 venv' >&2; exit 10; }
      package_install
      run_root rm -rf "$venv"
      run_root python3 -m venv "$venv"
    fi
  fi
  if [[ "$install_required" == true ]]; then
    local requirement=ansible-core
    [[ -n "$ANSIBLE_CORE_VERSION" ]] && requirement="ansible-core==$ANSIBLE_CORE_VERSION"
    local pip_args=(--disable-pip-version-check)
    if [[ "$MODE" == online ]]; then
      [[ -n "$PROXY_URL" ]] && pip_args+=(--proxy "$PROXY_URL")
    else
      local wheel_dir="$SOFTWARE_ROOT/control-dependencies/wheels"
      [[ -d "$wheel_dir" ]] || { echo "OFFLINE_FILE_MISSING: $wheel_dir" >&2; exit 10; }
      pip_args+=(--no-index --find-links "$wheel_dir")
    fi
    run_root mkdir -p "$SOFTWARE_ROOT/pip-cache"
    run_root env PIP_CACHE_DIR="$SOFTWARE_ROOT/pip-cache" "$venv/bin/pip" install "${pip_args[@]}" --upgrade "$requirement" paramiko
  fi
  for executable in ansible ansible-playbook ansible-config ansible-inventory ansible-galaxy; do
    run_root ln -sfn "$venv/bin/$executable" "/usr/local/bin/$executable"
  done
  ansible-playbook --version >/dev/null
}

install_helm() {
  if command -v helm >/dev/null 2>&1; then
    [[ -z "$HELM_VERSION" ]] && return
    helm version --short | grep -F "$HELM_VERSION" >/dev/null && return
  fi
  run_root mkdir -p "$SOFTWARE_ROOT/helm"
  if [[ -n "$HELM_VERSION" ]]; then
    local version="$HELM_VERSION"
    [[ "$version" == v* ]] || version="v$version"
    local archive_name="helm-${version}-linux-${ARCH}.tar.gz"
    local stored_archive="$SOFTWARE_ROOT/helm/$archive_name"
    local archive="$TMP_DIR/$archive_name"
    if [[ "$MODE" == online ]]; then
      curl_fetch "https://get.helm.sh/$archive_name" "$archive"
      run_root install -m 0644 "$archive" "$stored_archive"
    else
      [[ -s "$stored_archive" ]] || { echo "OFFLINE_FILE_MISSING: $stored_archive" >&2; exit 10; }
      archive="$stored_archive"
    fi
    tar -xzf "$archive" -C "$TMP_DIR"
    run_root install -m 0755 "$TMP_DIR/linux-${ARCH}/helm" /usr/local/bin/helm
  else
    [[ "$MODE" == online ]] || { echo 'OFFLINE_CONTROL_DEPENDENCY_MISSING: helm version must be pinned' >&2; exit 10; }
    local installer="$TMP_DIR/get-helm"
    curl_fetch https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 "$installer"
    run_root install -m 0755 "$installer" "$SOFTWARE_ROOT/helm/get-helm"
    local env_args=(HELM_INSTALL_DIR=/usr/local/bin)
    [[ -n "$PROXY_URL" ]] && env_args+=("http_proxy=$PROXY_URL" "https_proxy=$PROXY_URL")
    run_root env "${env_args[@]}" bash "$installer"
  fi
  helm version --short >/dev/null
}

install_kubectl() {
  local version="$KUBECTL_VERSION"
  if [[ -z "$version" ]]; then
    [[ "$MODE" == online ]] || { echo 'OFFLINE_CONTROL_DEPENDENCY_MISSING: kubectl version must be pinned' >&2; exit 10; }
    curl_fetch https://dl.k8s.io/release/stable.txt "$TMP_DIR/kubectl-version"
    version="$(tr -d '\r\n' < "$TMP_DIR/kubectl-version")"
  fi
  [[ "$version" == v* ]] || version="v$version"
  if command -v kubectl >/dev/null 2>&1 && kubectl version --client=true --output=yaml 2>/dev/null | grep -F "gitVersion: $version" >/dev/null; then
    return
  fi
  run_root mkdir -p "$SOFTWARE_ROOT/kubectl"
  local stored_kubectl="$SOFTWARE_ROOT/kubectl/kubectl-$version-linux-$ARCH"
  local kubectl_source="$TMP_DIR/kubectl"
  if [[ "$MODE" == online ]]; then
    curl_fetch "https://dl.k8s.io/release/$version/bin/linux/$ARCH/kubectl" "$kubectl_source"
    run_root install -m 0755 "$kubectl_source" "$stored_kubectl"
  else
    [[ -s "$stored_kubectl" ]] || { echo "OFFLINE_FILE_MISSING: $stored_kubectl" >&2; exit 10; }
    kubectl_source="$stored_kubectl"
  fi
  run_root install -m 0755 "$kubectl_source" /usr/local/bin/kubectl
  kubectl version --client=true >/dev/null
}

if has_component vm || has_component downstream; then
  install_terraform
fi
if has_component node-init || has_component local-rke2 || has_component rancher || has_component downstream; then
  install_ansible
fi
if has_component rancher; then
  install_helm
  install_kubectl
fi

echo "CONTROL_DEPENDENCIES_READY components=$COMPONENTS package_manager=$PACKAGE_MANAGER"


