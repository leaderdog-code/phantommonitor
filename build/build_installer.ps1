# Builds PhantomMonitor-Setup.exe from build\dist\PhantomMonitor.exe.
#
#   powershell -ExecutionPolicy Bypass -File build\build_installer.ps1
#
# Needs Inno Setup: https://jrsoftware.org/isdl.php  (or: winget install --id
# JRSoftware.InnoSetup.7 -e). Run build\build.ps1 first to produce the exe.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root

$exe = Join-Path $root 'build\dist\PhantomMonitor.exe'
if (-not (Test-Path $exe)) {
    throw "build\dist\PhantomMonitor.exe not found - run build\build.ps1 first"
}

# Inno Setup 7 installs per-user by default; 6 usually went to Program Files.
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source }
if (-not $iscc) {
    throw "Inno Setup not found. Install it, or run: winget install --id JRSoftware.InnoSetup.7 -e"
}

Write-Host "compiler: $iscc" -ForegroundColor Cyan
& $iscc (Join-Path $root 'build\installer.iss')
if ($LASTEXITCODE -ne 0) { throw "installer compile failed" }

$setup = Join-Path $root 'build\dist\PhantomMonitor-Setup.exe'
$size = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $setup  ($size MB)" -ForegroundColor Green
Pop-Location
