# GoldChart Windows VM Setup

Use a Windows Server cloud VM when the dashboard, Chrome, and trading automation must keep running while the laptop is off.

## Recommended VM

- Windows Server 2022
- 2 vCPU minimum
- 8 GB RAM preferred
- 50 GB SSD
- Public IP
- RDP enabled

## Files To Copy To The VM

Clone or copy this project anywhere on the VM, for example:

```text
C:\GoldChart\Playwright
```

The setup script now detects the project folder automatically from its own location.

Also copy your Cloudflare tunnel credentials folder from the laptop:

```text
C:\Users\purus\.cloudflared
```

to the VM user's profile:

```text
C:\Users\<vm-user>\.cloudflared
```

That folder should include:

```text
config.yml
f3893509-af50-4f84-a0ce-9aa4102d7a2f.json
```

Do not commit `.cloudflared` to GitHub. It contains tunnel credentials. Copy it directly to the VM user profile.

## Plus500 Login

The repository does not store Plus500 credentials. Use one of these options on the VM.

Option A: login manually through the Chrome window the first time. The session is saved in:

```text
C:\Users\<vm-user>\.plus500_profile
```

Option B: set environment variables for the VM user:

```powershell
[Environment]::SetEnvironmentVariable("PLUS500_USER", "your-email", "User")
[Environment]::SetEnvironmentVariable("PLUS500_PASS", "your-password", "User")
```

Then sign out/sign in or restart the dashboard task so the variables are loaded.

## Missing `.venv\Scripts\python.exe`

Do not copy `.venv` from the laptop and do not commit it to GitHub. On the VM, run:

```powershell
cd C:\path\to\-goldchart-dashboard\vm-setup
.\setup_goldchart_windows_vm.ps1
```

The setup script creates `.venv`, installs dependencies, and installs Playwright Chromium.

If you only want to start the app without the full setup script, run this from the project root:

```powershell
.\run_goldchart_permanent.ps1
```

That launcher also creates `.venv` if it is missing.

## Missing `.cloudflared\config.yml`

This file is intentionally not in GitHub. Copy the folder directly from the laptop to the VM user profile:

```powershell
robocopy "$env:USERPROFILE\.cloudflared" "C:\Users\<vm-user>\.cloudflared" /E
```

Replace `<vm-user>` with the Windows username on the VM. After copying, verify:

```powershell
Test-Path "$env:USERPROFILE\.cloudflared\config.yml"
Test-Path "$env:USERPROFILE\.cloudflared\f3893509-af50-4f84-a0ce-9aa4102d7a2f.json"
```

## Run Setup

Open PowerShell as Administrator on the VM:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
cd C:\GoldChart\Playwright\vm-setup
.\setup_goldchart_windows_vm.ps1
```

## After Setup

Open Chrome through RDP and log into Plus500 once. Then start the dashboard/trader from:

```text
https://dash.goldchart.win
```

The setup installs:

- Chrome
- Python virtual environment
- Playwright Chromium
- cloudflared as a Windows service
- GoldChart dashboard as a startup scheduled task
- no sleep/hibernate while plugged in
