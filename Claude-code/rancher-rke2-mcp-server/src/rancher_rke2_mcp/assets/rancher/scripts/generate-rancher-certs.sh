#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: $0 OUTPUT_DIR RANCHER_HOSTNAME LB_IP [VALIDITY_DAYS]" >&2
  exit 2
fi

output_dir=$1
rancher_hostname=$2
lb_ip=$3
validity_days=${4:-3650}

umask 077
mkdir -p "$output_dir"

for path in cacerts-key.pem cacerts.pem tls.key tls.crt; do
  if [[ -e "$output_dir/$path" ]]; then
    echo "refusing to overwrite existing certificate material: $output_dir/$path" >&2
    exit 1
  fi
done

openssl genrsa -out "$output_dir/cacerts-key.pem" 4096
openssl req -x509 -new -sha256 \
  -key "$output_dir/cacerts-key.pem" \
  -days "$validity_days" \
  -subj "/C=CN/O=Rancher Automation/OU=Platform/CN=cattle-ca" \
  -out "$output_dir/cacerts.pem"

openssl genrsa -out "$output_dir/tls.key" 2048

{
  echo '[req]'
  echo 'prompt = no'
  echo 'distinguished_name = dn'
  echo 'req_extensions = req_ext'
  echo '[dn]'
  echo "CN = $rancher_hostname"
  echo '[req_ext]'
  echo 'subjectAltName = @alt_names'
  echo '[alt_names]'
  echo "IP.1 = $lb_ip"
  if [[ "$rancher_hostname" != "$lb_ip" ]]; then
    echo "DNS.1 = $rancher_hostname"
  fi
} > "$output_dir/openssl.cnf"

openssl req -new -sha256 \
  -key "$output_dir/tls.key" \
  -config "$output_dir/openssl.cnf" \
  -out "$output_dir/tls.csr"

openssl x509 -req -sha256 \
  -in "$output_dir/tls.csr" \
  -CA "$output_dir/cacerts.pem" \
  -CAkey "$output_dir/cacerts-key.pem" \
  -CAcreateserial \
  -days "$validity_days" \
  -extensions req_ext \
  -extfile "$output_dir/openssl.cnf" \
  -out "$output_dir/tls.crt"

chmod 0600 "$output_dir/cacerts-key.pem" "$output_dir/tls.key"
chmod 0644 "$output_dir/cacerts.pem" "$output_dir/tls.crt"
openssl verify -CAfile "$output_dir/cacerts.pem" "$output_dir/tls.crt"
openssl x509 -in "$output_dir/tls.crt" -noout -checkip "$lb_ip"
if [[ "$rancher_hostname" != "$lb_ip" ]]; then
  openssl x509 -in "$output_dir/tls.crt" -noout -checkhost "$rancher_hostname"
fi
