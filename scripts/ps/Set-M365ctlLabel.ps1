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

.NOTES
Plan 3 installs PnP.PowerShell and converts the cert to PFX. This script
relies on both being already in place. It authenticates with certificate
+ app-only against the Microsoft 365 tenant using env vars set by the caller
(M365CTL_TENANT, M365CTL_CLIENT_ID, M365CTL_CERT_PFX).
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('apply','remove')][string]$Action,
    [Parameter(Mandatory=$true)][string]$SiteUrl,
    [Parameter(Mandatory=$true)][string]$ServerRelativeUrl,
    [string]$Label
)

$ErrorActionPreference = 'Stop'

Import-Module PnP.PowerShell -ErrorAction Stop
Connect-PnPOnline `
    -Url $SiteUrl `
    -Tenant $env:M365CTL_TENANT `
    -ClientId $env:M365CTL_CLIENT_ID `
    -CertificatePath $env:M365CTL_CERT_PFX `
    -CertificatePassword (ConvertTo-SecureString $env:M365CTL_CERT_PFX_PASS -AsPlainText -Force)

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
