param(
  [Parameter(Mandatory=$true)][string]$File
)
$ErrorActionPreference = "Stop"
if (-not $env:WINDOWS_CERTIFICATE_PFX) { throw "WINDOWS_CERTIFICATE_PFX is required" }
if (-not $env:WINDOWS_CERTIFICATE_PASSWORD) { throw "WINDOWS_CERTIFICATE_PASSWORD is required" }
if (-not $env:WINDOWS_TIMESTAMP_URL) { throw "WINDOWS_TIMESTAMP_URL is required" }

$kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$signtool = Get-ChildItem $kits -Filter signtool.exe -Recurse -ErrorAction Stop |
  Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
  Sort-Object FullName -Descending |
  Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found in Windows SDK" }

& $signtool.FullName sign /fd SHA256 /td SHA256 /tr $env:WINDOWS_TIMESTAMP_URL /f $env:WINDOWS_CERTIFICATE_PFX /p $env:WINDOWS_CERTIFICATE_PASSWORD /d "WG Map Backporter Studio" $File
if ($LASTEXITCODE -ne 0) { throw "SignTool sign failed for $File" }
& $signtool.FullName verify /pa /all /v /tw $File
if ($LASTEXITCODE -ne 0) { throw "SignTool verify failed for $File" }
