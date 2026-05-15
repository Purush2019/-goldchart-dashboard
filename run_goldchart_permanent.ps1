$ErrorActionPreference = "Continue"

$root = "C:\Users\purus\OneDrive\Documents\Playwright"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"
$dashboardLog = Join-Path $root "dashboard_task.log"
$dashboardErrLog = Join-Path $root "dashboard_task_err.log"
$tunnelLog = Join-Path $root "cloudflared_task.log"
$tunnelErrLog = Join-Path $root "cloudflared_task_err.log"
$monitorLog = Join-Path $root "goldchart_monitor.log"

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

function Write-MonitorLog {
    param([string] $Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $monitorLog -Value "[$timestamp] $Message"
}

function Test-GoldChartTunnelHealthy {
    try {
        $info = & $cloudflared tunnel info goldchart 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-MonitorLog "Tunnel info failed: $($info.Trim())"
            return $false
        }
        if ($info -match "does not have any active connection") {
            Write-MonitorLog "Tunnel is unhealthy: no active Cloudflare connection"
            return $false
        }
        return $info -match "CONNECTOR ID"
    } catch {
        Write-MonitorLog "Tunnel health check error: $($_.Exception.Message)"
        return $false
    }
}

function Stop-GoldChartTunnel {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "cloudflared.exe" -and
            $_.CommandLine -like "*tunnel*" -and
            $_.CommandLine -like "*goldchart*"
        }

    foreach ($proc in $procs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-MonitorLog "Stopped stale cloudflared process PID $($proc.ProcessId)"
        } catch {
            Write-MonitorLog "Failed stopping cloudflared PID $($proc.ProcessId): $($_.Exception.Message)"
        }
    }
}

function Start-GoldChartTunnel {
    $hasTunnelProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "cloudflared.exe" -and
            $_.CommandLine -like "*tunnel*" -and
            $_.CommandLine -like "*goldchart*"
        }

    if ($hasTunnelProcess -and -not (Test-GoldChartTunnelHealthy)) {
        Stop-GoldChartTunnel
        Start-Sleep -Seconds 5
        $hasTunnelProcess = $false
    }

    if (-not $hasTunnelProcess) {
        Write-MonitorLog "Starting cloudflared tunnel"
        Start-Process -FilePath $cloudflared `
            -ArgumentList "tunnel", "--config", "$env:USERPROFILE\.cloudflared\config.yml", "run", "goldchart" `
            -WindowStyle Hidden `
            -RedirectStandardOutput $tunnelLog `
            -RedirectStandardError $tunnelErrLog
        Start-Sleep -Seconds 15
        if (Test-GoldChartTunnelHealthy) {
            Write-MonitorLog "Tunnel connected"
        } else {
            Write-MonitorLog "Tunnel still unhealthy after restart"
        }
    }
}

function Start-GoldChartDashboard {
    if (-not (Test-PortListening 8090)) {
        Write-MonitorLog "Starting dashboard"
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
