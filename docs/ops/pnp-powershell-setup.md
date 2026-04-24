# PnP.PowerShell setup for m365ctl

One-time setup to enable `od-audit-sharing`, which shells out to PowerShell.

## 1. Install PowerShell + PnP module

**Homebrew heads-up (as of 2026-04):** the `powershell` cask was renamed
to `powershell@preview` and then deprecated; its checksum is currently
broken against the upstream `.pkg`. The durable path is to install the
signed `.pkg` directly from Microsoft's GitHub releases:

```bash
# Replace the URL with the latest from https://github.com/PowerShell/PowerShell/releases
# (use -osx-x64 for Intel Macs).
curl -L --output /tmp/powershell.pkg \
  https://github.com/PowerShell/PowerShell/releases/download/v7.6.1/powershell-lts-7.6.1-osx-arm64.pkg
sudo installer -pkg /tmp/powershell.pkg -target /
rm /tmp/powershell.pkg
```

Then install the PnP module into the current user (no sudo):

```bash
pwsh -NoLogo -Command "Install-Module PnP.PowerShell -Scope CurrentUser -Force -AllowClobber"
```

Verify:
```bash
pwsh --version                                                       # PowerShell 7.6.x
pwsh -NoLogo -Command "Get-Module -ListAvailable PnP.PowerShell | Select-Object Version"
```
Expected: a PowerShell version ≥ 7.4 and a PnP module version ≥ 2.x (3.x current).

## 2. Convert the PEM certificate to PKCS#12 (.pfx)

PnP.PowerShell's `Connect-PnPOnline -CertificatePath` takes a PFX, not the
PEM key + PEM cert we use for the Python flow. Run the one-shot helper:

```bash
./scripts/ps/convert-cert.sh
```

This produces `~/.config/fazla-od/fazla-od.pfx` (mode 600, gitignored —
`~/.config/fazla-od/` is outside the repo) and stores a ~40-char random
password in macOS Keychain under service `m365ctl:PfxPassword`,
account `fazla-od`.

Verify:
```bash
ls -la ~/.config/fazla-od/fazla-od.pfx
security find-generic-password -a fazla-od -s m365ctl:PfxPassword -w | wc -c
```
Expected: the PFX exists; the password is ~40 characters.

## 3. Confirm the Entra app has the same cert thumbprint

The PFX is built from the exact same PEM key+cert that Plan 1 uploaded to
Entra (thumbprint `<your-cert-thumbprint>`). No new cert
upload is required.

## 3b. Grant the Entra app SharePoint-API permissions (NOT just Graph)

`od-audit-sharing` (and other PnP-backed commands like `od-label`) call
the **SharePoint REST/CSOM API**, not Microsoft Graph. These APIs have a
separate permission surface from Graph, and the Entra app needs
**application-level** permissions granted there too. Plan 1 only granted
Microsoft Graph permissions (`Sites.ReadWrite.All` on Graph), which is
insufficient for PnP.

Symptom when this is missing: `Connect-PnPOnline` succeeds but any
subsequent PnP cmdlet (`Get-PnPList`, `Get-PnPListItemPermission`, …)
fails with `Unauthorized` from the SharePoint REST API.

Fix (one-time, tenant admin required):

1. [Entra admin center](https://entra.microsoft.com) → **App registrations** → open the toolkit's app.
2. **API permissions** → **Add a permission** → **SharePoint** (not Microsoft Graph) → **Application permissions**.
3. Check `Sites.FullControl.All`. Add.
4. Back on the permissions list, click **"Grant admin consent for <tenant>"**. Confirm.
5. Wait ~30 seconds for propagation, then retry.

If you don't need full control, `Sites.Manage.All` works for `od-audit-sharing` (read-only on permissions). `od-label` requires `Sites.FullControl.All` to set sensitivity labels.

The ODfB recycle-bin fallbacks (`scripts/ps/recycle-restore.ps1` and `scripts/ps/recycle-purge.ps1`, both dot-sourcing `scripts/ps/_M365ctlRecycleHelpers.ps1`) call `Restore-PnPRecycleBinItem` and `Clear-PnPRecycleBinItem` respectively. These are covered by the same `Sites.FullControl.All` grant above — no additional permission is needed.

## 4. Smoke-test the connection

```bash
pwsh -NoLogo -Command '
    $pwd = ConvertTo-SecureString -String (
        security find-generic-password -a fazla-od -s m365ctl:PfxPassword -w
    ) -AsPlainText -Force
    Connect-PnPOnline `
        -Tenant <your-tenant-id> `
        -ClientId <your-client-id> `
        -CertificatePath "$HOME/.config/fazla-od/fazla-od.pfx" `
        -CertificatePassword $pwd `
        -Url https://<your-tenant>.sharepoint.com
    Get-PnPTenantSite | Select-Object -First 3 Url, Title
'
```
Expected: three site URL + title rows printed, no error.

## Rotation

When the PEM cert rotates (every 2 years; see spec §3):

1. `rm ~/.config/fazla-od/fazla-od.pfx`
2. Re-run `./scripts/ps/convert-cert.sh`

The Keychain entry is overwritten in place; no additional steps required.
