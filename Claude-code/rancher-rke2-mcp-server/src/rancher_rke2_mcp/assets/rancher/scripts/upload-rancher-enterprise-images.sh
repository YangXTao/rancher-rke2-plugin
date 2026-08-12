#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 3 || $# -gt 5 ]]; then
  echo 'usage: upload-rancher-enterprise-images.sh ARCHIVE_PATH REGISTRY REGISTRY_JOB_IMAGE [JOBS] [TLS_VERIFY]' >&2
  exit 2
fi

archive=$1
registry=$2
registry_job_image=$3
jobs=${4:-4}
tls_verify=${5:-false}
registry_username=${REGISTRY_USERNAME:-}
registry_password=${REGISTRY_PASSWORD:-}

[[ -s "$archive" ]] || { echo "RANCHER_IMAGE_ARCHIVE_MISSING: $archive" >&2; exit 3; }
command -v docker >/dev/null 2>&1 || { echo 'DOCKER_REQUIRED_FOR_RANCHER_IMAGE_UPLOAD' >&2; exit 3; }

archive_dir=$(cd "$(dirname "$archive")" && pwd)
archive_name=$(basename "$archive")
marker_name=".${archive_name}.${registry//[^A-Za-z0-9_.-]/_}.uploaded"
fingerprint="$(sha256sum "$archive" | awk '{print $1}')|$registry|$registry_job_image|$jobs|$tls_verify"
if [[ -s "$archive_dir/$marker_name" ]] && [[ "$(cat "$archive_dir/$marker_name")" == "$fingerprint" ]]; then
  echo "RANCHER_IMAGES_ALREADY_UPLOADED: $registry"
  exit 0
fi

[[ -n "$registry_username" ]] || { echo 'REGISTRY_USERNAME_REQUIRED' >&2; exit 3; }
[[ -n "$registry_password" ]] || { echo 'REGISTRY_PASSWORD_REQUIRED' >&2; exit 3; }

docker_config_dir=$(mktemp -d "${TMPDIR:-/tmp}/rancher-registry-auth.XXXXXX")
cleanup() {
  rm -rf -- "$docker_config_dir"
}
trap cleanup EXIT
chmod 0700 "$docker_config_dir"

# Authenticate without an interactive prompt or a password-bearing command-line
# argument. Docker writes the credential only to the temporary config directory;
# the same file is mounted read-only for Hangar and removed on exit.
printf '%s' "$registry_password" |
  docker --config "$docker_config_dir" login \
    "$registry" \
    --username "$registry_username" \
    --password-stdin >/dev/null
[[ -s "$docker_config_dir/config.json" ]] || {
  echo 'TEMPORARY_REGISTRY_AUTH_CONFIG_MISSING' >&2
  exit 3
}

docker run --rm \
  -v "$archive_dir:/hangar/mnt" \
  -v "$docker_config_dir:/root/.docker:ro" \
  -e REGISTRY_AUTH_FILE=/root/.docker/config.json \
  --entrypoint /bin/sh \
  "$registry_job_image" -ec \
  "cd /hangar/mnt && hangar load -s '$archive_name' -d '$registry' -j '$jobs' --os linux,windows --arch amd64,arm64 --tls-verify='$tls_verify'"
printf '%s\n' "$fingerprint" > "$archive_dir/$marker_name"
