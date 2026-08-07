#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'Usage: prepare-rancher-enterprise-artifacts.sh <online|offline> <rancher-version-ent> <rke2-version> <helm-version> <cfssl-version> <docker-version> <software-root> <enterprise-chart-base-url> [rancher-image-archive-name]' \
    '' \
    'Online mode downloads required files into /software. Offline mode only verifies and installs pre-positioned files.'
}

[[ $# -ge 8 && $# -le 9 ]] || { usage >&2; exit 2; }
mode=$1
rancher_version=$2
rke2_version=$3
helm_version=${4#v}
cfssl_version=${5#v}
docker_version=${6#v}
software_root=${7%/}
enterprise_base_url=${8%/}
image_archive_name=${9:-}

[[ "$mode" == online || "$mode" == offline ]] || { usage >&2; exit 2; }
[[ "$rancher_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-ent$ ]] || { echo 'INVALID_ENTERPRISE_RANCHER_VERSION' >&2; exit 2; }
[[ "$rke2_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+rke2r[0-9]+$ ]] || { echo 'INVALID_RKE2_VERSION' >&2; exit 2; }

arch_raw=$(uname -m)
case "$arch_raw" in
  x86_64|amd64) arch=amd64 ;;
  *) echo "UNSUPPORTED_ARCHITECTURE: $arch_raw" >&2; exit 2 ;;
esac

cfssl_dir="$software_root/cfssl"
helm_dir="$software_root/helm"
docker_dir="$software_root/docker"
kubectl_dir="$software_root/kubectl"
rancher_dir="$software_root/rancher"
mkdir -p "$cfssl_dir" "$helm_dir" "$docker_dir" "$kubectl_dir" "$rancher_dir"

helm_archive="helm-v${helm_version}-linux-${arch}.tar.gz"
docker_archive="docker-${docker_version}.tgz"
kubectl_version=${rke2_version%%+*}
kubectl_file="kubectl-${kubectl_version}-linux-${arch}"
chart_name="rancher-${rancher_version}.tgz"
major_minor=$(printf '%s' "$rancher_version" | sed -E 's/^([0-9]+\.[0-9]+)\..*/\1/')

declare -A required_files=(
  [cfssl]="$cfssl_dir/cfssl"
  [cfssljson]="$cfssl_dir/cfssljson"
  [cfssl-certinfo]="$cfssl_dir/cfssl-certinfo"
  [helm]="$helm_dir/$helm_archive"
  [docker]="$docker_dir/$docker_archive"
  [kubectl]="$kubectl_dir/$kubectl_file"
  [rancher-chart]="$rancher_dir/$chart_name"
)
[[ -z "$image_archive_name" ]] || required_files[rancher-images]="$rancher_dir/$image_archive_name"

curl_args=(--fail --location --silent --show-error --retry 3 --retry-delay 2)
[[ -n "${DOWNLOAD_PROXY_URL:-}" ]] && curl_args+=(--proxy "$DOWNLOAD_PROXY_URL")

download_file() {
  local url=$1
  local target=$2
  local temporary="${target}.part"
  # A successful earlier run may already have placed this exact artifact under the
  # persistent /software mount.  Reuse it; the checks below still validate every
  # required binary, chart version, archive layout, and regenerated manifest.
  [[ -s "$target" ]] && return 0
  curl "${curl_args[@]}" "$url" --output "$temporary"
  mv -f "$temporary" "$target"
}

if [[ "$mode" == online ]]; then
  download_file "https://github.com/cloudflare/cfssl/releases/download/v${cfssl_version}/cfssl_${cfssl_version}_linux_${arch}" "${required_files[cfssl]}"
  download_file "https://github.com/cloudflare/cfssl/releases/download/v${cfssl_version}/cfssljson_${cfssl_version}_linux_${arch}" "${required_files[cfssljson]}"
  download_file "https://github.com/cloudflare/cfssl/releases/download/v${cfssl_version}/cfssl-certinfo_${cfssl_version}_linux_${arch}" "${required_files[cfssl-certinfo]}"
  download_file "https://get.helm.sh/$helm_archive" "${required_files[helm]}"
  download_file "https://rancher.blob.core.chinacloudapi.cn/docker/$docker_archive" "${required_files[docker]}"
  download_file "https://dl.k8s.io/release/$kubectl_version/bin/linux/$arch/kubectl" "${required_files[kubectl]}"
  download_file "$enterprise_base_url/${major_minor}-prime/latest/$chart_name" "${required_files[rancher-chart]}"
  if [[ -n "$image_archive_name" ]]; then
    [[ -n "${RANCHER_IMAGE_ARCHIVE_URL:-}" ]] || {
      echo 'RANCHER_IMAGE_ARCHIVE_URL_REQUIRED: enterprise image archives require an authorized download URL' >&2
      exit 3
    }
    download_file "$RANCHER_IMAGE_ARCHIVE_URL" "${required_files[rancher-images]}"
  fi
else
  for key in "${!required_files[@]}"; do
    [[ -s "${required_files[$key]}" ]] || {
      echo "OFFLINE_FILE_MISSING: ${required_files[$key]}" >&2
      exit 4
    }
  done
fi

chmod 0755 "${required_files[cfssl]}" "${required_files[cfssljson]}" \
  "${required_files[cfssl-certinfo]}" "${required_files[kubectl]}"
install -m 0755 "${required_files[cfssl]}" /usr/local/bin/cfssl
install -m 0755 "${required_files[cfssljson]}" /usr/local/bin/cfssljson
install -m 0755 "${required_files[cfssl-certinfo]}" /usr/local/bin/cfssl-certinfo
install -m 0755 "${required_files[kubectl]}" /usr/local/bin/kubectl

temporary_dir=$(mktemp -d /tmp/rancher-enterprise-artifacts.XXXXXX)
trap 'rm -rf "$temporary_dir"' EXIT
tar -xzf "${required_files[helm]}" -C "$temporary_dir"
install -m 0755 "$temporary_dir/linux-${arch}/helm" /usr/local/bin/helm

helm show chart "${required_files[rancher-chart]}" | awk -v expected="$rancher_version" \
  '$1 == "version:" {if ($2 != expected) exit 1; found=1} END {if (!found) exit 1}'
tar -tzf "${required_files[docker]}" | grep -Fx 'docker/dockerd' >/dev/null
cfssl version >/dev/null
helm version --short >/dev/null
kubectl version --client=true >/dev/null

chart_sha=$(sha256sum "${required_files[rancher-chart]}" | awk '{print $1}')
{
  printf 'edition: enterprise\n'
  printf 'version: %s\n' "$rancher_version"
  printf 'source: %s\n' "$mode"
  printf 'sha256: %s\n' "$chart_sha"
} > "${required_files[rancher-chart]}.manifest"

manifest="$rancher_dir/enterprise-artifacts.manifest"
{
  printf 'mode: %s\n' "$mode"
  printf 'rancher_version: %s\n' "$rancher_version"
  printf 'rke2_version: %s\n' "$rke2_version"
  printf 'helm_version: %s\n' "$helm_version"
  printf 'cfssl_version: %s\n' "$cfssl_version"
  printf 'docker_version: %s\n' "$docker_version"
  printf 'files:\n'
  for key in $(printf '%s\n' "${!required_files[@]}" | sort); do
    sha=$(sha256sum "${required_files[$key]}" | awk '{print $1}')
    printf '  - {purpose: "%s", path: "%s", sha256: "%s"}\n' "$key" "${required_files[$key]}" "$sha"
  done
} > "$manifest"
chmod 0644 "$manifest"
printf '%s\n' "$manifest"
