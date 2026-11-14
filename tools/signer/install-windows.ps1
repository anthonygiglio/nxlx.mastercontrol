# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Starts the node signing service (tools/nxlx-signer.py, D139) on this Windows computer every time you log on.
# Run it ONCE, in a PowerShell opened with "Run as administrator" (the firewall rule needs it):
#
#   powershell -ExecutionPolicy Bypass -File <this file> [-Dir C:\Users\you\nxlx-signer] [-Port 8781]
#
# What it does: copies nxlx-signer.py and boxcert.py into <Dir>\bin (so the service does not depend on this
# checkout), registers a scheduled task "NXLX node signer" that runs them with pythonw at your logon and restarts
# them if they stop, opens TCP <Port> in the firewall for private networks only, and starts the task now.
# Needs Python 3 for Windows (pythonw.exe on the path) and openssl (Git for Windows has one).
# To remove: Unregister-ScheduledTask "NXLX node signer"; Remove-NetFirewallRule -DisplayName "NXLX node signer".
param(
    [string]$Dir = (Join-Path $env:USERPROFILE "nxlx-signer"),
    [int]$Port = 8781
)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Open PowerShell with 'Run as administrator' and run this again (the firewall rule needs it)." }
foreach ($name in "signer.key", "signer.pem", "root.pem") {
    if (-not (Test-Path (Join-Path $Dir $name))) { throw "$Dir\$name is missing. Copy the folder 'signer' that boxcert.py make-signer wrote to $Dir first." }
}
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $pyw) { throw "pythonw.exe was not found: install Python 3 for Windows (python.org) and tick 'Add to PATH'." }
$tools = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $Dir "bin"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $tools "nxlx-signer.py"), (Join-Path $tools "boxcert.py") $bin -Force

$script = Join-Path $bin "nxlx-signer.py"
$action = New-ScheduledTaskAction -Execute $pyw.Source -Argument ("`"{0}`" --dir `"{1}`" --port {2}" -f $script, $Dir, $Port) -WorkingDirectory $bin
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
Write-Output ("Nodes reach it at http://{0}.local:{1} (or this computer's address). The network must be marked Private in Windows." -f $env:COMPUTERNAME.ToLower(), $Port)
