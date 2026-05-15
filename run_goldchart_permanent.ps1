$ErrorActionPreference = "Continue"

$root = "C:\Users\purus\OneDrive\Documents\Playwright"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"
$dashboardLog = Join-Path $root "dashboard_task.log"
$dashboardErrLog = Join-Path $root "dashboard_task_err.log"
$tunnelLog = Join-Path $root "cloudflared_task.log"
$tunnelErrLog = Join-Path $root "cloudflared_task_err.log"

Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DASHBOARD_SKIP_TUNNELS = "1"

function Test-PortListening {
    param([int] $Port)
    try {
        return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    } catch {
        $lines = netstat -ano | Select-String ":$Port"
        return [bool]($lines | Where-Object { $_ -match "LISTENING" })
    }
}

function Start-GoldChartTunnel {
    if (-not (Get-Process cloudflared -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $cloudflared `
            -ArgumentList "tunnel", "--config", "$env:USERPROFILE\.cloudflared\config.yml", "run", "goldchart" `
            -WindowStyle Hidden `
            -RedirectStandardOutput $tunnelLog `
            -RedirectStandardError $tunnelErrLog
    }
}

function Start-GoldChartDashboard {
    if (-not (Test-PortListening 8090)) {
        Start-Process -FilePath $python `
            -ArgumentList "-u", "dashboard.py" `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $dashboardLog `
            -RedirectStandardError $dashboardErrLog
    }
}

while ($true) {
    Start-GoldChartTunnel
    Start-GoldChartDashboard
    Start-Sleep -Seconds 30
}
