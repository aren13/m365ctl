<#
.SYNOPSIS
  Restore a single item from a SharePoint/OneDrive recycle bin via PnP.

.DESCRIPTION
  Dot-sources _M365ctlRecycleHelpers.ps1, connects to the target site with
  cert + Keychain auth, locates the most-recent recycle-bin item matching
  the supplied LeafName + DirName, restores it via Restore-PnPRecycleBinItem,
  and emits a single JSON line on stdout with the restored metadata.

  Errors are surfaced via non-zero exit + Write-Error so the Python caller
  (m365ctl.onedrive.mutate.delete._restore_via_pnp) can propagate stderr into
  DeleteResult.error and the audit log.

.EXAMPLE
  pwsh -NoProfile -File scripts/ps/recycle-restore.ps1 `
      -Tenant $t -ClientId $c `
      -SiteUrl 'https://contoso.sharepoint.com/sites/Finance' `
      -LeafName 'Q1.xlsx' -DirName '/Shared Documents/Finance'
#>
param(
    [Parameter(Mandatory=$true)][string]$Tenant,
    [Parameter(Mandatory=$true)][string]$ClientId,
    [Parameter(Mandatory=$true)][string]$SiteUrl,
    [Parameter(Mandatory=$true)][string]$LeafName,
    [Parameter(Mandatory=$true)][string]$DirName,
    [string]$PfxPath = "$HOME/.config/fazla-od/fazla-od.pfx"
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_M365ctlRecycleHelpers.ps1")

try {
    Connect-M365ctlSite -Tenant $Tenant -ClientId $ClientId `
                     -PfxPath $PfxPath -SiteUrl $SiteUrl
    try {
        $rb = Find-RecycleBinItem -LeafName $LeafName -DirName $DirName
        Restore-PnPRecycleBinItem -Identity $rb.Id -Force | Out-Null
        $payload = [ordered]@{
            recycle_bin_item_id   = [string]$rb.Id
            restored_name         = [string]$rb.LeafName
            restored_parent_path  = [string]$rb.DirName
        }
        $payload | ConvertTo-Json -Compress
    }
    finally {
        try { Disconnect-PnPOnline | Out-Null } catch { }
    }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
