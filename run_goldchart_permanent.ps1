$ErrorActionPreference = "Continue"

$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$cloudflaredCandidates = @(
    "C:\Program Files\cloudflared\cloudflared.exe",
    "C:\Program Files (x86)\cloudflared\cloudflared.exe"
)
$cloudflared = $cloudflaredCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
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

function Ensure-Cloudflared {
    if ($cloudflared) {
        return
    }

    $installDir = "C:\Program Files\cloudflared"
    New-Item -ItemType Directory -Force $installDir | Out-Null
    $script:cloudflared = Join-Path $installDir "cloudflared.exe"
    Write-MonitorLog "Downloading cloudflared"
    Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $script:cloudflared
}

function Ensure-CloudflaredConfig {
    $config = Join-Path $env:USERPROFILE ".cloudflared\config.yml"
    if (-not (Test-Path $config)) {
        Write-MonitorLog "Missing $config. Copy .cloudflared from the laptop to this VM user profile."
        return $false
    }
    return $true
}

function Ensure-PythonEnv {
    if (-not (Test-Path $python)) {
        Write-MonitorLog "Creating Python virtual environment"
        python -m venv (Join-Path $root ".venv")
    }

    if (-not (Test-Path $python)) {
        Write-MonitorLog "Missing Python executable: $python"
        return $false
    }

    if (-not (Test-Path (Join-Path $root ".venv\.deps-installed"))) {
        Write-MonitorLog "Installing Python dependencies"
        & $python -m pip install --upgrade pip
        & $python -m pip install playwright websockets requests pandas numpy
        & $python -m playwright install chromium
        Set-Content -Path (Join-Path $root ".venv\.deps-installed") -Value (Get-Date).ToString("s")
    }

    return $true
}

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
    Ensure-Cloudflared
    if (-not (Ensure-CloudflaredConfig)) {
        return
    }

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
    if (-not (Ensure-PythonEnv)) {
        return
    }

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
