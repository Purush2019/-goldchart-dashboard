"""
Gold Trading Dashboard - Master Controller
=============================================
Manages the Gold chart server (Coinbase Derivatives) and serves
the dashboard UI with restart controls.

Ports:
  - 8090  Dashboard (this server)
  - 8080  Gold chart (gold_chart.py) - 1-second refresh

Usage:  python dashboard.py
"""

import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
import ctypes

# ─── Settings ───────────────────────────────────────────────
DASHBOARD_PORT = 8090
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")

SERVERS = {
    "yahoo": {
        "script": os.path.join(BASE_DIR, "gold_chart.py"),
        "port": 8080,
        "label": "Coinbase Gold (1s refresh)",
    },
}

# ─── Keep-Alive ─────────────────────────────────────────────
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_DISPLAY_REQUIRED  = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040

def keep_alive_loop():
    while True:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_AWAYMODE_REQUIRED
            )
            ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
        except Exception:
            pass
        time.sleep(30)

# ─── Process Manager ────────────────────────────────────────
processes = {}   # name -> subprocess.Popen
proc_lock = threading.Lock()


def kill_port(port):
    """Kill whatever is listening on a port."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid > 4:  # don't kill system
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                    )
    except Exception:
        pass


def stop_server(name):
    """Stop a managed server by name."""
    with proc_lock:
        proc = processes.get(name)
        if proc and proc.poll() is None:
            print(f"   🛑 Stopping {name} (PID {proc.pid})...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            processes.pop(name, None)

    # Also kill anything on the port as fallback
    cfg = SERVERS[name]
    kill_port(cfg["port"])
    time.sleep(0.5)


def start_server(name):
    """Start a managed server by name."""
    cfg = SERVERS[name]
    stop_server(name)

    print(f"   ▶ Starting {name} ({cfg['label']})...")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # Disable auto-open browser — dashboard handles navigation
    # We pass an env flag so the servers can check it
    env["DASHBOARD_MODE"] = "1"

    proc = subprocess.Popen(
        [PYTHON, cfg["script"]],
        cwd=BASE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        encoding="utf-8",
        errors="replace",
    )

    with proc_lock:
        processes[name] = proc

    # Log forwarder in background
    threading.Thread(
        target=_log_forwarder, args=(name, proc), daemon=True
    ).start()

    print(f"   ✓ {name} started (PID {proc.pid})")
    return proc.pid


def _log_forwarder(name, proc):
    """Forward child process stdout to dashboard console."""
    tag = f"[{name.upper():>8}]"
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if line:
                print(f"  {tag} {line}")
    except Exception:
        pass


def restart_server(name):
    """Restart a server."""
    stop_server(name)
    time.sleep(1)
    pid = start_server(name)
    return pid


def server_alive(name):
    """Check if a managed server process is still running."""
    with proc_lock:
        proc = processes.get(name)
        return proc is not None and proc.poll() is None


def watchdog_loop():
    """Automatically restart servers that die."""
    while True:
        time.sleep(10)
        for name in SERVERS:
            if not server_alive(name):
                print(f"\n   ⚠️  {name} is down — auto-restarting...")
                try:
                    start_server(name)
                except Exception as e:
                    print(f"   ❌ Failed to restart {name}: {e}")


# ─── HTTP Server (Dashboard + API) ──────────────────────────
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        # Serve dashboard.html at root
        if self.path == "/" or self.path == "/dashboard":
            self.path = "/dashboard.html"
            return super().do_GET()

        # Ignore favicon
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # API: status
        if self.path == "/api/status":
            status = {}
            for name in SERVERS:
                status[name] = {
                    "alive": server_alive(name),
                    "port": SERVERS[name]["port"],
                    "label": SERVERS[name]["label"],
                }
            self._json_response(status)
            return

        return super().do_GET()

    def do_POST(self):
        # API: restart
        if self.path.startswith("/api/restart/"):
            target = self.path.split("/")[-1]

            if target == "all":
                results = {}
                for name in SERVERS:
                    pid = restart_server(name)
                    results[name] = {"pid": pid, "alive": True}
                self._json_response({
                    "message": "\u2705 Server restarted",
                    "servers": results
                })
                return

            if target in SERVERS:
                pid = restart_server(target)
                self._json_response({
                    "message": f"✅ {target.upper()} restarted (PID {pid})",
                    "pid": pid,
                    "alive": True,
                })
                return

            self._json_response({"error": f"Unknown server: {target}"}, 400)
            return

        # API: stop
        if self.path.startswith("/api/stop/"):
            target = self.path.split("/")[-1]
            if target == "all":
                for name in SERVERS:
                    stop_server(name)
                self._json_response({"message": "\U0001f6d1 Server stopped"})
                return
            if target in SERVERS:
                stop_server(target)
                self._json_response({"message": f"🛑 {target.upper()} stopped"})
                return

        self.send_error(404)

    def _json_response(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress all logs — dashboard is quiet
        pass


# ─── Helpers ─────────────────────────────────────────────────
def get_local_ip():
    """Get the local network IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


_tunnel_proc = None   # cloudflared subprocess
_public_url = None    # public URL from Cloudflare


def start_cloudflare_tunnel(port):
    """Start a Cloudflare quick-tunnel. Returns public URL like https://xxx.trycloudflare.com"""
    global _tunnel_proc, _public_url
    import re

    # Kill any stale cloudflared processes
    try:
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True, timeout=5)
        time.sleep(1)
    except Exception:
        pass

    cloudflared = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if not os.path.exists(cloudflared):
        print("   ❌ cloudflared not found at", cloudflared)
        return None

    print(f"   Starting Cloudflare tunnel on port {port}...")
    _tunnel_proc = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )

    # Read output lines until we find the public URL (timeout 30s)
    url_pattern = re.compile(r"https://[\w-]+\.trycloudflare\.com")
    deadline = time.time() + 30

    def _drain_tunnel_output():
        global _public_url
        try:
            for line in iter(_tunnel_proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                m = url_pattern.search(line)
                if m and not _public_url:
                    _public_url = m.group(0)
                    print(f"   ✓ Cloudflare tunnel ready: {_public_url}")
        except Exception:
            pass

    t = threading.Thread(target=_drain_tunnel_output, daemon=True)
    t.start()

    # Wait for URL to appear
    while not _public_url and time.time() < deadline:
        time.sleep(0.5)

    return _public_url


def stop_tunnel():
    """Stop the Cloudflare tunnel."""
    global _tunnel_proc, _public_url
    if _tunnel_proc:
        try:
            _tunnel_proc.terminate()
            _tunnel_proc.wait(timeout=5)
        except Exception:
            try:
                _tunnel_proc.kill()
            except Exception:
                pass
        _tunnel_proc = None
    _public_url = None
    try:
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


# ─── Main ────────────────────────────────────────────────────
def main():
    local_ip = get_local_ip()

    print("""
    ============================================================
           GOLD TRADING DASHBOARD
    ============================================================

      COINBASE Derivatives - Gold Futures - 1s refresh

      Dashboard:  http://localhost:{port}
      Chart:      http://localhost:8080/chart.html

      Mobile (same WiFi):
        Dashboard:  http://{ip}:{port}
        Chart:      http://{ip}:8080/chart.html

      Auto-restart watchdog | Keep-alive | Restart button
    ============================================================
    """.format(port=DASHBOARD_PORT, ip=local_ip))

    # Keep-alive thread
    threading.Thread(target=keep_alive_loop, daemon=True).start()
    print("   🔋 Keep-alive active\n")

    # Start chart server
    print("── Starting chart server ──")
    for name in SERVERS:
        try:
            start_server(name)
        except Exception as e:
            print(f"   ❌ Failed to start {name}: {e}")
    print()

    # Watchdog thread
    threading.Thread(target=watchdog_loop, daemon=True).start()
    print("   🛡️  Watchdog active — auto-restarts crashed servers\n")

    # Dashboard HTTP server
    print(f"── Starting dashboard on port {DASHBOARD_PORT} ──")
    kill_port(DASHBOARD_PORT)
    time.sleep(0.3)
    httpd = http.server.HTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
    print(f"   ✓ Dashboard ready: http://0.0.0.0:{DASHBOARD_PORT}\n")

    # Open dashboard in browser
    import webbrowser
    webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")
    print("   OK Dashboard opened in browser!")

    # Cloudflare tunnel for remote/mobile access over internet
    print("\n-- Setting up Cloudflare tunnel for public internet access --")
    chart_tunnel_url = start_cloudflare_tunnel(8080)
    if chart_tunnel_url:
        print(f"")
        print(f"   ==========================================================")
        print(f"   PUBLIC URL (works from anywhere):")
        print(f"   Chart:  {chart_tunnel_url}/chart.html")
        print(f"   QR:     {chart_tunnel_url}/qr")
        print(f"   ==========================================================")
        print(f"")
        print(f"   Open {chart_tunnel_url}/qr on your PC to scan with phone!")
    else:
        print(f"   Tunnel failed. Use same-WiFi access:")
        print(f"   Chart: http://{local_ip}:8080/chart.html")
        print(f"   QR:    http://{local_ip}:8080/qr")
    print(f"\n   Press Ctrl+C to stop everything\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n   Shutting down...")
        for name in SERVERS:
            stop_server(name)
        httpd.shutdown()
        stop_tunnel()
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass
        print("   ✓ All servers stopped. Goodbye!\n")


if __name__ == "__main__":
    main()
