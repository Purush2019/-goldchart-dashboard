param(
    [Parameter(Mandatory = $true)]
    [string] $Password
)

$ErrorActionPreference = "Stop"

$source = Join-Path $env:USERPROFILE ".cloudflared"
$projectRoot = Split-Path -Parent $PSScriptRoot
$tempZip = Join-Path $env:TEMP "cloudflared-vm-private.zip"
$output = Join-Path $projectRoot "cloudflared-vm-private.zip.enc"

if (-not (Test-Path (Join-Path $source "config.yml"))) {
    throw "Missing $source\config.yml"
}

$credentialFiles = Get-ChildItem $source -Filter "*.json" -File
if (-not $credentialFiles) {
    throw "Missing Cloudflare tunnel credential JSON in $source"
}

if (Test-Path $tempZip) {
    Remove-Item $tempZip -Force
}
if (Test-Path $output) {
    Remove-Item $output -Force
}

Compress-Archive -Path (Join-Path $source "*") -DestinationPath $tempZip -Force

$salt = New-Object byte[] 16
$iv = New-Object byte[] 16
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($salt)
$rng.GetBytes($iv)

$kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new(
    $Password,
    $salt,
    200000,
    [Security.Cryptography.HashAlgorithmName]::SHA256
)

$aes = [Security.Cryptography.Aes]::Create()
$aes.KeySize = 256
$aes.Key = $kdf.GetBytes(32)
$aes.IV = $iv
$aes.Mode = [Security.Cryptography.CipherMode]::CBC
$aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7

$plain = [IO.File]::ReadAllBytes($tempZip)
$encryptor = $aes.CreateEncryptor()
$cipher = $encryptor.TransformFinalBlock($plain, 0, $plain.Length)

$header = [Text.Encoding]::ASCII.GetBytes("GCFLD1")
$bytes = New-Object byte[] ($header.Length + $salt.Length + $iv.Length + $cipher.Length)
[Array]::Copy($header, 0, $bytes, 0, $header.Length)
[Array]::Copy($salt, 0, $bytes, $header.Length, $salt.Length)
[Array]::Copy($iv, 0, $bytes, $header.Length + $salt.Length, $iv.Length)
[Array]::Copy($cipher, 0, $bytes, $header.Length + $salt.Length + $iv.Length, $cipher.Length)

[IO.File]::WriteAllBytes($output, $bytes)
Remove-Item $tempZip -Force

Write-Host "Created encrypted Cloudflare credential package:"
Write-Host "  $output"
Write-Host ""
Write-Host "Commit only this .enc file, never the raw .zip or .cloudflared folder."
