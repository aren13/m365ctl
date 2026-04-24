#!/usr/bin/env bash
# convert-cert.sh — one-shot: PEM key+cert -> PFX, store password in Keychain.
#
# Usage:   scripts/ps/convert-cert.sh
# Result:  ~/.config/fazla-od/fazla-od.pfx (mode 600)
#          Keychain entry FazlaODToolkit:PfxPassword holds the export password.
#
# Requires: openssl (system), security (macOS), /dev/urandom.
set -euo pipefail

CERT_DIR="${HOME}/.config/fazla-od"
KEY="${CERT_DIR}/fazla-od.key"
CER="${CERT_DIR}/fazla-od.cer"
PFX="${CERT_DIR}/fazla-od.pfx"
KEYCHAIN_SERVICE="FazlaODToolkit:PfxPassword"
KEYCHAIN_ACCOUNT="fazla-od"

for f in "$KEY" "$CER"; do
    if [[ ! -r "$f" ]]; then
        echo "error: $f not readable" >&2
        exit 1
    fi
done

if [[ -e "$PFX" ]]; then
    echo "error: $PFX already exists — delete or rename it first" >&2
    exit 1
fi

# 32 random bytes -> base64 -> strip non-alphanumerics. Result is ~40 chars.
PASSWORD="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)"

openssl pkcs12 \
    -export \
    -inkey "$KEY" \
    -in "$CER" \
    -name "FazlaODToolkit" \
    -out "$PFX" \
    -passout "pass:${PASSWORD}"

chmod 600 "$PFX"

# Update-or-add (delete existing, then add).
security delete-generic-password \
    -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 || true

security add-generic-password \
    -a "$KEYCHAIN_ACCOUNT" \
    -s "$KEYCHAIN_SERVICE" \
    -w "$PASSWORD" \
    -T /usr/bin/security

echo "PFX written to $PFX (mode 600)."
echo "Password stored in Keychain:"
echo "  security find-generic-password -a ${KEYCHAIN_ACCOUNT} -s ${KEYCHAIN_SERVICE} -w"
