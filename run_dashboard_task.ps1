$ErrorActionPreference = "Stop"

Set-Location "C:\Users\purus\OneDrive\Documents\Playwright"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DASHBOARD_SKIP_TUNNELS = "1"

.\.venv\Scripts\python.exe -u dashboard.py *> dashboard_task.log
