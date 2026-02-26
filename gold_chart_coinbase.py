"""
Gold Chart — Coinbase Derivatives Edition
============================================
Downloads real Gold Futures data from COINBASE public API.
Same contract as your TradingView chart (GOLJ2026).
Computes ALL indicators from your Pine Script locally.

NO API key needed — uses public endpoints.
Runs on ports 8081 (HTTP) / 8766 (WebSocket) so you can
compare side-by-side with the Yahoo Finance version.

Just run:  python gold_chart_coinbase.py
"""

import numpy as np
import math
import json
import os
import webbrowser
import threading
import time
import http.server
import urllib.request
from datetime import datetime

try:
    import websockets
    import asyncio
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets
    import asyncio

import ctypes
import ctypes.wintypes
import traceback


# =============================================================================
# KEEP-ALIVE
# =============================================================================

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

def start_keep_alive():
    t = threading.Thread(target=keep_alive_loop, daemon=True)
    t.start()
    print("   🔋 Keep-alive active")
    return t


# =============================================================================
# SETTINGS
# =============================================================================

# Coinbase Derivatives Gold Futures — auto-detected at startup
COINBASE_API = "https://api.coinbase.com/api/v3/brokerage/market"
PRODUCT_ID = None          # Will be auto-detected (e.g. GOL-27MAR26-CDE)
GRANULARITY = "ONE_MINUTE"
REFRESH_SEC = 5            # Refresh every 5 seconds
HISTORY_HOURS = 72         # How many hours of history to fetch (3 days)

# Ports (different from Yahoo version so both can run simultaneously)
HTTP_PORT = 8081
WS_PORT = 8766

# --- SSL Hybrid ---
BASELINE_TYPE = "HMA"
BASELINE_LEN = 60
SSL2_TYPE = "EMA"
SSL2_LEN = 5
SSL3_TYPE = "HMA"
SSL3_LEN = 15

# --- EMA Crossover ---
EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21

# --- UT Bot Alerts ---
UT_SENSITIVITY = 1
UT_ATR_PERIOD = 10

# --- Bollinger Bands ---
BB_PERIOD = 20
BB_MULT = 2.0


# =============================================================================
# COINBASE DATA FETCHER
# =============================================================================

def discover_gold_product():
    """Find the active (front-month) gold futures contract on Coinbase."""
    global PRODUCT_ID
    url = f"{COINBASE_API}/products?product_type=FUTURE"
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    r = urllib.request.urlopen(req, timeout=10)
    data = json.loads(r.read())
    products = data.get("products", [])

    # Filter gold futures that are actively trading
    gold = [p for p in products
            if p.get("product_id", "").startswith("GOL")
            and not p.get("trading_disabled", True)
            and p.get("price", "")]

    if not gold:
        # Fallback: any GOL product
        gold = [p for p in products if p.get("product_id", "").startswith("GOL")]

    if not gold:
        raise RuntimeError("No Gold Futures found on Coinbase!")

    # Pick the one with the nearest expiry (front-month)
    gold.sort(key=lambda p: p.get("product_id", ""))
    PRODUCT_ID = gold[0]["product_id"]
    price = gold[0].get("price", "?")
    display = gold[0].get("display_name", PRODUCT_ID)
    print(f"   ✓ Found contract: {PRODUCT_ID} ({display}) @ ${price}")
    return PRODUCT_ID


# Persistent candle cache — avoids full re-download every refresh
_candle_cache = {}   # timestamp -> candle dict
_cache_lock = threading.Lock()


def fetch_coinbase_candles_full():
    """Full historical fetch (used on first load only)."""
    if not PRODUCT_ID:
        discover_gold_product()

    print(f"   📡 Full download: {PRODUCT_ID} ({GRANULARITY}, {HISTORY_HOURS}h)...")

    end_ts = int(time.time())
    start_ts = end_ts - HISTORY_HOURS * 3600
    batch_size = 300 * 60  # 300 minutes per batch (API limit ~350)

    all_candles = []
    cursor = end_ts
    requests_made = 0

    while cursor > start_ts and requests_made < 30:
        batch_end = cursor
        batch_start = max(cursor - batch_size, start_ts)

        url = (f"{COINBASE_API}/products/{PRODUCT_ID}/candles"
               f"?start={batch_start}&end={batch_end}&granularity={GRANULARITY}")

        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            r = urllib.request.urlopen(req, timeout=15)
            data = json.loads(r.read())
            candles = data.get("candles", [])
        except Exception as e:
            print(f"   ⚠️  Batch fetch error: {e}")
            break

        if not candles:
            break

        all_candles.extend(candles)
        requests_made += 1
        cursor = batch_start

        if requests_made < 30:
            time.sleep(0.15)

    # Store in cache
    with _cache_lock:
        for c in all_candles:
            _candle_cache[int(c["start"])] = c

    print(f"   ✓ Full download: {len(_candle_cache)} candles ({requests_made} API calls)")
    return _get_sorted_candles()


def fetch_realtime_price():
    """Fetch the real-time price from Coinbase (1 fast API call).
    Prefers mid_market_price (bid/ask midpoint) which updates even when
    no trades are happening.  Falls back to last-trade price."""
    try:
        url = f"{COINBASE_API}/products/{PRODUCT_ID}"
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        r = urllib.request.urlopen(req, timeout=5)
        data = json.loads(r.read())
        # mid_market_price updates with every bid/ask change (much more frequent)
        mid = data.get("mid_market_price") or data.get("price") or "0"
        return float(mid)
    except Exception:
        return None


def fetch_coinbase_candles_incremental():
    """Quick incremental fetch — last 10 min candles + real-time tick price."""
    if not PRODUCT_ID:
        discover_gold_product()

    end_ts = int(time.time())
    start_ts = end_ts - 600  # last 10 minutes

    url = (f"{COINBASE_API}/products/{PRODUCT_ID}/candles"
           f"?start={start_ts}&end={end_ts}&granularity={GRANULARITY}")

    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        r = urllib.request.urlopen(req, timeout=10)
        data = json.loads(r.read())
        candles = data.get("candles", [])
    except Exception as e:
        print(f"   ⚠️  Incremental fetch error: {e}")
        return _get_sorted_candles()

    # Also get real-time tick price
    live_price = fetch_realtime_price()

    new_count = 0
    updated = 0
    with _cache_lock:
        for c in candles:
            ts = int(c["start"])
            if ts not in _candle_cache:
                new_count += 1
            else:
                updated += 1
            _candle_cache[ts] = c

        # Inject real-time price into the latest candle
        # This makes the chart show live price changes within the minute
        if live_price and _candle_cache:
            latest_ts = max(_candle_cache.keys())
            latest = _candle_cache[latest_ts]
            # If live_price is newer than the candle's close, update it
            latest_copy = dict(latest)
            latest_copy["close"] = str(live_price)
            if live_price > float(latest_copy.get("high", 0)):
                latest_copy["high"] = str(live_price)
            if live_price < float(latest_copy.get("low", 999999)):
                latest_copy["low"] = str(live_price)
            _candle_cache[latest_ts] = latest_copy

        # Trim old candles beyond HISTORY_HOURS
        cutoff = end_ts - HISTORY_HOURS * 3600
        old_keys = [k for k in _candle_cache if k < cutoff]
        for k in old_keys:
            del _candle_cache[k]

    price_str = f" · live ${live_price:.2f}" if live_price else ""
    if new_count > 0:
        print(f"   ✓ +{new_count} new, {updated} updated ({len(_candle_cache)} total){price_str}")
    else:
        print(f"   ✓ {updated} updated ({len(_candle_cache)} total){price_str}")

    return _get_sorted_candles()


def _get_sorted_candles():
    """Return cache as a sorted list."""
    with _cache_lock:
        items = list(_candle_cache.values())
    items.sort(key=lambda c: int(c["start"]))
    return items


def fetch_coinbase_candles():
    """Smart fetch: full on first call, incremental after."""
    if not _candle_cache:
        return fetch_coinbase_candles_full()
    else:
        return fetch_coinbase_candles_incremental()


# =============================================================================
# MOVING AVERAGE FUNCTIONS (same as Yahoo version)
# =============================================================================

def sma_arr(data, period):
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    cs = np.cumsum(data)
    cs = np.insert(cs, 0, 0)
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        result[i] = (cs[i + 1] - cs[i - period + 1]) / period
    return result

def ema_arr(data, period):
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    start = -1
    for s in range(n - period + 1):
        window = data[s:s + period]
        if not np.any(np.isnan(window)):
            start = s
            break
    if start < 0:
        return result
    result[start + period - 1] = np.mean(data[start:start + period])
    k = 2.0 / (period + 1)
    for i in range(start + period, n):
        if np.isnan(data[i]):
            result[i] = result[i - 1]
        else:
            result[i] = data[i] * k + result[i - 1] * (1.0 - k)
    return result

def wma_arr(data, period):
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    weights = np.arange(1.0, period + 1)
    wsum = weights.sum()
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        result[i] = np.dot(window, weights) / wsum
    return result

def hma_arr(data, period):
    half = max(int(period / 2), 1)
    sqrt_p = max(round(math.sqrt(period)), 1)
    w_half = wma_arr(data, half)
    w_full = wma_arr(data, period)
    diff = 2.0 * w_half - w_full
    return wma_arr(diff, sqrt_p)

def dema_arr(data, period):
    e1 = ema_arr(data, period)
    e2 = ema_arr(e1, period)
    return 2.0 * e1 - e2

def tema_arr(data, period):
    e1 = ema_arr(data, period)
    e2 = ema_arr(e1, period)
    e3 = ema_arr(e2, period)
    return 3.0 * (e1 - e2) + e3

def tma_arr(data, period):
    s1 = sma_arr(data, period)
    return sma_arr(s1, period)

def lsma_arr(data, period):
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    x = np.arange(float(period))
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        coeffs = np.polyfit(x, window, 1)
        result[i] = coeffs[0] * (period - 1) + coeffs[1]
    return result

def kijun_arr(data, period):
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        result[i] = (np.max(window) + np.min(window)) / 2.0
    return result

def ma_dispatch(ma_type, data, period):
    dispatch = {
        "SMA": sma_arr, "EMA": ema_arr, "DEMA": dema_arr,
        "TEMA": tema_arr, "LSMA": lsma_arr, "WMA": wma_arr,
        "TMA": tma_arr, "HMA": hma_arr, "Kijun v2": kijun_arr,
        "McGinley": ema_arr,
    }
    return dispatch.get(ma_type, sma_arr)(data, period)


# =============================================================================
# INDICATOR CALCULATIONS (identical to Yahoo version)
# =============================================================================

def compute_ssl(highs, lows, closes, ma_type, period):
    n = len(closes)
    ma_high = ma_dispatch(ma_type, highs, period)
    ma_low = ma_dispatch(ma_type, lows, period)
    hlv = np.zeros(n)
    for i in range(n):
        if np.isnan(ma_high[i]) or np.isnan(ma_low[i]):
            hlv[i] = hlv[i - 1] if i > 0 else 0
        elif closes[i] > ma_high[i]:
            hlv[i] = 1
        elif closes[i] < ma_low[i]:
            hlv[i] = -1
        else:
            hlv[i] = hlv[i - 1] if i > 0 else 0
    ssl_down = np.where(hlv < 0, ma_high, ma_low)
    ssl_up = np.where(hlv < 0, ma_low, ma_high)
    return hlv, ssl_up, ssl_down

def compute_ema_cross(closes, fast_len, slow_len):
    fast = ema_arr(closes, fast_len)
    slow = ema_arr(closes, slow_len)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(fast[i]) or np.isnan(slow[i]) or np.isnan(fast[i-1]) or np.isnan(slow[i-1]):
            continue
        buy[i] = fast[i] > slow[i] and fast[i-1] <= slow[i-1]
        sell[i] = fast[i] < slow[i] and fast[i-1] >= slow[i-1]
    return buy, sell, fast, slow

def compute_ut_bot(closes, highs, lows, sensitivity, atr_period):
    n = len(closes)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = np.full(n, np.nan)
    if n >= atr_period:
        atr[atr_period - 1] = np.mean(tr[:atr_period])
        alpha = 1.0 / atr_period
        for i in range(atr_period, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]
    n_loss = sensitivity * atr
    stop = np.zeros(n)
    pos = np.zeros(n)
    for i in range(1, n):
        if np.isnan(n_loss[i]):
            stop[i] = stop[i-1]; pos[i] = pos[i-1]; continue
        ps = stop[i-1]; c = closes[i]; c1 = closes[i-1]; nl = n_loss[i]
        if c > ps and c1 > ps:
            stop[i] = max(ps, c - nl)
        elif c < ps and c1 < ps:
            stop[i] = min(ps, c + nl)
        elif c > ps:
            stop[i] = c - nl
        else:
            stop[i] = c + nl
        if c1 < stop[i-1] and c > stop[i-1]:
            pos[i] = 1
        elif c1 > stop[i-1] and c < stop[i-1]:
            pos[i] = -1
        else:
            pos[i] = pos[i-1]
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        buy[i] = pos[i] > 0 and pos[i-1] <= 0
        sell[i] = pos[i] < 0 and pos[i-1] >= 0
    return buy, sell, stop, pos

def compute_bollinger(closes, period, mult):
    n = len(closes)
    basis = sma_arr(closes, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        if np.isnan(basis[i]): continue
        window = closes[i - period + 1:i + 1]
        std = np.std(window, ddof=0)
        upper[i] = basis[i] + mult * std
        lower[i] = basis[i] - mult * std
    return basis, upper, lower


# =============================================================================
# SIGNAL GENERATION (identical Pine Script logic)
# =============================================================================

def compute_all(opens, highs, lows, closes):
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    n = len(c)

    hlv1, ssl1_up, ssl1_down = compute_ssl(h, l, c, BASELINE_TYPE, BASELINE_LEN)
    hlv2, ssl2_up, ssl2_down = compute_ssl(h, l, c, SSL2_TYPE, SSL2_LEN)
    hlv3, ssl3_up, ssl3_down = compute_ssl(h, l, c, SSL3_TYPE, SSL3_LEN)
    ema_buy_arr, ema_sell_arr, ema_fast, ema_slow = compute_ema_cross(c, EMA_FAST_LEN, EMA_SLOW_LEN)
    ut_buy_arr, ut_sell_arr, ut_stop, ut_pos = compute_ut_bot(c, h, l, UT_SENSITIVITY, UT_ATR_PERIOD)
    bb_basis, bb_upper, bb_lower = compute_bollinger(c, BB_PERIOD, BB_MULT)

    position = 0
    markers = []
    for i in range(1, n):
        ssl_buy  = bool(hlv1[i] > 0 and hlv1[i-1] <= 0)
        ssl_sell = bool(hlv1[i] < 0 and hlv1[i-1] >= 0)
        ssl3_buy  = bool(hlv3[i] > 0 and hlv3[i-1] <= 0)
        ssl3_sell = bool(hlv3[i] < 0 and hlv3[i-1] >= 0)
        ema_buy  = bool(ema_buy_arr[i])
        ema_sell = bool(ema_sell_arr[i])
        ut_buy  = bool(ut_buy_arr[i])
        ut_sell = bool(ut_sell_arr[i])

        long_entry  = ssl_buy or ema_buy or ut_buy
        short_entry = ssl_sell or ema_sell or ut_sell
        long_exit  = ssl3_sell
        short_exit = ssl3_buy

        action = None
        if short_entry and position != -1:
            action = "open_short"; position = -1
        elif long_entry and position != 1:
            action = "open_long"; position = 1

        if action is None:
            if long_exit and position == 1:
                action = "close_long"; position = 0
            elif short_exit and position == -1:
                action = "close_short"; position = 0

        if action == "open_long":
            markers.append({"idx": i, "action": "open_long", "label": "Buy\nopen_long"})
        elif action == "open_short":
            markers.append({"idx": i, "action": "open_short", "label": "Sell\nopen_short"})
        elif action == "close_long":
            markers.append({"idx": i, "action": "close_long", "label": "close_long"})
        elif action == "close_short":
            markers.append({"idx": i, "action": "close_short", "label": "close_short"})

    return {
        "ssl1_up": ssl1_up, "ssl1_down": ssl1_down,
        "ssl2_up": ssl2_up, "ssl2_down": ssl2_down,
        "ssl3_up": ssl3_up, "ssl3_down": ssl3_down,
        "ema_fast": ema_fast, "ema_slow": ema_slow,
        "ut_stop": ut_stop,
        "bb_basis": bb_basis, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "markers": markers,
    }


# =============================================================================
# BUILD CHART MESSAGE
# =============================================================================

def build_chart_message(raw_candles):
    """Build the full chart data message from Coinbase candle data."""
    n = len(raw_candles)
    if n == 0:
        return None

    timestamps = np.array([int(c["start"]) for c in raw_candles])
    opens  = np.array([float(c["open"])  for c in raw_candles])
    highs  = np.array([float(c["high"])  for c in raw_candles])
    lows   = np.array([float(c["low"])   for c in raw_candles])
    closes = np.array([float(c["close"]) for c in raw_candles])

    indicators = compute_all(opens, highs, lows, closes)

    candles = []
    for i in range(n):
        candles.append({
            "time": int(timestamps[i]),
            "open": round(float(opens[i]), 2),
            "high": round(float(highs[i]), 2),
            "low": round(float(lows[i]), 2),
            "close": round(float(closes[i]), 2),
        })

    def make_line(arr):
        line = []
        for j in range(len(arr)):
            if not np.isnan(arr[j]):
                line.append({"time": int(timestamps[j]), "value": round(float(arr[j]), 2)})
        return line

    chart_markers = []
    for m in indicators["markers"]:
        i = m["idx"]
        t = int(timestamps[i])
        a = m["action"]
        if a == "open_long":
            chart_markers.append({"time": t, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": m["label"]})
        elif a == "open_short":
            chart_markers.append({"time": t, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": m["label"]})
        elif a == "close_long":
            chart_markers.append({"time": t, "position": "aboveBar", "color": "#ff9800", "shape": "arrowDown", "text": m["label"]})
        elif a == "close_short":
            chart_markers.append({"time": t, "position": "belowBar", "color": "#2196f3", "shape": "arrowUp", "text": m["label"]})

    return {
        "type": "init",
        "candles": candles,
        "markers": chart_markers,
        "ssl_up": make_line(indicators["ssl1_up"]),
        "ssl_down": make_line(indicators["ssl1_down"]),
        "ssl2_up": make_line(indicators["ssl2_up"]),
        "ssl2_down": make_line(indicators["ssl2_down"]),
        "ssl3_up": make_line(indicators["ssl3_up"]),
        "ssl3_down": make_line(indicators["ssl3_down"]),
        "ema_fast": make_line(indicators["ema_fast"]),
        "ema_slow": make_line(indicators["ema_slow"]),
        "ut_stop": make_line(indicators["ut_stop"]),
        "bb_basis": make_line(indicators["bb_basis"]),
        "bb_upper": make_line(indicators["bb_upper"]),
        "bb_lower": make_line(indicators["bb_lower"]),
        "info": {
            "symbol": PRODUCT_ID or "COINBASE GOLD",
            "interval": "1m",
            "candles": len(candles),
            "signals": len(chart_markers),
            "last_price": round(float(closes[-1]), 2) if n > 0 else 0,
        },
    }


# =============================================================================
# WEB SERVER
# =============================================================================

current_data = None
ws_clients = set()

async def ws_handler(websocket):
    ws_clients.add(websocket)
    try:
        if current_data:
            await websocket.send(json.dumps(current_data))
        async for msg in websocket:
            if msg == "refresh":
                await refresh_data()
                if current_data:
                    await websocket.send(json.dumps(current_data))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        ws_clients.discard(websocket)

async def broadcast(msg):
    global ws_clients
    if not ws_clients:
        return
    data = json.dumps(msg)
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send(data)
        except:
            dead.add(ws)
    ws_clients -= dead

async def refresh_data():
    global current_data
    try:
        raw = await asyncio.to_thread(fetch_coinbase_candles)
        if raw:
            current_data = await asyncio.to_thread(build_chart_message, raw)
            if current_data:
                print(f"   ✓ Data refreshed: {current_data['info']['candles']} candles, "
                      f"{current_data['info']['signals']} signals, "
                      f"last price: ${current_data['info']['last_price']:.2f}")
                await broadcast(current_data)
    except Exception as e:
        print(f"   ❌ Refresh error: {e}")

async def auto_refresh():
    while True:
        await asyncio.sleep(REFRESH_SEC)
        print(f"\n   🔄 Auto-refreshing data...")
        await refresh_data()

def start_http_server():
    chart_dir = os.path.dirname(os.path.abspath(__file__))
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=chart_dir, **kwargs)
        def log_message(self, format, *args):
            pass
    httpd = http.server.HTTPServer(("localhost", HTTP_PORT), QuietHandler)
    httpd.serve_forever()


# =============================================================================
# PORT CLEANUP
# =============================================================================

def kill_port(port):
    try:
        import subprocess
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid > 0:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
    except Exception:
        pass

def free_ports():
    kill_port(HTTP_PORT)
    kill_port(WS_PORT)
    time.sleep(0.5)


# =============================================================================
# MAIN + WATCHDOG
# =============================================================================

async def main():
    global ws_clients, current_data
    ws_clients = set()
    current_data = None

    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║       GOLD CHART — COINBASE Edition                       ║
    ║                                                           ║
    ║  Real Gold Futures from COINBASE Derivatives              ║
    ║  Same contract as TradingView (GOLJ2026)                 ║
    ║  All Pine Script indicators computed in Python:           ║
    ║    • SSL Hybrid (HMA 60 / EMA 5 / HMA 15 exit)          ║
    ║    • EMA Crossover (9 / 21)                              ║
    ║    • UT Bot Alerts (sensitivity 1, ATR 10)               ║
    ║    • Bollinger Bands (20, 2.0)                           ║
    ║                                                           ║
    ║  NO API key needed — public endpoints only               ║
    ║  Ports: HTTP {HTTP_PORT} / WS {WS_PORT} (Yahoo uses 8080/8765)       ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    start_keep_alive()

    # 1. Discover contract
    print("── Step 1: Finding Gold Futures contract ──")
    discover_gold_product()

    # 2. Start servers
    print("\n── Step 2: Starting servers ──")
    threading.Thread(target=start_http_server, daemon=True).start()
    print(f"   ✓ HTTP server on http://localhost:{HTTP_PORT}/chart_coinbase.html")
    ws_server = await websockets.serve(ws_handler, "localhost", WS_PORT)
    print(f"   ✓ WebSocket server on ws://localhost:{WS_PORT}")

    # 3. Load data
    print(f"\n── Step 3: Loading {HISTORY_HOURS}h of Coinbase Gold data ──")
    await refresh_data()

    # 4. Open browser
    print("\n── Step 4: Opening chart ──")
    await asyncio.sleep(0.5)
    if not os.environ.get("DASHBOARD_MODE"):
        webbrowser.open(f"http://localhost:{HTTP_PORT}/chart_coinbase.html")
        print("   ✓ Chart opened in browser!")
    else:
        print("   ✓ Running under dashboard — skipping browser open")
    print(f"\n   📊 Auto-refreshes every {REFRESH_SEC} seconds")
    print(f"   🛡️  Auto-restart watchdog is ON")
    print(f"   Press Ctrl+C to stop\n")

    await auto_refresh()


def run_with_watchdog():
    MAX_FAST_CRASHES = 5
    FAST_CRASH_WINDOW = 60
    crash_times = []
    attempt = 0

    while True:
        attempt += 1
        start_time = time.time()
        try:
            print(f"\n{'='*60}")
            if attempt > 1:
                print(f"   🔄 AUTO-RESTART — attempt #{attempt}")
                print(f"   ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            free_ports()
            asyncio.run(main())

        except KeyboardInterrupt:
            print("\n⏹  Stopped by user (Ctrl+C). Goodbye!")
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass
            break

        except Exception as e:
            elapsed = time.time() - start_time
            now = time.time()
            crash_times.append(now)
            crash_times[:] = [t for t in crash_times if now - t < FAST_CRASH_WINDOW]
            print(f"\n   ❌ Server crashed: {e}")
            traceback.print_exc()
            if len(crash_times) >= MAX_FAST_CRASHES:
                wait = 30; crash_times.clear()
                print(f"   ⚠️  Too many crashes — waiting {wait}s...")
            elif elapsed < 5:
                wait = 5
            else:
                wait = 2
            print(f"   ⏳ Restarting in {wait}s...")
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                print("\n⏹  Stopped by user (Ctrl+C). Goodbye!")
                break


if __name__ == "__main__":
    run_with_watchdog()
