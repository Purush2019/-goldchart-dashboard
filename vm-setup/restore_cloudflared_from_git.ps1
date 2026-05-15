param(
    [Parameter(Mandatory = $true)]
    [string] $Password
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$inputFile = Join-Path $projectRoot "cloudflared-vm-private.zip.enc"
$tempZip = Join-Path $env:TEMP "cloudflared-vm-private.zip"
$destination = Join-Path $env:USERPROFILE ".cloudflared"

if (-not (Test-Path $inputFile)) {
    throw "Missing encrypted package: $inputFile"
}

$bytes = [IO.File]::ReadAllBytes($inputFile)
$header = [Text.Encoding]::ASCII.GetString($bytes, 0, 6)
if ($header -ne "GCFLD1") {
    throw "Invalid encrypted package format."
}

$salt = New-Object byte[] 16
$iv = New-Object byte[] 16
[Array]::Copy($bytes, 6, $salt, 0, 16)
[Array]::Copy($bytes, 22, $iv, 0, 16)

$cipherLength = $bytes.Length - 38
$cipher = New-Object byte[] $cipherLength
[Array]::Copy($bytes, 38, $cipher, 0, $cipherLength)

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

try {
    $decryptor = $aes.CreateDecryptor()
    $plain = $decryptor.TransformFinalBlock($cipher, 0, $cipher.Length)
} catch {
    throw "Could not decrypt. Check the password."
}

[IO.File]::WriteAllBytes($tempZip, $plain)

if (Test-Path $destination) {
    Remove-Item $destination -Recurse -Force
}
New-Item -ItemType Directory -Force $destination | Out-Null
Expand-Archive -Path $tempZip -DestinationPath $destination -Force
Remove-Item $tempZip -Force

Write-Host "Restored Cloudflare tunnel credentials to:"
Write-Host "  $destination"
Write-Host ""
Write-Host "Verify:"
Write-Host "  Test-Path `"$destination\config.yml`""
