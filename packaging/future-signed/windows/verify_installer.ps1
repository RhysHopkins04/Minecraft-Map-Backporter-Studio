param(
  [Parameter(Mandatory=$true)][string]$Installer,
  [string]$ExpectedPublisher = ""
)
$ErrorActionPreference = "Stop"
$kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$signtool = Get-ChildItem $kits -Filter signtool.exe -Recurse -ErrorAction Stop |
  Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
  Sort-Object FullName -Descending |
  Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found in Windows SDK" }

& $signtool.FullName verify /pa /all /v /tw $Installer
if ($LASTEXITCODE -ne 0) { throw "Installer Authenticode verification failed" }
$sig = Get-AuthenticodeSignature $Installer
if ($sig.Status -ne 'Valid') { throw "Get-AuthenticodeSignature returned $($sig.Status)" }
if ($ExpectedPublisher -and $sig.SignerCertificate.Subject -notlike "*$ExpectedPublisher*") {
  throw "Unexpected signer subject: $($sig.SignerCertificate.Subject)"
}
Write-Host "Installer verification passed. Signer: $($sig.SignerCertificate.Subject)"
