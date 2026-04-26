<#
.SYNOPSIS
  Shared PnP.PowerShell helpers for m365ctl recycle-bin operations.

.DESCRIPTION
  This file is dot-sourced by the recycle-bin restore/purge scripts. It
  provides three helpers:

    - Connect-M365ctlSite         : Connect-PnPOnline wrapper with cert +
                                  macOS Keychain auth.
    - Find-RecycleBinItem       : Locate a single recycle-bin item by
                                  LeafName + DirName, with most-recent-
                                  wins ambiguity handling.
    - Resolve-SiteUrlFromDriveId: Self-contained Graph call that maps a
                                  driveId to its SharePoint/OneDrive site
                                  URL.

  Keychain defaults mirror scripts/ps/audit-sharing.ps1:
    service = m365ctl:PfxPassword
    account = m365ctl   (legacy "fazla-od" account is honoured as fallback)
#>

$ErrorActionPreference = "Stop"

function Get-M365ctlPfxPassword {
    <#
    .SYNOPSIS
      Read the PFX password from the macOS Keychain. Throws if missing.

    .DESCRIPTION
      Tries the supplied KeychainAccount first. If empty and the account is
      not already "fazla-od", retries against the legacy "fazla-od" account
      and emits a one-line deprecation notice to stderr so existing installs
      keep working without manual intervention.
    #>
    param(
        [string] $KeychainService = "m365ctl:PfxPassword",
        [string] $KeychainAccount = "m365ctl"
    )
    $raw = /usr/bin/security find-generic-password -a $KeychainAccount -s $KeychainService -w 2>$null
    if (-not $raw -and $KeychainAccount -ne "fazla-od") {
        $legacy = /usr/bin/security find-generic-password -a "fazla-od" -s $KeychainService -w 2>$null
        if ($legacy) {
            [Console]::Error.WriteLine("warning: Keychain account '$KeychainAccount' empty; falling back to legacy 'fazla-od' (rotate via docs/ops/pnp-powershell-setup.md)")
            $raw = $legacy
        }
    }
    if (-not $raw) {
        throw "KeychainMissing: no entry for service=$KeychainService account=$KeychainAccount. Run ./scripts/ps/convert-cert.sh."
    }
    return (ConvertTo-SecureString -String $raw -AsPlainText -Force)
}

function Connect-M365ctlSite {
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
      Target site URL (e.g. https://contoso.sharepoint.com/sites/Foo or a
      personal OneDrive /personal/... URL).

    .EXAMPLE
      Connect-M365ctlSite -Tenant $t -ClientId $c `
                       -PfxPath "$HOME/.config/m365ctl/m365ctl.pfx" `
                       -SiteUrl "https://contoso.sharepoint.com/sites/Finance"
    #>
    param(
        [Parameter(Mandatory=$true)] [string] $Tenant,
        [Parameter(Mandatory=$true)] [string] $ClientId,
        [Parameter(Mandatory=$true)] [string] $PfxPath,
        [Parameter(Mandatory=$true)] [string] $SiteUrl
    )
    if (-not (Test-Path -LiteralPath $PfxPath)) {
        $legacyPfx = "$HOME/.config/fazla-od/fazla-od.pfx"
        if (Test-Path -LiteralPath $legacyPfx) {
            [Console]::Error.WriteLine("warning: PFX not found at $PfxPath; falling back to legacy $legacyPfx (rename to ~/.config/m365ctl/m365ctl.pfx — see docs/ops/pnp-powershell-setup.md)")
            $PfxPath = $legacyPfx
        } else {
            throw "PfxMissing: $PfxPath not found. Run ./scripts/ps/convert-cert.sh."
        }
    }
    $pfxSecret = Get-M365ctlPfxPassword
    Connect-PnPOnline `
        -Tenant $Tenant `
        -ClientId $ClientId `
        -CertificatePath $PfxPath `
        -CertificatePassword $pfxSecret `
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

    .NOTES
      Recycle-bin enumeration is capped at 100000 items per call (the
      -RowLimit passed to Get-PnPRecycleBinItem). PnP.PowerShell does not
      expose native paging on this cmdlet, so bins larger than the ceiling
      will truncate silently at the PnP layer. If the total hits the cap,
      a "RowLimitReached" warning is emitted so operators know the search
      may have missed the target. True paging (e.g. split by FirstStage /
      SecondStage) is a future enhancement.
    #>
    param(
        [Parameter(Mandatory=$true)] [string] $LeafName,
        [Parameter(Mandatory=$true)] [string] $DirName
    )
    $escapedDirName = [System.Management.Automation.WildcardPattern]::Escape($DirName)
    $all = Get-PnPRecycleBinItem -RowLimit 100000
    if ($all.Count -ge 100000) { Write-Warning "RowLimitReached: recycle bin returned 100000 items (the enumeration cap); target may be beyond the ceiling. Empty older items or narrow the search before retrying." }
    $candidates = @($all | Where-Object {
        $_.LeafName -eq $LeafName -and $_.DirName -like "*$escapedDirName"
    } | Sort-Object -Property DeletedDate -Descending)

    if ($candidates.Count -eq 0) {
        throw "NoMatch: no recycle-bin item with LeafName='$LeafName' under DirName like '*$DirName'."
    }
    if ($candidates.Count -gt 1) {
        $summary = ($candidates | ForEach-Object {
            "id=$($_.Id) deleted=$($_.DeletedDate.ToString('o')) dir=$($_.DirName)"
        }) -join "; "
        Write-Warning "AmbiguousMatch: $($candidates.Count) candidates for '$LeafName' in '*$DirName'; picking newest. Candidates: $summary"
    }
    return $candidates[0]
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
      SharePoint tenant host, e.g. 'contoso.sharepoint.com'. Used to derive
      the admin endpoint for the bootstrap connect.

    .PARAMETER Tenant
      Tenant (directory) ID.

    .PARAMETER ClientId
      Azure AD app client ID.

    .PARAMETER PfxPath
      Path to the PFX cert on disk.

    .EXAMPLE
      Resolve-SiteUrlFromDriveId -DriveId 'b!abc...' -TenantHost 'contoso.sharepoint.com' `
          -Tenant $t -ClientId $c -PfxPath "$HOME/.config/m365ctl/m365ctl.pfx"
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
    Connect-M365ctlSite -Tenant $Tenant -ClientId $ClientId -PfxPath $PfxPath -SiteUrl $adminUrl
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
