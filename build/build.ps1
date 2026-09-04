# Builds PhantomMonitor.exe - a single file with Python bundled inside, so
# users need nothing installed. Run from anywhere:
#
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
#
# Output: build\dist\PhantomMonitor.exe

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root

Write-Host "Checking build tools..." -ForegroundColor Cyan
python -m pip install --quiet --upgrade pyinstaller pywin32 pillow
if ($LASTEXITCODE -ne 0) { throw "could not install build dependencies" }

Write-Host "Building..." -ForegroundColor Cyan
# Absolute paths: --specpath makes PyInstaller resolve relative paths against
# the spec folder, so build\app.ico would be looked for at build\build\app.ico.
$icon = Join-Path $root 'build\app.ico'
python -m PyInstaller `
    --noconfirm `
    --onefile `
    --noconsole `
    --name PhantomMonitor `
    --icon "$icon" `
    --distpath (Join-Path $root 'build\dist') `
    --workpath (Join-Path $root 'build\work') `
    --specpath (Join-Path $root 'build') `
    (Join-Path $root 'phantommonitor.py')

if ($LASTEXITCODE -ne 0) { throw "build failed" }

$exe = Join-Path $root 'build\dist\PhantomMonitor.exe'
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe  ($size MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Smoke test:" -ForegroundColor Cyan
& $exe --list
Pop-Location
