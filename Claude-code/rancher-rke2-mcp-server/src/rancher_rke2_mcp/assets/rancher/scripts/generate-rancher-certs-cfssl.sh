#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo 'usage: generate-rancher-certs-cfssl.sh OUTPUT_DIR RANCHER_HOSTNAME LB_IP [VALIDITY_DAYS]' >&2
  exit 2
fi

output_dir=$1
rancher_hostname=$2
lb_ip=$3
validity_days=${4:-3650}
expiry_hours=$((validity_days * 24))

for command_name in cfssl cfssljson cfssl-certinfo openssl; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "MISSING_CERTIFICATE_TOOL: $command_name" >&2; exit 3; }
done

umask 077
mkdir -p "$output_dir"
for path in cacerts-key.pem cacerts.pem tls.key tls.crt; do
  [[ ! -e "$output_dir/$path" ]] || { echo "refusing to overwrite $output_dir/$path" >&2; exit 1; }
done

cat > "$output_dir/cacerts-csr.json" <<EOF
{
  "CA": {"expiry": "${expiry_hours}h", "pathlen": 0},
  "CN": "cattle-ca",
  "key": {"algo": "rsa", "size": 2048},
  "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}]
}
EOF

cat > "$output_dir/ssl-config.json" <<EOF
{
  "signing": {
    "default": {"expiry": "${expiry_hours}h"},
    "profiles": {
      "server": {
        "usages": ["signing", "key encipherment", "server auth", "client auth"],
        "expiry": "${expiry_hours}h"
      }
    }
  }
}
EOF

cat > "$output_dir/ssl-csr.json" <<EOF
{
  "CN": "${rancher_hostname}",
  "hosts": ["${rancher_hostname}", "${lb_ip}"],
  "key": {"algo": "rsa", "size": 2048},
  "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}]
}
EOF

(
  cd "$output_dir"
  cfssl gencert -initca cacerts-csr.json | cfssljson -bare cacerts
  cfssl gencert -ca=cacerts.pem -ca-key=cacerts-key.pem \
    -config=ssl-config.json -profile=server ssl-csr.json | cfssljson -bare tls
  cfssl-certinfo -cert cacerts.pem >/dev/null
  cfssl-certinfo -cert tls.pem >/dev/null
  mv tls-key.pem tls.key
  mv tls.pem tls.crt
  chmod 0600 cacerts-key.pem tls.key
  chmod 0644 cacerts.pem tls.crt
  openssl verify -CAfile cacerts.pem tls.crt
  openssl x509 -in tls.crt -noout -checkip "$lb_ip"
  if [[ "$rancher_hostname" != "$lb_ip" ]]; then
    openssl x509 -in tls.crt -noout -checkhost "$rancher_hostname"
  fi
)
