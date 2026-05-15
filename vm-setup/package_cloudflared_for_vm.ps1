$ErrorActionPreference = "Stop"

$source = Join-Path $env:USERPROFILE ".cloudflared"
$projectRoot = Split-Path -Parent $PSScriptRoot
$output = Join-Path $projectRoot "cloudflared-vm-private.zip"

if (-not (Test-Path (Join-Path $source "config.yml"))) {
    throw "Missing $source\config.yml"
}

$credentialFiles = Get-ChildItem $source -Filter "*.json" -File
if (-not $credentialFiles) {
    throw "Missing Cloudflare tunnel credential JSON in $source"
}

if (Test-Path $output) {
    Remove-Item $output -Force
}

Compress-Archive -Path (Join-Path $source "*") -DestinationPath $output -Force

Write-Host "Created private tunnel credential package:"
Write-Host "  $output"
Write-Host ""
Write-Host "Do not commit this ZIP to GitHub. Copy it to the VM and extract it to:"
Write-Host "  C:\Users\<vm-user>\.cloudflared"
