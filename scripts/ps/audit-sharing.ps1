<#
.SYNOPSIS
  Emit one row per permission for every item in a SharePoint site or one drive.

.PARAMETER Scope
  One of: site:<site-id-or-url>, drive:<drive-id>

.PARAMETER OutputFormat
  json (default) or tsv.

.PARAMETER Tenant
  Tenant (directory) ID. Required.

.PARAMETER ClientId
  Azure AD app client ID. Required.

.PARAMETER PfxPath
  Path to the PFX cert (default ~/.config/m365ctl/m365ctl.pfx; falls back
  to ~/.config/fazla-od/fazla-od.pfx with a deprecation warning if the new
  path is missing but the legacy one exists).

.PARAMETER KeychainService
  Keychain service name holding the PFX password
  (default m365ctl:PfxPassword).

.PARAMETER KeychainAccount
  Keychain account holding the PFX password (default m365ctl; falls back to
  the legacy "fazla-od" account with a deprecation warning if the new account
  is absent but the legacy one resolves).

.PARAMETER InternalDomainPattern
  Regex applied to a permission's principal name to decide whether the
  share is *internal*. Empty (default) means treat every "@"-bearing
  principal as external. Pass e.g. "@(contoso|contoso\.onmicrosoft)\."
  to mark same-tenant principals as internal.

.EXAMPLE
  pwsh scripts/ps/audit-sharing.ps1 -Scope "site:contoso.sharepoint.com,abc,def" \
      -Tenant <your-tenant-id> -ClientId <your-client-id>
#>
param(
    [Parameter(Mandatory=$true)] [string] $Scope,
    [ValidateSet("json","tsv")] [string] $OutputFormat = "json",
    [Parameter(Mandatory=$true)] [string] $Tenant,
    [Parameter(Mandatory=$true)] [string] $ClientId,
    [string] $PfxPath = "$HOME/.config/m365ctl/m365ctl.pfx",
    [string] $KeychainService = "m365ctl:PfxPassword",
    [string] $KeychainAccount = "m365ctl",
    [string] $InternalDomainPattern = ""
)

$ErrorActionPreference = "Stop"

# Legacy fallbacks: prefer the new m365ctl-named PFX/Keychain account, but
# silently honour the historical fazla-od locations with a one-line stderr
# deprecation notice so existing installs keep working.
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

function Connect-SiteByUrl($url) {
    $pwd = Get-PfxPassword
    Connect-PnPOnline `
        -Tenant $Tenant `
        -ClientId $ClientId `
        -CertificatePath $PfxPath `
        -CertificatePassword $pwd `
        -Url $url | Out-Null
}

function Parse-Scope {
    param([string]$s)
    if ($s -like "site:*") {
        $ident = $s.Substring(5)
        if ($ident -match "^https?://") { return @{ Kind="site-url"; Value=$ident } }
        return @{ Kind="site-id"; Value=$ident }
    } elseif ($s -like "drive:*") {
        return @{ Kind="drive"; Value=$s.Substring(6) }
    } else {
        throw "Unsupported scope: $s (expected site:<id|url> or drive:<id>)"
    }
}

function Resolve-SiteUrl {
    param($parsed)
    if ($parsed.Kind -eq "site-url") { return $parsed.Value }
    # Connect to tenant admin to resolve the id -> url, then reconnect.
    $pwd = Get-PfxPassword
    $adminUrl = "https://$($Tenant.Split('-')[0])-admin.sharepoint.com"
    # Fall back: the caller typically supplies a URL in practice. If we only
    # have a site-id, the operator must pass it as site:<url> — document this.
    throw "site:<id> form requires an admin endpoint; please pass site:<full-url>."
}

function Emit-Row {
    param($row)
    if ($OutputFormat -eq "json") {
        return $row
    }
    # TSV
    "{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}" -f `
        $row.drive_id, $row.item_id, $row.full_path, $row.shared_with,
        $row.permission_level, $row.is_external, $row.expires_at
}

$parsed = Parse-Scope $Scope
if ($parsed.Kind -eq "drive") {
    throw "Drive-only audit not yet implemented; pass site:<url> for now."
}

$siteUrl = Resolve-SiteUrl $parsed
Connect-SiteByUrl $siteUrl

$rows = New-Object System.Collections.Generic.List[object]
$lists = Get-PnPList | Where-Object { $_.BaseTemplate -eq 101 }  # Document Library
foreach ($lst in $lists) {
    $items = Get-PnPListItem -List $lst -PageSize 1000 -Fields "FileRef","UniqueId"
    foreach ($it in $items) {
        $path = $it["FileRef"]
        $uid  = $it["UniqueId"]
        $perms = Get-PnPListItemPermission -List $lst -Identity $it.Id
        foreach ($p in $perms.Permissions) {
            $shared = $p.PrincipalName
            $isExternal = $false
            if ($shared -match "#ext#" -or $shared -match "@") {
                if ($InternalDomainPattern) {
                    $isExternal = ($shared -notmatch $InternalDomainPattern)
                } else {
                    $isExternal = $true
                }
            }
            $rows.Add([ordered]@{
                drive_id          = $lst.Id.ToString()
                item_id           = $uid.ToString()
                full_path         = $path
                shared_with       = $shared
                permission_level  = $p.Roles -join ","
                is_external       = $isExternal
                expires_at        = $p.ExpirationDateTime
            })
        }
    }
}

if ($OutputFormat -eq "json") {
    $rows | ConvertTo-Json -Depth 4 -Compress
} else {
    "drive_id`titem_id`tfull_path`tshared_with`tpermission_level`tis_external`texpires_at"
    foreach ($r in $rows) { Emit-Row $r }
}
