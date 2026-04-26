<#
.SYNOPSIS
Apply or remove a sensitivity label on a SharePoint file via PnP.PowerShell.

.PARAMETER Action
'apply' or 'remove'.

.PARAMETER SiteUrl
Full site URL, e.g. 'https://contoso.sharepoint.com/sites/Finance'.

.PARAMETER ServerRelativeUrl
Server-relative file path, e.g. '/sites/Finance/Shared Documents/Q1.xlsx'.

.PARAMETER Label
Label display name (required for apply).

.PARAMETER Tenant
Tenant (directory) ID. Required.

.PARAMETER ClientId
Azure AD app client ID. Required.

.PARAMETER PfxPath
Path to the PFX cert (default ~/.config/m365ctl/m365ctl.pfx; falls back
to ~/.config/fazla-od/fazla-od.pfx with a deprecation warning if the new
path is missing but the legacy one exists).

.PARAMETER KeychainService
Keychain service name holding the PFX password (default m365ctl:PfxPassword).

.PARAMETER KeychainAccount
Keychain account holding the PFX password (default m365ctl; falls back to
the legacy "fazla-od" account with a deprecation warning if the new account
is absent but the legacy one resolves).

.NOTES
Requires PnP.PowerShell installed and the cert converted to PFX via
scripts/ps/convert-cert.sh. See docs/ops/pnp-powershell-setup.md.
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('apply','remove')][string]$Action,
    [Parameter(Mandatory=$true)][string]$SiteUrl,
    [Parameter(Mandatory=$true)][string]$ServerRelativeUrl,
    [string]$Label,
    [Parameter(Mandatory=$true)][string]$Tenant,
    [Parameter(Mandatory=$true)][string]$ClientId,
    [string]$PfxPath = "$HOME/.config/m365ctl/m365ctl.pfx",
    [string]$KeychainService = "m365ctl:PfxPassword",
    [string]$KeychainAccount = "m365ctl"
)

$ErrorActionPreference = 'Stop'

# Legacy fallback: prefer the new m365ctl-named PFX, but silently honour the
# historical fazla-od location with a one-line stderr deprecation notice.
if (-not (Test-Path -LiteralPath $PfxPath)) {
    $legacyPfx = "$HOME/.config/fazla-od/fazla-od.pfx"
    if (Test-Path -LiteralPath $legacyPfx) {
        [Console]::Error.WriteLine("warning: PFX not found at $PfxPath; falling back to legacy $legacyPfx (rename to ~/.config/m365ctl/m365ctl.pfx — see docs/ops/pnp-powershell-setup.md)")
        $PfxPath = $legacyPfx
    }
}

function Get-PfxPassword {
    $raw = /usr/bin/security find-generic-password -a $KeychainAccount -s $KeychainService -w 2>$null
    if (-not $raw -and $KeychainAccount -ne "fazla-od") {
        $legacy = /usr/bin/security find-generic-password -a "fazla-od" -s $KeychainService -w 2>$null
        if ($legacy) {
            [Console]::Error.WriteLine("warning: Keychain account '$KeychainAccount' empty; falling back to legacy 'fazla-od' (rotate via docs/ops/pnp-powershell-setup.md)")
            $raw = $legacy
        }
    }
    if (-not $raw) { throw "Could not read PFX password from Keychain." }
    return (ConvertTo-SecureString -String $raw -AsPlainText -Force)
}

Import-Module PnP.PowerShell -ErrorAction Stop
Connect-PnPOnline `
    -Url $SiteUrl `
    -Tenant $Tenant `
    -ClientId $ClientId `
    -CertificatePath $PfxPath `
    -CertificatePassword (Get-PfxPassword)

try {
    if ($Action -eq 'apply') {
        if (-not $Label) { throw "Label required for 'apply'." }
        Set-PnPFileSensitivityLabel -ServerRelativeUrl $ServerRelativeUrl -Label $Label | Out-Null
        $payload = @{ status = 'ok'; label = $Label; path = $ServerRelativeUrl }
    } else {
        Remove-PnPFileSensitivityLabel -ServerRelativeUrl $ServerRelativeUrl | Out-Null
        $payload = @{ status = 'ok'; label = $null; path = $ServerRelativeUrl }
    }
    $payload | ConvertTo-Json -Compress
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    Disconnect-PnPOnline
}
