"""
Real-Time 1-Second Gold Trader
================================
Implements the "All Strategies Combined" Pine Script indicator in Python.
Reads live gold price from Plus500, builds 1-second OHLC candles,
runs SSL Hybrid + EMA Crossover + UT Bot indicators locally.

NO TradingView Premium needed — all indicators computed in Python.

Indicators:
  • SSL Hybrid (HMA 60 baseline, EMA 5 SSL2, HMA 15 Exit)
  • EMA Crossover (9 / 21)
  • UT Bot Alerts (sensitivity 1, ATR period 10)

Signals: open_long, close_long, open_short, close_short
"""

import asyncio
import numpy as np
import math
import json
import os
import webbrowser
from datetime import datetime
from playwright.async_api import async_playwright
import ctypes
import threading
import time as _time
import http.server
import functools

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False
    print("⚠️  yfinance not installed — run: pip install yfinance")

# =============================================================================
# INDICATOR SETTINGS (matching your Pine Script exactly)
# =============================================================================

# Plus500
PLUS500_URL = "https://futures.plus500.com/trade"
PLUS500_INSTRUMENT = "Gold"  # Text to match in instrument list

# TradingView (visual reference only — signals come from Python)
CHART_URL = "https://www.tradingview.com/chart/iDzbrr6O/?symbol=COINBASE%3AGOLJ2026"

# --- SSL Hybrid ---
BASELINE_TYPE = "HMA"
BASELINE_LEN = 60
SSL2_TYPE = "EMA"
SSL2_LEN = 5
SSL3_TYPE = "HMA"   # Exit
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

# --- Candle / Timing ---
CANDLE_SECONDS = 60       # 1-minute candles (matches TradingView)
TICK_INTERVAL = 0.5       # Read price every 500ms
MIN_CANDLES = 60          # HMA 60 needs ~60 candles minimum
MAX_CANDLES = 1000        # Keep ~16 hours of 1-minute history
YF_SYMBOL = "GC=F"        # Yahoo Finance gold futures symbol


# =============================================================================
# MOVING AVERAGE FUNCTIONS (numpy array-based, NaN-safe)
# =============================================================================

def sma_arr(data, period):
    """Simple Moving Average."""
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    # Use cumsum for speed
    cs = np.cumsum(data)
    cs = np.insert(cs, 0, 0)
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        if np.any(np.isnan(window)):
            continue
        result[i] = (cs[i + 1] - cs[i - period + 1]) / period
    return result


def ema_arr(data, period):
    """Exponential Moving Average (handles NaN in input)."""
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    # Find first valid window of `period` non-NaN values
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
    """Weighted Moving Average."""
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
    """Hull Moving Average: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    half = max(int(period / 2), 1)
    sqrt_p = max(round(math.sqrt(period)), 1)
    w_half = wma_arr(data, half)
    w_full = wma_arr(data, period)
    diff = 2.0 * w_half - w_full
    return wma_arr(diff, sqrt_p)


def dema_arr(data, period):
    """Double EMA: 2*EMA - EMA(EMA)."""
    e1 = ema_arr(data, period)
    e2 = ema_arr(e1, period)
    return 2.0 * e1 - e2


def tema_arr(data, period):
    """Triple EMA: 3*(EMA - EMA(EMA)) + EMA(EMA(EMA))."""
    e1 = ema_arr(data, period)
    e2 = ema_arr(e1, period)
    e3 = ema_arr(e2, period)
    return 3.0 * (e1 - e2) + e3


def tma_arr(data, period):
    """Triangular MA: SMA(SMA(data))."""
    s1 = sma_arr(data, period)
    return sma_arr(s1, period)


def lsma_arr(data, period):
    """Least Squares MA (linear regression value)."""
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
    """Kijun v2: (highest + lowest) / 2."""
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
    """Dispatch to correct MA. Matches Pine Script ma() function."""
    dispatch = {
        "SMA": sma_arr, "EMA": ema_arr, "DEMA": dema_arr,
        "TEMA": tema_arr, "LSMA": lsma_arr, "WMA": wma_arr,
        "TMA": tma_arr, "HMA": hma_arr, "Kijun v2": kijun_arr,
        "McGinley": ema_arr,
    }
    fn = dispatch.get(ma_type, sma_arr)
    return fn(data, period)


# =============================================================================
# INDICATOR CALCULATIONS
# =============================================================================

def compute_ssl(highs, lows, closes, ma_type, period):
    """
    SSL Channel calculation.
    Returns (hlv, ssl_up, ssl_down) arrays.
    
    hlv: 1 = bullish, -1 = bearish
    ssl_up: the "up" line
    ssl_down: the "down" line
    """
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
    """
    EMA crossover signals.
    Returns (buy_arr, sell_arr, fast_ema, slow_ema).
    """
    fast = ema_arr(closes, fast_len)
    slow = ema_arr(closes, slow_len)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(fast[i]) or np.isnan(slow[i]):
            continue
        if np.isnan(fast[i - 1]) or np.isnan(slow[i - 1]):
            continue
        buy[i] = fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]
        sell[i] = fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]
    return buy, sell, fast, slow


def compute_ut_bot(closes, highs, lows, sensitivity, atr_period):
    """
    UT Bot Alerts.
    Returns (buy_arr, sell_arr, trailing_stop, pos).
    """
    n = len(closes)

    # True Range
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # ATR via RMA (Wilder's smoothing, alpha = 1/period)
    atr = np.full(n, np.nan)
    if n >= atr_period:
        atr[atr_period - 1] = np.mean(tr[:atr_period])
        alpha = 1.0 / atr_period
        for i in range(atr_period, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]

    n_loss = sensitivity * atr

    # ATR Trailing Stop & Position
    stop = np.zeros(n)
    pos = np.zeros(n)

    for i in range(1, n):
        if np.isnan(n_loss[i]):
            stop[i] = stop[i - 1]
            pos[i] = pos[i - 1]
            continue

        ps = stop[i - 1]
        c = closes[i]
        c1 = closes[i - 1]
        nl = n_loss[i]

        # Trailing stop logic (matches Pine Script exactly)
        if c > ps and c1 > ps:
            stop[i] = max(ps, c - nl)
        elif c < ps and c1 < ps:
            stop[i] = min(ps, c + nl)
        elif c > ps:
            stop[i] = c - nl
        else:
            stop[i] = c + nl

        # Position tracking
        if c1 < stop[i - 1] and c > stop[i - 1]:
            pos[i] = 1
        elif c1 > stop[i - 1] and c < stop[i - 1]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]

    # Crossover / crossunder of pos with 0
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        buy[i] = pos[i] > 0 and pos[i - 1] <= 0
        sell[i] = pos[i] < 0 and pos[i - 1] >= 0

    return buy, sell, stop, pos


# =============================================================================
# BOLLINGER BANDS
# =============================================================================

def compute_bollinger(closes, period, mult):
    """Bollinger Bands: basis=SMA, upper=basis+mult*stdev, lower=basis-mult*stdev."""
    n = len(closes)
    basis = sma_arr(closes, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        if np.isnan(basis[i]):
            continue
        window = closes[i - period + 1:i + 1]
        std = np.std(window, ddof=0)
        upper[i] = basis[i] + mult * std
        lower[i] = basis[i] - mult * std
    return basis, upper, lower


# =============================================================================
# SIGNAL GENERATION (mirrors Pine Script position management exactly)
# =============================================================================

def generate_signals(opens, highs, lows, closes, current_position):
    """
    Run all indicators and generate trading signals.
    Position management is sequential (matching Pine Script execution order).
    Returns dict or None if insufficient data.
    """
    n = len(closes)
    if n < MIN_CANDLES:
        return None

    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)

    # --- SSL Hybrid (3 channels) ---
    hlv1, ssl1_up, ssl1_down = compute_ssl(h, l, c, BASELINE_TYPE, BASELINE_LEN)
    hlv2, ssl2_up, ssl2_down = compute_ssl(h, l, c, SSL2_TYPE, SSL2_LEN)
    hlv3, ssl3_up, ssl3_down = compute_ssl(h, l, c, SSL3_TYPE, SSL3_LEN)

    # --- EMA Crossover ---
    ema_buy_arr, ema_sell_arr, ema_fast, ema_slow = compute_ema_cross(
        c, EMA_FAST_LEN, EMA_SLOW_LEN
    )

    # --- UT Bot ---
    ut_buy_arr, ut_sell_arr, ut_stop, ut_pos = compute_ut_bot(
        c, h, l, UT_SENSITIVITY, UT_ATR_PERIOD
    )

    # --- Bollinger Bands ---
    bb_basis, bb_upper, bb_lower = compute_bollinger(c, BB_PERIOD, BB_MULT)

    # --- Last-candle signals (crossovers) ---
    i = n - 1

    # SSL Baseline crossover
    ssl_buy = bool(hlv1[i] > 0 and (hlv1[i - 1] <= 0 if i > 0 else False))
    ssl_sell = bool(hlv1[i] < 0 and (hlv1[i - 1] >= 0 if i > 0 else False))

    # SSL3 Exit crossover
    ssl3_buy = bool(hlv3[i] > 0 and (hlv3[i - 1] <= 0 if i > 0 else False))
    ssl3_sell = bool(hlv3[i] < 0 and (hlv3[i - 1] >= 0 if i > 0 else False))

    # EMA & UT Bot
    ema_buy = bool(ema_buy_arr[i])
    ema_sell = bool(ema_sell_arr[i])
    ut_buy = bool(ut_buy_arr[i])
    ut_sell = bool(ut_sell_arr[i])

    # --- Position Management (sequential, same as Pine Script) ---
    position = current_position

    # Entry conditions
    any_entry_buy = ssl_buy or ema_buy or ut_buy
    any_entry_sell = ssl_sell or ema_sell or ut_sell

    # Exit conditions (include SSL3 exit signals)
    any_exit_sell = ssl3_sell or ssl_sell or ema_sell or ut_sell
    any_exit_buy = ssl3_buy or ssl_buy or ema_buy or ut_buy

    # Open Long
    open_long = any_entry_buy and position != 1
    if open_long:
        position = 1

    # Close Long
    close_long = any_exit_sell and position == 1
    if close_long:
        position = 0

    # Open Short
    open_short = any_entry_sell and position != -1
    if open_short:
        position = -1

    # Close Short
    close_short = any_exit_buy and position == -1
    if close_short:
        position = 0

    # --- Trend info for status display ---
    ssl_dir = "UP" if hlv1[i] > 0 else "DOWN" if hlv1[i] < 0 else "FLAT"
    ema_trend = "BULL"
    if np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]):
        ema_trend = "---"
    elif ema_fast[i] <= ema_slow[i]:
        ema_trend = "BEAR"
    ut_dir = "LONG" if ut_pos[i] > 0 else "SHORT" if ut_pos[i] < 0 else "FLAT"

    return {
        "open_long": open_long,
        "close_long": close_long,
        "open_short": open_short,
        "close_short": close_short,
        "position": position,
        "price": c[i],
        # Individual trigger flags (for diagnostics)
        "ssl_buy": ssl_buy, "ssl_sell": ssl_sell,
        "ssl3_buy": ssl3_buy, "ssl3_sell": ssl3_sell,
        "ema_buy": ema_buy, "ema_sell": ema_sell,
        "ut_buy": ut_buy, "ut_sell": ut_sell,
        # Trend display
        "ssl_dir": ssl_dir,
        "ema_trend": ema_trend,
        "ut_dir": ut_dir,
        # Full indicator arrays for chart (last values)
        "ssl1_up_val": float(ssl1_up[i]) if not np.isnan(ssl1_up[i]) else None,
        "ssl1_down_val": float(ssl1_down[i]) if not np.isnan(ssl1_down[i]) else None,
        "ssl3_up_val": float(ssl3_up[i]) if not np.isnan(ssl3_up[i]) else None,
        "ssl3_down_val": float(ssl3_down[i]) if not np.isnan(ssl3_down[i]) else None,
        "ema_fast_val": float(ema_fast[i]) if not np.isnan(ema_fast[i]) else None,
        "ema_slow_val": float(ema_slow[i]) if not np.isnan(ema_slow[i]) else None,
        "ut_stop_val": float(ut_stop[i]) if not np.isnan(ut_stop[i]) else None,
        # Bollinger Bands
        "bb_basis_val": float(bb_basis[i]) if not np.isnan(bb_basis[i]) else None,
        "bb_upper_val": float(bb_upper[i]) if not np.isnan(bb_upper[i]) else None,
        "bb_lower_val": float(bb_lower[i]) if not np.isnan(bb_lower[i]) else None,
        # SSL2 channel
        "ssl2_up_val": float(ssl2_up[i]) if not np.isnan(ssl2_up[i]) else None,
        "ssl2_down_val": float(ssl2_down[i]) if not np.isnan(ssl2_down[i]) else None,
        # Full arrays for init (used for bulk chart load)
        "_ssl1_up": ssl1_up,
        "_ssl1_down": ssl1_down,
        "_ssl2_up": ssl2_up,
        "_ssl2_down": ssl2_down,
        "_ssl3_up": ssl3_up,
        "_ssl3_down": ssl3_down,
        "_ema_fast": ema_fast,
        "_ema_slow": ema_slow,
        "_ut_stop": ut_stop,
        "_bb_basis": bb_basis,
        "_bb_upper": bb_upper,
        "_bb_lower": bb_lower,
    }


# =============================================================================
# HISTORICAL DATA LOADER
# =============================================================================

def load_historical_gold():
    """Download historical gold futures 1-minute candle data from Yahoo Finance."""
    if not HAS_YFINANCE:
        return None
    try:
        print("   📥 Downloading historical gold data from Yahoo Finance...")
        data = yf.download(YF_SYMBOL, period="5d", interval="1m", progress=False)
        if data.empty:
            print("   ⚠️  No historical data returned")
            return None
        # yfinance sometimes returns MultiIndex columns
        if hasattr(data.columns, 'levels'):
            data.columns = data.columns.get_level_values(0)
        opens = data['Open'].values.astype(float)
        highs = data['High'].values.astype(float)
        lows = data['Low'].values.astype(float)
        closes = data['Close'].values.astype(float)
        timestamps = [datetime.fromtimestamp(t.timestamp()) for t in data.index]
        # Remove NaN rows
        valid = ~(np.isnan(opens) | np.isnan(highs) | np.isnan(lows) | np.isnan(closes))
        opens, highs, lows, closes = opens[valid], highs[valid], lows[valid], closes[valid]
        timestamps = [t for t, v in zip(timestamps, valid) if v]
        n = len(closes)
        print(f"   ✓ Loaded {n} historical 1-minute candles")
        if n > 0:
            price_range = max(highs) - min(lows)
            print(f"   ✓ Price range: ${min(lows):.2f} — ${max(highs):.2f} (${price_range:.2f})")
        return opens, highs, lows, closes, timestamps
    except Exception as e:
        print(f"   ⚠️  Could not load historical data: {e}")
        return None


# =============================================================================
# CANDLE BUFFER — builds N-second OHLC candles from price ticks
# =============================================================================

class CandleBuffer:
    """Aggregates price ticks into OHLC candles of configurable duration."""

    def __init__(self, max_candles=MAX_CANDLES, candle_seconds=CANDLE_SECONDS):
        self.opens = []
        self.highs = []
        self.lows = []
        self.closes = []
        self.timestamps = []
        self.max_candles = max_candles
        self.candle_seconds = candle_seconds
        # Current forming candle
        self._bucket = None
        self._o = self._h = self._l = self._c = None

    def _get_bucket(self, ts):
        """Get the candle time bucket for a timestamp."""
        epoch = int(ts.timestamp())
        bucket_epoch = (epoch // self.candle_seconds) * self.candle_seconds
        return datetime.fromtimestamp(bucket_epoch)

    def prefill(self, opens, highs, lows, closes, timestamps):
        """Pre-fill buffer with historical candle data."""
        n = len(opens)
        start = max(0, n - self.max_candles)
        for i in range(start, n):
            self.opens.append(float(opens[i]))
            self.highs.append(float(highs[i]))
            self.lows.append(float(lows[i]))
            self.closes.append(float(closes[i]))
            self.timestamps.append(timestamps[i])

    def tick(self, price, ts=None):
        """Add a price tick. Returns True when a candle completes."""
        if ts is None:
            ts = datetime.now()
        bucket = self._get_bucket(ts)

        if self._bucket is None:
            self._bucket = bucket
            self._o = self._h = self._l = self._c = price
            return False

        if bucket > self._bucket:
            # New candle period → close the previous candle
            self.opens.append(self._o)
            self.highs.append(self._h)
            self.lows.append(self._l)
            self.closes.append(self._c)
            self.timestamps.append(self._bucket)

            # Trim to max
            if len(self.closes) > self.max_candles:
                for lst in (self.opens, self.highs, self.lows, self.closes, self.timestamps):
                    lst.pop(0)

            # Start new candle
            self._bucket = bucket
            self._o = self._h = self._l = self._c = price
            return True

        # Same candle period → update current candle
        self._h = max(self._h, price)
        self._l = min(self._l, price)
        self._c = price
        return False

    @property
    def count(self):
        return len(self.closes)

    def arrays(self):
        """Return (opens, highs, lows, closes) as numpy arrays."""
        return (
            np.array(self.opens, dtype=float),
            np.array(self.highs, dtype=float),
            np.array(self.lows, dtype=float),
            np.array(self.closes, dtype=float),
        )


# =============================================================================
# REAL-TIME TRADER
# =============================================================================

class RealTimeTrader:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.tv_page = None
        self.p500_page = None
        self.candles = CandleBuffer()
        self.position = 0   # 0=flat, 1=long, -1=short
        self.trades = 0
        self.log = []

    # ── Browser Setup ──

    async def start(self):
        """Launch Chromium browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=["--start-maximized", "--force-device-scale-factor=1.0"],
        )
        self.context = await self.browser.new_context(
            no_viewport=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self.tv_page = await self.context.new_page()
        print("✓ Browser started")

    async def start_chart_server(self):
        """Start WebSocket server + HTTP server for the live chart."""
        self.ws_clients = set()
        self.chart_markers = []

        # WebSocket handler
        async def ws_handler(websocket):
            self.ws_clients.add(websocket)
            try:
                # Send current state on connect
                if self.candles.count > 0:
                    init_msg = self._build_init_message()
                    if init_msg:
                        await websocket.send(json.dumps(init_msg))
                async for _ in websocket:
                    pass  # Keep connection open
            except websockets.exceptions.ConnectionClosed:
                pass
            finally:
                self.ws_clients.discard(websocket)

        # Start WebSocket server on port 8765
        self._ws_server = await websockets.serve(ws_handler, "localhost", 8765)
        print("   ✓ WebSocket chart server on ws://localhost:8765")

        # Start HTTP server for chart.html on port 8080
        chart_dir = os.path.dirname(os.path.abspath(__file__))
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=chart_dir)

        def run_http():
            httpd = http.server.HTTPServer(("localhost", 8080), handler)
            httpd.serve_forever()

        threading.Thread(target=run_http, daemon=True).start()
        print("   ✓ Chart HTTP server on http://localhost:8080/chart.html")

        # Open chart in default browser
        await asyncio.sleep(0.5)
        webbrowser.open("http://localhost:8080/chart.html")
        print("   ✓ Live chart opened in browser!")
        print("   📊 1-second candles + SSL + EMA + UT Bot overlays\n")

    def _ts_to_epoch(self, dt):
        """Convert datetime to UTC epoch seconds for lightweight-charts."""
        return int(dt.timestamp())

    def _build_init_message(self):
        """Build a full chart initialization message with all history."""
        if self.candles.count == 0:
            return None

        candles_data = []
        for i in range(self.candles.count):
            t = self._ts_to_epoch(self.candles.timestamps[i])
            candles_data.append({
                "time": t,
                "open": round(self.candles.opens[i], 2),
                "high": round(self.candles.highs[i], 2),
                "low": round(self.candles.lows[i], 2),
                "close": round(self.candles.closes[i], 2),
            })

        msg = {"type": "init", "candles": candles_data, "markers": self.chart_markers}

        # Add indicator lines if we have enough candles
        if self.candles.count >= MIN_CANDLES:
            o, h, l, c = self.candles.arrays()
            signals = generate_signals(o, h, l, c, self.position)
            if signals:
                timestamps = [self._ts_to_epoch(t) for t in self.candles.timestamps]
                for key, arr_key in [
                    ("ssl_up", "_ssl1_up"), ("ssl_down", "_ssl1_down"),
                    ("ssl2_up", "_ssl2_up"), ("ssl2_down", "_ssl2_down"),
                    ("ssl3_up", "_ssl3_up"), ("ssl3_down", "_ssl3_down"),
                    ("ema_fast", "_ema_fast"), ("ema_slow", "_ema_slow"),
                    ("ut_stop", "_ut_stop"),
                    ("bb_basis", "_bb_basis"), ("bb_upper", "_bb_upper"), ("bb_lower", "_bb_lower"),
                ]:
                    arr = signals[arr_key]
                    line_data = []
                    for j in range(len(arr)):
                        if not np.isnan(arr[j]):
                            line_data.append({"time": timestamps[j], "value": round(float(arr[j]), 2)})
                    msg[key] = line_data

        return msg

    async def broadcast(self, msg):
        """Send message to all connected chart clients."""
        if not self.ws_clients:
            return
        data = json.dumps(msg)
        dead = set()
        for ws in self.ws_clients:
            try:
                await ws.send(data)
            except:
                dead.add(ws)
        self.ws_clients -= dead

    async def open_tradingview(self):
        """Open TradingView chart (visual reference only)."""
        print("   Opening TradingView chart...")
        await self.tv_page.goto(CHART_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        # TradingView is visual-only — no need to wait for login
        # Just let it load, user can log in later if they want
        print("   ✓ TradingView chart loaded (visual reference only)")
        print("   📊 All signals are computed in Python — no Premium needed!")
        print("   💡 You can log into TradingView anytime — it won't affect trading.\n")

    async def open_plus500(self):
        """Open Plus500 and auto-detect login by polling for gold price."""
        self.p500_page = await self.context.new_page()
        await self.p500_page.goto(PLUS500_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        print("   📱 Plus500 Futures opened")
        print("   ⏳ Waiting for you to login and navigate to Gold...")
        print("      (Login, then go to Metals > Gold — script auto-detects when ready)\n")

        # Poll for price instead of waiting for Enter key
        attempt = 0
        while True:
            price = await self.read_price()
            if price:
                print(f"\n   ✓ Plus500 ready — Gold price detected: ${price:.2f}")
                print(f"   🚀 Starting trading automatically!\n")
                return True
            attempt += 1
            if attempt % 15 == 0:  # Remind every ~15 seconds
                secs = attempt
                print(f"   ⏳ Still waiting for Gold price... ({secs}s elapsed)")
                print(f"      Make sure Gold instrument is visible on the Plus500 page")
            await asyncio.sleep(1)

    # ── Price Reading ──

    async def read_price(self):
        """Read live gold price from Plus500 page."""
        try:
            result = await self.p500_page.evaluate(
                """() => {
                // Strategy 1: Instrument repeater
                const repeater = document.querySelector('#instrumentsRepeater');
                if (repeater) {
                    const items = repeater.querySelectorAll('div, tr, li, section');
                    for (const item of items) {
                        const text = item.textContent || '';
                        if (text.includes('Gold') || text.includes('GOLD') || text.includes('XAU')) {
                            const matches = text.match(/(\\d[\\d,]*\\.\\d{1,2})/g);
                            if (matches) {
                                const nums = matches
                                    .map(m => parseFloat(m.replace(/,/g, '')))
                                    .filter(p => p > 500 && p < 50000);
                                if (nums.length >= 2) {
                                    nums.sort((a, b) => a - b);
                                    return (nums[0] + nums[nums.length - 1]) / 2;
                                }
                                if (nums.length === 1) return nums[0];
                            }
                        }
                    }
                }
                // Strategy 2: Any price-class element
                const priceEls = document.querySelectorAll(
                    '[class*="price"], [class*="bid"], [class*="ask"], [class*="buySell"], [class*="rate"]'
                );
                const prices = [];
                priceEls.forEach(el => {
                    const m = el.textContent.trim().match(/^(\\d[\\d,]*\\.\\d{1,2})$/);
                    if (m) {
                        const p = parseFloat(m[1].replace(/,/g, ''));
                        if (p > 500 && p < 50000) prices.push(p);
                    }
                });
                if (prices.length >= 2) {
                    prices.sort((a, b) => a - b);
                    return (prices[0] + prices[prices.length - 1]) / 2;
                }
                if (prices.length === 1) return prices[0];
                // Strategy 3: Scan all buttons for price numbers
                const btns = document.querySelectorAll('button');
                const btnPrices = [];
                btns.forEach(btn => {
                    const m = btn.textContent.match(/(\\d[\\d,]*\\.\\d{1,2})/);
                    if (m) {
                        const p = parseFloat(m[1].replace(/,/g, ''));
                        if (p > 500 && p < 50000) btnPrices.push(p);
                    }
                });
                if (btnPrices.length >= 2) {
                    btnPrices.sort((a, b) => a - b);
                    return (btnPrices[0] + btnPrices[btnPrices.length - 1]) / 2;
                }
                if (btnPrices.length === 1) return btnPrices[0];
                return null;
            }"""
            )
            return result
        except:
            return None

    # ── Trade Execution ──

    async def execute_buy(self):
        """Click BUY on Plus500 Futures for Gold."""
        try:
            await self.p500_page.bring_to_front()
            await asyncio.sleep(0.3)

            # Click BUY button on Gold instrument row
            await self.p500_page.locator("#instrumentsRepeater div").filter(
                has_text="1 Ounce Gold"
            ).get_by_role("button").nth(1).click()
            await asyncio.sleep(1)

            # Set Limit order
            await self.p500_page.get_by_text("Limit", exact=True).click()
            await asyncio.sleep(0.5)

            # Enable Take Profit
            await self.p500_page.get_by_role("switch", name=" Take Profit").check()
            await asyncio.sleep(0.5)

            # Set Take Profit = $10
            await self.p500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)

            # Accept terms if needed
            try:
                btn = self.p500_page.get_by_role("button", name="Accept")
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await asyncio.sleep(0.5)
            except:
                pass

            # Place order
            await self.p500_page.get_by_role("button", name="Place Buy Order").click()
            await asyncio.sleep(1)

            self.trades += 1
            print(f"      ✅ BUY order placed! (Trade #{self.trades})")

            await self.tv_page.bring_to_front()
            return True
        except Exception as e:
            print(f"      ❌ BUY failed: {e}")
            try:
                await self.tv_page.bring_to_front()
            except:
                pass
            return False

    async def execute_sell(self):
        """Click SELL on Plus500 Futures for Gold."""
        try:
            await self.p500_page.bring_to_front()
            await asyncio.sleep(0.3)

            # Click SELL button on Gold instrument
            await self.p500_page.locator(
                "div:nth-child(9) > .short-button > .buySellButton"
            ).click()
            await asyncio.sleep(1)

            # Set Limit order
            await self.p500_page.get_by_text("Limit", exact=True).click()
            await asyncio.sleep(0.5)

            # Enable Take Profit
            await self.p500_page.get_by_role("switch", name=" Take Profit").check()
            await asyncio.sleep(0.5)

            # Set Take Profit = $10
            await self.p500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)

            # Place order
            await self.p500_page.get_by_role("button", name="Place Sell Order").click()
            await asyncio.sleep(1)

            self.trades += 1
            print(f"      ✅ SELL order placed! (Trade #{self.trades})")

            await self.tv_page.bring_to_front()
            return True
        except Exception as e:
            print(f"      ❌ SELL failed: {e}")
            try:
                await self.tv_page.bring_to_front()
            except:
                pass
            return False

    # ── Main Trading Loop ──

    async def run(self):
        """Load historical data, then read live prices and trade."""
        # ── Load Historical Data ──
        print(f"\n{'='*60}")
        print(f"  📊 LOADING HISTORICAL GOLD DATA")
        print(f"{'='*60}")
        hist = load_historical_gold()
        history_loaded = False
        if hist:
            opens, highs, lows, closes, timestamps = hist
            self.candles.prefill(opens, highs, lows, closes, timestamps)
            history_loaded = True
            print(f"  ✓ Chart pre-loaded with {self.candles.count} candles")
            print(f"  ✓ All indicators computed — chart is READY!")
            # Send full chart to browser immediately
            init_msg = self._build_init_message()
            if init_msg:
                await self.broadcast(init_msg)
                print(f"  ✓ Sent {self.candles.count} candles + indicators to chart")
        else:
            print(f"  ⚠️  No historical data — chart starts empty")
            print(f"  ⚠️  Warmup needed: {MIN_CANDLES} candles")

        print(f"\n{'='*60}")
        print(f"  🚀 REAL-TIME GOLD TRADING — ACTIVE")
        print(f"{'='*60}")
        print(f"  Candle interval : {CANDLE_SECONDS}s ({'1 minute' if CANDLE_SECONDS == 60 else str(CANDLE_SECONDS) + 's'})")
        print(f"  Price polling   : every {int(TICK_INTERVAL*1000)}ms")
        print(f"  Indicators      : SSL + EMA + UT Bot + Bollinger Bands")
        print(f"  History loaded  : {'YES (' + str(self.candles.count) + ' candles)' if history_loaded else 'NO'}")
        print(f"  Press Ctrl+C to stop")
        print(f"{'='*60}\n")

        status_interval = 5   # Print status every N candles
        last_status_count = 0
        price_errors = 0

        while True:
            try:
                # Read price from Plus500
                price = await self.read_price()
                now = datetime.now()

                if price is None:
                    price_errors += 1
                    if price_errors % 50 == 0:  # Warn every ~10 seconds
                        print(
                            f"   ⚠️ Can't read price ({price_errors} fails) "
                            f"— is Gold visible on Plus500?"
                        )
                    await asyncio.sleep(TICK_INTERVAL)
                    continue

                price_errors = 0
                candle_done = self.candles.tick(price, now)

                if not candle_done:
                    await asyncio.sleep(TICK_INTERVAL)
                    continue

                count = self.candles.count

                # ── Warmup Phase (only if no historical data) ──
                if not history_loaded and count < MIN_CANDLES:
                    remaining = MIN_CANDLES - count
                    pct = count * 100 // MIN_CANDLES
                    filled = pct * 30 // 100
                    bar = "█" * filled + "░" * (30 - filled)
                    mins_left = (remaining * CANDLE_SECONDS) // 60
                    print(
                        f"\r   Warmup [{bar}] {pct:3d}% "
                        f"({count}/{MIN_CANDLES}) "
                        f"${price:.2f}  ~{mins_left}min left   ",
                        end="",
                        flush=True,
                    )
                    # Send warmup candle to chart
                    t = self._ts_to_epoch(self.candles.timestamps[-1])
                    await self.broadcast({
                        "type": "warmup",
                        "pct": pct,
                        "count": count,
                        "total": MIN_CANDLES,
                        "candle": {
                            "time": t,
                            "open": round(self.candles.opens[-1], 2),
                            "high": round(self.candles.highs[-1], 2),
                            "low": round(self.candles.lows[-1], 2),
                            "close": round(self.candles.closes[-1], 2),
                        },
                    })
                    continue

                if not history_loaded and count == MIN_CANDLES:
                    print(f"\n\n   ✅ WARMUP COMPLETE — Signals are now LIVE!\n")
                    # Send full init to chart with all indicator lines
                    init_msg = self._build_init_message()
                    if init_msg:
                        await self.broadcast(init_msg)

                # ── Compute Signals ──
                o, h, l, c = self.candles.arrays()
                signals = generate_signals(o, h, l, c, self.position)

                if signals is None:
                    await asyncio.sleep(TICK_INTERVAL)
                    continue

                self.position = signals["position"]
                ts = now.strftime("%H:%M:%S")
                pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(self.position, "?")

                # ── Send chart update ──
                candle_time = self._ts_to_epoch(self.candles.timestamps[-1])
                update_msg = {
                    "type": "update",
                    "count": count,
                    "candle": {
                        "time": candle_time,
                        "open": round(self.candles.opens[-1], 2),
                        "high": round(self.candles.highs[-1], 2),
                        "low": round(self.candles.lows[-1], 2),
                        "close": round(self.candles.closes[-1], 2),
                    },
                    "ssl_dir": signals["ssl_dir"],
                    "ema_trend": signals["ema_trend"],
                    "ut_dir": signals["ut_dir"],
                    "ind": {},
                }
                # Add indicator line points
                ind_map = {
                    "ssl1_up_val": "ssl_up", "ssl1_down_val": "ssl_down",
                    "ssl2_up_val": "ssl2_up", "ssl2_down_val": "ssl2_down",
                    "ssl3_up_val": "ssl3_up", "ssl3_down_val": "ssl3_down",
                    "ema_fast_val": "ema_fast", "ema_slow_val": "ema_slow",
                    "ut_stop_val": "ut_stop",
                    "bb_basis_val": "bb_basis", "bb_upper_val": "bb_upper", "bb_lower_val": "bb_lower",
                }
                for key, chart_key in ind_map.items():
                    val = signals.get(key)
                    if val is not None:
                        update_msg[chart_key] = {"time": candle_time, "value": round(val, 2)}
                        update_msg["ind"][chart_key] = round(val, 2)
                await self.broadcast(update_msg)

                # ── Execute Trades ──
                if signals["open_long"]:
                    print(f"\n   🟢 [{ts}] ════ OPEN LONG ════  ${price:.2f}")
                    print(
                        f"      Triggers: SSL={'✓' if signals['ssl_buy'] else '✗'}"
                        f" EMA={'✓' if signals['ema_buy'] else '✗'}"
                        f" UT={'✓' if signals['ut_buy'] else '✗'}"
                    )
                    await self.execute_buy()
                    self._log("OPEN_LONG", price, ts)
                    self.chart_markers.append({"time": candle_time, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "OPEN LONG"})
                    await self.broadcast({"type": "signal", "action": "open_long", "price": price, "time": ts, "candle_time": candle_time, "position": self.position, "trades": self.trades})

                elif signals["close_long"]:
                    print(f"\n   🟠 [{ts}] ════ CLOSE LONG ════  ${price:.2f}")
                    print(
                        f"      Triggers: SSL3={'✓' if signals['ssl3_sell'] else '✗'}"
                        f" SSL={'✓' if signals['ssl_sell'] else '✗'}"
                        f" EMA={'✓' if signals['ema_sell'] else '✗'}"
                        f" UT={'✓' if signals['ut_sell'] else '✗'}"
                    )
                    await self.execute_sell()
                    self._log("CLOSE_LONG", price, ts)
                    self.chart_markers.append({"time": candle_time, "position": "aboveBar", "color": "#ff9800", "shape": "arrowDown", "text": "CLOSE LONG"})
                    await self.broadcast({"type": "signal", "action": "close_long", "price": price, "time": ts, "candle_time": candle_time, "position": self.position, "trades": self.trades})

                elif signals["open_short"]:
                    print(f"\n   🔴 [{ts}] ════ OPEN SHORT ════  ${price:.2f}")
                    print(
                        f"      Triggers: SSL={'✓' if signals['ssl_sell'] else '✗'}"
                        f" EMA={'✓' if signals['ema_sell'] else '✗'}"
                        f" UT={'✓' if signals['ut_sell'] else '✗'}"
                    )
                    await self.execute_sell()
                    self._log("OPEN_SHORT", price, ts)
                    self.chart_markers.append({"time": candle_time, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "OPEN SHORT"})
                    await self.broadcast({"type": "signal", "action": "open_short", "price": price, "time": ts, "candle_time": candle_time, "position": self.position, "trades": self.trades})

                elif signals["close_short"]:
                    print(f"\n   🔵 [{ts}] ════ CLOSE SHORT ════  ${price:.2f}")
                    print(
                        f"      Triggers: SSL3={'✓' if signals['ssl3_buy'] else '✗'}"
                        f" SSL={'✓' if signals['ssl_buy'] else '✗'}"
                        f" EMA={'✓' if signals['ema_buy'] else '✗'}"
                        f" UT={'✓' if signals['ut_buy'] else '✗'}"
                    )
                    await self.execute_buy()
                    self._log("CLOSE_SHORT", price, ts)
                    self.chart_markers.append({"time": candle_time, "position": "belowBar", "color": "#2196f3", "shape": "arrowUp", "text": "CLOSE SHORT"})
                    await self.broadcast({"type": "signal", "action": "close_short", "price": price, "time": ts, "candle_time": candle_time, "position": self.position, "trades": self.trades})

                # ── Periodic Status ──
                elif count - last_status_count >= status_interval:
                    last_status_count = count
                    print(
                        f"   [{ts}] ${price:.2f} | "
                        f"{count} candles | Pos: {pos_str} | "
                        f"SSL: {signals['ssl_dir']} | "
                        f"EMA: {signals['ema_trend']} | "
                        f"UT: {signals['ut_dir']} | "
                        f"Trades: {self.trades}"
                    )

                await asyncio.sleep(TICK_INTERVAL)

            except KeyboardInterrupt:
                print(
                    f"\n\n   ⏹ Stopped. Candles: {self.candles.count} | "
                    f"Trades: {self.trades}"
                )
                break
            except Exception as e:
                print(f"\n   ❌ Error: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(1)

    def _log(self, action, price, ts):
        """Log signal to console and file."""
        entry = f"[{ts}] {action} @ ${price:.2f}"
        self.log.append(entry)
        try:
            with open("trade_log.txt", "a") as f:
                f.write(f"{datetime.now().isoformat()} | {action} | ${price:.2f}\n")
        except:
            pass

    async def close(self):
        """Cleanup."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("✓ Browser closed")


# =============================================================================
# KEEP-ALIVE (prevent Windows sleep/lock)
# =============================================================================

def keep_alive_thread():
    """Background thread to prevent system sleep/lock."""
    ES = 0x80000000 | 0x00000001 | 0x00000002 | 0x00000040
    while True:
        ctypes.windll.kernel32.SetThreadExecutionState(ES)
        ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
        _time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
        _time.sleep(30)


# =============================================================================
# MAIN
# =============================================================================

async def main():
    # Start keep-alive
    threading.Thread(target=keep_alive_thread, daemon=True).start()
    print("🔋 Keep-Alive active — system will NOT sleep or lock\n")

    trader = RealTimeTrader()
    try:
        await trader.start()

        print("\n── Step 1: Starting Live Chart Server ──")
        await trader.start_chart_server()

        print("── Step 2: TradingView (visual reference) ──")
        await trader.open_tradingview()

        print("── Step 3: Plus500 Futures (live prices + trading) ──")
        await trader.open_plus500()

        print("\n── Step 4: Loading History + Starting Live Trading ──")
        await trader.run()

    except KeyboardInterrupt:
        print("\n⏹ Stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await trader.close()


if __name__ == "__main__":
    print(
        """
    ╔═══════════════════════════════════════════════════════════╗
    ║     GOLD TRADER + LIVE CHART (like TradingView)           ║
    ║                                                           ║
    ║  Your Pine Script indicators running in Python:           ║
    ║    • SSL Hybrid (HMA 60 / EMA 5 / HMA 15 exit)          ║
    ║    • EMA Crossover (9 / 21)                              ║
    ║    • UT Bot Alerts (sensitivity 1, ATR 10)               ║
    ║    • Bollinger Bands (20, 2.0)                           ║
    ║                                                           ║
    ║  CHART: Real gold data from Yahoo Finance                 ║
    ║  1-minute candles with all indicators overlaid            ║
    ║                                                           ║
    ║  LIVE: Reads price from Plus500 for trade execution       ║
    ║  NO TradingView Premium needed!                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    )
    asyncio.run(main())
