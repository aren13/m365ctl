#!/usr/bin/env bash
# convert-cert.sh — one-shot: PEM key+cert -> PFX, store password in Keychain.
#
# Usage:   scripts/ps/convert-cert.sh
# Result:  ~/.config/m365ctl/m365ctl.pfx (mode 600)
#          Keychain entry m365ctl:PfxPassword (account m365ctl) holds the
#          export password.
#
# Existing fazla-od installs:
#   The PnP.PowerShell scripts continue to honour ~/.config/fazla-od/fazla-od.pfx
#   and Keychain account "fazla-od" as a legacy fallback (with a deprecation
#   warning on stderr). To cut over cleanly, see
#   docs/ops/pnp-powershell-setup.md ("Migrating from a legacy install").
#
# Requires: openssl (system), security (macOS), /dev/urandom.
set -euo pipefail

CERT_DIR="${HOME}/.config/m365ctl"
KEY="${CERT_DIR}/m365ctl.key"
CER="${CERT_DIR}/m365ctl.cer"
PFX="${CERT_DIR}/m365ctl.pfx"
KEYCHAIN_SERVICE="m365ctl:PfxPassword"
KEYCHAIN_ACCOUNT="m365ctl"

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
    -name "m365ctl" \
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
