<#
.SYNOPSIS
  Shared PnP.PowerShell helpers for Fazla recycle-bin operations.

.DESCRIPTION
  This file is dot-sourced by the recycle-bin restore/purge scripts. It
  provides three helpers:

    - Connect-FazlaSite         : Connect-PnPOnline wrapper with cert +
                                  macOS Keychain auth.
    - Find-RecycleBinItem       : Locate a single recycle-bin item by
                                  LeafName + DirName, with most-recent-
                                  wins ambiguity handling.
    - Resolve-SiteUrlFromDriveId: Self-contained Graph call that maps a
                                  driveId to its SharePoint/OneDrive site
                                  URL.

  Keychain defaults mirror scripts/ps/audit-sharing.ps1:
    service = FazlaODToolkit:PfxPassword
    account = fazla-od
#>

$ErrorActionPreference = "Stop"

function Get-FazlaPfxPassword {
    <#
    .SYNOPSIS
      Read the PFX password from the macOS Keychain. Throws if missing.
    #>
    param(
        [string] $KeychainService = "FazlaODToolkit:PfxPassword",
        [string] $KeychainAccount = "fazla-od"
    )
    $raw = /usr/bin/security find-generic-password -a $KeychainAccount -s $KeychainService -w 2>$null
    if (-not $raw) {
        throw "KeychainMissing: no entry for service=$KeychainService account=$KeychainAccount. Run ./scripts/ps/convert-cert.sh."
    }
    return (ConvertTo-SecureString -String $raw -AsPlainText -Force)
}

function Connect-FazlaSite {
    <#
    .SYNOPSIS
      Connect to a SharePoint/OneDrive site with cert + Keychain auth.

    .PARAMETER Tenant
      Tenant (directory) ID.

    .PARAMETER ClientId
      Azure AD app client ID.

    .PARAMETER PfxPath
      Path to the PFX cert on disk.

    .PARAMETER SiteUrl
      Target site URL (e.g. https://fazla.sharepoint.com/sites/Foo or a
      personal OneDrive /personal/... URL).

    .EXAMPLE
      Connect-FazlaSite -Tenant $t -ClientId $c `
                       -PfxPath "$HOME/.config/fazla-od/fazla-od.pfx" `
                       -SiteUrl "https://fazla.sharepoint.com/sites/Finance"
    #>
    param(
        [Parameter(Mandatory=$true)] [string] $Tenant,
        [Parameter(Mandatory=$true)] [string] $ClientId,
        [Parameter(Mandatory=$true)] [string] $PfxPath,
        [Parameter(Mandatory=$true)] [string] $SiteUrl
    )
    if (-not (Test-Path -LiteralPath $PfxPath)) {
        throw "PfxMissing: $PfxPath not found. Run ./scripts/ps/convert-cert.sh."
    }
    $pwd = Get-FazlaPfxPassword
    Connect-PnPOnline `
        -Tenant $Tenant `
        -ClientId $ClientId `
        -CertificatePath $PfxPath `
        -CertificatePassword $pwd `
        -Url $SiteUrl | Out-Null
}

function Find-RecycleBinItem {
    <#
    .SYNOPSIS
      Locate a single recycle-bin item by leaf + parent-dir suffix match.

    .DESCRIPTION
      Filters Get-PnPRecycleBinItem by LeafName exact match and DirName
      like-suffix match (case-insensitive). Sorts matches by DeletedDate
      DESC and returns the newest. On multiple matches, writes an
      'AmbiguousMatch' warning listing every candidate to stderr, then
      returns the newest. On zero matches, throws "NoMatch: ...".

    .PARAMETER LeafName
      Filename as it existed at delete time (e.g. 'Q1.xlsx').

    .PARAMETER DirName
      Original parent directory (site-relative suffix). Matched via
      DirName -like "*$DirName".

    .EXAMPLE
      $item = Find-RecycleBinItem -LeafName 'Q1.xlsx' -DirName 'Shared Documents/Finance'
    #>
    param(
        [Parameter(Mandatory=$true)] [string] $LeafName,
        [Parameter(Mandatory=$true)] [string] $DirName
    )
    $all = Get-PnPRecycleBinItem -RowLimit 5000
    $matches = @($all | Where-Object {
        $_.LeafName -eq $LeafName -and $_.DirName -like "*$DirName"
    } | Sort-Object -Property DeletedDate -Descending)

    if ($matches.Count -eq 0) {
        throw "NoMatch: no recycle-bin item with LeafName='$LeafName' under DirName like '*$DirName'."
    }
    if ($matches.Count -gt 1) {
        $summary = ($matches | ForEach-Object {
            "id=$($_.Id) deleted=$($_.DeletedDate.ToString('o')) dir=$($_.DirName)"
        }) -join "; "
        Write-Warning "AmbiguousMatch: $($matches.Count) candidates for '$LeafName' in '*$DirName'; picking newest. Candidates: $summary"
    }
    return $matches[0]
}

function Resolve-SiteUrlFromDriveId {
    <#
    .SYNOPSIS
      Map a Microsoft Graph driveId to its owning site URL.

    .DESCRIPTION
      Self-contained: connects to the tenant admin endpoint with the
      supplied cert, calls Graph GET /drives/{id}, reads webUrl, trims
      trailing '/Documents' (personal OneDrive) or '/Shared Documents'
      (site library) — URL-encoded or plain — and returns the remainder.
      Disconnects on the way out.

    .PARAMETER DriveId
      Graph drive id (e.g. 'b!...').

    .PARAMETER TenantHost
      SharePoint tenant host, e.g. 'fazla.sharepoint.com'. Used to derive
      the admin endpoint for the bootstrap connect.

    .PARAMETER Tenant
      Tenant (directory) ID.

    .PARAMETER ClientId
      Azure AD app client ID.

    .PARAMETER PfxPath
      Path to the PFX cert on disk.

    .EXAMPLE
      Resolve-SiteUrlFromDriveId -DriveId 'b!abc...' -TenantHost 'fazla.sharepoint.com' `
          -Tenant $t -ClientId $c -PfxPath "$HOME/.config/fazla-od/fazla-od.pfx"
    #>
    param(
        [Parameter(Mandatory=$true)] [string] $DriveId,
        [Parameter(Mandatory=$true)] [string] $TenantHost,
        [Parameter(Mandatory=$true)] [string] $Tenant,
        [Parameter(Mandatory=$true)] [string] $ClientId,
        [Parameter(Mandatory=$true)] [string] $PfxPath
    )
    $hostPrefix = $TenantHost.Split('.')[0]
    $adminUrl = "https://$hostPrefix-admin.sharepoint.com"
    Connect-FazlaSite -Tenant $Tenant -ClientId $ClientId -PfxPath $PfxPath -SiteUrl $adminUrl
    try {
        $resp = Invoke-PnPGraphMethod -Url "v1.0/drives/$DriveId" -Method Get
        $webUrl = [string]$resp.webUrl
        if (-not $webUrl) { throw "GraphNoWebUrl: drive $DriveId returned no webUrl." }
        $webUrl = $webUrl.TrimEnd('/')
        $suffixes = @(
            '/Shared%20Documents', '/Shared Documents',
            '/Documents'
        )
        foreach ($sfx in $suffixes) {
            if ($webUrl.EndsWith($sfx, [System.StringComparison]::OrdinalIgnoreCase)) {
                $webUrl = $webUrl.Substring(0, $webUrl.Length - $sfx.Length)
                break
            }
        }
        return $webUrl.TrimEnd('/')
    }
    finally {
        try { Disconnect-PnPOnline | Out-Null } catch { }
    }
}
