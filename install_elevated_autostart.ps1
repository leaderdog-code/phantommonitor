# Registers PhantomMonitor to start at logon with highest privileges.
#
# Why bother: Windows' UIPI stops a normal-privilege process from moving a
# window that belongs to an elevated app. Without this, an admin Command Prompt
# stranded on a blocked display cannot be rescued. Task Scheduler is the only
# way to auto-start elevated at logon without a UAC prompt every time.
#
# Run once, from an ADMIN PowerShell:
#   powershell -ExecutionPolicy Bypass -File install_elevated_autostart.ps1
# Undo with:
#   Unregister-ScheduledTask -TaskName PhantomMonitor -Confirm:$false

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "This needs an elevated PowerShell. Right-click PowerShell -> Run as administrator, then re-run." -ForegroundColor Yellow
    exit 1
}

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $appDir 'phantommonitor.py'
if (-not (Test-Path $script)) { throw "phantommonitor.py not found in $appDir" }

# pythonw.exe runs without a console window. Prefer PATH, then the usual spots.
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $pyw = Get-ChildItem -ErrorAction SilentlyContinue -Path @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\pythonw.exe"
        "$env:ProgramFiles\Python3*\pythonw.exe"
    ) | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $pyw) { throw "pythonw.exe not found. Install Python, or edit `$pyw in this script." }

Write-Host "python : $pyw"
Write-Host "script : $script"

$action    = New-ScheduledTaskAction -Execute $pyw -Argument ('"{0}"' -f $script) -WorkingDirectory $appDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Register-ScheduledTask -TaskName 'PhantomMonitor' -Description 'Keeps windows off blocked displays' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task 'PhantomMonitor' (runs elevated at logon)." -ForegroundColor Green

# The Startup shortcut would launch a second, non-elevated copy - remove it.
$startupVbs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\PhantomMonitor.vbs'
if (Test-Path $startupVbs) {
    Remove-Item $startupVbs -Force
    Write-Host "Removed the non-elevated Startup launcher so only one copy runs." -ForegroundColor Green
}

Write-Host "Starting it now..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName 'PhantomMonitor'
