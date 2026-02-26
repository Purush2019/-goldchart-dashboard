#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  GOLD CHART — Oracle Cloud Free Tier Setup Script
# ═══════════════════════════════════════════════════════════════════
#  Run this on your Oracle Cloud VM (Ubuntu 22.04 / Oracle Linux)
#
#  Usage:
#    chmod +x setup_oracle.sh
#    sudo bash setup_oracle.sh
#
#  What this does:
#    1. Installs Python 3.11+, nginx, and dependencies
#    2. Creates /opt/goldchart with a virtual environment
#    3. Copies your app files (gold_chart.py, chart.html, qr.html)
#    4. Sets up nginx as reverse proxy (port 80 → 8080)
#    5. Creates a systemd service for auto-start on boot
#    6. Opens firewall ports (80)
#    7. Starts everything
#
#  After running, your chart will be at:
#    http://<YOUR-PUBLIC-IP>/chart.html
# ═══════════════════════════════════════════════════════════════════

set -e  # Exit on any error

echo ""
echo "  ╔═══════════════════════════════════════════════════════╗"
echo "  ║     GOLD CHART — Oracle Cloud Setup                  ║"
echo "  ║     Setting up 24/7 gold chart server                ║"
echo "  ╚═══════════════════════════════════════════════════════╝"
echo ""

APP_DIR="/opt/goldchart"
APP_USER="goldchart"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Step 1: Detect OS ──
echo "── Step 1: Detecting OS ──"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "   OS: $PRETTY_NAME"
else
    echo "   Could not detect OS, assuming Ubuntu-like"
    ID="ubuntu"
fi

# ── Step 2: Install system packages ──
echo ""
echo "── Step 2: Installing system packages ──"

if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv nginx psmisc curl > /dev/null 2>&1
    echo "   [OK] Python3, nginx, pip installed (apt)"

elif [[ "$ID" == "ol" || "$ID" == "centos" || "$ID" == "rhel" || "$ID" == "almalinux" ]]; then
    # Oracle Linux / CentOS / RHEL
    dnf install -y python3 python3-pip nginx psmisc curl > /dev/null 2>&1 || \
    yum install -y python3 python3-pip nginx psmisc curl > /dev/null 2>&1
    echo "   [OK] Python3, nginx, pip installed (dnf/yum)"
else
    echo "   WARNING: Unknown OS '$ID' — trying apt then dnf..."
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv nginx psmisc curl > /dev/null 2>&1 || \
    dnf install -y python3 python3-pip nginx psmisc curl > /dev/null 2>&1
fi

PYTHON3=$(command -v python3)
echo "   Python: $($PYTHON3 --version)"

# ── Step 3: Create app user and directory ──
echo ""
echo "── Step 3: Creating app directory ──"

# Create system user (no login shell)
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$APP_DIR" "$APP_USER"
    echo "   [OK] Created user: $APP_USER"
else
    echo "   [OK] User $APP_USER already exists"
fi

mkdir -p "$APP_DIR"
echo "   [OK] App directory: $APP_DIR"

# ── Step 4: Copy application files ──
echo ""
echo "── Step 4: Copying application files ──"

# Check if files are in the same directory as this script
for file in gold_chart.py chart.html qr.html; do
    if [ -f "$SCRIPT_DIR/../$file" ]; then
        cp "$SCRIPT_DIR/../$file" "$APP_DIR/"
        echo "   [OK] Copied $file"
    elif [ -f "$SCRIPT_DIR/$file" ]; then
        cp "$SCRIPT_DIR/$file" "$APP_DIR/"
        echo "   [OK] Copied $file"
    elif [ -f "$APP_DIR/$file" ]; then
        echo "   [OK] $file already in $APP_DIR"
    else
        echo "   ERROR: Cannot find $file!"
        echo "   Please copy gold_chart.py, chart.html, qr.html to $APP_DIR or $SCRIPT_DIR"
        exit 1
    fi
done

# ── Step 5: Create Python virtual environment ──
echo ""
echo "── Step 5: Setting up Python virtual environment ──"

if [ ! -d "$APP_DIR/venv" ]; then
    $PYTHON3 -m venv "$APP_DIR/venv"
    echo "   [OK] Created venv at $APP_DIR/venv"
else
    echo "   [OK] Venv already exists"
fi

# Install dependencies
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install numpy websockets -q
echo "   [OK] Installed: numpy, websockets"
echo "   Python: $("$APP_DIR/venv/bin/python" --version)"

# ── Step 6: Set permissions ──
echo ""
echo "── Step 6: Setting permissions ──"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
echo "   [OK] Ownership: $APP_USER:$APP_USER"

# ── Step 7: Install systemd service ──
echo ""
echo "── Step 7: Installing systemd service ──"

if [ -f "$SCRIPT_DIR/goldchart.service" ]; then
    cp "$SCRIPT_DIR/goldchart.service" /etc/systemd/system/goldchart.service
else
    # Create it inline if the file wasn't found
    cat > /etc/systemd/system/goldchart.service << 'SVCEOF'
[Unit]
Description=Gold Chart - Coinbase Futures (Live)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=goldchart
Group=goldchart
WorkingDirectory=/opt/goldchart
ExecStart=/opt/goldchart/venv/bin/python gold_chart.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=DASHBOARD_MODE=1
LimitNOFILE=65536
MemoryMax=512M
StandardOutput=journal
StandardError=journal
SyslogIdentifier=goldchart

[Install]
WantedBy=multi-user.target
SVCEOF
fi

systemctl daemon-reload
systemctl enable goldchart
echo "   [OK] Service installed and enabled (auto-starts on boot)"

# ── Step 8: Configure nginx reverse proxy ──
echo ""
echo "── Step 8: Configuring nginx ──"

# Remove default site
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true

# Determine nginx config directory
if [ -d /etc/nginx/sites-available ]; then
    NGINX_CONF="/etc/nginx/sites-available/goldchart.conf"
    NGINX_LINK="/etc/nginx/sites-enabled/goldchart.conf"
else
    NGINX_CONF="/etc/nginx/conf.d/goldchart.conf"
    NGINX_LINK=""
fi

if [ -f "$SCRIPT_DIR/nginx_goldchart.conf" ]; then
    cp "$SCRIPT_DIR/nginx_goldchart.conf" "$NGINX_CONF"
else
    cat > "$NGINX_CONF" << 'NGXEOF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
}
NGXEOF
fi

# Create symlink if sites-enabled style
if [ -n "$NGINX_LINK" ]; then
    ln -sf "$NGINX_CONF" "$NGINX_LINK"
fi

# Test nginx config
nginx -t 2>/dev/null
echo "   [OK] Nginx configured (port 80 -> 8080 with WebSocket support)"

# ── Step 9: Open firewall ──
echo ""
echo "── Step 9: Opening firewall ports ──"

# iptables (Oracle Linux default)
if command -v iptables &>/dev/null; then
    iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
    iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
    # Save rules
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save 2>/dev/null || true
    elif [ -f /etc/sysconfig/iptables ]; then
        iptables-save > /etc/sysconfig/iptables 2>/dev/null || true
    fi
    echo "   [OK] iptables: port 80, 443 open"
fi

# firewalld (if active)
if command -v firewall-cmd &>/dev/null && systemctl is-active firewalld &>/dev/null; then
    firewall-cmd --permanent --add-service=http 2>/dev/null || true
    firewall-cmd --permanent --add-service=https 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
    echo "   [OK] firewalld: http/https open"
fi

# ufw (Ubuntu)
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp 2>/dev/null || true
    ufw allow 443/tcp 2>/dev/null || true
    echo "   [OK] ufw: port 80, 443 open"
fi

# ── Step 10: Start services ──
echo ""
echo "── Step 10: Starting services ──"

systemctl restart nginx
systemctl start goldchart

sleep 3

# Check if running
if systemctl is-active --quiet goldchart; then
    echo "   [OK] goldchart service is RUNNING"
else
    echo "   WARNING: goldchart service may not be running yet"
    echo "   Check logs: sudo journalctl -u goldchart -f"
fi

if systemctl is-active --quiet nginx; then
    echo "   [OK] nginx is RUNNING"
fi

# ── Get public IP ──
PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || curl -s -4 icanhazip.com 2>/dev/null || echo "<YOUR-PUBLIC-IP>")

echo ""
echo "  ╔═══════════════════════════════════════════════════════════╗"
echo "  ║                    SETUP COMPLETE!                        ║"
echo "  ╠═══════════════════════════════════════════════════════════╣"
echo "  ║                                                           ║"
echo "  ║  Chart URL:  http://$PUBLIC_IP/chart.html"
echo "  ║  QR Page:    http://$PUBLIC_IP/qr"
echo "  ║                                                           ║"
echo "  ║  The chart is now running 24/7!                           ║"
echo "  ║  It auto-starts on VM reboot.                             ║"
echo "  ║                                                           ║"
echo "  ║  Useful commands:                                         ║"
echo "  ║    sudo systemctl status goldchart                        ║"
echo "  ║    sudo journalctl -u goldchart -f     (live logs)        ║"
echo "  ║    sudo systemctl restart goldchart     (restart)         ║"
echo "  ║                                                           ║"
echo "  ╠═══════════════════════════════════════════════════════════╣"
echo "  ║  IMPORTANT: Open port 80 in Oracle Cloud Console!        ║"
echo "  ║  VCN > Security Lists > Add Ingress Rule:                ║"
echo "  ║    Source: 0.0.0.0/0  Protocol: TCP  Port: 80            ║"
echo "  ╚═══════════════════════════════════════════════════════════╝"
echo ""
