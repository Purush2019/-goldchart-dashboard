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

Copy this project to:

```text
C:\GoldChart\Playwright
```

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

