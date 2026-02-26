"""Quick price comparison between Yahoo and Coinbase"""
import yfinance as yf
import urllib.request, json
from datetime import datetime
from zoneinfo import ZoneInfo

now_est = datetime.now(ZoneInfo("America/New_York"))

print("=" * 50)
print("  LIVE PRICE COMPARISON")
print(f"  {now_est.strftime('%A, %b %d %Y  %I:%M:%S %p EST')}")
print("=" * 50)

# Yahoo
t = yf.Ticker("GC=F")
yp = t.fast_info['last_price']
print(f"\n  Yahoo  GC=F:            ${yp:.2f}")

# Coinbase  
url = "https://api.coinbase.com/api/v3/brokerage/market/products/GOL-27MAR26-CDE"
r = urllib.request.urlopen(url)
d = json.loads(r.read())
cp = float(d['price'])
print(f"  Coinbase GOL-27MAR26:   ${cp:.2f}")
print(f"  Spread:                 ${yp - cp:.2f}")

print(f"\n  GC=F = CME Gold Apr 2026 (continuous front-month)")
print(f"  GOL-27MAR26 = Coinbase Gold (expires Mar 27)")
print(f"  Different contracts, different exchanges = different prices")
print("=" * 50)
