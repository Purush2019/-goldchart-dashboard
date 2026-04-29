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
import sys
import webbrowser
import threading
import time
import http.server
import re
import urllib.request
from datetime import datetime

# Force UTF-8 output on Windows to avoid emoji encode errors
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import websockets
    import asyncio
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets
    import asyncio

import traceback
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes


# =============================================================================
# KEEP-ALIVE (Windows only — prevents sleep/screen-off)
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
    if not IS_WINDOWS:
        print("   🔋 Keep-alive skipped (not Windows)")
        return None
    t = threading.Thread(target=keep_alive_loop, daemon=True)
    t.start()
    print("   🔋 Keep-alive active")
    return t


# =============================================================================
# SETTINGS
# =============================================================================

# Coinbase API
COINBASE_API = "https://api.coinbase.com/api/v3/brokerage/market"
GRANULARITY = "ONE_MINUTE"
REFRESH_SEC = 5            # Refresh every 5 seconds (default)
_refresh_sec = REFRESH_SEC  # Dynamic refresh interval (mutable)
HISTORY_HOURS = 72         # How many hours of history to fetch (3 days)

# Port (HTTP + WebSocket on single port for easy tunnel/mobile access)
HTTP_PORT = 8081

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

# --- Multi-Instrument Support ---
INSTRUMENTS = {
    "GOLD":    {"type": "future", "prefix": "GOL", "display": "Gold Futures",   "icon": "\U0001f947"},
    "SILVER":  {"type": "future", "prefix": "SLR", "display": "Silver Futures", "icon": "\U0001f948"},
    "OIL":     {"type": "future", "prefix": "NOL", "display": "Crude Oil",      "icon": "\U0001f6e2\ufe0f"},
    "BTC-USD": {"type": "spot", "product_id": "BTC-USD", "display": "Bitcoin",  "icon": "\u20bf"},
    "ETH-USD": {"type": "spot", "product_id": "ETH-USD", "display": "Ethereum", "icon": "\u039e"},
    "SOL-USD": {"type": "spot", "product_id": "SOL-USD", "display": "Solana",   "icon": "\u25ce"},
}
DEFAULT_INSTRUMENT = "GOLD"


# =============================================================================
# COINBASE DATA FETCHER (multi-instrument)
# =============================================================================

# Per-instrument state
_product_ids = {}        # inst_key -> resolved Coinbase product_id
_candle_caches = {}      # inst_key -> {timestamp -> candle dict}
_cache_lock = threading.Lock()
_discovery_times = {}    # inst_key -> epoch when contract was last discovered
_REDISCOVER_INTERVAL = 3600  # re-check contracts every hour


def _parse_contract_expiry(product_id):
    """Parse the expiry date from a Coinbase futures product ID like NOL-27MAR26-CDE."""
    m = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:-|$)", product_id)
    if not m:
        return None
    day = int(m.group(1))
    month = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    }.get(m.group(2), 0)
    year = 2000 + int(m.group(3))
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def discover_product(inst_key):
    """Discover/resolve the Coinbase product ID for an instrument."""
    inst = INSTRUMENTS[inst_key]

    if inst["type"] == "spot":
        pid = inst["product_id"]
        _product_ids[inst_key] = pid
        try:
            url = f"{COINBASE_API}/products/{pid}"
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            r = urllib.request.urlopen(req, timeout=10)
            data = json.loads(r.read())
            price = data.get("price", "?")
            display = data.get("display_name", pid)
            print(f"   ✓ Found {inst_key}: {pid} ({display}) @ ${price}")
        except Exception as e:
            print(f"   ✓ Registered {inst_key}: {pid} (price check skipped: {e})")
        return pid

    # Future products need auto-discovery
    prefix = inst["prefix"]
    url = f"{COINBASE_API}/products?product_type=FUTURE"
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    r = urllib.request.urlopen(req, timeout=10)
    data = json.loads(r.read())
    products = data.get("products", [])

    matches = [p for p in products
               if p.get("product_id", "").startswith(prefix)
               and not p.get("trading_disabled", True)
               and p.get("price", "")]

    if not matches:
        matches = [p for p in products if p.get("product_id", "").startswith(prefix)]

    if not matches:
        raise RuntimeError(f"No {inst_key} futures found on Coinbase!")

    matches.sort(key=lambda p: (_parse_contract_expiry(p.get("product_id", "")) or datetime.max,
                               p.get("product_id", "")))
    pid = matches[0]["product_id"]
    _product_ids[inst_key] = pid
    price = matches[0].get("price", "?")
    display = matches[0].get("display_name", pid)
    print(f"   ✓ Found {inst_key}: {pid} ({display}) @ ${price}")
    return pid


def _get_cache(inst_key):
    """Get the candle cache for an instrument, creating if needed."""
    if inst_key not in _candle_caches:
        _candle_caches[inst_key] = {}
    return _candle_caches[inst_key]


def _get_product_id(inst_key):
    """Get/discover the product ID for an instrument."""
    if inst_key not in _product_ids:
        discover_product(inst_key)
    return _product_ids[inst_key]


def fetch_candles_full(inst_key):
    """Full historical fetch for an instrument."""
    pid = _get_product_id(inst_key)
    cache = _get_cache(inst_key)

    print(f"   \U0001f4e1 Full download: {pid} ({GRANULARITY}, {HISTORY_HOURS}h)...")

    end_ts = int(time.time())
    start_ts = end_ts - HISTORY_HOURS * 3600
    batch_size = 300 * 60  # 300 minutes per batch (API limit ~350)

    all_candles = []
    cursor = end_ts
    requests_made = 0

    while cursor > start_ts and requests_made < 30:
        batch_end = cursor
        batch_start = max(cursor - batch_size, start_ts)

        url = (f"{COINBASE_API}/products/{pid}/candles"
               f"?start={batch_start}&end={batch_end}&granularity={GRANULARITY}")

        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            r = urllib.request.urlopen(req, timeout=15)
            data = json.loads(r.read())
            candles = data.get("candles", [])
        except Exception as e:
            print(f"   \u26a0\ufe0f  Batch fetch error: {e}")
            break

        if not candles:
            break

        all_candles.extend(candles)
        requests_made += 1
        cursor = batch_start

        if requests_made < 30:
            time.sleep(0.15)

    with _cache_lock:
        for c in all_candles:
            cache[int(c["start"])] = c

    print(f"   ✓ Full download [{inst_key}]: {len(cache)} candles ({requests_made} API calls)")
    return _get_sorted_candles(inst_key)


def fetch_realtime_price(inst_key):
    """Fetch the real-time price for an instrument."""
    try:
        pid = _get_product_id(inst_key)
        url = f"{COINBASE_API}/products/{pid}"
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        r = urllib.request.urlopen(req, timeout=5)
        data = json.loads(r.read())
        mid = data.get("mid_market_price") or data.get("price") or "0"
        return float(mid)
    except Exception:
        return None


def fetch_candles_incremental(inst_key):
    """Quick incremental fetch for an instrument."""
    pid = _get_product_id(inst_key)
    cache = _get_cache(inst_key)

    end_ts = int(time.time())
    start_ts = end_ts - 600  # last 10 minutes

    url = (f"{COINBASE_API}/products/{pid}/candles"
           f"?start={start_ts}&end={end_ts}&granularity={GRANULARITY}")

    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        r = urllib.request.urlopen(req, timeout=10)
        data = json.loads(r.read())
        candles = data.get("candles", [])
    except Exception as e:
        print(f"   \u26a0\ufe0f  Incremental fetch error ({inst_key}): {e}")
        return _get_sorted_candles(inst_key)

    live_price = fetch_realtime_price(inst_key)

    new_count = 0
    updated = 0
    with _cache_lock:
        for c in candles:
            ts = int(c["start"])
            if ts not in cache:
                new_count += 1
            else:
                updated += 1
            cache[ts] = c

        if live_price and cache:
            latest_ts = max(cache.keys())
            latest = cache[latest_ts]
            latest_copy = dict(latest)
            latest_copy["close"] = str(live_price)
            if live_price > float(latest_copy.get("high", 0)):
                latest_copy["high"] = str(live_price)
            if live_price < float(latest_copy.get("low", 999999)):
                latest_copy["low"] = str(live_price)
            cache[latest_ts] = latest_copy

        cutoff = end_ts - HISTORY_HOURS * 3600
        old_keys = [k for k in cache if k < cutoff]
        for k in old_keys:
            del cache[k]

    price_str = f" · live ${live_price:.2f}" if live_price else ""
    if new_count > 0:
        print(f"   ✓ [{inst_key}] +{new_count} new, {updated} updated ({len(cache)} total){price_str}")
    else:
        print(f"   ✓ [{inst_key}] {updated} updated ({len(cache)} total){price_str}")

    return _get_sorted_candles(inst_key)


def _get_sorted_candles(inst_key):
    """Return cache for an instrument as sorted list."""
    cache = _get_cache(inst_key)
    with _cache_lock:
        items = list(cache.values())
    items.sort(key=lambda c: int(c["start"]))
    return items


def fetch_candles(inst_key):
    """Smart fetch: full on first call, incremental after."""
    cache = _get_cache(inst_key)
    if not cache:
        return fetch_candles_full(inst_key)
    else:
        return fetch_candles_incremental(inst_key)


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

def build_chart_message(raw_candles, inst_key=None):
    """Build the full chart data message from Coinbase candle data."""
    if inst_key is None:
        inst_key = DEFAULT_INSTRUMENT
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
            chart_markers.append({"time": t, "position": "belowBar", "color": "#10b981", "shape": "arrowUp", "text": m["label"]})
        elif a == "open_short":
            chart_markers.append({"time": t, "position": "aboveBar", "color": "#ef4444", "shape": "arrowDown", "text": m["label"]})
        elif a == "close_long":
            chart_markers.append({"time": t, "position": "aboveBar", "color": "#f59e0b", "shape": "arrowDown", "text": m["label"]})
        elif a == "close_short":
            chart_markers.append({"time": t, "position": "belowBar", "color": "#6366f1", "shape": "arrowUp", "text": m["label"]})

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
            "symbol": _product_ids.get(inst_key, inst_key),
            "display": INSTRUMENTS.get(inst_key, {}).get("display", inst_key),
            "instrument": inst_key,
            "interval": "1m",
            "candles": len(candles),
            "signals": len(chart_markers),
            "last_price": round(float(closes[-1]), 2) if n > 0 else 0,
        },
    }


# =============================================================================
# WEB SERVER
# =============================================================================

# Per-instrument cached data + per-client instrument tracking
_current_data = {}       # inst_key -> chart message
ws_clients = set()
_client_instruments = {} # websocket -> inst_key

async def ws_handler(websocket):
    ws_clients.add(websocket)
    _client_instruments[websocket] = DEFAULT_INSTRUMENT
    try:
        # Send default instrument data on connect
        inst = DEFAULT_INSTRUMENT
        if inst in _current_data and _current_data[inst]:
            await websocket.send(json.dumps(_current_data[inst]))
        # Also send instrument list
        inst_list = []
        for key, info in INSTRUMENTS.items():
            inst_list.append({"key": key, "display": info["display"], "icon": info.get("icon", ""), "type": info["type"]})
        await websocket.send(json.dumps({"type": "instruments", "list": inst_list, "default": DEFAULT_INSTRUMENT}))

        async for msg in websocket:
            try:
                parsed = json.loads(msg)
                if isinstance(parsed, dict) and "switch" in parsed:
                    new_inst = parsed["switch"]
                    if new_inst in INSTRUMENTS:
                        _client_instruments[websocket] = new_inst
                        if new_inst in _current_data and _current_data[new_inst]:
                            await websocket.send(json.dumps(_current_data[new_inst]))
                        else:
                            await refresh_instrument(new_inst)
                            if new_inst in _current_data and _current_data[new_inst]:
                                await websocket.send(json.dumps(_current_data[new_inst]))
                    continue
                if "set_refresh" in parsed:
                    val = parsed["set_refresh"]
                    if isinstance(val, (int, float)) and 1 <= val <= 300:
                        global _refresh_sec
                        _refresh_sec = int(val)
                        print(f"   ⏱️  Refresh interval changed to {_refresh_sec}s")
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            if msg == "refresh":
                inst = _client_instruments.get(websocket, DEFAULT_INSTRUMENT)
                await refresh_instrument(inst)
                if inst in _current_data and _current_data[inst]:
                    await websocket.send(json.dumps(_current_data[inst]))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        ws_clients.discard(websocket)
        _client_instruments.pop(websocket, None)

async def broadcast_instrument(inst_key, msg):
    """Broadcast to all clients viewing a specific instrument."""
    global ws_clients
    if not ws_clients:
        return
    data = json.dumps(msg)
    dead = set()
    for ws in ws_clients:
        if _client_instruments.get(ws) == inst_key:
            try:
                await ws.send(data)
            except:
                dead.add(ws)
    ws_clients -= dead
    for ws in dead:
        _client_instruments.pop(ws, None)

def _maybe_rediscover(inst_key, candle_count=None):
    """Re-discover contract if it's stale or returning too few candles."""
    inst = INSTRUMENTS.get(inst_key)
    if not inst or inst.get("type") == "spot":
        return  # spot products don't need re-discovery

    now = time.time()
    last_disc = _discovery_times.get(inst_key, 0)
    need_rediscover = False

    if now - last_disc > _REDISCOVER_INTERVAL:
        need_rediscover = True
        print(f"   🔄 Periodic contract re-discovery for {inst_key}...")

    if candle_count is not None and candle_count < 10:
        need_rediscover = True
        print(f"   ⚠️  Only {candle_count} candles for {inst_key} — contract may have expired")

    if need_rediscover:
        old_pid = _product_ids.get(inst_key)
        try:
            discover_product(inst_key)
            _discovery_times[inst_key] = now
            new_pid = _product_ids.get(inst_key)
            if new_pid != old_pid:
                print(f"   🔄 Contract changed for {inst_key}: {old_pid} → {new_pid} — resetting cache")
                with _cache_lock:
                    _candle_caches[inst_key] = {}
        except Exception as e:
            print(f"   ⚠️  Re-discovery failed for {inst_key}: {e}")


async def refresh_instrument(inst_key):
    """Refresh data for a specific instrument."""
    try:
        # Check if contract needs re-discovery before fetching
        cur = _current_data.get(inst_key)
        candle_count = cur['info']['candles'] if cur and 'info' in cur else None
        await asyncio.to_thread(_maybe_rediscover, inst_key, candle_count)

        raw = await asyncio.to_thread(fetch_candles, inst_key)
        if raw:
            msg = await asyncio.to_thread(build_chart_message, raw, inst_key)
            if msg:
                _current_data[inst_key] = msg
                print(f"   ✓ [{inst_key}] {msg['info']['candles']} candles, "
                      f"{msg['info']['signals']} signals, "
                      f"last: ${msg['info']['last_price']:.2f}")
                await broadcast_instrument(inst_key, msg)
    except Exception as e:
        print(f"   ❌ Refresh error ({inst_key}): {e}")

async def refresh_data():
    """Refresh all instruments that have active viewers."""
    active = {DEFAULT_INSTRUMENT}
    for ws, inst in _client_instruments.items():
        active.add(inst)
    for inst_key in active:
        await refresh_instrument(inst_key)

async def auto_refresh():
    while True:
        await asyncio.sleep(_refresh_sec)
        active = {DEFAULT_INSTRUMENT}
        for ws, inst in _client_instruments.items():
            active.add(inst)
        for inst_key in active:
            print(f"\n   🔄 Auto-refreshing {inst_key}...")
            await refresh_instrument(inst_key)


def _serve_file(file_path, content_type="text/html"):
    """Read a file and return websockets Response."""
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    try:
        with open(file_path, "rb") as f:
            body = f.read()
        print(f"   [HTTP] Serving {os.path.basename(file_path)}: {len(body)} bytes, has rightOffset: {b'rightOffset' in body}")
        return Response(200, "OK", Headers({
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }), body)
    except FileNotFoundError:
        return Response(404, "Not Found", Headers(), b"Not Found")


async def process_request(connection, request):
    """Handle HTTP requests on the same port as WebSocket.
    Serves chart_coinbase.html for regular HTTP; returns None for WS upgrades.
    """
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    # Let WebSocket upgrades through
    upgrade_header = request.headers.get("Upgrade", "").lower()
    if upgrade_header == "websocket":
        return None

    chart_dir = os.path.dirname(os.path.abspath(__file__))
    path = request.path.split('?')[0]

    if path in ("/", "/chart_coinbase.html", "/chart.html", "/v2"):
        return _serve_file(os.path.join(chart_dir, "chart_coinbase.html"), "text/html; charset=utf-8")
    if path == "/qr" or path == "/qr.html":
        return _serve_file(os.path.join(chart_dir, "qr.html"), "text/html; charset=utf-8")
    if path == "/favicon.ico":
        return Response(204, "No Content", Headers(), b"")
    if path == "/ping":
        import time as _t
        body = json.dumps({"server": "local-coinbase", "pid": os.getpid(), "time": _t.time(), "product": _product_ids.get("GOLD", "?")}).encode()
        return Response(200, "OK", Headers({"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}), body)
    if path == "/api/instruments":
        inst_list = []
        for key, info in INSTRUMENTS.items():
            inst_list.append({"key": key, "display": info["display"], "icon": info.get("icon", ""), "type": info["type"]})
        body = json.dumps(inst_list).encode()
        return Response(200, "OK", Headers({
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        }), body)
    if path == "/api/signal":
        # Return the latest signal from chart data (used by auto-trader for pre-order confirmation)
        inst_key = DEFAULT_INSTRUMENT
        signal_data = {"signal": None, "time": 0, "instrument": inst_key}
        if inst_key in _current_data and _current_data[inst_key]:
            markers = _current_data[inst_key].get("markers", [])
            if markers:
                last_marker = markers[-1]
                # Extract signal action from marker text (e.g. "Buy\nopen_long" -> "open_long")
                text = last_marker.get("text", "")
                action = text.split("\n")[-1].strip() if text else ""
                signal_data = {
                    "signal": action,
                    "time": last_marker.get("time", 0),
                    "instrument": inst_key,
                    "price": _current_data[inst_key].get("info", {}).get("last_price", 0),
                }
        body = json.dumps(signal_data).encode()
        return Response(200, "OK", Headers({
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        }), body)
    if path == "/config.json":
        import socket
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        config = json.dumps({"port": HTTP_PORT, "local_ip": local_ip}).encode()
        return Response(200, "OK", Headers({
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        }), config)

    return Response(404, "Not Found", Headers(), b"Not Found")


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
    time.sleep(0.5)


# =============================================================================
# MAIN + WATCHDOG
# =============================================================================

async def main():
    global ws_clients, _current_data
    ws_clients = set()
    _current_data = {}

    inst_names = ", ".join(INSTRUMENTS[k]["display"] for k in INSTRUMENTS)
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║   MULTI-INSTRUMENT CHART — COINBASE Edition              ║
    ║                                                           ║
    ║  Instruments: {inst_names:<43s} ║
    ║  All Pine Script indicators computed in Python:           ║
    ║    • SSL Hybrid (HMA 60 / EMA 5 / HMA 15 exit)          ║
    ║    • EMA Crossover (9 / 21)                              ║
    ║    • UT Bot Alerts (sensitivity 1, ATR 10)               ║
    ║    • Bollinger Bands (20, 2.0)                           ║
    ║                                                           ║
    ║  NO API key needed — public endpoints only               ║
    ║  Port: {HTTP_PORT} (HTTP + WebSocket combined)                    ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    start_keep_alive()

    # 1. Discover default instrument
    print(f"── Step 1: Discovering {DEFAULT_INSTRUMENT} contract ──")
    discover_product(DEFAULT_INSTRUMENT)
    _discovery_times[DEFAULT_INSTRUMENT] = time.time()

    # 2. Start combined HTTP+WS server on single port
    print("\n── Step 2: Starting server (HTTP + WebSocket on single port) ──")
    ws_server = await websockets.serve(
        ws_handler, "0.0.0.0", HTTP_PORT,
        process_request=process_request,
        max_size=10 * 1024 * 1024,
    )
    import socket as _sock
    try:
        _s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _local_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _local_ip = "127.0.0.1"
    print(f"   ✓ Server on http://0.0.0.0:{HTTP_PORT}/chart_coinbase.html")
    print(f"   📱 Mobile (same WiFi): http://{_local_ip}:{HTTP_PORT}/chart_coinbase.html")

    # 3. Load data for default instrument
    print(f"\n── Step 3: Loading {HISTORY_HOURS}h of {DEFAULT_INSTRUMENT} data ──")
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
            if IS_WINDOWS:
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
