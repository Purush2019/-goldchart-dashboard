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
import asyncio
import ctypes
import urllib.request
import urllib.parse

# Force UTF-8 output on Windows to avoid emoji encode errors
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── Settings ───────────────────────────────────────────────
DASHBOARD_PORT = 8090
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")

SERVERS = {
    "coinbase": {
        "script": os.path.join(BASE_DIR, "gold_chart_coinbase.py"),
        "port": 8081,
        "label": "Coinbase Gold (5s refresh)",
    },
}

# ─── Tunnel Settings ────────────────────────────────────────
TUNNEL_DOMAIN = "goldchart.win"
DASHBOARD_TUNNEL_NAME = "goldchart-dash"  # New tunnel for dashboard
SKIP_TUNNELS = os.environ.get("DASHBOARD_SKIP_TUNNELS", "").lower() in ("1", "true", "yes")
GCP_DEPLOY_FILES = [
    "chart_coinbase.html",
    "gold_chart_coinbase.py",
    "qr.html",
]

# ─── GCP Server Configuration ────────────────────────────────
GCP_VM_IP = "34.55.216.69"
GCP_VM_USER = "ubuntu"  # ⚠️  Verify this is correct - usually 'ubuntu', 'root', or your username
GCP_APP_DIR = "/opt/goldchart"
GCP_SERVICE = "goldchart"
GCP_SSH_KEY = os.path.expanduser("~/.ssh/gcp_key")

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

# ─── Plus500 Auto-Trader (async bridge) ────────────────────
import plus500_trader

_trader_loop = asyncio.new_event_loop()
_trader_log_entries = []  # [{msg, cls}]


def _run_trader_loop():
    """Run asyncio event loop for the trader in a background thread."""
    asyncio.set_event_loop(_trader_loop)
    _trader_loop.run_forever()


threading.Thread(target=_run_trader_loop, daemon=True).start()


def _run_async(coro):
    """Run an async coroutine from sync code, return result."""
    future = asyncio.run_coroutine_threadsafe(coro, _trader_loop)
    return future.result(timeout=120)


def trader_status():
    """Get current trader status dict."""
    st = plus500_trader.get_status()
    return {
        "running": st["enabled"],
        "position": st["position_label"],
        "last_signal": st["last_signal"],
        "trade_count": st["trade_count"],
        "plus500_ready": st["plus500_ready"],
        "ws_connected": st["ws_connected"],
        "recent_trades": st["recent_trades"],
        "log": _trader_log_entries[-30:],
    }


def _add_trader_log(msg, cls=""):
    _trader_log_entries.append({"msg": msg, "cls": cls})
    if len(_trader_log_entries) > 200:
        _trader_log_entries.pop(0)


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
    """Automatically restart servers and tunnel that die."""
    while True:
        time.sleep(10)
        for name in SERVERS:
            if not server_alive(name):
                print(f"\n   ⚠️  {name} is down — auto-restarting...")
                try:
                    start_server(name)
                except Exception as e:
                    print(f"   ❌ Failed to restart {name}: {e}")
        # Check tunnels
        if SKIP_TUNNELS:
            continue
        if not tunnel_alive():
            print(f"\n   ⚠️  Cloudflare tunnel (charts) is down — auto-restarting...")
            try:
                restart_tunnel()
            except Exception as e:
                print(f"   ❌ Failed to restart chart tunnel: {e}")
        if not dashboard_tunnel_alive():
            print(f"\n   ⚠️  Dashboard tunnel is down — auto-restarting...")
            try:
                restart_dashboard_tunnel()
            except Exception as e:
                print(f"   ❌ Failed to restart dashboard tunnel: {e}")


# ─── HTTP Server (Dashboard + API) ──────────────────────────

def public_url_alive(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "goldchart-dashboard-health/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 400
    except Exception:
        return False

def deploy_to_gcp():
    """Deploy local files to GCP VM via SCP+SSH. Returns (success, steps)."""
    steps = []
    ssh_opts = ["-i", GCP_SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
    remote = f"{GCP_VM_USER}@{GCP_VM_IP}"

    # Step 1: Test SSH connection
    steps.append({"msg": "Testing SSH connection...", "ok": None})
    try:
        r = subprocess.run(
            ["ssh"] + ssh_opts + [remote, "echo SSH_OK"],
            capture_output=True, text=True, timeout=15,
        )
        if "SSH_OK" in r.stdout:
            steps.append({"msg": f"  ✓ Connected to {GCP_VM_IP}", "ok": True})
        else:
            steps.append({"msg": f"  ✗ SSH failed: {r.stderr.strip()}", "ok": False})
            return False, steps
    except Exception as e:
        steps.append({"msg": f"  ✗ SSH error: {e}", "ok": False})
        return False, steps

    # Step 2: Upload files via SCP
    steps.append({"msg": "Uploading files...", "ok": None})
    for fname in GCP_DEPLOY_FILES:
        local_path = os.path.join(BASE_DIR, fname)
        if not os.path.exists(local_path):
            steps.append({"msg": f"  ⚠ Skipped {fname} (not found)", "ok": False})
            continue
        size_kb = round(os.path.getsize(local_path) / 1024, 1)
        try:
            r = subprocess.run(
                ["scp"] + ssh_opts + [local_path, f"{remote}:~/"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                steps.append({"msg": f"  ✓ {fname} ({size_kb} KB)", "ok": True})
            else:
                steps.append({"msg": f"  ✗ {fname}: {r.stderr.strip()}", "ok": False})
                return False, steps
        except Exception as e:
            steps.append({"msg": f"  ✗ {fname}: {e}", "ok": False})
            return False, steps

    # Step 3: Copy to app dir, fix ownership, restart service
    steps.append({"msg": "Deploying on server...", "ok": None})
    cp_cmds = "; ".join(
        f"sudo cp ~/{f} {GCP_APP_DIR}/" for f in GCP_DEPLOY_FILES
    )
    chown_cmd = f"sudo chown goldchart:goldchart {GCP_APP_DIR}/*"
    restart_cmd = f"sudo systemctl restart {GCP_SERVICE}"
    verify_cmd = f"sudo systemctl is-active {GCP_SERVICE}"
    remote_cmd = f"{cp_cmds}; {chown_cmd}; {restart_cmd}; sleep 1; {verify_cmd}"

    try:
        r = subprocess.run(
            ["ssh"] + ssh_opts + [remote, remote_cmd],
            capture_output=True, text=True, timeout=30,
        )
        output = r.stdout.strip()
        if "active" in output:
            steps.append({"msg": f"  ✓ Files copied to {GCP_APP_DIR}", "ok": True})
            steps.append({"msg": f"  ✓ Service restarted and active", "ok": True})
        else:
            steps.append({"msg": f"  ✗ Service status: {output}", "ok": False})
            if r.stderr.strip():
                steps.append({"msg": f"  ✗ Error: {r.stderr.strip()}", "ok": False})
            return False, steps
    except Exception as e:
        steps.append({"msg": f"  ✗ Remote error: {e}", "ok": False})
        return False, steps

    # Step 4: Verify deployment
    steps.append({"msg": "Verifying deployment...", "ok": None})
    try:
        verify_parts = []
        for f in GCP_DEPLOY_FILES:
            verify_parts.append(f"wc -c {GCP_APP_DIR}/{f}")
        r = subprocess.run(
            ["ssh"] + ssh_opts + [remote, "; ".join(verify_parts)],
            capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.strip().splitlines():
            steps.append({"msg": f"  {line.strip()}", "ok": True})
    except Exception:
        pass

    steps.append({"msg": "\n🎉 Deploy complete! goldchart.win is updated.", "ok": True})
    return True, steps


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        # Serve dashboard.html at root
        if self.path == "/" or self.path == "/dashboard":
            self.path = "/dashboard.html"
            return super().do_GET()

        # Serve combined dashboard + chart page
        if self.path == "/combined" or self.path == "/all":
            combined_path = os.path.join(BASE_DIR, "dashboard-chart-combined.html")
            try:
                with open(combined_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "dashboard-chart-combined.html not found")
            return

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
            tunnel_status_alive = public_url_alive("https://goldchart.win/chart_coinbase.html") if SKIP_TUNNELS else tunnel_alive()
            status["tunnel"] = {
                "alive": tunnel_status_alive,
                "domain": TUNNEL_DOMAIN,
                "url": "https://goldchart.win" if SKIP_TUNNELS else _public_url,
                "label": f"Cloudflare Tunnel ({TUNNEL_DOMAIN})",
                "managed_by": "watchdog" if SKIP_TUNNELS else "dashboard",
            }
            self._json_response(status)
            return

        # API: trader status
        if self.path == "/api/trader/status":
            self._json_response(trader_status())
            return

        # API: full trade log for monitor
        if self.path == "/api/trader/trades":
            self._json_response(plus500_trader.get_all_trades())
            return

        # Trade monitor page
        if self.path == "/monitor":
            monitor_path = os.path.join(BASE_DIR, "trade_monitor.html")
            try:
                with open(monitor_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "trade_monitor.html not found")
            return

        # Financial info page
        if self.path == "/finance":
            fin_path = os.path.join(BASE_DIR, "financial_info.html")
            try:
                with open(fin_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "financial_info.html not found")
            return

        # Spotify controller
        if self.path == "/spotify" or self.path.startswith("/spotify?"):
            sp_path = os.path.join(BASE_DIR, "spotify.html")
            try:
                with open(sp_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "spotify.html not found")
            return

        # Video player (ad-free)
        if self.path == "/video":
            vp = os.path.join(BASE_DIR, "video.html")
            try:
                with open(vp, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "video.html not found")
            return

        # StreamFlix (Netflix-style UI)
        if self.path == "/netflix":
            nf = os.path.join(BASE_DIR, "netflix.html")
            try:
                with open(nf, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "netflix.html not found")
            return

        # PWA files for Music, Dashboard, Monitor, Video apps
        pwa_files = (
            "/music-manifest.json", "/music-sw.js", "/music-icon.svg",
            "/dash-manifest.json", "/dash-sw.js", "/dash-icon.svg",
            "/monitor-manifest.json", "/monitor-icon.svg",
            "/video-manifest.json", "/video-sw.js", "/video-icon.svg",
            "/netflix-manifest.json", "/netflix-sw.js", "/netflix-icon.svg",
        )
        if self.path in pwa_files:
            fname = self.path.lstrip("/")
            fpath = os.path.join(BASE_DIR, fname)
            content_types = {
                ".json": "application/manifest+json",
                ".js": "application/javascript",
                ".svg": "image/svg+xml",
            }
            ext = os.path.splitext(fname)[1]
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
                self.send_header("Content-Length", str(len(content)))
                if fname.endswith("-sw.js"):
                    self.send_header("Service-Worker-Allowed", "/")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, f"{fname} not found")
            return

        # Music player (JioSaavn)
        if self.path == "/music":
            mu_path = os.path.join(BASE_DIR, "music.html")
            try:
                with open(mu_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404, "music.html not found")
            return

        # ── TMDB API proxy ──
        if self.path.startswith("/api/video/tmdb/"):
            try:
                self._handle_tmdb_api()
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
            return

        # ── YouTube Video API proxy (InnerTube) ──
        if self.path.startswith("/api/video/"):
            try:
                self._handle_video_api()
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
            return

        return super().do_GET()

    def do_POST(self):
        # API: trader start
        if self.path == "/api/trader/start":
            print("   ▶ Starting Plus500 auto-trader from dashboard...")
            try:
                result = _run_async(plus500_trader.start_trader())
                _add_trader_log("Trader started — listening for signals", "ok")
                print(f"   ✓ Trader started: {result}")
                self._json_response(result)
            except Exception as e:
                _add_trader_log(f"Start failed: {e}", "fail")
                print(f"   ❌ Trader start error: {e}")
                self._json_response({"ok": False, "error": str(e)})
            return

        # API: trader stop
        if self.path == "/api/trader/stop":
            print("   ■ Stopping Plus500 auto-trader...")
            try:
                result = _run_async(plus500_trader.stop_trader())
                _add_trader_log("Trader stopped", "fail")
                print(f"   ✓ Trader stopped: {result}")
                self._json_response(result)
            except Exception as e:
                print(f"   ❌ Trader stop error: {e}")
                self._json_response({"ok": False, "error": str(e)})
            return

        # API: set target profit
        if self.path == "/api/trader/target-profit":
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                value = body.get('value')
                if value is None or float(value) <= 0:
                    self._json_response({"ok": False, "error": "Invalid value"})
                    return
                result = plus500_trader.set_target_profit(float(value))
                self._json_response(result)
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)})
            return

        # API: set instrument
        if self.path == "/api/trader/instrument":
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                name = body.get('name', '')
                if not name:
                    self._json_response({"ok": False, "error": "Missing name"})
                    return
                result = plus500_trader.set_instrument(name)
                self._json_response(result)
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)})
            return

        # API: trader toggle
        if self.path == "/api/trader/toggle":
            try:
                result = _run_async(plus500_trader.toggle_trader())
                state = "enabled" if result.get("enabled") else "paused"
                _add_trader_log(f"Auto-trading {state}", "sig")
                self._json_response(result)
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)})
            return

        # API: deploy to GCP
        if self.path == "/api/deploy/gcp":
            print("   🚀 Deploy to GCP triggered from dashboard...")
            success, steps = deploy_to_gcp()
            if success:
                print("   ✅ Deploy to GCP completed successfully")
            else:
                print("   ❌ Deploy to GCP failed")
            self._json_response({
                "success": success,
                "steps": steps,
                "error": None if success else "Deploy failed — check logs",
            })
            return

        # API: restart
        if self.path.startswith("/api/restart/"):
            target = self.path.split("/")[-1]

            if target == "all":
                results = {}
                for name in SERVERS:
                    pid = restart_server(name)
                    results[name] = {"pid": pid, "alive": True}
                if SKIP_TUNNELS:
                    results["tunnel"] = {"alive": True, "url": "https://goldchart.win", "managed_by": "cloudflared service"}
                    results["dashboard_tunnel"] = {"alive": True, "url": "https://dash.goldchart.win", "managed_by": "cloudflared service"}
                else:
                    url = restart_tunnel()
                    results["tunnel"] = {"alive": tunnel_alive(), "url": url}
                    dashboard_url = restart_dashboard_tunnel()
                    results["dashboard_tunnel"] = {"alive": dashboard_tunnel_alive(), "url": dashboard_url}
                self._json_response({
                    "message": "\u2705 Everything restarted",
                    "servers": results
                })
                return

            if target == "tunnel":
                if SKIP_TUNNELS:
                    self._json_response({
                        "message": "Tunnel is managed by the Windows cloudflared service",
                        "alive": True,
                        "url": "https://goldchart.win",
                    })
                    return
                url = restart_tunnel()
                self._json_response({
                    "message": f"✅ Tunnel restarted",
                    "alive": tunnel_alive(),
                    "url": url,
                })
                return
            if target == "dashboard_tunnel":
                if SKIP_TUNNELS:
                    self._json_response({
                        "message": "Dashboard tunnel is managed by the Windows cloudflared service",
                        "alive": True,
                        "url": "https://dash.goldchart.win",
                    })
                    return
                url = restart_dashboard_tunnel()
                self._json_response({
                    "message": f"✅ Dashboard tunnel restarted",
                    "alive": dashboard_tunnel_alive(),
                    "url": url,
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
                if not SKIP_TUNNELS:
                    stop_tunnel()
                    stop_dashboard_tunnel()
                self._json_response({"message": "\U0001f6d1 Everything stopped"})
                return
            if target == "tunnel":
                if SKIP_TUNNELS:
                    self._json_response({"message": "Tunnel is managed by the Windows cloudflared service"})
                    return
                stop_tunnel()
                self._json_response({"message": "🛑 Tunnel stopped"})
                return
            if target == "dashboard_tunnel":
                if SKIP_TUNNELS:
                    self._json_response({"message": "Dashboard tunnel is managed by the Windows cloudflared service"})
                    return
                stop_dashboard_tunnel()
                self._json_response({"message": "🛑 Dashboard tunnel stopped"})
                return
            if target in SERVERS:
                stop_server(target)
                self._json_response({"message": f"🛑 {target.upper()} stopped"})
                return

        self.send_error(404)

    # ── TMDB API proxy ──
    _TMDB_KEY = os.environ.get("TMDB_API_KEY", "4dbff1f9a47b5a3f1a41feee4ce25076")
    _TMDB_BASE = "https://api.themoviedb.org/3"
    _TMDB_HEADERS = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
    # TMDB language codes for South Indian + others
    _TMDB_LANGS = {
        "tamil": "ta", "hindi": "hi", "telugu": "te", "malayalam": "ml",
        "kannada": "kn", "english": "en", "korean": "ko", "bengali": "bn",
        "marathi": "mr", "punjabi": "pa", "gujarati": "gu", "japanese": "ja",
    }
    _TMDB_GENRES = {
        "action": 28, "comedy": 35, "romance": 10749, "thriller": 53,
        "horror": 27, "drama": 18, "family": 10751, "crime": 80,
        "scifi": 878, "animation": 16, "documentary": 99, "mystery": 9648,
    }

    def _tmdb_fetch(self, endpoint, params=None):
        """Fetch from TMDB API."""
        if params is None:
            params = {}
        params["api_key"] = self._TMDB_KEY
        url = self._TMDB_BASE + endpoint + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._TMDB_HEADERS)
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())

    def _tmdb_movie_to_dict(self, m):
        """Convert TMDB movie object to simplified dict."""
        return {
            "id": m.get("id"),
            "title": m.get("title", ""),
            "original_title": m.get("original_title", ""),
            "poster": ("https://image.tmdb.org/t/p/w342" + m["poster_path"]) if m.get("poster_path") else "",
            "backdrop": ("https://image.tmdb.org/t/p/w780" + m["backdrop_path"]) if m.get("backdrop_path") else "",
            "rating": m.get("vote_average", 0),
            "votes": m.get("vote_count", 0),
            "release_date": m.get("release_date", ""),
            "year": (m.get("release_date") or "")[:4],
            "overview": m.get("overview", ""),
            "genre_ids": m.get("genre_ids", []),
        }

    def _handle_tmdb_api(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.replace("/api/video/tmdb/", "")
        qs = urllib.parse.parse_qs(parsed.query)

        # /api/video/tmdb/trending?page=1
        if route == "trending":
            page = qs.get("page", ["1"])[0]
            data = self._tmdb_fetch("/trending/movie/week", {"language": "en-US", "page": page})
            movies = [self._tmdb_movie_to_dict(m) for m in data.get("results", [])]
            self._json_response({"movies": movies, "page": data.get("page", 1), "total_pages": data.get("total_pages", 1)})
            return

        # /api/video/tmdb/discover?lang=tamil&year=2026&genre=action&page=1
        if route == "discover":
            lang = qs.get("lang", [""])[0]
            year = qs.get("year", [""])[0]
            genre = qs.get("genre", [""])[0]
            page = qs.get("page", ["1"])[0]
            params = {"sort_by": "popularity.desc", "page": page, "include_adult": "false"}
            if lang and lang in self._TMDB_LANGS:
                params["with_original_language"] = self._TMDB_LANGS[lang]
            if year:
                params["primary_release_year"] = year
            if genre and genre in self._TMDB_GENRES:
                params["with_genres"] = str(self._TMDB_GENRES[genre])
            data = self._tmdb_fetch("/discover/movie", params)
            movies = [self._tmdb_movie_to_dict(m) for m in data.get("results", [])]
            self._json_response({"movies": movies, "page": data.get("page", 1), "total_pages": data.get("total_pages", 1)})
            return

        # /api/video/tmdb/now_playing?lang=tamil&page=1
        if route == "now_playing":
            lang = qs.get("lang", [""])[0]
            page = qs.get("page", ["1"])[0]
            region = "IN"
            params = {"language": "en-US", "page": page, "region": region}
            data = self._tmdb_fetch("/movie/now_playing", params)
            movies = [self._tmdb_movie_to_dict(m) for m in data.get("results", [])]
            # Filter by language if specified
            if lang and lang in self._TMDB_LANGS:
                lc = self._TMDB_LANGS[lang]
                movies = [m for m in movies if m.get("original_title") and True]  # TMDB now_playing doesn't filter by lang easily
            self._json_response({"movies": movies, "page": data.get("page", 1), "total_pages": data.get("total_pages", 1)})
            return

        # /api/video/tmdb/movie/<id> - get movie details + trailer
        if route.startswith("movie/"):
            movie_id = route.replace("movie/", "")
            data = self._tmdb_fetch(f"/movie/{movie_id}", {"language": "en-US", "append_to_response": "videos,credits,watch/providers"})
            # Find YouTube trailer
            trailer = None
            for v in data.get("videos", {}).get("results", []):
                if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
                    trailer = v.get("key")
                    break
            # Cast (top 10)
            cast = []
            for c in data.get("credits", {}).get("cast", [])[:10]:
                cast.append({"name": c.get("name", ""), "character": c.get("character", ""),
                             "photo": ("https://image.tmdb.org/t/p/w185" + c["profile_path"]) if c.get("profile_path") else ""})
            # Watch providers for India
            providers = data.get("watch/providers", {}).get("results", {}).get("IN", {})
            streaming = [{"name": p.get("provider_name", ""), "logo": ("https://image.tmdb.org/t/p/w92" + p["logo_path"]) if p.get("logo_path") else ""}
                         for p in providers.get("flatrate", [])]
            result = self._tmdb_movie_to_dict(data)
            result["trailer"] = trailer
            result["cast"] = cast
            result["streaming"] = streaming
            result["runtime"] = data.get("runtime", 0)
            result["tagline"] = data.get("tagline", "")
            genres = [g.get("name", "") for g in data.get("genres", [])]
            result["genres"] = genres
            self._json_response(result)
            return

        # /api/video/tmdb/search?q=...&page=1
        if route == "search":
            q = qs.get("q", [""])[0]
            page = qs.get("page", ["1"])[0]
            if not q:
                self._json_response({"movies": [], "page": 1, "total_pages": 0})
                return
            data = self._tmdb_fetch("/search/movie", {"query": q, "language": "en-US", "page": page, "region": "IN"})
            movies = [self._tmdb_movie_to_dict(m) for m in data.get("results", [])]
            self._json_response({"movies": movies, "page": data.get("page", 1), "total_pages": data.get("total_pages", 1)})
            return

        self._json_response({"error": "unknown tmdb route"}, 404)

    # ── YouTube InnerTube proxy for Video app ──
    _YT_SEARCH = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
    _YT_HEADERS = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    _YT_CTX = {"client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00", "hl": "en", "gl": "IN"}}

    def _handle_video_api(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.replace("/api/video/", "")
        qs = urllib.parse.parse_qs(parsed.query)

        if route == "search":
            q = qs.get("q", [""])[0]
            token = qs.get("token", [""])[0]
            if not q and not token:
                self._json_response({"error": "missing q"}, 400)
                return
            if token:
                payload = json.dumps({"context": self._YT_CTX, "continuation": token}).encode()
                req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read())
                vids, cont = self._extract_continuation(data)
                self._json_response({"videos": vids, "token": cont})
                return
            payload = json.dumps({"context": self._YT_CTX, "query": q, "params": "EgIQAQ%3D%3D"}).encode()
            req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            vids, cont = self._extract_videos_paged(data)
            self._json_response({"videos": vids, "token": cont})
            return

        if route == "trending":
            cat = qs.get("cat", [""])[0]
            token = qs.get("token", [""])[0]
            if token:
                payload = json.dumps({"context": self._YT_CTX, "continuation": token}).encode()
                req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read())
                vids, cont = self._extract_continuation(data)
                self._json_response({"videos": vids, "token": cont})
                return
            queries = {
                "": "trending India today 2025",
                "music": "latest Hindi songs 2025",
                "gaming": "gaming India trending",
                "news": "India news today",
                "sports": "India cricket highlights 2025",
                "movies": "Bollywood movie trailers 2025",
                "comedy": "Indian comedy trending 2025",
                "tech": "tech reviews India 2025",
            }
            q = queries.get(cat, queries[""])
            payload = json.dumps({"context": self._YT_CTX, "query": q, "params": "EgIQAQ%3D%3D"}).encode()
            req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            vids, cont = self._extract_videos_paged(data)
            self._json_response({"videos": vids, "token": cont})
            return

        if route == "movies":
            lang = qs.get("lang", [""])[0]
            year = qs.get("year", [""])[0]
            genre = qs.get("genre", [""])[0]
            artist = qs.get("artist", [""])[0]
            token = qs.get("token", [""])[0]
            if token:
                payload = json.dumps({"context": self._YT_CTX, "continuation": token}).encode()
                req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read())
                vids, cont = self._extract_continuation(data)
                self._json_response({"videos": vids, "token": cont})
                return
            lang_map = {
                "": "", "tamil": "Tamil", "hindi": "Hindi",
                "telugu": "Telugu", "malayalam": "Malayalam",
                "kannada": "Kannada", "english": "English",
                "korean": "Korean", "bengali": "Bengali",
            }
            genre_map = {
                "": "", "action": "action", "comedy": "comedy",
                "romance": "romantic", "thriller": "thriller",
                "horror": "horror", "drama": "drama",
                "dubbed": "dubbed", "family": "family",
            }
            l = lang_map.get(lang, "")
            g = genre_map.get(genre, "")
            parts = []
            if artist:
                parts.append(artist)
            if l:
                parts.append(l)
            if g:
                parts.append(g)
            if year:
                parts.append(year)
            parts.append("full movie")
            q = " ".join(parts)
            # No params - YouTube's duration filter param breaks movie results
            payload = json.dumps({"context": self._YT_CTX, "query": q}).encode()
            req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            vids, cont = self._extract_videos_paged(data)
            all_vids = vids
            # Filter: keep only videos >= 1 hour (real full movies)
            def dur_secs(d):
                if not d:
                    return 0
                parts = d.split(":")
                try:
                    if len(parts) == 3:
                        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    elif len(parts) == 2:
                        return int(parts[0]) * 60 + int(parts[1])
                except ValueError:
                    return 0
                return 0
            movies = [v for v in all_vids if dur_secs(v.get("duration", "")) >= 3600]
            if not movies:
                movies = [v for v in all_vids if dur_secs(v.get("duration", "")) >= 1200]
            self._json_response({"videos": movies, "token": cont})
            return

        if route == "songs":
            lang = qs.get("lang", [""])[0]
            year = qs.get("year", [""])[0]
            cat = qs.get("cat", [""])[0]
            singer = qs.get("singer", [""])[0]
            token = qs.get("token", [""])[0]

            # Continuation request (Load More)
            if token:
                payload = json.dumps({"context": self._YT_CTX, "continuation": token}).encode()
                req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read())
                vids, cont = self._extract_continuation(data)
                self._json_response({"videos": vids, "token": cont})
                return

            lang_map = {
                "": "", "tamil": "Tamil", "hindi": "Hindi",
                "telugu": "Telugu", "malayalam": "Malayalam",
                "kannada": "Kannada", "english": "English",
                "korean": "Korean", "bengali": "Bengali",
                "marathi": "Marathi", "punjabi": "Punjabi",
                "gujarati": "Gujarati",
            }
            l = lang_map.get(lang, "")
            parts = []
            if singer:
                parts.append(singer)
            if l:
                parts.append(l)
            if cat:
                parts.append(cat)
            else:
                parts.append("songs")
            if year:
                parts.append(year)
            parts.append("video song")
            q = " ".join(parts)
            payload = json.dumps({"context": self._YT_CTX, "query": q, "params": "EgIQAQ%3D%3D"}).encode()
            req = urllib.request.Request(self._YT_SEARCH, data=payload, headers=self._YT_HEADERS)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            more = qs.get("more", [""])[0]
            if more:
                vids, cont = self._extract_videos_paged(data)
                self._json_response({"videos": vids, "token": cont})
            else:
                vids, cont = self._extract_videos_paged(data)
                self._json_response({"videos": vids, "token": cont})
            return

        if route == "suggest":
            q = qs.get("q", [""])[0]
            if not q:
                self._json_response([])
                return
            url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q={urllib.parse.quote(q)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=5)
            raw = resp.read().decode("utf-8")
            # Response is JSONP: window.google.ac.h([...])
            start = raw.find("(")
            if start > 0:
                inner = raw[start + 1:-1]
                parsed_data = json.loads(inner)
                suggestions = [s[0] for s in parsed_data[1]] if len(parsed_data) > 1 else []
                self._json_response(suggestions)
            else:
                self._json_response([])
            return

        self._json_response({"error": "unknown route"}, 404)

    @staticmethod
    def _parse_video_items(items):
        """Extract video data from a list of renderer items."""
        videos = []
        for item in items:
            vr = item.get("videoRenderer", {})
            vid = vr.get("videoId")
            if not vid:
                continue
            title_runs = vr.get("title", {}).get("runs", [])
            title = title_runs[0].get("text", "") if title_runs else ""
            thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
            thumb = thumbs[-1].get("url", "") if thumbs else ""
            owner_runs = vr.get("ownerText", {}).get("runs", [])
            channel = owner_runs[0].get("text", "") if owner_runs else ""
            views = vr.get("viewCountText", {}).get("simpleText", "")
            length = vr.get("lengthText", {}).get("simpleText", "")
            published = vr.get("publishedTimeText", {}).get("simpleText", "")
            ch_thumbs = vr.get("channelThumbnailSupportedRenderers", {}).get(
                "channelThumbnailWithLinkRenderer", {}).get("thumbnail", {}).get("thumbnails", [])
            ch_avatar = ch_thumbs[0].get("url", "") if ch_thumbs else ""
            videos.append({
                "id": vid,
                "title": title,
                "thumbnail": thumb,
                "channel": channel,
                "channelAvatar": ch_avatar,
                "views": views,
                "duration": length,
                "published": published,
            })
        return videos

    @staticmethod
    def _extract_videos(data, mode="search"):
        if mode == "search":
            contents = (data.get("contents", {})
                        .get("twoColumnSearchResultsRenderer", {})
                        .get("primaryContents", {})
                        .get("sectionListRenderer", {})
                        .get("contents", []))
        else:
            contents = []
        all_items = []
        for sec in contents:
            items = sec.get("itemSectionRenderer", {}).get("contents", [])
            all_items.extend(items)
        return DashboardHandler._parse_video_items(all_items)

    @staticmethod
    def _extract_videos_paged(data):
        """Extract videos + continuation token from initial search response."""
        contents = (data.get("contents", {})
                    .get("twoColumnSearchResultsRenderer", {})
                    .get("primaryContents", {})
                    .get("sectionListRenderer", {})
                    .get("contents", []))
        all_items = []
        token = None
        for sec in contents:
            items = sec.get("itemSectionRenderer", {}).get("contents", [])
            all_items.extend(items)
            # Look for continuation token
            cir = sec.get("continuationItemRenderer", {})
            t = (cir.get("continuationEndpoint", {})
                 .get("continuationCommand", {})
                 .get("token"))
            if t:
                token = t
        videos = DashboardHandler._parse_video_items(all_items)
        return videos, token

    @staticmethod
    def _extract_continuation(data):
        """Extract videos + token from a continuation response."""
        all_items = []
        token = None
        for cmd in data.get("onResponseReceivedCommands", []):
            ci = cmd.get("appendContinuationItemsAction", {}).get("continuationItems", [])
            for sec in ci:
                items = sec.get("itemSectionRenderer", {}).get("contents", [])
                all_items.extend(items)
                cir = sec.get("continuationItemRenderer", {})
                t = (cir.get("continuationEndpoint", {})
                     .get("continuationCommand", {})
                     .get("token"))
                if t:
                    token = t
        videos = DashboardHandler._parse_video_items(all_items)
        return videos, token

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

    def handle_one_request(self):
        """Override to catch broken pipe / connection reset from clients."""
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
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


_tunnel_proc = None   # cloudflared subprocess for chart (goldchart.win)
_dashboard_tunnel_proc = None  # cloudflared subprocess for dashboard (dash.goldchart.win)
_public_url = None    # public URL from Cloudflare
_dashboard_public_url = None  # dashboard public URL


TUNNEL_DOMAIN = "goldchart.win"

def start_cloudflare_tunnel(port):
    """Start the permanent Cloudflare named tunnel for goldchart.win."""
    global _tunnel_proc, _public_url

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

    print(f"   Starting permanent Cloudflare tunnel (goldchart.win → localhost:{port})...")
    _tunnel_proc = subprocess.Popen(
        [cloudflared, "tunnel", "run", "goldchart"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )

    def _drain_tunnel_output():
        global _public_url
        try:
            for line in iter(_tunnel_proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if "Registered tunnel connection" in line or "Connection registered" in line:
                    if not _public_url:
                        _public_url = f"https://{TUNNEL_DOMAIN}"
                        print(f"   ✓ Tunnel connected: {_public_url}")
        except Exception:
            pass

    t = threading.Thread(target=_drain_tunnel_output, daemon=True)
    t.start()

    # Wait for tunnel to connect (timeout 30s)
    deadline = time.time() + 30
    while not _public_url and time.time() < deadline:
        time.sleep(0.5)

    # Named tunnel URL is known even if we miss the log line
    if not _public_url:
        _public_url = f"https://{TUNNEL_DOMAIN}"
        print(f"   ✓ Tunnel started: {_public_url} (assuming connected)")

    return _public_url


def tunnel_alive():
    """Check if the cloudflared tunnel process is still running."""
    return _tunnel_proc is not None and _tunnel_proc.poll() is None


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


def restart_tunnel():
    """Restart the Cloudflare tunnel for charts."""
    stop_tunnel()
    time.sleep(1)
    return start_cloudflare_tunnel(8081)


def start_dashboard_tunnel():
    """Start the Cloudflare tunnel for dashboard (dash.goldchart.win)."""
    global _dashboard_tunnel_proc, _dashboard_public_url

    cloudflared = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if not os.path.exists(cloudflared):
        print("   ❌ cloudflared not found at", cloudflared)
        return None

    print("   Starting dashboard tunnel (dash.goldchart.win → localhost:8090)...")
    _dashboard_tunnel_proc = subprocess.Popen(
        [cloudflared, "tunnel", "run", "goldchart-dash"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )

    def _drain_dashboard_tunnel_output():
        global _dashboard_public_url
        try:
            for line in iter(_dashboard_tunnel_proc.stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if "Registered tunnel connection" in line or "Connection registered" in line:
                    if not _dashboard_public_url:
                        _dashboard_public_url = "https://dash.goldchart.win"
                        print(f"   ✓ Dashboard tunnel connected: {_dashboard_public_url}")
        except Exception:
            pass

    t = threading.Thread(target=_drain_dashboard_tunnel_output, daemon=True)
    t.start()

    # Wait for tunnel to connect (timeout 30s)
    deadline = time.time() + 30
    while not _dashboard_public_url and time.time() < deadline:
        time.sleep(0.5)

    if not _dashboard_public_url:
        _dashboard_public_url = "https://dash.goldchart.win"
        print(f"   ✓ Dashboard tunnel started: {_dashboard_public_url} (assuming connected)")

    return _dashboard_public_url


def dashboard_tunnel_alive():
    """Check if the dashboard tunnel is still running."""
    return _dashboard_tunnel_proc is not None and _dashboard_tunnel_proc.poll() is None


def stop_dashboard_tunnel():
    """Stop the dashboard tunnel."""
    global _dashboard_tunnel_proc, _dashboard_public_url
    if _dashboard_tunnel_proc:
        try:
            _dashboard_tunnel_proc.terminate()
            _dashboard_tunnel_proc.wait(timeout=5)
        except Exception:
            try:
                _dashboard_tunnel_proc.kill()
            except Exception:
                pass
        _dashboard_tunnel_proc = None
    _dashboard_public_url = None


def restart_dashboard_tunnel():
    """Restart the dashboard tunnel."""
    stop_dashboard_tunnel()
    time.sleep(1)
    return start_dashboard_tunnel()


# ─── Main ────────────────────────────────────────────────────
def main():
    local_ip = get_local_ip()

    print("""
    ============================================================
           GOLD TRADING DASHBOARD
    ============================================================

      COINBASE Derivatives - Gold Futures - 1s refresh

      Dashboard:  http://localhost:{port}
      Chart:      http://localhost:8081/chart_coinbase.html

      Mobile (same WiFi):
        Dashboard:  http://{ip}:{port}
        Chart:      http://{ip}:8081/chart_coinbase.html

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
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
    print(f"   ✓ Dashboard ready: http://0.0.0.0:{DASHBOARD_PORT}\n")

    # Open dashboard in browser
    import webbrowser
    webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")
    print("   OK Dashboard opened in browser!")

    # Cloudflare tunnel for remote/mobile access over internet
    if SKIP_TUNNELS:
        chart_tunnel_url = "https://goldchart.win"
        dashboard_tunnel_url = "https://dash.goldchart.win"
        print("\n-- Cloudflare tunnel is managed by the Windows cloudflared service --")
    else:
        print("\n-- Setting up permanent Cloudflare tunnel (goldchart.win) --")
        chart_tunnel_url = start_cloudflare_tunnel(8081)
        dashboard_tunnel_url = start_dashboard_tunnel()
    if chart_tunnel_url:
        print(f"")
        print(f"   ==========================================================")
        print(f"   🌐 PERMANENT PUBLIC URL (works from anywhere):")
        print(f"   Chart:  https://goldchart.win")
        print(f"   QR:     https://goldchart.win/qr")
        print(f"   Dashboard: https://dash.goldchart.win")
        print(f"   ==========================================================")
        print(f"")
        print(f"   Open https://goldchart.win/qr on your PC to scan with phone!")
    else:
        print(f"   Tunnel failed. Use same-WiFi access:")
        print(f"   Chart: http://{local_ip}:8081/chart_coinbase.html")
        print(f"   QR:    http://{local_ip}:8081/qr")
        print(f"   Dashboard: http://{local_ip}:{DASHBOARD_PORT}")
    print(f"\n   Press Ctrl+C to stop everything\n")

    while True:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n   Shutting down...")
            for name in SERVERS:
                stop_server(name)
            # Stop auto-trader
            try:
                _run_async(plus500_trader.stop_trader())
            except Exception:
                pass
            httpd.shutdown()
            if not SKIP_TUNNELS:
                stop_tunnel()
                stop_dashboard_tunnel()
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass
            print("   ✓ All servers stopped. Goodbye!\n")
            break
        except Exception as e:
            print(f"\n   ⚠️  Dashboard HTTP server crashed: {e}")
            print("   🔄 Restarting dashboard in 3 seconds...")
            time.sleep(3)
            try:
                httpd.server_close()
            except Exception:
                pass
            kill_port(DASHBOARD_PORT)
            time.sleep(0.5)
            httpd = http.server.ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
            print(f"   ✓ Dashboard recovered on port {DASHBOARD_PORT}")


if __name__ == "__main__":
    main()
