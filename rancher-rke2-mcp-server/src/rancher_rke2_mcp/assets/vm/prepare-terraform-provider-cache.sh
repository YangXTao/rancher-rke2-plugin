#!/usr/bin/env bash
set -Eeuo pipefail

mode=${1:?download mode is required}
version=${2:?vSphere provider version is required}
software_root=${3:?software root is required}
run_dir=${4:?run directory is required}

[[ "$mode" == online || "$mode" == offline ]] || { echo "INVALID_PROVIDER_CACHE_MODE" >&2; exit 2; }
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9.-]+)?$ ]] || { echo "EXACT_PROVIDER_VERSION_REQUIRED" >&2; exit 2; }
[[ -f "$run_dir/.dependency.env" ]] || { echo "DEPENDENCY_ENV_MISSING" >&2; exit 2; }
set -a
source "$run_dir/.dependency.env"
set +a

# This script runs inside the control container. The inherited proxy variables are
# intentionally used only by curl below; Terraform is invoked later with them unset.
source_dir=${software_root%/}/terraform
source_name=vmware/vsphere
platform=linux_amd64
release_url="https://github.com/vmware/terraform-provider-vsphere/releases/download/v${version}"
archive_name="terraform-provider-vsphere_${version}_${platform}.zip"
sums_name="terraform-provider-vsphere_${version}_SHA256SUMS"
download_dir="$source_dir/provider-downloads/registry.terraform.io/vmware/vsphere/$version"
mirror_dir="$source_dir/providers/registry.terraform.io/vmware/vsphere/$version/$platform"
plugin_cache_dir="$source_dir/plugin-cache"
config_dir="$source_dir/provider-cache-config"
config_file="$config_dir/vmware-vsphere.tfrc"
archive="$download_dir/$archive_name"
sums="$download_dir/$sums_name"
manifest="$mirror_dir/provider-cache.manifest"

mkdir -p "$download_dir" "$mirror_dir" "$plugin_cache_dir" "$config_dir"
temporary_dir=$(mktemp -d)
trap 'rm -rf "$temporary_dir"' EXIT

fetch() {
  local url=$1 destination=$2
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --output "$temporary_dir/$(basename "$destination")" "$url"
  install -m 0644 "$temporary_dir/$(basename "$destination")" "$destination"
}

archive_sha() {
  sha256sum "$archive" | awk '{print $1}'
}

verify_archive() {
  local expected actual
  expected=$(awk -v name="$archive_name" '{gsub("\\r", ""); if ($2 == name || $2 == "*" name) {print $1; exit}}' "$sums")
  actual=$(archive_sha)
  [[ -n "$expected" && "$expected" == "$actual" ]]
}

if [[ ! -s "$archive" || ! -s "$sums" ]]; then
  if [[ "$mode" == offline ]]; then
    [[ -s "$archive" ]] || echo "OFFLINE_PROVIDER_FILE_MISSING: $archive" >&2
    [[ -s "$sums" ]] || echo "OFFLINE_PROVIDER_FILE_MISSING: $sums" >&2
    exit 10
  fi
  [[ -s "$archive" ]] || fetch "$release_url/$archive_name" "$archive"
  [[ -s "$sums" ]] || fetch "$release_url/$sums_name" "$sums"
fi

if ! verify_archive; then
  [[ "$mode" == online ]] || { echo "PROVIDER_CHECKSUM_MISMATCH: $archive" >&2; exit 11; }
  fetch "$release_url/$archive_name" "$archive"
  fetch "$release_url/$sums_name" "$sums"
  verify_archive || { echo "PROVIDER_CHECKSUM_MISMATCH_AFTER_REDOWNLOAD" >&2; exit 11; }
fi

expected_archive_sha=$(archive_sha)
provider_binary=$(find "$mirror_dir" -maxdepth 1 -type f -name "terraform-provider-vsphere_v${version}*" -print -quit)
cache_hit=false
if [[ -n "$provider_binary" && -x "$provider_binary" && -s "$manifest" ]]; then
  recorded_sha=$(awk '$1 == "archive_sha256:" {print $2; exit}' "$manifest")
  [[ "$recorded_sha" == "$expected_archive_sha" ]] && cache_hit=true
fi

if [[ "$cache_hit" != true ]]; then
  unzip -oq "$archive" -d "$temporary_dir/provider"
  extracted=$(find "$temporary_dir/provider" -maxdepth 1 -type f -name 'terraform-provider-vsphere_v*' -print -quit)
  [[ -n "$extracted" ]] || { echo "PROVIDER_BINARY_NOT_FOUND_IN_ARCHIVE" >&2; exit 12; }
  provider_binary="$mirror_dir/$(basename "$extracted")"
  install -m 0755 "$extracted" "$provider_binary"
  {
    printf 'source: %s\n' "$source_name"
    printf 'version: %s\n' "$version"
    printf 'platform: %s\n' "$platform"
    printf 'archive: %s\n' "$archive"
    printf 'archive_sha256: %s\n' "$expected_archive_sha"
    printf 'provider_binary: %s\n' "$provider_binary"
  } > "$manifest"
fi

mkdir -p "$HOME/.terraform.d/plugins/registry.terraform.io/vmware/vsphere/$version/$platform"
ln -sfn "$provider_binary" "$HOME/.terraform.d/plugins/registry.terraform.io/vmware/vsphere/$version/$platform/$(basename "$provider_binary")"

cat > "$config_file" <<EOF
provider_installation {
  filesystem_mirror {
    path    = "$source_dir/providers"
    include = ["registry.terraform.io/vmware/vsphere"]
  }
  direct {
    exclude = ["registry.terraform.io/vmware/vsphere"]
  }
}
plugin_cache_dir = "$plugin_cache_dir"
EOF

if [[ "$cache_hit" == true ]]; then
  echo "TERRAFORM_PROVIDER_CACHE_HIT source=$source_name version=$version"
else
  echo "TERRAFORM_PROVIDER_CACHE_READY source=$source_name version=$version"
fi
echo "TF_CLI_CONFIG_FILE=$config_file"
