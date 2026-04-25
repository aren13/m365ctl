<#
.SYNOPSIS
  Manage mailbox delegation via Exchange Online PowerShell.

.DESCRIPTION
  Wrapper used by m365ctl mail-delegate. Outputs JSONL on stdout so the
  Python caller can parse cleanly.

.PARAMETER Mailbox
  Target mailbox UPN (the mailbox being delegated).

.PARAMETER Action
  One of: List, Grant, Revoke.

.PARAMETER Delegate
  Delegate UPN (required for Grant and Revoke).

.PARAMETER AccessRights
  Permission level. One of: FullAccess, SendAs, SendOnBehalf.
  Defaults to FullAccess.

.EXAMPLE
  pwsh -NoProfile -File Set-MailboxDelegate.ps1 -Mailbox team@example.com -Action List
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Mailbox,
  [Parameter(Mandatory=$true)][ValidateSet('List','Grant','Revoke')][string]$Action,
  [string]$Delegate,
  [ValidateSet('FullAccess','SendAs','SendOnBehalf')][string]$AccessRights = 'FullAccess'
)

$ErrorActionPreference = 'Stop'

# Connect-ExchangeOnline silently if not already connected.
if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
  Write-Error "ExchangeOnlineManagement module not installed. Install-Module ExchangeOnlineManagement -Scope CurrentUser"
  exit 2
}
try {
  Get-PSSession -ErrorAction SilentlyContinue | Where-Object { $_.ConfigurationName -eq 'Microsoft.Exchange' -and $_.State -eq 'Opened' } | Out-Null
  if (-not $?) { Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop }
} catch {
  Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop
}

function Emit-Json([object]$obj) {
  $obj | ConvertTo-Json -Compress -Depth 5
}

switch ($Action) {
  'List' {
    $perms = Get-MailboxPermission -Identity $Mailbox |
      Where-Object { -not $_.IsInherited -and $_.User -notmatch '^NT AUTHORITY\\' }
    foreach ($p in $perms) {
      Emit-Json @{
        kind          = 'FullAccess'
        mailbox       = $Mailbox
        delegate      = [string]$p.User
        access_rights = $p.AccessRights -join ','
        deny          = $p.Deny
      }
    }
    $sendas = Get-RecipientPermission -Identity $Mailbox -ErrorAction SilentlyContinue |
      Where-Object { $_.AccessRights -contains 'SendAs' }
    foreach ($s in $sendas) {
      Emit-Json @{
        kind          = 'SendAs'
        mailbox       = $Mailbox
        delegate      = [string]$s.Trustee
        access_rights = 'SendAs'
        deny          = $false
      }
    }
    $mbx = Get-Mailbox -Identity $Mailbox
    foreach ($g in @($mbx.GrantSendOnBehalfTo)) {
      if (-not $g) { continue }
      Emit-Json @{
        kind          = 'SendOnBehalf'
        mailbox       = $Mailbox
        delegate      = [string]$g
        access_rights = 'SendOnBehalf'
        deny          = $false
      }
    }
    exit 0
  }
  'Grant' {
    if (-not $Delegate) { Write-Error 'Grant requires -Delegate'; exit 2 }
    switch ($AccessRights) {
      'FullAccess' {
        Add-MailboxPermission -Identity $Mailbox -User $Delegate -AccessRights FullAccess -InheritanceType All -AutoMapping:$false | Out-Null
      }
      'SendAs' {
        Add-RecipientPermission -Identity $Mailbox -Trustee $Delegate -AccessRights SendAs -Confirm:$false | Out-Null
      }
      'SendOnBehalf' {
        Set-Mailbox -Identity $Mailbox -GrantSendOnBehalfTo @{Add=$Delegate} | Out-Null
      }
    }
    Emit-Json @{ status='ok'; action='Grant'; mailbox=$Mailbox; delegate=$Delegate; access_rights=$AccessRights }
    exit 0
  }
  'Revoke' {
    if (-not $Delegate) { Write-Error 'Revoke requires -Delegate'; exit 2 }
    switch ($AccessRights) {
      'FullAccess' {
        Remove-MailboxPermission -Identity $Mailbox -User $Delegate -AccessRights FullAccess -InheritanceType All -Confirm:$false | Out-Null
      }
      'SendAs' {
        Remove-RecipientPermission -Identity $Mailbox -Trustee $Delegate -AccessRights SendAs -Confirm:$false | Out-Null
      }
      'SendOnBehalf' {
        Set-Mailbox -Identity $Mailbox -GrantSendOnBehalfTo @{Remove=$Delegate} | Out-Null
      }
    }
    Emit-Json @{ status='ok'; action='Revoke'; mailbox=$Mailbox; delegate=$Delegate; access_rights=$AccessRights }
    exit 0
  }
}
