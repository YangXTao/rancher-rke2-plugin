#!/usr/bin/env bash
set -Eeuo pipefail

MODE=online
SOURCE=""
VERSION=""
RELEASE_BASE_URL=""
SOFTWARE_ROOT=/software
PROXY_URL=""

usage() {
  printf '%s\n' \
    'Prepare one exact Terraform provider in the persistent control-host mirror.' \
    '' \
    'Usage:' \
    '  prepare-terraform-provider-cache.sh --mode online|offline --source NAMESPACE/NAME' \
    '    --version X.Y.Z --release-base-url URL [--software-root /software]' \
    '    [--proxy-url URL]'
}

while (($#)); do
  case "$1" in
    --mode) MODE=$2; shift 2 ;;
    --source) SOURCE=$2; shift 2 ;;
    --version) VERSION=${2#v}; shift 2 ;;
    --release-base-url) RELEASE_BASE_URL=${2%/}; shift 2 ;;
    --software-root) SOFTWARE_ROOT=${2%/}; shift 2 ;;
    --proxy-url) PROXY_URL=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$MODE" == online || "$MODE" == offline ]] || { echo 'INVALID_PROVIDER_CACHE_MODE' >&2; exit 2; }
[[ "$SOURCE" =~ ^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$ ]] || { echo 'INVALID_PROVIDER_SOURCE' >&2; exit 2; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9.-]+)?$ ]] || { echo 'EXACT_PROVIDER_VERSION_REQUIRED' >&2; exit 2; }
[[ -n "$RELEASE_BASE_URL" ]] || { echo 'PROVIDER_RELEASE_BASE_URL_REQUIRED' >&2; exit 2; }

case "$(uname -m)" in
  x86_64|amd64) PLATFORM=linux_amd64 ;;
  *) echo 'UNSUPPORTED_PROVIDER_PLATFORM: only linux_amd64 is supported' >&2; exit 2 ;;
esac

if ((EUID == 0)); then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  echo 'Root or sudo is required to maintain /software Terraform providers.' >&2
  exit 1
fi

run_root() {
  "${SUDO[@]}" "$@"
}

namespace=${SOURCE%%/*}
provider_name=${SOURCE##*/}
archive_name="terraform-provider-${provider_name}_${VERSION}_${PLATFORM}.zip"
sums_name="terraform-provider-${provider_name}_${VERSION}_SHA256SUMS"
download_dir="$SOFTWARE_ROOT/terraform/provider-downloads/registry.terraform.io/$namespace/$provider_name/$VERSION"
mirror_root="$SOFTWARE_ROOT/terraform/providers"
mirror_dir="$mirror_root/registry.terraform.io/$namespace/$provider_name/$VERSION/$PLATFORM"
plugin_cache_dir="$SOFTWARE_ROOT/terraform/plugin-cache"
config_dir="$SOFTWARE_ROOT/terraform/provider-cache-config"
config_file="$config_dir/${namespace}-${provider_name}.tfrc"
archive="$download_dir/$archive_name"
sums="$download_dir/$sums_name"
release_url="$RELEASE_BASE_URL/v$VERSION"

tmp_dir=$(mktemp -d /tmp/terraform-provider-cache.XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT
run_root mkdir -p "$download_dir" "$mirror_dir" "$plugin_cache_dir" "$config_dir"

curl_fetch() {
  local url=$1
  local destination=$2
  local temporary="$tmp_dir/$(basename "$destination")"
  local args=(--fail --location --silent --show-error --retry 3 --retry-delay 2)
  [[ -n "$PROXY_URL" ]] && args+=(--proxy "$PROXY_URL")
  curl "${args[@]}" "$url" --output "$temporary"
  run_root install -m 0644 "$temporary" "$destination"
}

verify_archive() {
  local expected actual
  expected=$(awk -v name="$archive_name" '{gsub("\\r", ""); if ($2 == name || $2 == "*" name) {print $1; exit}}' "$sums")
  [[ -n "$expected" ]] || return 1
  actual=$(sha256sum "$archive" | awk '{print $1}')
  [[ "$actual" == "$expected" ]]
}

if [[ ! -s "$archive" || ! -s "$sums" ]]; then
  if [[ "$MODE" == offline ]]; then
    [[ -s "$archive" ]] || echo "OFFLINE_PROVIDER_FILE_MISSING: $archive" >&2
    [[ -s "$sums" ]] || echo "OFFLINE_PROVIDER_FILE_MISSING: $sums" >&2
    exit 10
  fi
  [[ -s "$archive" ]] || curl_fetch "$release_url/$archive_name" "$archive"
  [[ -s "$sums" ]] || curl_fetch "$release_url/$sums_name" "$sums"
fi

if ! verify_archive; then
  if [[ "$MODE" == offline ]]; then
    echo "PROVIDER_CHECKSUM_MISMATCH: $archive_name" >&2
    exit 11
  fi
  curl_fetch "$release_url/$archive_name" "$archive"
  curl_fetch "$release_url/$sums_name" "$sums"
  verify_archive || { echo "PROVIDER_CHECKSUM_MISMATCH_AFTER_REDOWNLOAD: $archive_name" >&2; exit 11; }
fi

archive_sha=$(sha256sum "$archive" | awk '{print $1}')
manifest="$mirror_dir/provider-cache.manifest"
provider_binary=$(find "$mirror_dir" -maxdepth 1 -type f -name "terraform-provider-${provider_name}_v${VERSION}*" -print -quit)
cache_hit=false
if [[ -n "$provider_binary" && -x "$provider_binary" && -s "$manifest" ]]; then
  recorded_sha=$(awk '$1 == "archive_sha256:" {print $2; exit}' "$manifest")
  recorded_binary_sha=$(awk '$1 == "provider_sha256:" {print $2; exit}' "$manifest")
  actual_binary_sha=$(sha256sum "$provider_binary" | awk '{print $1}')
  [[ "$recorded_sha" == "$archive_sha" && "$recorded_binary_sha" == "$actual_binary_sha" ]] && cache_hit=true
fi

if [[ "$cache_hit" != true ]]; then
  unzip -oq "$archive" -d "$tmp_dir/provider"
  extracted=$(find "$tmp_dir/provider" -maxdepth 1 -type f -name "terraform-provider-${provider_name}*" -print -quit)
  [[ -n "$extracted" ]] || { echo "PROVIDER_BINARY_NOT_FOUND_IN_ARCHIVE: $archive_name" >&2; exit 12; }
  target_name=$(basename "$extracted")
  run_root install -m 0755 "$extracted" "$mirror_dir/$target_name"
  provider_binary="$mirror_dir/$target_name"
  provider_sha=$(sha256sum "$provider_binary" | awk '{print $1}')
  manifest_tmp="$tmp_dir/provider-cache.manifest"
  {
    printf 'source: %s\n' "$SOURCE"
    printf 'version: %s\n' "$VERSION"
    printf 'platform: %s\n' "$PLATFORM"
    printf 'archive: %s\n' "$archive"
    printf 'archive_sha256: %s\n' "$archive_sha"
    printf 'provider_binary: %s\n' "$provider_binary"
    printf 'provider_sha256: %s\n' "$provider_sha"
  } > "$manifest_tmp"
  run_root install -m 0644 "$manifest_tmp" "$manifest"
fi

# Keep the conventional per-user plugin path available for direct/manual Terraform use.
home_mirror_dir="$HOME/.terraform.d/plugins/registry.terraform.io/$namespace/$provider_name/$VERSION/$PLATFORM"
mkdir -p "$home_mirror_dir"
ln -sfn "$provider_binary" "$home_mirror_dir/$(basename "$provider_binary")"

config_tmp="$tmp_dir/provider-cache.tfrc"
cat > "$config_tmp" <<EOF
provider_installation {
  filesystem_mirror {
    path    = "$mirror_root"
    include = ["$SOURCE"]
  }
  direct {
    exclude = ["$SOURCE"]
  }
}
plugin_cache_dir = "$plugin_cache_dir"
EOF
run_root install -m 0644 "$config_tmp" "$config_file"

if [[ "$cache_hit" == true ]]; then
  echo "TERRAFORM_PROVIDER_CACHE_HIT source=$SOURCE version=$VERSION"
else
  echo "TERRAFORM_PROVIDER_CACHE_READY source=$SOURCE version=$VERSION"
fi
echo "TF_CLI_CONFIG_FILE=$config_file"
