$ErrorActionPreference = "Stop"

$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $root

.\run_goldchart_permanent.ps1
