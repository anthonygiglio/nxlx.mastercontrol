# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Starts the node signing service (tools/nxlx-signer.py, D139) on this Windows computer every time you log on.
# Run it in a PowerShell opened with "Run as administrator" (the firewall rule needs it):
#
#   powershell -ExecutionPolicy Bypass -File <this file> -From <the signer folder> [-Dir C:\Users\you\nxlx-signer]
#              [-Port 8781] [-HostName another-name.local]
#
# What it does:
#   * -From: copies the signer folder boxcert.py make-signer wrote into <Dir>. The pin file (signed.json and its .bak) is
#     copied only when <Dir> has none: the pins already here are the live ones and are never overwritten, also when
#     a new signer replaces the old one. Without -From, <Dir> must hold the signer already.
#   * copies nxlx-signer.py and boxcert.py into <Dir>\bin (so the service does not depend on this checkout);
#   * registers a scheduled task "NXLX node signer" that runs them with pythonw at your logon and restarts them if they
#     stop, with --host-name when -HostName is given (the service answers only its own names and addresses);
#   * opens TCP <Port> in the firewall for private networks only, starts the task, and prints the address for nodes
#     from the same name the service checks.
# Needs Python 3 for Windows (pythonw.exe on the path) and openssl (Git for Windows has one).
# To remove: Unregister-ScheduledTask "NXLX node signer"; Remove-NetFirewallRule -DisplayName "NXLX node signer".
param(
    [string]$From = "",
    [string]$Dir = (Join-Path $env:USERPROFILE "nxlx-signer"),
    [int]$Port = 8781,
    [string]$HostName = ""
)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Open PowerShell with 'Run as administrator' and run this again (the firewall rule needs it)." }
if ($HostName -and $HostName -notmatch '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$') { throw "-HostName must be a host name such as hp.local" }
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $pyw) { throw "pythonw.exe was not found: install Python 3 for Windows (python.org) and tick 'Add to PATH'." }
$py = Join-Path (Split-Path -Parent $pyw.Source) "python.exe"

if ($From) {
    foreach ($name in "signer.key", "signer.pem", "root.pem", "signed.json") {
        if (-not (Test-Path (Join-Path $From $name))) { throw "$From\$name is missing: -From must be the folder 'signer' that boxcert.py make-signer wrote." }
    }
    if (Get-ScheduledTask -TaskName "NXLX node signer" -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName "NXLX node signer" }
    New-Item -ItemType Directory -Force $Dir | Out-Null
    $livePins = Test-Path (Join-Path $Dir "signed.json")
    Get-ChildItem -File $From | Where-Object { $_.Name -notin @("signer.log", "signer.lock") } | ForEach-Object {
        if ($_.Name -like "signed.json*" -and $livePins) { return }
        Copy-Item $_.FullName (Join-Path $Dir $_.Name) -Force
    }
    if ($livePins) { Write-Output "Kept the pins already in $Dir (signed.json was not replaced)." }
}
foreach ($name in "signer.key", "signer.pem", "root.pem", "signed.json") {
    if (-not (Test-Path (Join-Path $Dir $name))) { throw "$Dir\$name is missing. Run this again with -From <the folder 'signer' that boxcert.py make-signer wrote>." }
}
$tools = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $Dir "bin"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $tools "nxlx-signer.py"), (Join-Path $tools "boxcert.py") $bin -Force

$script = Join-Path $bin "nxlx-signer.py"
$taskArgs = "`"{0}`" --dir `"{1}`" --port {2}" -f $script, $Dir, $Port
if ($HostName) { $taskArgs += " --host-name {0}" -f $HostName.ToLower() }
$action = New-ScheduledTaskAction -Execute $pyw.Source -Argument $taskArgs -WorkingDirectory $bin
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "NXLX node signer" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

if (-not (Get-NetFirewallRule -DisplayName "NXLX node signer" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "NXLX node signer" -Direction Inbound -Protocol TCP -LocalPort $Port -Profile Private -Action Allow | Out-Null
}
Start-ScheduledTask -TaskName "NXLX node signer"
Start-Sleep -Seconds 3
try {
    $info = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/v1/info" -f $Port) -TimeoutSec 5
    Write-Output ("The signer is running on port {0}, for the root {1}..." -f $Port, $info.fingerprint.Substring(0, 16))
} catch {
    Write-Output ("The task is registered but the signer did not answer yet; see {0}\signer.log" -f $Dir)
}
# the name the service itself answers to (nxlx-signer.py host_names: Python's host name, lower case, first label)
$own = (& $py -c "import socket; print((socket.gethostname() or '').strip().lower().split('.')[0])").Trim()
$name = if ($HostName) { $HostName.ToLower() } elseif ($own) { "$own.local" } else { "" }
if ($name) {
    Write-Output ("Nodes reach it at http://{0}:{1} (use this with install.sh --signer). The network must be marked Private in Windows." -f $name, $Port)
} else {
    Write-Output ("Nodes reach it at http://<this computer's address>:{0}. The network must be marked Private in Windows." -f $Port)
}
