#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <online|offline> <standard|enterprise> <version> <chart-dir> <standard-repo-name> <standard-repo-url> <enterprise-base-url> [offline-chart-file]" >&2
}

[[ $# -ge 7 && $# -le 8 ]] || { usage; exit 2; }

mode=$1
edition=$2
version=$3
chart_dir=${4%/}
repo_name=$5
repo_url=$6
enterprise_base_url=${7%/}
offline_chart_file=${8:-}
chart_name="rancher-${version}.tgz"
chart_path="${chart_dir}/${chart_name}"
manifest_path="${chart_path}.manifest"

[[ "$mode" == "online" || "$mode" == "offline" ]] || { echo "INVALID_DOWNLOAD_MODE: $mode" >&2; exit 2; }
[[ "$edition" == "standard" || "$edition" == "enterprise" ]] || { echo "INVALID_RANCHER_EDITION: $edition" >&2; exit 2; }
if [[ "$edition" == "enterprise" ]]; then
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-ent$ ]] || { echo "INVALID_ENTERPRISE_VERSION: $version" >&2; exit 2; }
else
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "INVALID_STANDARD_VERSION: $version" >&2; exit 2; }
fi

mkdir -p "$chart_dir"
if [[ -s "$chart_path" && -s "$manifest_path" ]]; then
  recorded_version=$(awk '$1 == "version:" {print $2; exit}' "$manifest_path")
  recorded_sha256=$(awk '$1 == "sha256:" {print $2; exit}' "$manifest_path")
  actual_sha256=$(sha256sum "$chart_path" | awk '{print $1}')
  chart_version=$(helm show chart "$chart_path" | awk '$1 == "version:" {print $2; exit}')
  if [[ "$recorded_version" == "$version" && "$recorded_sha256" == "$actual_sha256" && "$chart_version" == "$version" ]]; then
    echo "$chart_path"
    exit 0
  fi
  echo "RANCHER_CHART_CHECKPOINT_INVALID: existing chart or manifest does not match the approved version" >&2
  exit 4
fi
tmp_dir=$(mktemp -d "${chart_dir}/.prepare-rancher-chart.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

if [[ "$mode" == "offline" ]]; then
  [[ -n "$offline_chart_file" && -f "$offline_chart_file" ]] || {
    echo "OFFLINE_PROFILE_NOT_READY: Stage 40 requires an existing offline chart file" >&2
    exit 3
  }
  source_description="file:${offline_chart_file}"
  install -m 0644 "$offline_chart_file" "${tmp_dir}/${chart_name}"
elif [[ "$edition" == "enterprise" ]]; then
  major_minor=$(printf '%s' "$version" | sed -E 's/^([0-9]+\.[0-9]+)\..*/\1/')
  chart_url="${enterprise_base_url}/${major_minor}-prime/latest/${chart_name}"
  source_description=$chart_url
  downloaded="${chart_path}.part"
  wget_args=(-c "$chart_url" -O "$downloaded")
  if [[ -n "${DOWNLOAD_PROXY_URL:-}" ]]; then
    wget_args=(--execute use_proxy=yes --execute "http_proxy=${DOWNLOAD_PROXY_URL}" --execute "https_proxy=${DOWNLOAD_PROXY_URL}" "${wget_args[@]}")
  fi
  wget "${wget_args[@]}"
else
  source_description="${repo_url}/${repo_name}/rancher:${version}"
  helm repo add "$repo_name" "$repo_url" --force-update
  helm repo update "$repo_name"
  helm pull "${repo_name}/rancher" --version "$version" --destination "$tmp_dir"
fi

downloaded=${downloaded:-"${tmp_dir}/${chart_name}"}
[[ -s "$downloaded" ]] || { echo "RANCHER_CHART_EMPTY: $downloaded" >&2; exit 4; }
chart_version=$(helm show chart "$downloaded" | awk '$1 == "version:" {print $2; exit}')
[[ "$chart_version" == "$version" ]] || {
  echo "RANCHER_CHART_VERSION_MISMATCH: requested=$version actual=${chart_version:-unknown}" >&2
  exit 4
}
chart_sha256=$(sha256sum "$downloaded" | awk '{print $1}')
mv -f "$downloaded" "$chart_path"
{
  printf 'edition: %s\n' "$edition"
  printf 'version: %s\n' "$version"
  printf 'source: %s\n' "$source_description"
  printf 'sha256: %s\n' "$chart_sha256"
} > "$manifest_path"
chmod 0644 "$chart_path" "$manifest_path"
echo "$chart_path"
