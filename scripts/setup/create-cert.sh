#!/usr/bin/env bash
# create-cert.sh - generate a self-signed cert for m365ctl app-only auth.
# Usage:
#   scripts/setup/create-cert.sh [CN]
# Default CN is "m365ctl".
#
# Output:
#   ~/.config/m365ctl/m365ctl.key   - PEM private key, mode 600
#   ~/.config/m365ctl/m365ctl.cer   - PEM public cert (upload this to Entra)
#
# Next steps printed after success.
set -euo pipefail

CN="${1:-m365ctl}"
OUTDIR="${HOME}/.config/m365ctl"
KEY="${OUTDIR}/m365ctl.key"
CER="${OUTDIR}/m365ctl.cer"

mkdir -p "${OUTDIR}"
chmod 700 "${OUTDIR}"

if [[ -e "${KEY}" || -e "${CER}" ]]; then
    echo "create-cert.sh: ${KEY} or ${CER} already exists; refusing to overwrite." >&2
    exit 1
fi

openssl req -x509 -newkey rsa:4096 -sha256 -days 730 -nodes \
    -keyout "${KEY}" -out "${CER}" \
    -subj "/CN=${CN}"
chmod 600 "${KEY}"
chmod 644 "${CER}"

THUMB=$(openssl x509 -in "${CER}" -fingerprint -noout -sha1 | sed 's/.*=//' | tr -d ':')

cat <<EOF

Cert generated.
  CN:         ${CN}
  Private key: ${KEY}
  Public cert: ${CER}
  SHA-1:       ${THUMB}

Next steps:
  1. Upload ${CER} to your Entra app registration (Certificates & secrets → Certificates).
  2. Copy the thumbprint above into any tooling that needs it (e.g. PnP.PowerShell).
  3. Update config.toml: cert_path and cert_public should match the paths above.
EOF
