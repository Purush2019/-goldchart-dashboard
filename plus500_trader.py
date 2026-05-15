"""
Plus500 Auto-Trader — Signal-Driven
=====================================
Connects to the Gold Chart WebSocket (localhost:8081) and
automatically executes trades on Plus500 Futures when signals fire.

Signals from gold_chart_coinbase.py indicators:
  - open_long  → BUY on Plus500
  - open_short → SELL on Plus500
  - close_long  → Close buy position
  - close_short → Close sell position

Safety:
  - Max 1 position at a time
  - Cooldown between trades (configurable)
  - Signal re-confirmation before placing order
  - Trade log with timestamps

Usage:  python plus500_trader.py
  or start from the Dashboard UI
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

import urllib.request

# =============================================================================
# CONFIGURATION
# =============================================================================

PLUS500_URL = "https://futures.plus500.com/trade"
PLUS500_USERNAME = os.environ.get("PLUS500_USER", "")
PLUS500_PASSWORD = os.environ.get("PLUS500_PASS", "")

# Persistent browser profile — survives restarts, keeps login session
# Use a path outside OneDrive to avoid sync conflicts with Chrome
BROWSER_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".plus500_profile")

# Gold chart WebSocket (local server)
CHART_WS_URL = "ws://localhost:8081"

# Trading parameters
TRADE_COOLDOWN = 10       # seconds between trades
MAX_POSITIONS = 1         # max simultaneous positions
SIGNAL_API_URL = "http://localhost:8081/api/signal"  # chart server signal endpoint
MAX_LOSS = 200.0          # close position if P/L loss exceeds this dollar amount
TARGET_PROFIT = 10.0      # take profit amount in dollars
INSTRUMENT = "Micro Gold"  # which gold instrument to trade

# Available instruments for UI dropdown
INSTRUMENT_OPTIONS = ["Micro Gold", "E-mini Gold", "1 Ounce Gold", "Gold"]

# State
_position = 0             # 0=flat, 1=long, -1=short
_last_trade_time = 0
_trade_log = []           # list of trade dicts
_trade_stats = {"total": 0, "success": 0, "failed": 0, "start_time": None}
_enabled = False          # auto-trading on/off
_plus500_ready = False
_ws_connected = False
_last_signal = None       # last processed signal action
_last_signal_time = 0     # timestamp of last signal
_order_in_progress = False  # True while executing an order (lock out new signals)
_order_lock = None          # asyncio.Lock — initialized when event loop starts


def log_trade(action, success, detail=""):
    """Log a trade execution."""
    now = datetime.now()
    entry = {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        "action": action,
        "success": success,
        "detail": detail,
    }
    _trade_log.append(entry)
    _trade_stats["total"] += 1
    if success:
        _trade_stats["success"] += 1
    else:
        _trade_stats["failed"] += 1
    # Keep last 200 trades
    if len(_trade_log) > 200:
        _trade_log.pop(0)
    status = "✅" if success else "❌"
    print(f"   {status} [{entry['time']}] {action} — {detail}")


# Step-by-step execution log (detailed per-trade steps for monitor dashboard)
_step_log = []  # [{time, step, status, detail, trade_id}]
_current_trade_id = 0

def _log(msg):
    """Simple debug logger."""
    print(f"   {msg}")

def log_step(step, status, detail=""):
    """Log a single execution step for the monitor dashboard.
    status: 'ok', 'fail', 'skip', 'info', 'warn'
    """
    now = datetime.now()
    entry = {
        "time": now.strftime("%H:%M:%S.%f")[:-3],
        "step": step,
        "status": status,
        "detail": detail,
        "trade_id": _current_trade_id,
    }
    _step_log.append(entry)
    if len(_step_log) > 500:
        _step_log.pop(0)
    icon = {"ok": "✓", "fail": "✗", "skip": "⊘", "info": "ℹ", "warn": "⚠"}.get(status, "·")
    print(f"   [{entry['time']}] {icon} {step}{(' — ' + detail) if detail else ''}")


async def fetch_latest_signal():
    """Fetch the latest signal from chart server's /api/signal endpoint."""
    try:
        def _do_fetch():
            req = urllib.request.Request(SIGNAL_API_URL, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())
        data = await asyncio.to_thread(_do_fetch)
        return data.get("signal"), data.get("time", 0), data.get("price", 0)
    except Exception as e:
        print(f"   ⚠️  Could not fetch latest signal: {e}")
        return None, 0, 0


async def confirm_signal(expected_action):
    """Re-check chart signal right before placing order. Returns True if signal still matches."""
    signal, sig_time, price = await fetch_latest_signal()
    if signal is None:
        print(f"   ⚠️  Signal API unreachable — proceeding with order anyway")
        return True  # Fail open: if API is down, don't block the trade
    # Map signal actions to expected trade direction
    buy_signals = {"open_long"}
    sell_signals = {"open_short"}
    if expected_action == "BUY" and signal not in buy_signals:
        print(f"   🚫 Signal changed! Expected open_long but latest is '{signal}' — ABORTING BUY")
        log_trade("BUY", False, f"Signal flipped to '{signal}' before order — aborted")
        return False
    if expected_action == "SELL" and signal not in sell_signals:
        print(f"   🚫 Signal changed! Expected open_short but latest is '{signal}' — ABORTING SELL")
        log_trade("SELL", False, f"Signal flipped to '{signal}' before order — aborted")
        return False
    print(f"   ✅ Signal confirmed: {signal} (price=${price}) — proceeding with {expected_action}")
    return True


def get_status():
    """Return current trader status as dict (for dashboard API)."""
    return {
        "enabled": _enabled,
        "plus500_ready": _plus500_ready,
        "ws_connected": _ws_connected,
        "position": _position,
        "position_label": {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(_position, "?"),
        "last_signal": _last_signal,
        "last_signal_time": _last_signal_time,
        "trade_count": len(_trade_log),
        "recent_trades": _trade_log[-10:],
        "stats": _trade_stats,
        "order_in_progress": _order_in_progress,
    }


def get_all_trades():
    """Return full trade log + stats + step log for the monitor dashboard."""
    return {
        "trades": list(_trade_log),
        "stats": _trade_stats,
        "steps": _step_log[-100:],  # last 100 steps
        "current_trade_id": _current_trade_id,
        "position": _position,
        "position_label": {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(_position, "?"),
        "enabled": _enabled,
        "plus500_ready": _plus500_ready,
        "ws_connected": _ws_connected,
        "last_signal": _last_signal,
        "order_in_progress": _order_in_progress,
        "target_profit": TARGET_PROFIT,
        "instrument": INSTRUMENT,
        "instrument_options": INSTRUMENT_OPTIONS,
    }


def set_target_profit(value):
    """Set take profit amount (called from dashboard API)."""
    global TARGET_PROFIT
    TARGET_PROFIT = float(value)
    print(f"   \U0001f3af Target Profit set to ${TARGET_PROFIT}")
    return {"ok": True, "target_profit": TARGET_PROFIT}


def set_instrument(name):
    """Set which gold instrument to trade (called from dashboard API)."""
    global INSTRUMENT
    if name not in INSTRUMENT_OPTIONS:
        return {"ok": False, "error": f"Unknown instrument: {name}"}
    INSTRUMENT = name
    print(f"   \U0001f4ca Instrument set to {INSTRUMENT}")
    return {"ok": True, "instrument": INSTRUMENT}


# =============================================================================
# PLUS500 BROWSER AUTOMATION
# =============================================================================

class Plus500Bot:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def _is_logged_in(self):
        """Check if we're already logged in (trading platform visible)."""
        try:
            # If we see instrument categories or trading UI, we're logged in
            categories = self.page.locator("#categories")
            if await categories.is_visible(timeout=5000):
                return True
        except Exception:
            pass
        try:
            # Check for any trading-related UI elements
            trade_ui = self.page.locator(".instruments-list, #instrumentsRepeater, .trade-panel")
            if await trade_ui.first.is_visible(timeout=3000):
                return True
        except Exception:
            pass
        return False

    async def start(self):
        """Launch browser with persistent profile and login to Plus500."""
        global _plus500_ready
        self.playwright = await async_playwright().start()

        # Use persistent context — saves cookies, localStorage, session
        # So after first login + CAPTCHA solve, future restarts skip login
        print(f"   📁 Using persistent profile: {BROWSER_PROFILE_DIR}")
        self.context = await self.playwright.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR,
            headless=False,
            channel="chrome",
            args=["--start-maximized"],
            no_viewport=True,
        )
        # launch_persistent_context gives us the context directly (no separate browser)
        self.browser = None
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        print("   ▶ Opening Plus500 Futures...")
        await self.page.goto(PLUS500_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)

        # Check if session is still active (already logged in)
        if await self._is_logged_in():
            print("   ✅ Existing session found — already logged in! (no CAPTCHA needed)")
            try:
                metals = self.page.locator("#categories").get_by_text("Metals")
                if await metals.is_visible(timeout=3000):
                    await metals.click()
                    await asyncio.sleep(2)
                    print("   ✓ GOLD selected")
            except Exception:
                pass
            _plus500_ready = True
            return True

        print("   🔐 No existing session — starting fresh login...")

        if not PLUS500_USERNAME or not PLUS500_PASSWORD:
            print("   ⚠️  PLUS500_USER / PLUS500_PASS are not set.")
            print("   Please complete Plus500 login manually in the browser window.")
            for _ in range(180):
                if await self._is_logged_in():
                    print("   ✓ Manual login successful! Session saved for future restarts.")
                    _plus500_ready = True
                    return True
                await asyncio.sleep(1)
            print("   ❌ Manual login was not completed within 3 minutes.")
            _plus500_ready = False
            return False

        # Auto-login
        try:
            # Accept cookies if prompted
            try:
                accept_cookies = self.page.locator("#onetrust-accept-btn-handler")
                if await accept_cookies.is_visible(timeout=3000):
                    await accept_cookies.click()
                    await asyncio.sleep(1)
                    print("   ✓ Cookies accepted")
            except Exception:
                pass

            # Step 1: Click "Demo Mode" button (first screen shows Real Money vs Demo)
            print("   🎮 Selecting Demo Mode...")
            demo_btn = self.page.locator("#demoMode")
            if await demo_btn.is_visible(timeout=5000):
                await demo_btn.click()
                await asyncio.sleep(3)
                print("   ✓ Demo Mode selected")
            else:
                print("   ⚠️  Demo Mode button not found — may already be past this screen")

            # Step 2: Click "Already have an account?" to switch from signup to login
            print("   📋 Looking for 'Already have an account?' link...")
            switched = False
            await asyncio.sleep(2)

            for attempt in range(5):
                # Strategy A: Playwright locator targeting <a> tags
                try:
                    link = self.page.locator("a").filter(has_text="Already have an account")
                    if await link.count() > 0:
                        await link.first.click(force=True)
                        await asyncio.sleep(2)
                except Exception:
                    pass

                # Check if form switched (look for "Log In" button)
                try:
                    login_btn = self.page.get_by_role("button", name="Log In")
                    if await login_btn.is_visible(timeout=2000):
                        switched = True
                        print(f"   ✓ Switched to login form (attempt {attempt+1}, strategy A)")
                        break
                except Exception:
                    pass

                # Strategy B: JavaScript click targeting <a> tags first
                try:
                    await self.page.evaluate("""() => {
                        for (const a of document.querySelectorAll('a')) {
                            if (a.textContent.trim().includes('Already have an account')) {
                                a.scrollIntoView();
                                a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                                return;
                            }
                        }
                    }""")
                    await asyncio.sleep(2)
                except Exception:
                    pass

                # Check again
                try:
                    login_btn = self.page.get_by_role("button", name="Log In")
                    if await login_btn.is_visible(timeout=2000):
                        switched = True
                        print(f"   ✓ Switched to login form (attempt {attempt+1}, strategy B)")
                        break
                except Exception:
                    pass

                # Strategy C: Playwright get_by_text
                try:
                    await self.page.get_by_text("Already have an account?").click(timeout=3000)
                    await asyncio.sleep(2)
                    login_btn = self.page.get_by_role("button", name="Log In")
                    if await login_btn.is_visible(timeout=2000):
                        switched = True
                        print(f"   ✓ Switched to login form (attempt {attempt+1}, strategy C)")
                        break
                except Exception:
                    pass

                print(f"   ⚠️  Attempt {attempt+1}/5: form not switched yet, retrying...")
                await asyncio.sleep(2)

            if not switched:
                print("   ❌ FAILED to switch to login form after 5 attempts — aborting")
                _plus500_ready = False
                return False

            # Step 3: Fill credentials on the LOGIN form (only after verified switch)
            print("   📝 Entering credentials...")
            email_field = self.page.get_by_role("textbox", name="Email")
            await email_field.wait_for(state="visible", timeout=10000)
            await email_field.clear()
            await email_field.fill(PLUS500_USERNAME)
            await asyncio.sleep(0.5)

            password_field = self.page.get_by_role("textbox", name="Password")
            await password_field.clear()
            await password_field.fill(PLUS500_PASSWORD)
            await asyncio.sleep(0.5)

            # Step 4: Click "Log In" button (NOT "Create Demo Account")
            print("   🔑 Clicking Log In...")
            login_btn = self.page.get_by_role("button", name="Log In")
            if await login_btn.is_visible(timeout=3000):
                await login_btn.click()
            else:
                # Try alternate casing
                login_btn2 = self.page.get_by_role("button", name="Log in")
                if await login_btn2.is_visible(timeout=2000):
                    await login_btn2.click()
                else:
                    print("   ⚠️  'Log In' button not visible, trying submit...")
                    await self.page.locator("button[type='submit']").first.click()

            await asyncio.sleep(5)
            print("   ✓ Login submitted")

            # Wait for CAPTCHA — give user time to solve it manually
            print("   ⏳ Waiting for login to complete (solve CAPTCHA if shown)...")
            for i in range(60):  # Wait up to 60 seconds for user to solve CAPTCHA
                if await self._is_logged_in():
                    print("   ✓ Login successful! Session saved for future restarts.")
                    break
                await asyncio.sleep(1)
            else:
                print("   ⚠️  Login not confirmed after 60s — continue anyway")

            # Dismiss notifications
            try:
                allow = self.page.get_by_role("button", name="Allow")
                if await allow.is_visible(timeout=3000):
                    await allow.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Step 5: Select Gold from Metals category
            print("   📊 Selecting GOLD instrument...")
            try:
                await self.page.locator("#categories").get_by_text("Metals").click()
                await asyncio.sleep(2)
                print("   ✓ Plus500 ready — GOLD selected")
            except Exception:
                pass
            _plus500_ready = True
            return True

        except Exception as e:
            print(f"   ⚠️  Auto-login issue: {e}")
            print("   Please complete login manually in the browser window")
            print("   The bot will wait for you to be on the Gold trading page")
            # Still mark as ready after a delay so user can manually set up
            await asyncio.sleep(10)
            _plus500_ready = True
            return True

    async def _open_gold_instrument(self):
        """Double-click selected gold instrument name to open detail view."""
        instrument = INSTRUMENT
        # Ensure Metals category is selected
        try:
            metals = self.page.locator("#categories").get_by_text("Metals")
            if await metals.is_visible(timeout=2000):
                await metals.click()
                log_step("Select Metals category", "ok")
                await asyncio.sleep(1)
            else:
                log_step("Select Metals category", "skip", "Not visible")
        except Exception as e:
            log_step("Select Metals category", "warn", str(e))

        # Double-click on the instrument NAME text (not the whole row)
        try:
            gold_name = self.page.locator("#instrumentsRepeater").get_by_text(re.compile(re.escape(instrument))).first
            await gold_name.dblclick()
            log_step(f"Double-click {instrument}", "ok")
            await asyncio.sleep(1.5)
        except Exception as e:
            log_step(f"Double-click {instrument}", "fail", str(e))
            raise

    async def _click_detail_button(self, action="Buy"):
        """Click Buy or Sell in the instrument detail panel that opens after double-click."""
        # After double-click, a detail panel opens with larger Buy/Sell buttons
        # These buttons contain prices like 'Buy 5,152.7' or 'Sell 5,152.4'
        # Use regex to match 'Buy' or 'Sell' followed by a space and digits
        btn = self.page.get_by_role("button", name=re.compile(rf"^{action}\s+[\d,.\.]+$"))
        if await btn.count() > 0:
            await btn.first.click()
            log_step(f"Click {action} button (price match)", "ok")
            return
        # Fallback: look for the button inside the detail/trade panel area
        panel = self.page.locator(".trade-panel, .instrument-detail, [class*='detail'], [class*='trade-box']")
        if await panel.count() > 0:
            panel_btn = panel.get_by_role("button", name=action)
            if await panel_btn.count() > 0:
                await panel_btn.first.click()
                log_step(f"Click {action} button (panel fallback)", "ok")
                return
        # Last fallback: just click the last matching button
        all_btns = self.page.get_by_role("button", name=action)
        count = await all_btns.count()
        if count > 0:
            await all_btns.nth(count - 1).click()
            log_step(f"Click {action} button (last of {count})", "ok")
        else:
            log_step(f"Click {action} button", "fail", f"No {action} button found on page")

    async def _scroll_trade_panel(self, direction="down"):
        """Scroll the right-side trade panel to reveal hidden elements like TP/SL."""
        scroll_result = await self.page.evaluate("""(dir) => {
            // Strategy: find the scrollable container that holds "Place" button or "Risk Management"
            // EXCLUDE elements with 'sidebar' or 'nav' in class name (left nav panel)
            const allDivs = document.querySelectorAll('div');
            let best = null;
            let bestScore = 0;
            for (const d of allDivs) {
                if (d.scrollHeight <= d.clientHeight + 20) continue;
                const cls = (d.className || '').toLowerCase();
                const id = (d.id || '').toLowerCase();
                // Skip left sidebar/nav elements
                if (cls.includes('sidebar') || cls.includes('nav') || id.includes('sidebar') || id.includes('nav')) continue;
                const rect = d.getBoundingClientRect();
                // Must be on the right half of the screen
                if (rect.left < window.innerWidth * 0.6) continue;
                // Must have reasonable size
                if (rect.width < 100 || rect.height < 100) continue;
                // Score: prefer containers that have "Risk Management" or "Place" text
                const txt = d.textContent || '';
                let score = 1;
                if (txt.includes('Risk Management')) score += 10;
                if (txt.includes('Place')) score += 5;
                if (txt.includes('Take Profit')) score += 8;
                if (txt.includes('Stop Loss')) score += 3;
                // Prefer narrower containers (more specific)
                score += (1000 - rect.width) / 100;
                if (score > bestScore) {
                    bestScore = score;
                    best = d;
                }
            }
            if (best) {
                best.scrollTop = dir === 'down' ? best.scrollHeight : 0;
                return 'scrolled:' + (best.className || best.id || 'div').substring(0, 80) + ' score=' + bestScore.toFixed(1);
            }
            return 'no-scrollable-container-found';
        }""", direction)
        return scroll_result

    async def _enable_take_profit(self):
        """Toggle Take Profit ON and set amount to $10.00.
        Plus500 layout: Right panel → Risk Management section → Stop Loss / Trailing Stop / Take Profit rows.
        Each row has a label (SPAN.inner-label) and a toggle switch to the right.
        When TP is toggled ON, an input field appears below/beside it for the dollar amount.
        """
        try:
            # Step 1: Scroll "Take Profit" label into view using JS scrollIntoView
            log_step("Scroll TP into view", "info", "Using JS scrollIntoView")
            scroll_result = await self.page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                        const rect = el.getBoundingClientRect();
                        if (rect.left > window.innerWidth * 0.5) {
                            el.scrollIntoView({block: 'center', behavior: 'instant'});
                            const newRect = el.getBoundingClientRect();
                            return {
                                found: true,
                                tag: el.tagName,
                                cls: (el.className || '').substring(0, 80),
                                oldY: Math.round(rect.top),
                                newY: Math.round(newRect.top),
                                inViewport: newRect.top > 0 && newRect.top < window.innerHeight
                            };
                        }
                    }
                }
                return {found: false};
            }""")
            log_step("Scroll TP into view", "ok" if scroll_result.get('found') else "fail",
                     str(scroll_result)[:200])
            if not scroll_result.get('found'):
                log_step("Take Profit", "fail", "Cannot find TP label in DOM")
                return
            await asyncio.sleep(0.5)

            # Step 2: Find and click the toggle switch
            # The toggle in Plus500 is typically the clickable element to the RIGHT of the label
            # on the same row. Let's find it by position and structure.
            log_step("Toggle Take Profit ON", "info", "Finding toggle by position")

            toggle_clicked = await self.page.evaluate("""() => {
                // Find the small TP label element (SPAN with just "Take Profit" text)
                const all = document.querySelectorAll('*');
                let tpEl = null;
                for (const el of all) {
                    if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                        const rect = el.getBoundingClientRect();
                        if (rect.left > window.innerWidth * 0.5 && rect.width < 200) {
                            tpEl = el;
                            break;
                        }
                    }
                }
                if (!tpEl) return {clicked: false, error: 'TP label not found'};

                const tpRect = tpEl.getBoundingClientRect();

                // Strategy 1: Look for standard toggle attributes in parent rows
                let row = tpEl;
                for (let i = 0; i < 4; i++) {
                    if (!row.parentElement) break;
                    row = row.parentElement;
                    // Check if this row itself is narrow enough to be the TP row (not the whole form)
                    const rowRect = row.getBoundingClientRect();
                    if (rowRect.height > 100) continue; // Too tall, skip

                    const candidates = row.querySelectorAll('[role="switch"], [role="checkbox"], input[type="checkbox"]');
                    for (const c of candidates) {
                        if (c.offsetHeight > 0) {
                            c.click();
                            return {clicked: true, method: 'role-switch', tag: c.tagName, level: i};
                        }
                    }
                }

                // Strategy 2: Find any clickable element to the RIGHT of "Take Profit" label
                // on the same vertical line (within 30px vertical range)
                const clickables = document.querySelectorAll('div, span, button, label, input');
                let bestToggle = null;
                let bestDist = 999;
                for (const el of clickables) {
                    if (el === tpEl || el.contains(tpEl) || tpEl.contains(el)) continue;
                    if (el.offsetHeight === 0 || el.offsetWidth === 0) continue;
                    const r = el.getBoundingClientRect();
                    // Must be to the right of TP label
                    if (r.left <= tpRect.right) continue;
                    // Must be on the same row (vertical alignment within 20px)
                    const vertDist = Math.abs((r.top + r.height/2) - (tpRect.top + tpRect.height/2));
                    if (vertDist > 20) continue;
                    // Prefer elements that look like toggles (small width, not too big)
                    if (r.width > 100 || r.height > 40) continue;
                    const dist = r.left - tpRect.right;
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestToggle = el;
                    }
                }
                if (bestToggle) {
                    bestToggle.click();
                    const cls = (bestToggle.className || '').substring(0, 80);
                    return {clicked: true, method: 'position-right', tag: bestToggle.tagName, cls: cls, dist: Math.round(bestDist)};
                }

                // Strategy 3: Use Playwright click at coordinates to the right of TP label
                // Return the coordinates for the caller to click
                return {clicked: false, needCoordClick: true, x: tpRect.right + 80, y: tpRect.top + tpRect.height / 2};
            }""")

            log_step("Toggle TP", "ok" if toggle_clicked.get('clicked') else "warn",
                     str(toggle_clicked)[:200])

            if not toggle_clicked.get('clicked') and toggle_clicked.get('needCoordClick'):
                # Click at the computed coordinates using Playwright mouse
                cx = toggle_clicked['x']
                cy = toggle_clicked['y']
                await self.page.mouse.click(cx, cy)
                log_step("Toggle TP (coord click)", "warn", f"Clicked at ({cx:.0f}, {cy:.0f})")

            await asyncio.sleep(1.0)

            # Step 3: Verify TP was toggled ON by checking if a new input appeared
            log_step("Verify TP toggle", "info", "Checking if TP input appeared")

            verify = await self.page.evaluate("""() => {
                // After toggling TP ON, Plus500 shows an input field for the profit amount.
                // Find "Take Profit" label, then look for a NEARBY input that appeared.
                const all = document.querySelectorAll('*');
                let tpEl = null;
                for (const el of all) {
                    if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                        const rect = el.getBoundingClientRect();
                        if (rect.left > window.innerWidth * 0.5 && rect.width < 200) {
                            tpEl = el;
                            break;
                        }
                    }
                }
                if (!tpEl) return {toggled: false, error: 'TP label gone'};

                const tpRect = tpEl.getBoundingClientRect();

                // Look for inputs NEAR the TP label (within 200px vertically below, right side of screen)
                const inputs = document.querySelectorAll('input');
                const nearInputs = [];
                for (const inp of inputs) {
                    if (inp.offsetHeight === 0 || inp.offsetWidth < 15) continue;
                    const iRect = inp.getBoundingClientRect();
                    // Must be on right side
                    if (iRect.left < window.innerWidth * 0.5) continue;
                    // Must be BELOW or at same level as TP label (not above — that would be Amount)
                    const dy = iRect.top - tpRect.top;
                    if (dy < -10 || dy > 200) continue;
                    nearInputs.push({
                        type: inp.type,
                        value: inp.value,
                        dy: Math.round(dy),
                        x: Math.round(iRect.left),
                        y: Math.round(iRect.top),
                        w: Math.round(iRect.width)
                    });
                }
                return {toggled: nearInputs.length > 0, inputCount: nearInputs.length, inputs: nearInputs};
            }""")

            log_step("Verify TP toggle", "ok" if verify.get('toggled') else "fail",
                     str(verify)[:200])

            if not verify.get('toggled'):
                # TP didn't toggle — try clicking again with Playwright
                log_step("Retry TP toggle", "warn", "No input appeared, trying Playwright click")
                tp_text = self.page.locator("text='Take Profit'")
                if await tp_text.count() > 0:
                    box = await tp_text.first.bounding_box()
                    if box:
                        # Click 80px to the right of the label (where toggle should be)
                        await self.page.mouse.click(box['x'] + box['width'] + 80, box['y'] + box['height'] / 2)
                        await asyncio.sleep(1.0)
                        # Re-verify
                        verify = await self.page.evaluate("""() => {
                            const all = document.querySelectorAll('*');
                            let tpEl = null;
                            for (const el of all) {
                                if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                                    const rect = el.getBoundingClientRect();
                                    if (rect.left > window.innerWidth * 0.5 && rect.width < 200) { tpEl = el; break; }
                                }
                            }
                            if (!tpEl) return {toggled: false};
                            const tpRect = tpEl.getBoundingClientRect();
                            const inputs = document.querySelectorAll('input');
                            for (const inp of inputs) {
                                if (inp.offsetHeight === 0) continue;
                                const iRect = inp.getBoundingClientRect();
                                if (iRect.left < window.innerWidth * 0.5) continue;
                                const dy = iRect.top - tpRect.top;
                                if (dy >= -10 && dy <= 200) return {toggled: true, type: inp.type, value: inp.value, dy: Math.round(dy)};
                            }
                            return {toggled: false};
                        }""")
                        log_step("Retry TP verify", "ok" if verify.get('toggled') else "fail",
                                 str(verify)[:150])

            if not verify.get('toggled'):
                log_step("Take Profit", "fail", "Could not toggle TP ON — no input appeared. Skipping TP.")
                return

            # Step 4: Fill TP value into the TP input (the one BELOW/AT the TP label, NOT the Amount input above)
            tp_val = str(int(TARGET_PROFIT)) if TARGET_PROFIT == int(TARGET_PROFIT) else str(TARGET_PROFIT)
            log_step(f"Set TP ${tp_val}", "info", "Filling TP input")

            fill_result = await self.page.evaluate("""(tpAmount) => {
                const all = document.querySelectorAll('*');
                let tpEl = null;
                for (const el of all) {
                    if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                        const rect = el.getBoundingClientRect();
                        if (rect.left > window.innerWidth * 0.5 && rect.width < 200) { tpEl = el; break; }
                    }
                }
                if (!tpEl) return {ok: false, error: 'TP label gone'};

                const tpRect = tpEl.getBoundingClientRect();
                const inputs = document.querySelectorAll('input');
                let bestInput = null;
                let bestDy = 999;

                for (const inp of inputs) {
                    if (inp.offsetHeight === 0 || inp.offsetWidth < 15) continue;
                    const iRect = inp.getBoundingClientRect();
                    if (iRect.left < window.innerWidth * 0.5) continue;
                    // Must be BELOW or at TP label level (dy >= -10), NOT above it (Amount field is above)
                    const dy = iRect.top - tpRect.top;
                    if (dy < -10 || dy > 200) continue;
                    if (dy < bestDy) {
                        bestDy = dy;
                        bestInput = inp;
                    }
                }

                if (!bestInput) return {ok: false, error: 'No input below TP label'};

                const oldVal = bestInput.value;
                // Focus, select all, then set value
                bestInput.focus();
                bestInput.select();
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(bestInput, tpAmount);
                bestInput.dispatchEvent(new Event('input', {bubbles: true}));
                bestInput.dispatchEvent(new Event('change', {bubbles: true}));
                bestInput.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));

                return {ok: true, oldVal: oldVal, newVal: bestInput.value, dy: Math.round(bestDy), type: bestInput.type};
            }""", tp_val)

            log_step(f"Set TP ${tp_val}", "ok" if fill_result.get('ok') else "fail",
                     str(fill_result)[:200])

            if not fill_result.get('ok'):
                log_step(f"Set TP ${tp_val}", "fail", "Could not fill TP input. Skipping.")
                return

            # Step 5: Ensure Place Order button is visible
            await asyncio.sleep(0.3)
            place_btn = self.page.locator("button:has-text('Place')")
            if await place_btn.count() > 0:
                try:
                    await place_btn.first.scroll_into_view_if_needed(timeout=3000)
                    log_step("Place Order visible", "ok")
                except Exception:
                    await self._scroll_trade_panel("down")
                    log_step("Place Order visible", "warn", "scroll_into_view failed, used panel scroll")
            await asyncio.sleep(0.3)

            # Deselect input focus
            await self.page.keyboard.press("Tab")
            await asyncio.sleep(0.3)
            log_step("Deselect input (Tab)", "ok")
        except Exception as e:
            log_step("Take Profit setup", "fail", str(e))

    async def _set_risk_management(self):
        """After order is placed, click 'Add Risk Management' link on the position row,
        toggle Take Profit ON (default $10), then click the green
        'Add Risk Management' button at the bottom to save.
        """
        try:
            # Step 1: Click "Add Risk Management" link on the position row
            add_rm = self.page.locator("text='Add Risk Management'")
            try:
                if await add_rm.first.is_visible(timeout=3000):
                    await add_rm.first.click()
                    log_step("Add Risk Management", "ok", "Clicked link on position row")
                    await asyncio.sleep(1)
                else:
                    pos_info = self.page.locator("text=/Position:.*contract/i")
                    if await pos_info.count() > 0:
                        await pos_info.first.click()
                        await asyncio.sleep(0.5)
                        if await add_rm.first.is_visible(timeout=2000):
                            await add_rm.first.click()
                            log_step("Add Risk Management", "ok", "Clicked after expanding row")
                            await asyncio.sleep(1)
                        else:
                            log_step("Add Risk Management", "fail", "Link not visible")
                            return
                    else:
                        log_step("Add Risk Management", "fail", "No position row or link found")
                        return
            except Exception as e:
                log_step("Add Risk Management", "fail", str(e)[:60])
                return

            # Step 2: Just toggle Take Profit ON (default is $10, no need to type value)
            tp_toggle = self.page.locator("text='Take Profit'")
            if await tp_toggle.count() > 0:
                # Find and click the toggle switch to the right of "Take Profit" label
                toggle_clicked = await self.page.evaluate("""() => {
                    const all = document.querySelectorAll('*');
                    let tpEl = null;
                    for (const el of all) {
                        if (el.textContent.trim() === 'Take Profit' && el.offsetHeight > 0 && el.offsetHeight < 50) {
                            const rect = el.getBoundingClientRect();
                            if (rect.left > window.innerWidth * 0.5 && rect.width < 200) {
                                tpEl = el; break;
                            }
                        }
                    }
                    if (!tpEl) return {clicked: false, error: 'TP label not found'};
                    const tpRect = tpEl.getBoundingClientRect();
                    // Look for toggle in parent rows
                    let row = tpEl;
                    for (let i = 0; i < 4; i++) {
                        if (!row.parentElement) break;
                        row = row.parentElement;
                        if (row.getBoundingClientRect().height > 100) continue;
                        const candidates = row.querySelectorAll('[role="switch"], [role="checkbox"], input[type="checkbox"]');
                        for (const c of candidates) {
                            if (c.offsetHeight > 0) { c.click(); return {clicked: true, method: 'role-switch'}; }
                        }
                    }
                    // Fallback: click element to the right of label
                    const clickables = document.querySelectorAll('div, span, button, label, input');
                    let best = null, bestDist = 999;
                    for (const el of clickables) {
                        if (el === tpEl || el.contains(tpEl) || tpEl.contains(el)) continue;
                        if (el.offsetHeight === 0 || el.offsetWidth === 0) continue;
                        const r = el.getBoundingClientRect();
                        if (r.left <= tpRect.right) continue;
                        if (Math.abs((r.top + r.height/2) - (tpRect.top + tpRect.height/2)) > 20) continue;
                        if (r.width > 100 || r.height > 40) continue;
                        const dist = r.left - tpRect.right;
                        if (dist < bestDist) { bestDist = dist; best = el; }
                    }
                    if (best) { best.click(); return {clicked: true, method: 'position-right'}; }
                    return {clicked: false, needCoordClick: true, x: tpRect.right + 80, y: tpRect.top + tpRect.height / 2};
                }""")
                if not toggle_clicked.get('clicked') and toggle_clicked.get('needCoordClick'):
                    await self.page.mouse.click(toggle_clicked['x'], toggle_clicked['y'])
                    log_step("Toggle TP (coord)", "ok", f"Clicked at ({toggle_clicked['x']:.0f}, {toggle_clicked['y']:.0f})")
                else:
                    log_step("Toggle TP", "ok" if toggle_clicked.get('clicked') else "fail", str(toggle_clicked)[:100])
                await asyncio.sleep(0.5)
            else:
                log_step("Toggle TP", "fail", "Take Profit label not found")
                return

            # Step 3: Click the green "Add Risk Management" button at bottom to save
            save_rm = self.page.get_by_role("button", name="Add Risk Management")
            try:
                if await save_rm.is_visible(timeout=3000):
                    await save_rm.scroll_into_view_if_needed(timeout=2000)
                    await asyncio.sleep(0.2)
                    await save_rm.click()
                    log_step("Save Risk Management", "ok", "Clicked 'Add Risk Management' button")
                    await asyncio.sleep(0.5)
                else:
                    fallback = self.page.locator("button:has-text('Add Risk Management')")
                    if await fallback.count() > 0:
                        await fallback.last.click()
                        log_step("Save Risk Management", "ok", "Clicked fallback button")
                        await asyncio.sleep(0.5)
                    else:
                        log_step("Save Risk Management", "fail", "Button not found")
            except Exception as e:
                log_step("Save Risk Management", "fail", str(e)[:60])

            log_step("Risk Management", "ok", "TP toggled ON (default $10) and saved")

        except Exception as e:
            log_step("Risk Management", "fail", str(e)[:80])

    async def _check_existing_position(self):
        """Check Plus500 Positions tab for open/pending positions. Returns True if position exists.
        
        Exact DOM structure (from live inspection):
          - Position row: div.position or div.position.selected
          - Position type: div.type with text 'Bought' or 'Sold'
          - Close button: div.close-button > button.icon-times
          - P/L display: div.pl or div.pl.red
          - No positions: 'Start trading now' or 'no open positions' text
        """
        try:
            # Click Positions tab using exact Plus500 sidebar ID: #positionsFuturesNav
            pos_nav = self.page.locator("#positionsFuturesNav")
            if await pos_nav.is_visible(timeout=2000):
                await pos_nav.click()
                log_step("Positions tab", "ok", "Clicked #positionsFuturesNav")
            else:
                log_step("Positions tab", "warn", "#positionsFuturesNav not visible")
            await asyncio.sleep(1.5)

            # Check for "You have no open positions" or "Start trading now" text (empty state)
            no_positions = self.page.locator("text=/no open positions/i")
            start_trading = self.page.locator("text=/Start trading now/i")
            if await no_positions.count() > 0 or await start_trading.count() > 0:
                log_step("Check positions", "ok", "No open positions — clean")
                await self._navigate_to_trade()
                return False

            # Check for actual position row using exact class from DOM
            position_row = self.page.locator("div.position")
            pos_count = await position_row.count()
            if pos_count > 0:
                try:
                    # Try to get position type (Bought/Sold)
                    type_el = self.page.locator("div.position div.type")
                    detail = await type_el.first.text_content() if await type_el.count() > 0 else "unknown"
                    log_step("Check positions", "warn", f"Open position found: {detail.strip()} (count={pos_count})")
                except Exception:
                    log_step("Check positions", "warn", f"Open position found (count={pos_count})")
                await self._navigate_to_trade()
                return True

            # Check for pending orders (e.g., "Order: Buy 1 Contract at 5,063.5")
            pending = self.page.locator("text=/Order.*Buy|Order.*Sell/i")
            if await pending.count() > 0:
                try:
                    detail = await pending.first.text_content()
                    log_step("Check positions", "warn", f"Pending order found: {detail.strip()[:80]}")
                except Exception:
                    log_step("Check positions", "warn", "Pending order found")
                await self._navigate_to_trade()
                return True

            # Fallback: Check for P/L or close button indicators
            pos_indicators = self.page.locator("div.pl, div.close-button, button.icon-times")
            if await pos_indicators.count() > 0:
                log_step("Check positions", "warn", "Position indicators (P/L or close button) found")
                await self._navigate_to_trade()
                return True

            log_step("Check positions", "ok", "No open positions or pending orders detected")
            await self._navigate_to_trade()
            return False

        except Exception as e:
            log_step("Check positions", "skip", f"Check failed: {str(e)[:60]}")
            await self._navigate_to_trade()
            return False

    async def _navigate_to_trade(self):
        """Navigate back to Trade tab after checking Positions."""
        try:
            trade_nav = self.page.locator("#tradeNav")
            if await trade_nav.is_visible(timeout=1000):
                await trade_nav.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

    async def execute_buy(self):
        """Click BUY on Plus500."""
        global _position, _current_trade_id, _order_in_progress
        if _order_in_progress:
            log_step("BUY blocked", "warn", "Order already in progress")
            return False
        _current_trade_id += 1
        tid = _current_trade_id
        log_step("═══ START BUY ORDER ═══", "info", f"Trade #{tid}")
        _order_in_progress = True
        try:
            if not self.page:
                log_step("Check page ready", "fail", "Plus500 not connected")
                log_trade("BUY", False, "Plus500 not connected")
                return False

            log_step("Bring Plus500 to front", "info")
            await self.page.bring_to_front()
            await asyncio.sleep(0.3)

            # Double-click instrument to open detail
            log_step("Open instrument", "info", f"Double-clicking {INSTRUMENT}")
            await self._open_gold_instrument()

            # Click BUY button in the instrument detail panel
            log_step("Click BUY button", "info", "Looking for Buy price button")
            await self._click_detail_button("Buy")
            await asyncio.sleep(0.5)

            # Accept terms if prompted
            try:
                accept = self.page.get_by_role("button", name="Accept")
                if await accept.is_visible(timeout=1000):
                    await accept.click()
                    log_step("Accept terms dialog", "ok")
                    await asyncio.sleep(0.3)
            except Exception:
                pass

            # Skip Take Profit setup — pl_monitor handles TP/SL automatically

            # If TP > $10, set it in the order form before placing (old procedure)
            if TARGET_PROFIT > 10:
                log_step("Take Profit setup", "info", f"TP ${TARGET_PROFIT} > $10 — setting in order form")
                await self._enable_take_profit()

            # Place Buy Order
            log_step("Click Place Buy Order", "info", "Looking for button")
            place_btn = self.page.get_by_role("button", name="Place Buy Order")
            btn_visible = False
            try:
                btn_visible = await place_btn.is_visible(timeout=3000)
            except Exception:
                pass
            if btn_visible:
                await place_btn.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.2)
                await place_btn.click()
                log_step("Click Place Buy Order", "ok", "Button clicked")
            else:
                log_step("Click Place Buy Order", "warn", "Not visible, scrolling trade panel")
                await self._scroll_trade_panel("down")
                await asyncio.sleep(0.5)
                place_btn2 = self.page.locator("button:has-text('Place Buy Order'), button:has-text('Place Buy')")
                if await place_btn2.count() > 0:
                    await place_btn2.first.scroll_into_view_if_needed(timeout=3000)
                    await asyncio.sleep(0.2)
                    await place_btn2.first.click()
                    log_step("Click Place Buy Order", "ok", "Clicked via text selector after scroll")
                else:
                    await place_btn.click(timeout=3000)
                    log_step("Click Place Buy Order", "ok", "Clicked after panel scroll")
            await asyncio.sleep(1)

            # Verify order via Positions tab
            verified = await self._verify_order_placed("BUY")
            if verified:
                log_step("═══ BUY ORDER COMPLETE ═══", "ok", f"Trade #{tid} confirmed")
                log_trade("BUY", True, "Order placed and confirmed")
                # If TP <= $10, set via Risk Management link (default $10, just toggle)
                if TARGET_PROFIT <= 10:
                    await self._navigate_to_trade()
                    await asyncio.sleep(0.5)
                    await self._set_risk_management()
            else:
                log_step("═══ BUY ORDER FAILED ═══", "fail", f"Trade #{tid} not confirmed in Positions")
                log_trade("BUY", False, "Order not confirmed in Positions tab")
            return verified

        except Exception as e:
            log_step("═══ BUY ORDER FAILED ═══", "fail", str(e))
            log_trade("BUY", False, str(e))
            return False
        finally:
            _order_in_progress = False

    async def execute_sell(self):
        """Click SELL on Plus500."""
        global _position, _current_trade_id, _order_in_progress
        if _order_in_progress:
            log_step("SELL blocked", "warn", "Order already in progress")
            return False
        _current_trade_id += 1
        tid = _current_trade_id
        log_step("═══ START SELL ORDER ═══", "info", f"Trade #{tid}")
        _order_in_progress = True
        try:
            if not self.page:
                log_step("Check page ready", "fail", "Plus500 not connected")
                log_trade("SELL", False, "Plus500 not connected")
                return False

            log_step("Bring Plus500 to front", "info")
            await self.page.bring_to_front()
            await asyncio.sleep(0.3)

            # Double-click instrument to open detail
            log_step("Open instrument", "info", f"Double-clicking {INSTRUMENT}")
            await self._open_gold_instrument()

            # Click SELL button in the instrument detail panel
            log_step("Click SELL button", "info", "Looking for Sell price button")
            await self._click_detail_button("Sell")
            await asyncio.sleep(0.5)

            # Accept terms if prompted
            try:
                accept = self.page.get_by_role("button", name="Accept")
                if await accept.is_visible(timeout=1000):
                    await accept.click()
                    log_step("Accept terms dialog", "ok")
                    await asyncio.sleep(0.3)
            except Exception:
                pass

            # Skip Take Profit setup — pl_monitor handles TP/SL automatically

            # If TP > $10, set it in the order form before placing (old procedure)
            if TARGET_PROFIT > 10:
                log_step("Take Profit setup", "info", f"TP ${TARGET_PROFIT} > $10 — setting in order form")
                await self._enable_take_profit()

            # Place Sell Order
            log_step("Click Place Sell Order", "info", "Looking for button")
            place_btn = self.page.get_by_role("button", name="Place Sell Order")
            btn_visible = False
            try:
                btn_visible = await place_btn.is_visible(timeout=3000)
            except Exception:
                pass
            if btn_visible:
                await place_btn.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.2)
                await place_btn.click()
                log_step("Click Place Sell Order", "ok", "Button clicked")
            else:
                log_step("Click Place Sell Order", "warn", "Not visible, scrolling trade panel")
                await self._scroll_trade_panel("down")
                await asyncio.sleep(0.5)
                # Also try broader selector
                place_btn2 = self.page.locator("button:has-text('Place Sell Order'), button:has-text('Place Sell')")
                if await place_btn2.count() > 0:
                    await place_btn2.first.scroll_into_view_if_needed(timeout=3000)
                    await asyncio.sleep(0.2)
                    await place_btn2.first.click()
                    log_step("Click Place Sell Order", "ok", "Clicked via text selector after scroll")
                else:
                    await place_btn.click(timeout=3000)
                    log_step("Click Place Sell Order", "ok", "Clicked after panel scroll")
            await asyncio.sleep(1)

            # Verify order via Positions tab
            verified = await self._verify_order_placed("SELL")
            if verified:
                log_step("═══ SELL ORDER COMPLETE ═══", "ok", f"Trade #{tid} confirmed")
                log_trade("SELL", True, "Order placed and confirmed")
                # If TP <= $10, set via Risk Management link (default $10, just toggle)
                if TARGET_PROFIT <= 10:
                    await self._navigate_to_trade()
                    await asyncio.sleep(0.5)
                    await self._set_risk_management()
            else:
                log_step("═══ SELL ORDER FAILED ═══", "fail", f"Trade #{tid} not confirmed in Positions")
                log_trade("SELL", False, "Order not confirmed in Positions tab")
            return verified

        except Exception as e:
            log_step("═══ SELL ORDER FAILED ═══", "fail", str(e))
            log_trade("SELL", False, str(e))
            return False
        finally:
            _order_in_progress = False

    async def _verify_order_placed(self, direction):
        """After placing order, check Positions tab to confirm it went through.
        direction: 'BUY' or 'SELL'. Returns True if position confirmed."""
        global _position
        try:
            log_step("Verify order", "info", "Checking Positions tab...")
            await asyncio.sleep(1)  # Wait for order to settle

            has_position = await self._check_existing_position()

            if has_position:
                _position = 1 if direction == "BUY" else -1
                log_step("Verify order", "ok", f"{direction} confirmed in Positions tab")
                return True
            else:
                # "You have no open positions" — order didn't go through
                _position = 0
                log_step("Verify order", "fail",
                         f"{direction} NOT confirmed — Positions tab shows no open positions")
                return False

        except Exception as e:
            log_step("Verify order", "warn", f"Could not verify: {str(e)[:60]}")
            # Assume order went through since Place button was clicked
            _position = 1 if direction == "BUY" else -1
            return True

    async def _confirm_close_dialog(self):
        """Handle the 'Close Trade' confirmation dialog.
        Dialog shows: 'Close Micro Gold Apr 26 position & orders?'
        Buttons: 'Cancel' and 'Place Order'
        """
        try:
            # Primary: "Place Order" button (exact match from screenshot)
            place_order = self.page.get_by_role("button", name="Place Order")
            if await place_order.is_visible(timeout=3000):
                await place_order.click()
                log_step("Confirm close dialog", "ok", "Clicked 'Place Order'")
                await asyncio.sleep(1)
                return True
        except Exception:
            pass

        try:
            # Fallback: any button with Place Order / Close / Yes text
            fallbacks = self.page.locator(
                "button:has-text('Place Order'), button:has-text('Close Trade'), "
                "button:has-text('Yes'), button:has-text('Confirm')"
            )
            if await fallbacks.count() > 0:
                await fallbacks.first.click()
                log_step("Confirm close dialog", "ok", "Clicked fallback confirm button")
                await asyncio.sleep(1)
                return True
        except Exception:
            pass

        log_step("Confirm close dialog", "skip", "No confirmation dialog appeared")
        return False

    async def _find_close_button(self):
        """Find the close position button. First navigates to Positions tab (#positionsFuturesNav).
        Returns locator or None. Returns True if already clicked via JS fallback.
        
        Exact selectors (from live DOM inspection):
          - button.icon-times inside div.close-button (the actual clickable close)
          - div.close-button (container)
          - button.buySellButton.icon-times (alternate close style)
          - Position row: div.position or div.position.selected
        """
        # Navigate to Positions tab where close buttons are visible
        pos_nav = self.page.locator("#positionsFuturesNav")
        try:
            if await pos_nav.is_visible(timeout=2000):
                await pos_nav.click()
                log_step("Navigate to Positions", "ok", "Clicked #positionsFuturesNav")
                await asyncio.sleep(1.5)
        except Exception:
            log_step("Navigate to Positions", "warn", "Could not click Positions tab")

        # Hover over the position row to reveal the close button (Plus500 hides it until hover)
        try:
            pos_row = self.page.locator("div.position").first
            if await pos_row.is_visible(timeout=2000):
                await pos_row.hover()
                log_step("Hover position row", "ok", "Hovered to reveal close button")
                await asyncio.sleep(0.5)
        except Exception:
            log_step("Hover position row", "warn", "Could not hover position row")

        # Try exact selectors found from live DOM inspection (priority order)
        strategies = [
            # 1. Exact: button inside div.close-button on the position row
            ("button.icon-times (row)", self.page.locator("div.position button.icon-times")),
            # 2. Any button.icon-times in the close-button container
            ("div.close-button button", self.page.locator("div.close-button button")),
            # 3. button with class icon-times (the close X)
            ("button.icon-times", self.page.locator("button.icon-times")),
            # 4. button with buySellButton + icon-times classes
            ("buySellButton.icon-times", self.page.locator("button.buySellButton.icon-times")),
            # 5. The close-button container div itself (clickable)
            ("div.close-button", self.page.locator("div.close-button:not(.no-sort)")),
            # 6. Fallback: any button with Close text
            ("button:has-text('Close')", self.page.locator("button:has-text('Close')")),
        ]
        for name, locator in strategies:
            try:
                count = await locator.count()
                if count > 0 and await locator.first.is_visible(timeout=800):
                    log_step("Found close button", "ok", f"Strategy: {name} (count={count})")
                    return locator.first
            except Exception:
                continue

        # Fallback: JS scan for any clickable element with "Close" text
        close_elements = await self.page.evaluate("""() => {
            const results = [];
            const skipTexts = ['closed positions', 'market is closed', 'close dialog'];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.children.length <= 2) {
                    const txt = (el.textContent || '').trim();
                    const lower = txt.toLowerCase();
                    if (/close/i.test(txt) && txt.length < 30 &&
                        !skipTexts.some(s => lower.includes(s))) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.push({
                                text: txt.substring(0, 30),
                                tag: el.tagName,
                                cls: (el.className || '').toString().substring(0, 80),
                                x: Math.round(rect.x + rect.width / 2),
                                y: Math.round(rect.y + rect.height / 2)
                            });
                        }
                    }
                }
            }
            return results;
        }""")

        if close_elements:
            for ce in close_elements[:5]:
                log_step("Close element scan", "info",
                         f"'{ce['text']}' <{ce['tag']}> .{ce.get('cls','')} at ({ce['x']},{ce['y']})")
            # Try clicking the first matching element via coordinates
            best = close_elements[0]
            log_step("Fallback click", "info", f"Clicking '{best['text']}' at ({best['x']},{best['y']})")
            await self.page.mouse.click(best['x'], best['y'])
            return True  # special: already clicked
        else:
            log_step("Close button scan", "fail", "No elements with 'Close' text found in DOM")

        # Navigate back to Trade before returning None
        await self._navigate_to_trade()
        return None

    async def _emergency_close(self, pl_value=None):
        """Emergency close position when loss exceeds MAX_LOSS. Clicks X Close on the position row."""
        global _position, _current_trade_id, _order_in_progress
        if _order_in_progress:
            log_step("Emergency close", "warn", "Order in progress, waiting...")
            return False

        _current_trade_id += 1
        tid = _current_trade_id
        log_step("═══ EMERGENCY CLOSE (LOSS LIMIT) ═══", "fail",
                 f"Trade #{tid} P/L: ${pl_value:.2f}" if pl_value else f"Trade #{tid}")
        _order_in_progress = True
        try:
            await self.page.bring_to_front()
            await asyncio.sleep(0.3)

            # Find and click the close button using multiple strategies
            result = await self._find_close_button()

            if result is not None:
                # result is True (already clicked via JS) or a locator
                if result is not True:
                    await result.click()
                log_step("Click Close", "ok", "Clicked close on position row")
                await asyncio.sleep(1)

                # Confirm the "Close Trade" dialog — button is "Place Order"
                await self._confirm_close_dialog()

                # Verify close via Positions tab
                await asyncio.sleep(1)
                still_open = await self._check_existing_position()
                prev = _position
                if not still_open:
                    _position = 0
                    detail = f"Closed {'LONG' if prev == 1 else 'SHORT'} at P/L ${pl_value:.2f}" if pl_value else "Emergency closed"
                    log_step("═══ EMERGENCY CLOSE DONE ═══", "ok", detail + " — verified")
                    log_trade("EMERGENCY_CLOSE", True, detail)
                    return True
                else:
                    log_step("Emergency close", "warn", "Position still showing after close — retrying")
                    # One more attempt
                    result2 = await self._find_close_button()
                    if result2 is not None:
                        if result2 is not True:
                            await result2.click()
                        await asyncio.sleep(1)
                        await self._confirm_close_dialog()
                    # Final check
                    still_open2 = await self._check_existing_position()
                    if not still_open2:
                        _position = 0
                        detail = f"Closed {'LONG' if prev == 1 else 'SHORT'} at P/L ${pl_value:.2f}" if pl_value else "Emergency closed"
                        log_step("═══ EMERGENCY CLOSE DONE (retry) ═══", "ok", detail)
                        log_trade("EMERGENCY_CLOSE", True, detail)
                        return True
                    else:
                        log_step("Emergency close", "fail", "Position still open after retry")
                        log_trade("EMERGENCY_CLOSE", False, "Position still open after retry")
                        return False
            else:
                log_step("Emergency close", "fail", "No Close button found after all strategies")
                log_trade("EMERGENCY_CLOSE", False, "No Close button")
                return False

        except Exception as e:
            log_step("Emergency close", "fail", str(e))
            log_trade("EMERGENCY_CLOSE", False, str(e))
            return False
        finally:
            _order_in_progress = False
            await self._navigate_to_trade()

    async def close_position(self):
        """Close current open position by clicking Close in open positions."""
        global _position, _current_trade_id, _order_in_progress
        _current_trade_id += 1
        tid = _current_trade_id
        log_step("═══ START CLOSE POSITION ═══", "info", f"Trade #{tid}")
        _order_in_progress = True
        try:
            if not self.page:
                log_step("Check page ready", "fail", "Plus500 not connected")
                log_trade("CLOSE", False, "Plus500 not connected")
                return False

            await self.page.bring_to_front()
            await asyncio.sleep(0.3)

            # Find close button using shared multi-strategy helper
            log_step("Find close button", "info", "Trying multiple strategies")
            result = await self._find_close_button()

            if result is not None:
                if result is not True:
                    await result.click()
                log_step("Click close button", "ok")
                await asyncio.sleep(1)

                # Confirm the "Close Trade" dialog — button is "Place Order"
                await self._confirm_close_dialog()

                # Verify close via Positions tab
                await asyncio.sleep(1)
                still_open = await self._check_existing_position()
                prev = _position
                if not still_open:
                    _position = 0
                    log_step("═══ CLOSE COMPLETE ═══", "ok", f"Closed {'LONG' if prev == 1 else 'SHORT'} — verified")
                    log_trade("CLOSE", True, f"Closed {'LONG' if prev == 1 else 'SHORT'}")
                    return True
                else:
                    log_step("═══ CLOSE INCOMPLETE ═══", "warn", "Position still showing after close click")
                    log_trade("CLOSE", False, "Position still open after close attempt")
                    return False
            else:
                log_step("Find close button", "fail", "No close button visible after all strategies")
                log_trade("CLOSE", False, "No close button found")
                # Do NOT reset _position — position is still open on Plus500
                return False

        except Exception as e:
            log_step("═══ CLOSE FAILED ═══", "fail", str(e))
            log_trade("CLOSE", False, str(e))
            # Do NOT reset _position — position is still open on Plus500
            return False
        finally:
            _order_in_progress = False
            # Always return to Trade tab so other operations (P/L monitor, etc.) work
            await self._navigate_to_trade()

    async def stop(self):
        """Close browser (session persists in profile directory)."""
        global _plus500_ready
        _plus500_ready = False
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        print("   ✓ Plus500 browser closed (session saved for next restart)")


# =============================================================================
# P/L LOSS MONITOR — auto-close if loss exceeds MAX_LOSS
# =============================================================================

async def pl_monitor(bot):
    """Monitor open position P/L on Plus500. Auto-close if loss > MAX_LOSS.
    Checks every 3 seconds. Reads P/L from multiple sources:
    1. Header bar 'Profit' field (#accountStatus) — always visible
    2. Instrument row P/L text (when on Trade tab)
    3. Position row P/L (div.pl) (when on Positions tab)
    """
    global _position
    _consecutive_null = 0
    _consecutive_zero = 0
    while True:
        try:
            await asyncio.sleep(2)  # check every 2 seconds

            if not _enabled or not _plus500_ready or not bot.page:
                continue

            if _order_in_progress:
                continue

            # Read P/L from Plus500 UI — try multiple sources (with timeout)
            try:
                pl_value = await asyncio.wait_for(bot.page.evaluate(r"""() => {
                // Helper: strip Unicode directional/invisible chars before matching
                function clean(s) {
                    return s.replace(/[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]/g, '').trim();
                }
                function extractPL(txt) {
                    const c = clean(txt);
                    const m = c.match(/(-?)\$\s*(-?)([\d,]+\.?\d*)/);
                    if (m) {
                        const sign = (m[1] === '-' || m[2] === '-') ? -1 : 1;
                        return sign * parseFloat(m[3].replace(/,/g, ''));
                    }
                    return null;
                }

                // Source 1: Header bar — Profit field in account status (always visible)
                // Shows "$-35.00 Profit" with Unicode directional marks
                const headerEls = document.querySelectorAll('#accountStatus strong, .account-status strong, .account-status-carousel-futures strong');
                for (const el of headerEls) {
                    const txt = clean(el.textContent || '');
                    if (txt === 'Profit') {
                        const parent = el.closest('li') || el.closest('div');
                        if (parent) {
                            const spans = parent.querySelectorAll('span');
                            for (const sp of spans) {
                                const v = extractPL(sp.textContent || '');
                                if (v !== null) return v;
                            }
                        }
                    }
                }

                // Source 2: Instrument row — "P/L: $-35.00" or "P/L: $12.50"
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.children.length <= 2) {
                        const txt = clean(el.textContent || '');
                        if (txt.includes('P/L')) {
                            const v = extractPL(txt);
                            if (v !== null) return v;
                        }
                    }
                }

                // Source 3: Position row div.pl — "P/L$-130.00" or just "-$35.00"
                const plDivs = document.querySelectorAll('div.pl, .pl-value');
                for (const el of plDivs) {
                    const v = extractPL(el.textContent || '');
                    if (v !== null) return v;
                }

                return null;
            }"""), timeout=5)
            except (asyncio.TimeoutError, Exception):
                pl_value = None

            if pl_value is None:
                _consecutive_null += 1
                # If we think we have a position but can't read P/L for 30+ seconds, 
                # check Positions tab to see if position still exists
                if _position != 0 and _consecutive_null >= 10:
                    log_step("P/L monitor", "warn", f"Cannot read P/L for {_consecutive_null * 2}s — checking positions")
                    has_pos = await bot._check_existing_position()
                    if not has_pos:
                        log_step("P/L monitor", "info", "Position gone — resetting _position to 0")
                        _position = 0
                    _consecutive_null = 0
                continue

            _consecutive_null = 0

            # No active trade and no P/L — nothing to do
            if _position == 0 and pl_value == 0.0:
                _consecutive_zero = 0
                continue

            # Position tracked but P/L is $0.00 — Plus500 may have auto-closed (TP hit)
            if _position != 0 and pl_value == 0.0:
                _consecutive_zero += 1
                if _consecutive_zero >= 3:  # 6 seconds of $0.00
                    log_step("P/L monitor", "info", "P/L $0.00 with tracked position — checking Plus500")
                    has_pos = await bot._check_existing_position()
                    if not has_pos:
                        log_step("P/L monitor", "ok", "Position closed (TP hit?) — resetting to FLAT")
                        _position = 0
                    _consecutive_zero = 0
                continue

            _consecutive_zero = 0

            # Log P/L periodically (every ~15 seconds = every 5th check)
            if hasattr(pl_monitor, '_log_counter'):
                pl_monitor._log_counter += 1
                if pl_monitor._log_counter % 5 == 0:
                    log_step("P/L", "info" if pl_value >= 0 else "warn", f"${pl_value:.2f}")
            else:
                pl_monitor._log_counter = 0

            # Check TARGET_PROFIT
            if _position != 0 and pl_value >= TARGET_PROFIT and TARGET_PROFIT > 0:
                log_step("🎯 TARGET PROFIT HIT", "ok",
                         f"P/L: ${pl_value:.2f} >= ${TARGET_PROFIT:.2f} — CLOSING NOW")
                await bot._emergency_close(pl_value)
                continue

            # Check if loss exceeds MAX_LOSS
            if _position != 0 and pl_value < -MAX_LOSS:
                log_step("⚠ LOSS LIMIT HIT", "fail",
                         f"P/L: ${pl_value:.2f} exceeds -${MAX_LOSS:.2f} — CLOSING NOW")
                print(f"   🚨 LOSS LIMIT: P/L ${pl_value:.2f} exceeds -${MAX_LOSS:.2f} — emergency close!")
                await bot._emergency_close(pl_value)
                continue

            # Even if _position==0, check if there's a real P/L showing (position exists but not tracked)
            if _position == 0 and pl_value != 0 and abs(pl_value) > 0.01:
                log_step("P/L monitor", "warn",
                         f"P/L ${pl_value:.2f} detected but _position=0 — checking Plus500")
                has_pos = await bot._check_existing_position()
                if has_pos:
                    _position = 1  # Default to LONG — will be corrected by next position check
                    log_step("P/L monitor", "warn", f"Position exists on Plus500! Synced _position=1, P/L=${pl_value:.2f}")
                    # If already in loss, emergency close immediately
                    if pl_value < -MAX_LOSS:
                        await bot._emergency_close(pl_value)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log_step("P/L monitor", "fail", f"Error: {str(e)[:60]}")
            await asyncio.sleep(2)  # brief pause then continue monitoring


# =============================================================================
# SIGNAL LISTENER — connects to gold chart WebSocket
# =============================================================================

async def signal_listener(bot):
    """Connect to gold chart WebSocket and execute trades on new signals."""
    global _ws_connected, _last_signal, _last_signal_time, _position, _last_trade_time

    while True:
        try:
            print(f"   📡 Connecting to chart WebSocket ({CHART_WS_URL})...")
            async with websockets.connect(CHART_WS_URL, max_size=10 * 1024 * 1024) as ws:
                _ws_connected = True
                print("   ✓ Connected to gold chart WebSocket")

                async for raw in ws:
                    if not _enabled:
                        continue

                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Only process messages with markers (signals)
                    markers = msg.get("markers", [])
                    if not markers:
                        continue

                    # Get the latest signal
                    latest = markers[-1]
                    signal_action = latest.get("text", "").split("\n")[-1].strip()
                    signal_time = latest.get("time", 0)

                    # Skip if same signal we already processed
                    if signal_time == _last_signal_time and signal_action == _last_signal:
                        continue

                    # New signal detected!
                    _last_signal = signal_action
                    _last_signal_time = signal_time

                    # Order-in-progress guard — NEVER overlap orders
                    if _order_in_progress:
                        continue

                    # Cooldown check
                    now = time.time()
                    if now - _last_trade_time < TRADE_COOLDOWN:
                        continue

                    # Check Plus500 readiness
                    if not _plus500_ready:
                        continue

                    # ── Execute based on signal ──
                    # Only open_long (BUY) and open_short (SELL) place orders.
                    # close_long / close_short are internal tracking only — no action on Plus500.
                    # Rule: Check Positions tab → empty = place order, not empty = skip.

                    if signal_action in ("close_long", "close_short"):
                        _position = 0
                        continue

                    if signal_action == "open_long":
                        # Re-confirm signal before executing
                        sig, _, _ = await fetch_latest_signal()
                        if sig != "open_long":
                            log_trade("open_long", False, f"Signal changed to '{sig}' — skipped")
                            continue

                        # Check Positions tab — only place order if EMPTY
                        has_pos = await bot._check_existing_position()
                        if has_pos:
                            log_trade("open_long", False, "Position still open — skip")
                            continue

                        _position = 0  # sync: confirmed empty
                        await bot.execute_buy()
                        _last_trade_time = time.time()

                    elif signal_action == "open_short":
                        # Re-confirm signal before executing
                        sig, _, _ = await fetch_latest_signal()
                        if sig != "open_short":
                            log_trade("open_short", False, f"Signal changed to '{sig}' — skipped")
                            continue

                        # Check Positions tab — only place order if EMPTY
                        has_pos = await bot._check_existing_position()
                        if has_pos:
                            log_trade("open_short", False, "Position still open — skip")
                            continue

                        _position = 0  # sync: confirmed empty
                        await bot.execute_sell()
                        _last_trade_time = time.time()

        except websockets.exceptions.ConnectionClosed:
            _ws_connected = False
            print("   ⚠️  WebSocket disconnected — reconnecting in 5s...")
        except Exception as e:
            _ws_connected = False
            print(f"   ❌ WebSocket error: {e} — reconnecting in 5s...")

        await asyncio.sleep(5)


# =============================================================================
# CONTROL API (called from dashboard.py)
# =============================================================================

_bot_instance = None
_bot_task = None
_pl_monitor_task = None


async def start_trader():
    """Start the Plus500 auto-trader (called from dashboard)."""
    global _bot_instance, _bot_task, _enabled

    if _bot_instance is not None:
        return {"ok": True, "msg": "Trader already running"}

    _bot_instance = Plus500Bot()
    await _bot_instance.start()
    _enabled = True
    _trade_stats["start_time"] = datetime.now().isoformat()
    _bot_task = asyncio.create_task(signal_listener(_bot_instance))
    _pl_monitor_task = asyncio.create_task(pl_monitor(_bot_instance))
    return {"ok": True, "msg": f"Trader started — listening for signals (max loss: ${MAX_LOSS})"}


async def stop_trader():
    """Stop the Plus500 auto-trader (called from dashboard)."""
    global _bot_instance, _bot_task, _enabled, _position, _pl_monitor_task

    _enabled = False
    if _bot_task:
        _bot_task.cancel()
        _bot_task = None
    if _pl_monitor_task:
        _pl_monitor_task.cancel()
        _pl_monitor_task = None
    if _bot_instance:
        await _bot_instance.stop()
        _bot_instance = None
    _position = 0
    return {"ok": True, "msg": "Trader stopped"}


async def toggle_trader():
    """Toggle auto-trading on/off without closing Plus500."""
    global _enabled
    _enabled = not _enabled
    state = "ENABLED" if _enabled else "PAUSED"
    print(f"   ⚡ Auto-trading {state}")
    return {"ok": True, "enabled": _enabled, "msg": f"Auto-trading {state}"}


# =============================================================================
# STANDALONE MODE
# =============================================================================

async def main():
    """Run as standalone script."""
    global _enabled

    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║         PLUS500 AUTO-TRADER — Signal Driven              ║
    ║                                                          ║
    ║  Connects to Gold Chart → executes on Plus500            ║
    ║                                                          ║
    ║  Signals: open_long → BUY, open_short → SELL            ║
    ║           close_long/short → Close position              ║
    ║                                                          ║
    ║  TP: ${tp}    SL: ${sl}    Cooldown: {cd}s            ║
    ║  Max positions: {mp}                                     ║
    ╚══════════════════════════════════════════════════════════╝
    """.format(tp=TAKE_PROFIT, sl=STOP_LOSS, cd=TRADE_COOLDOWN, mp=MAX_POSITIONS))

    bot = Plus500Bot()
    print("── Step 1: Opening Plus500 ──")
    await bot.start()

    print("\n── Step 2: Waiting for you to verify Plus500 is ready ──")
    print("   Make sure you're on the Gold trading page")
    print("   Press Enter to start auto-trading...")
    await asyncio.to_thread(input)

    _enabled = True
    print("\n── Step 3: Listening for signals ──")
    print("   Auto-trading is ON — press Ctrl+C to stop\n")

    try:
        await signal_listener(bot)
    except KeyboardInterrupt:
        print("\n   ⏹ Stopping...")
    finally:
        _enabled = False
        await bot.stop()
        print("   ✓ Auto-trader stopped")


if __name__ == "__main__":
    asyncio.run(main())
