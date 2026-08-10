param(
  [Parameter(Mandatory=$true)][string]$Installer
)
$ErrorActionPreference = "Stop"
$target = Join-Path $env:LOCALAPPDATA "Programs\WG Map Backporter Studio"

if (Test-Path $target) {
  Remove-Item -Recurse -Force $target
}

$p = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Installer failed with exit code $($p.ExitCode)" }

$exe = Join-Path $target "WGMapBackporterStudio.exe"
if (-not (Test-Path $exe)) { throw "Installed executable missing: $exe" }

& "$PSScriptRoot\verify_packaged_exe.ps1" -Executable $exe

$uninstaller = Get-ChildItem $target -Filter 'unins*.exe' -ErrorAction Stop | Select-Object -First 1
if (-not $uninstaller) { throw "Inno Setup uninstaller was not installed" }

$p = Start-Process -FilePath $uninstaller.FullName -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($p.ExitCode)" }
if (Test-Path $exe) { throw "Executable remained after uninstall" }

Write-Host "Windows community installer / packaged self-test / uninstall smoke test passed."
