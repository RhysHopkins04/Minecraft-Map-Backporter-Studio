param(
  [Parameter(Mandatory=$true)][string]$Executable
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
  throw "Packaged executable is missing: $Executable"
}

$resolved = (Resolve-Path -LiteralPath $Executable).Path
Write-Host "Running packaged self-test with synchronous process wait: $resolved"

$process = Start-Process `
  -FilePath $resolved `
  -ArgumentList '--self-test' `
  -Wait `
  -PassThru

if ($process.ExitCode -ne 0) {
  throw "Packaged executable self-test failed with exit code $($process.ExitCode)"
}

Write-Host "Packaged executable self-test passed with exit code 0."
