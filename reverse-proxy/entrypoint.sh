#!/usr/bin/env bash
set -euo pipefail

CERT_DIR="/etc/nginx/certs"
CERT="${CERT_DIR}/server.crt"
KEY="${CERT_DIR}/server.key"
: "${LAN_IP:?Set LAN_IP env to the LAN IP of this host (e.g., 192.168.1.246)}"

mkdir -p "$CERT_DIR"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  echo "Generating self-signed cert for IP SAN: ${LAN_IP}"
  cat >/tmp/openssl.cnf <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no
[dn]
CN = LAN
[v3_req]
subjectAltName = IP:${LAN_IP}
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF
  openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout "${KEY}" \
    -out "${CERT}" \
    -config /tmp/openssl.cnf
  chmod 600 "${KEY}"
fi

echo "Starting nginx..."
exec nginx -g 'daemon off;'
