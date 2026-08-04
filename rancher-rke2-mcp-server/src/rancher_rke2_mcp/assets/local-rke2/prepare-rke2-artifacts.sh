#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'Usage: prepare-rke2-artifacts.sh <online|offline> <rke2-version> <amd64> <software-root> [offline-image-file ...]' \
    '' \
    'The console layout is fixed: install.sh and manifest.yaml in /software/rke2,' \
    'with the binary archive, checksum, and optional air-gap image archives in /software/rke2/images.'
}

if [[ $# -lt 4 ]]; then
  usage >&2
  exit 2
fi

mode=$1
version=$2
arch=$3
software_root=${4%/}
shift 4
offline_image_files=("$@")

[[ "$mode" == online || "$mode" == offline ]] || { usage >&2; exit 2; }
[[ "$arch" == amd64 ]] || { echo 'UNSUPPORTED_ARCHITECTURE: only amd64 is currently supported' >&2; exit 5; }
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+rke2r[0-9]+$ ]] || {
  echo 'INVALID_RKE2_VERSION: expected vX.Y.Z+rke2rN' >&2
  exit 6
}
if [[ "$mode" == online && ${#offline_image_files[@]} -gt 0 ]]; then
  echo 'ONLINE_IMAGE_ARCHIVE_FORBIDDEN: online mode pulls runtime images from the configured registry' >&2
  exit 3
fi

images_dir="$software_root/images"
installer="$software_root/install.sh"
binary="$images_dir/rke2.linux-amd64.tar.gz"
checksum="$images_dir/sha256sum-amd64.txt"
manifest="$software_root/manifest.yaml"
mkdir -p "$images_dir"

installer_url=${RKE2_INSTALLER_URL:-https://get.rke2.io}
release_base_url=${RKE2_RELEASE_BASE_URL:-https://github.com/rancher/rke2/releases/download}
encoded_version=${version//+/%2B}
release_url="${release_base_url}/${encoded_version}"

curl_args=(--fail --location --silent --show-error --retry 3 --retry-delay 2)
[[ -n "${DOWNLOAD_PROXY_URL:-}" ]] && curl_args+=(--proxy "$DOWNLOAD_PROXY_URL")

download_file() {
  local url=$1
  local target=$2
  local temporary="${target}.part"
  curl "${curl_args[@]}" "$url" --output "$temporary"
  mv -f "$temporary" "$target"
}

if [[ "$mode" == online ]]; then
  download_file "$installer_url" "$installer"
  download_file "$release_url/rke2.linux-amd64.tar.gz" "$binary"
  download_file "$release_url/sha256sum-amd64.txt" "$checksum"
else
  for required in "$installer" "$binary" "$checksum"; do
    [[ -s "$required" ]] || {
      echo "OFFLINE_FILE_MISSING: place $required on the console before running" >&2
      exit 4
    }
  done
  for filename in "${offline_image_files[@]}"; do
    [[ "$filename" != */* && "$filename" =~ ^rke2-images(-[A-Za-z0-9_.-]+)?\.linux-amd64\.tar\.(zst|gz)$ ]] || {
      echo "INVALID_OFFLINE_IMAGE_FILENAME: $filename" >&2
      exit 9
    }
    [[ -s "$images_dir/$filename" ]] || {
      echo "OFFLINE_FILE_MISSING: place $images_dir/$filename on the console before running" >&2
      exit 4
    }
  done
fi
chmod 0755 "$installer"

verify_release_file() {
  local path=$1
  local filename
  local expected
  local actual
  filename=$(basename "$path")
  expected=$(awk -v name="$filename" '$2 == name || $2 == "*" name {print $1; exit}' "$checksum")
  [[ -n "$expected" ]] || { echo "RKE2_CHECKSUM_ENTRY_MISSING: $filename" >&2; exit 7; }
  actual=$(sha256sum "$path" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || { echo "RKE2_CHECKSUM_MISMATCH: $filename" >&2; exit 8; }
}

verify_release_file "$binary"
for filename in "${offline_image_files[@]}"; do
  verify_release_file "$images_dir/$filename"
done

installer_sha=$(sha256sum "$installer" | awk '{print $1}')
binary_sha=$(sha256sum "$binary" | awk '{print $1}')
{
  printf 'mode: %s\n' "$mode"
  printf 'rke2_version: %s\n' "$version"
  printf 'architecture: %s\n' "$arch"
  printf 'software_root: %s\n' "$software_root"
  printf 'files:\n'
  printf '  - {name: install.sh, path: "%s", sha256: "%s"}\n' "$installer" "$installer_sha"
  printf '  - {name: rke2.linux-amd64.tar.gz, path: "%s", sha256: "%s"}\n' "$binary" "$binary_sha"
  printf '  - {name: sha256sum-amd64.txt, path: "%s"}\n' "$checksum"
  for filename in "${offline_image_files[@]}"; do
    image_sha=$(sha256sum "$images_dir/$filename" | awk '{print $1}')
    printf '  - {name: "%s", path: "%s", sha256: "%s"}\n' "$filename" "$images_dir/$filename" "$image_sha"
  done
  if [[ ${#offline_image_files[@]} -eq 0 ]]; then
    printf 'image_source: registry\n'
  else
    printf 'image_source: local-archives\n'
  fi
} > "$manifest"
chmod 0644 "$manifest"
printf '%s\n' "$manifest"
