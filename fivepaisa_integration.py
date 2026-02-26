"""
5paisa Trading Integration
This module handles order execution on 5paisa platform.

Prerequisites:
1. Install py5paisa: pip install py5paisa
2. Have a 5paisa trading account
3. Get your API credentials from 5paisa

Note: You can use either:
- Option A: py5paisa library (API-based, recommended)
- Option B: Playwright browser automation (web-based)
"""

import asyncio
from datetime import datetime

# ============================================
# OPTION A: Using py5paisa API (Recommended)
# ============================================

class FivePaisaAPI:
    """5paisa API-based trading"""
    
    def __init__(self, email, password, dob):
        """
        Initialize 5paisa client
        
        Args:
            email: Your 5paisa registered email
            password: Your 5paisa password
            dob: Date of birth in YYYYMMDD format
        """
        self.email = email
        self.password = password
        self.dob = dob
        self.client = None
        
    def login(self):
        """Login to 5paisa"""
        try:
            from py5paisa import FivePaisaClient
            
            self.client = FivePaisaClient(
                email=self.email,
                passwd=self.password,
                dob=self.dob
            )
            self.client.login()
            print("✓ Logged into 5paisa")
            return True
        except ImportError:
            print("✗ py5paisa not installed. Run: pip install py5paisa")
            return False
        except Exception as e:
            print(f"✗ 5paisa login failed: {e}")
            return False
    
    def get_margin(self):
        """Get available margin"""
        if self.client:
            margin = self.client.margin()
            print(f"Available Margin: {margin}")
            return margin
        return None
    
    def get_holdings(self):
        """Get current holdings"""
        if self.client:
            holdings = self.client.holdings()
            print(f"Holdings: {holdings}")
            return holdings
        return None
    
    def place_order(self, symbol, qty, buy_sell, exchange="N", order_type="MKT", price=0):
        """
        Place an order on 5paisa
        
        Args:
            symbol: Stock/Scrip code (e.g., 1660 for ITC)
            qty: Quantity to trade
            buy_sell: "B" for Buy, "S" for Sell
            exchange: "N" for NSE, "B" for BSE, "M" for MCX
            order_type: "MKT" for Market, "L" for Limit
            price: Limit price (0 for market orders)
        """
        if not self.client:
            print("Not logged in!")
            return None
            
        try:
            order_response = self.client.place_order(
                OrderType=buy_sell,
                Exchange=exchange,
                ExchangeType="C",  # Cash
                ScripCode=symbol,
                Qty=qty,
                Price=price,
                IsIntraday=True,  # Set False for delivery
                StopLossPrice=0,
                RemoteOrderID="1"
            )
            print(f"✓ Order placed: {order_response}")
            return order_response
        except Exception as e:
            print(f"✗ Order failed: {e}")
            return None
    
    def get_order_status(self):
        """Get status of orders"""
        if self.client:
            return self.client.order_book()
        return None


# ============================================
# OPTION B: Using Playwright (Browser-based)
# ============================================

class FivePaisaPlaywright:
    """5paisa browser automation using Playwright"""
    
    def __init__(self, client_code, password, pin):
        """
        Initialize 5paisa Playwright automation
        
        Args:
            client_code: Your 5paisa client code
            password: Your trading password
            pin: Your 5paisa PIN
        """
        self.client_code = client_code
        self.password = password
        self.pin = pin
        self.browser = None
        self.page = None
        
    async def start_browser(self, headless=False):
        """Start browser"""
        from playwright.async_api import async_playwright
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=headless)
        self.context = await self.browser.new_context(
            viewport={'width': 1366, 'height': 768}
        )
        self.page = await self.context.new_page()
        print("✓ Browser started for 5paisa")
        
    async def login(self):
        """Login to 5paisa web platform"""
        try:
            await self.page.goto("https://login.5paisa.com/", wait_until='networkidle')
            await asyncio.sleep(2)
            
            # Enter client code
            await self.page.fill('input[placeholder*="Client Code"], input[name="clientcode"]', self.client_code)
            await asyncio.sleep(0.5)
            
            # Enter password
            await self.page.fill('input[type="password"]', self.password)
            await asyncio.sleep(0.5)
            
            # Click login
            await self.page.click('button[type="submit"], button:has-text("Login")')
            await asyncio.sleep(3)
            
            # Enter PIN if required
            pin_input = self.page.locator('input[placeholder*="PIN"], input[type="password"]')
            if await pin_input.is_visible():
                await pin_input.fill(self.pin)
                await self.page.click('button[type="submit"]')
                await asyncio.sleep(3)
            
            print("✓ Logged into 5paisa")
            return True
            
        except Exception as e:
            print(f"✗ 5paisa login failed: {e}")
            return False
    
    async def search_stock(self, symbol):
        """Search for a stock"""
        try:
            search_box = self.page.locator('input[placeholder*="Search"], input[type="search"]').first
            await search_box.fill(symbol)
            await asyncio.sleep(1)
            
            # Click on first result
            await self.page.click(f'text={symbol}')
            await asyncio.sleep(1)
            return True
        except Exception as e:
            print(f"Error searching stock: {e}")
            return False
    
    async def place_order(self, symbol, qty, action="BUY", order_type="MARKET"):
        """
        Place order through web interface
        
        Args:
            symbol: Stock symbol
            qty: Quantity
            action: "BUY" or "SELL"
            order_type: "MARKET" or "LIMIT"
        """
        try:
            # Search for stock
            await self.search_stock(symbol)
            
            # Click Buy or Sell button
            if action.upper() == "BUY":
                await self.page.click('button:has-text("Buy"), button:has-text("BUY")')
            else:
                await self.page.click('button:has-text("Sell"), button:has-text("SELL")')
            await asyncio.sleep(1)
            
            # Enter quantity
            qty_input = self.page.locator('input[placeholder*="Qty"], input[name="qty"]').first
            await qty_input.fill(str(qty))
            await asyncio.sleep(0.5)
            
            # Select order type
            if order_type.upper() == "MARKET":
                await self.page.click('text=Market, label:has-text("Market")')
            
            # Confirm order
            await self.page.click('button:has-text("Place Order"), button:has-text("Submit")')
            await asyncio.sleep(2)
            
            print(f"✓ Order placed: {action} {qty} {symbol}")
            return True
            
        except Exception as e:
            print(f"✗ Order failed: {e}")
            return False
    
    async def close(self):
        """Close browser"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


# ============================================
# COMBINED: TradingView + 5paisa Integration
# ============================================

class TradingBot:
    """
    Combined trading bot that reads signals from TradingView
    and executes trades on 5paisa
    """
    
    def __init__(self, tv_automation, paisa_client):
        """
        Args:
            tv_automation: TradingViewAutomation instance
            paisa_client: FivePaisaAPI or FivePaisaPlaywright instance
        """
        self.tv = tv_automation
        self.paisa = paisa_client
        self.last_signal = None
        self.trade_log = []
        
    async def execute_signal(self, signal):
        """Execute a trading signal on 5paisa"""
        
        if signal['action'] == 'HOLD':
            return
            
        # Avoid duplicate trades
        if self.last_signal and self.last_signal['action'] == signal['action']:
            print("⚠ Duplicate signal, skipping...")
            return
            
        timestamp = datetime.now().isoformat()
        
        print(f"\n{'='*50}")
        print(f"🚨 EXECUTING {signal['action']} SIGNAL")
        print(f"   Symbol: {signal['symbol']}")
        print(f"   Time: {timestamp}")
        print(f"{'='*50}\n")
        
        # Execute on 5paisa
        # Modify these parameters based on your trading preferences
        if isinstance(self.paisa, FivePaisaAPI):
            # API-based execution
            result = self.paisa.place_order(
                symbol=signal.get('scrip_code', 0),  # You need to map symbol to scrip code
                qty=signal.get('quantity', 1),
                buy_sell="B" if signal['action'] == "BUY" else "S"
            )
        else:
            # Playwright-based execution
            result = await self.paisa.place_order(
                symbol=signal['symbol'],
                qty=signal.get('quantity', 1),
                action=signal['action']
            )
        
        # Log the trade
        trade = {
            'timestamp': timestamp,
            'signal': signal,
            'result': result
        }
        self.trade_log.append(trade)
        self.last_signal = signal
        
        return result
    
    async def run(self, check_interval=60):
        """
        Main loop: Monitor TradingView signals and execute on 5paisa
        
        Args:
            check_interval: Seconds between signal checks
        """
        print("\n🤖 Trading Bot Started")
        print(f"   Checking signals every {check_interval} seconds")
        print("   Press Ctrl+C to stop\n")
        
        while True:
            try:
                # Get signal from TradingView
                signal = await self.tv.get_signal()
                
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] Signal: {signal['action']}")
                
                # Execute if BUY or SELL
                if signal['action'] in ['BUY', 'SELL']:
                    await self.execute_signal(signal)
                
                await asyncio.sleep(check_interval)
                
            except KeyboardInterrupt:
                print("\n⏹ Bot stopped by user")
                break
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(check_interval)
        
        # Print trade summary
        print(f"\n📊 Trade Summary: {len(self.trade_log)} trades executed")
        for trade in self.trade_log:
            print(f"   {trade['timestamp']}: {trade['signal']['action']}")


# ============================================
# CONFIGURATION - UPDATE WITH YOUR DETAILS
# ============================================

# 5paisa Credentials (UPDATE THESE)
FIVEPAISA_EMAIL = "your_email@example.com"
FIVEPAISA_PASSWORD = "your_password"
FIVEPAISA_DOB = "19880101"  # YYYYMMDD format

# Or for web automation
FIVEPAISA_CLIENT_CODE = "your_client_code"
FIVEPAISA_PIN = "your_pin"


async def main():
    """Example usage"""
    
    # Import TradingView automation
    from tradingview_automation import TradingViewAutomation
    
    # Initialize TradingView
    tv = TradingViewAutomation()
    await tv.start_browser(headless=False)
    await tv.login_tradingview()
    await tv.open_chart()
    
    # Initialize 5paisa (choose one method)
    
    # Method 1: API-based (recommended)
    # paisa = FivePaisaAPI(
    #     email=FIVEPAISA_EMAIL,
    #     password=FIVEPAISA_PASSWORD,
    #     dob=FIVEPAISA_DOB
    # )
    # paisa.login()
    
    # Method 2: Playwright-based
    paisa = FivePaisaPlaywright(
        client_code=FIVEPAISA_CLIENT_CODE,
        password=FIVEPAISA_PASSWORD,
        pin=FIVEPAISA_PIN
    )
    await paisa.start_browser(headless=False)
    await paisa.login()
    
    # Create and run trading bot
    bot = TradingBot(tv, paisa)
    await bot.run(check_interval=60)
    
    # Cleanup
    await tv.close()
    await paisa.close()


if __name__ == "__main__":
    asyncio.run(main())
