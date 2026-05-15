$ErrorActionPreference = "Stop"

$ProjectSource = "C:\GoldChart\Playwright"
$CloudflaredDir = "C:\Program Files\cloudflared"
$CloudflaredExe = Join-Path $CloudflaredDir "cloudflared.exe"
$CloudflaredUserDir = Join-Path $env:USERPROFILE ".cloudflared"
$Launcher = Join-Path $ProjectSource "run_goldchart_permanent.ps1"

function Require-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated PowerShell window."
    }
}

function Install-Chrome {
    if (Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe") {
        Write-Host "Chrome already installed"
        return
    }

    $installer = Join-Path $env:TEMP "ChromeSetup.exe"
    Invoke-WebRequest "https://dl.google.com/chrome/install/latest/chrome_installer.exe" -OutFile $installer
    Start-Process $installer -ArgumentList "/silent", "/install" -Wait
}

function Install-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Host "Python already available"
        return
    }

    $installer = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.13.3/python-3.13.3-amd64.exe" -OutFile $installer
    Start-Process $installer -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0" -Wait
}

function Install-Cloudflared {
    New-Item -ItemType Directory -Force $CloudflaredDir | Out-Null
    if (-not (Test-Path $CloudflaredExe)) {
        Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $CloudflaredExe
    }
}

function Install-ProjectDeps {
    Set-Location $ProjectSource
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        python -m venv .venv
    }

    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install playwright websockets requests pandas numpy
    .\.venv\Scripts\python.exe -m playwright install chromium
}

function Install-CloudflaredService {
    if (-not (Test-Path (Join-Path $CloudflaredUserDir "config.yml"))) {
        throw "Missing $CloudflaredUserDir\config.yml. Copy your .cloudflared folder to this VM first."
    }

    $svc = Get-Service cloudflared -ErrorAction SilentlyContinue
    if (-not $svc) {
        & $CloudflaredExe service install
    }

    Set-Service cloudflared -StartupType Automatic
    Start-Service cloudflared
}

function Install-DashboardTask {
    if (-not (Test-Path $Launcher)) {
        throw "Missing launcher: $Launcher"
    }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`"" `
        -WorkingDirectory $ProjectSource

    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName "GoldChartDashboard" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Runs GoldChart dashboard and trading automation" `
        -Force | Out-Null

    Start-ScheduledTask -TaskName "GoldChartDashboard"
}

function Disable-Sleep {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
}

Require-Admin
Install-Chrome
Install-Python
Install-Cloudflared
Install-ProjectDeps
Install-CloudflaredService
Install-DashboardTask
Disable-Sleep

Write-Host ""
Write-Host "GoldChart VM setup complete."
Write-Host "Check:"
Write-Host "  http://localhost:8090"
Write-Host "  https://dash.goldchart.win"
