#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 docker|nerdctl ARCHIVE_PATH" >&2
  exit 2
fi

runtime=$1
archive=$2
install_root=/opt/rancher-rke2/lb-runtime/$runtime

test -f "$archive"
mkdir -p "$install_root"

case "$runtime" in
  docker)
    tar -xzf "$archive" -C "$install_root"
    test -x "$install_root/docker/docker"
    for binary in "$install_root"/docker/*; do
      install -m 0755 "$binary" "/usr/local/bin/$(basename "$binary")"
    done
    install -d -m 0755 /etc/docker
    install -m 0644 /dev/stdin /etc/systemd/system/docker.service <<'UNIT'
[Unit]
Description=Docker Application Container Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=infinity
LimitNPROC=infinity
Delegate=yes
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now docker.service
    ;;
  nerdctl)
    tar -xzf "$archive" -C /usr/local
    test -x /usr/local/bin/nerdctl
    systemctl daemon-reload
    systemctl enable --now containerd.service
    ;;
  *)
    echo "unsupported runtime: $runtime" >&2
    exit 2
    ;;
esac

"$runtime" version
