# PnP.PowerShell setup for Fazla OneDrive Toolkit

One-time setup to enable `od-audit-sharing`, which shells out to PowerShell.

## 1. Install PowerShell + PnP module

```bash
brew install --cask powershell    # macOS
pwsh -NoLogo -Command "Install-Module PnP.PowerShell -Scope CurrentUser -Force"
```

Verify:
```bash
pwsh -NoLogo -Command "Get-Module -ListAvailable PnP.PowerShell | Select-Object Version"
```
Expected: a version line (2.x or newer).

## 2. Convert the PEM certificate to PKCS#12 (.pfx)

PnP.PowerShell's `Connect-PnPOnline -CertificatePath` takes a PFX, not the
PEM key + PEM cert we use for the Python flow. Run the one-shot helper:

```bash
./scripts/ps/convert-cert.sh
```

This produces `~/.config/fazla-od/fazla-od.pfx` (mode 600, gitignored —
`~/.config/fazla-od/` is outside the repo) and stores a ~40-char random
password in macOS Keychain under service `FazlaODToolkit:PfxPassword`,
account `fazla-od`.

Verify:
```bash
ls -la ~/.config/fazla-od/fazla-od.pfx
security find-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w | wc -c
```
Expected: the PFX exists; the password is ~40 characters.

## 3. Confirm the Entra app has the same cert thumbprint

The PFX is built from the exact same PEM key+cert that Plan 1 uploaded to
Entra (thumbprint `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`). No new cert
upload is required.

## 4. Smoke-test the connection

```bash
pwsh -NoLogo -Command '
    $pwd = ConvertTo-SecureString -String (
        security find-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w
    ) -AsPlainText -Force
    Connect-PnPOnline `
        -Tenant 361efb70-ca20-41ae-b204-9045df001350 `
        -ClientId b22e6fd3-4859-43ae-b997-997ad3aaf14b `
        -CertificatePath "$HOME/.config/fazla-od/fazla-od.pfx" `
        -CertificatePassword $pwd `
        -Url https://fazla.sharepoint.com
    Get-PnPTenantSite | Select-Object -First 3 Url, Title
'
```
Expected: three site URL + title rows printed, no error.

## Rotation

When the PEM cert rotates (every 2 years; see spec §3):

1. `rm ~/.config/fazla-od/fazla-od.pfx`
2. Re-run `./scripts/ps/convert-cert.sh`

The Keychain entry is overwritten in place; no additional steps required.
