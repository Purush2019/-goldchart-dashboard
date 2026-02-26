@echo off
REM ═══════════════════════════════════════════════════════════════
REM  Upload Gold Chart to Oracle Cloud VM
REM ═══════════════════════════════════════════════════════════════
REM  Usage:  deploy_to_oracle.bat <VM_PUBLIC_IP> <SSH_KEY_PATH>
REM
REM  Example:
REM    deploy_to_oracle.bat 129.213.45.67 C:\Users\purus\.ssh\oracle_key
REM ═══════════════════════════════════════════════════════════════

if "%~1"=="" (
    echo.
    echo  Usage: deploy_to_oracle.bat ^<VM_IP^> ^<SSH_KEY_PATH^>
    echo.
    echo  Example:
    echo    deploy_to_oracle.bat 129.213.45.67 C:\Users\purus\.ssh\oracle_key
    echo.
    exit /b 1
)

set VM_IP=%~1
set SSH_KEY=%~2
set VM_USER=ubuntu
set SCRIPT_DIR=%~dp0

echo.
echo  ========================================
echo   Uploading Gold Chart to %VM_IP%
echo  ========================================
echo.

REM Upload app files
echo  [1/3] Uploading application files...
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%..\gold_chart.py" %VM_USER%@%VM_IP%:~/
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%..\chart.html" %VM_USER%@%VM_IP%:~/
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%..\qr.html" %VM_USER%@%VM_IP%:~/
echo  [OK] App files uploaded

REM Upload deploy files
echo  [2/3] Uploading setup scripts...
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%setup_oracle.sh" %VM_USER%@%VM_IP%:~/
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%goldchart.service" %VM_USER%@%VM_IP%:~/
scp -i "%SSH_KEY%" -o StrictHostKeyChecking=no "%SCRIPT_DIR%nginx_goldchart.conf" %VM_USER%@%VM_IP%:~/
echo  [OK] Setup scripts uploaded

REM Run setup on the VM
echo  [3/3] Running setup on VM (this takes ~60 seconds)...
ssh -i "%SSH_KEY%" -o StrictHostKeyChecking=no %VM_USER%@%VM_IP% "chmod +x ~/setup_oracle.sh && sudo bash ~/setup_oracle.sh"

echo.
echo  ========================================
echo   DONE! Open in browser:
echo   http://%VM_IP%/chart.html
echo  ========================================
echo.
pause
