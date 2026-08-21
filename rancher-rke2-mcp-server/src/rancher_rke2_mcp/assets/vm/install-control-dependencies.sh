#!/usr/bin/env bash
set -Eeuo pipefail

run_dir=${1:?run directory is required}
mode=${2:?download mode is required}
terraform_version=${3:?terraform version is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_DOWNLOAD_MODE" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
set -a
source "$run_dir/.dependency.env"
set +a
export DEBIAN_FRONTEND=noninteractive

# apt-get does not honor http_proxy/HTTP_PROXY environment variables, so the
# inherited dependency proxy (from .dependency.env) must be passed explicitly.
proxy_apt_args=()
if [[ -n "${HTTP_PROXY:-}" ]]; then
  proxy_apt_args+=("-o" "Acquire::http::Proxy=$HTTP_PROXY")
  proxy_apt_args+=("-o" "Acquire::https::Proxy=$HTTP_PROXY")
fi

protect_readonly_localtime() {
  command -v apt-mark >/dev/null 2>&1 || return 0
  command -v dpkg-query >/dev/null 2>&1 || return 0
  dpkg-query -W -f='${Status}' tzdata 2>/dev/null |
    grep -F 'install ok installed' >/dev/null || return 0
  apt-mark hold tzdata >/dev/null
}

install_apt_dependencies() {
  apt-get "${proxy_apt_args[@]}" update
  protect_readonly_localtime
  if apt-get "${proxy_apt_args[@]}" install -y --no-upgrade ca-certificates curl unzip python3 iputils-ping; then
    protect_readonly_localtime
    return 0
  fi

  # /etc/localtime is a deliberate read-only host bind mount.  A pristine Ubuntu
  # image can first install tzdata while installing Python, and tzdata's vendor
  # post-install script then fails trying to replace that mount.  Suppress only
  # that one failed action, finish dpkg, immediately restore the vendor script,
  # and hold tzdata so future unrelated package installs cannot repeat it.
  if grep -Fq ' /etc/localtime ' /proc/self/mountinfo &&
     dpkg --audit 2>&1 | grep -F 'tzdata' >/dev/null &&
     [[ -f /var/lib/dpkg/info/tzdata.postinst ]]; then
    local postinst=/var/lib/dpkg/info/tzdata.postinst
    local backup="${postinst}.rancher-rke2-backup"
    cp -a "$postinst" "$backup"
    printf '#!/bin/sh\nexit 0\n' > "$postinst"
    chmod 0755 "$postinst"
    if ! dpkg --configure -a; then
      mv -f "$backup" "$postinst"
      return 1
    fi
    mv -f "$backup" "$postinst"
    protect_readonly_localtime
    apt-get "${proxy_apt_args[@]}" install -f -y --no-upgrade
    return 0
  fi
  return 1
}

if ! command -v apt-get >/dev/null 2>&1; then
  echo "UNSUPPORTED_CONTROL_CONTAINER" >&2
  exit 6
fi
for required_command in curl unzip python3 ping; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    install_apt_dependencies
    break
  fi
done

if ! command -v terraform >/dev/null 2>&1 || ! terraform version | grep -Fq "v${terraform_version}"; then
  mkdir -p /software/terraform
  archive="/software/terraform/terraform_${terraform_version}_linux_amd64.zip"
  if [[ ! -s "$archive" ]]; then
    [[ "$mode" == online ]] || { echo "OFFLINE_FILE_MISSING:$archive" >&2; exit 10; }
    curl_args=(--fail --location --retry 3)
    [[ -n "${HTTP_PROXY:-}" ]] && curl_args+=(--proxy "$HTTP_PROXY")
    curl "${curl_args[@]}" -o "$archive" \
      "https://releases.hashicorp.com/terraform/${terraform_version}/terraform_${terraform_version}_linux_amd64.zip"
  fi
  temporary_dir=$(mktemp -d)
  trap 'rm -rf "$temporary_dir"' EXIT
  unzip -oq "$archive" -d "$temporary_dir"
  install -m 0755 "$temporary_dir/terraform" /usr/local/bin/terraform
fi
terraform version
