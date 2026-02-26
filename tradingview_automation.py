import asyncio
from playwright.async_api import async_playwright
import json
import ctypes
import threading
from datetime import datetime

# TradingView Credentials
TRADINGVIEW_EMAIL = "AlphaMind"
TRADINGVIEW_PASSWORD = "PurushKalai@1988"

# Plus500 Futures Credentials
PLUS500_URL = "https://futures.plus500.com/trade"
PLUS500_USERNAME = "purushsubramani92@gmail.com"
PLUS500_PASSWORD = "Testing#2026"

# Your TradingView Chart URL (interval=1S = 1 second candles, requires Premium/Ultimate plan)
CHART_URL = "https://www.tradingview.com/chart/iDzbrr6O/?symbol=COINBASE%3AGOLJ2026&interval=1S"

# Signal check interval (in seconds)
CHECK_INTERVAL = 1  # Check every 1 second

# Signal keywords from your indicator
BUY_SIGNALS = ['buy', 'open_long']
SELL_SIGNALS = ['sell', 'open_short']
CLOSE_LONG_SIGNALS = ['close_long']
CLOSE_SHORT_SIGNALS = ['close_short']


class TradingViewAutomation:
    def __init__(self):
        self.browser = None
        self.page = None
        self.context = None
        self.last_signal = None  # Track last signal to avoid duplicates
        self.plus500_page = None  # Plus500 browser page
        
    async def start_browser(self, headless=False):
        """Start the browser"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=['--start-maximized', '--force-device-scale-factor=1.0']
        )
        # no_viewport=True lets browser use its actual window size (no zoom issues)
        self.context = await self.browser.new_context(
            no_viewport=True,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        self.page = await self.context.new_page()
        print("✓ Browser started")
        
    async def login_tradingview(self):
        """Login to TradingView - Opens your chart directly"""
        try:
            # Go directly to your chart URL (will prompt login if needed)
            print("Opening your TradingView chart...")
            await self.page.goto(CHART_URL, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(5)
            
            # Check if login is needed
            try:
                # Look for sign-in elements
                signin_visible = await self.page.locator('button:has-text("Sign in"), [data-name="header-user-menu-sign-in"]').first.is_visible(timeout=3000)
                if signin_visible:
                    print("\n⚠️  Login Detected - Please login manually:")
                    print("   1. Click 'Sign in' button in the browser")
                    print("   2. Enter your credentials manually")
                    print("   3. Wait for the chart to fully load")
                    print("   4. Then press Enter here to continue...")
                    await asyncio.to_thread(input)
                else:
                    print("✓ Already logged in")
            except:
                # Already logged in or login not needed
                print("✓ Chart accessible")
            
            # Wait for chart to load
            await asyncio.sleep(3)
            print("✓ Chart opened and ready")
            return True
            
        except Exception as e:
            print(f"✗ Failed to open chart: {e}")
            return False
    
    async def open_chart(self):
        """Open your custom chart"""
        try:
            await self.page.goto(CHART_URL, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(5)  # Wait for chart to fully load
            print(f"✓ Chart loaded: {CHART_URL}")
            return True
        except Exception as e:
            print(f"✗ Failed to open chart: {e}")
            return False
    
    async def get_indicator_values(self):
        """
        Extract indicator values from the chart.
        This reads values from the indicator panel on the chart.
        """
        try:
            # Wait briefly for any updates
            await asyncio.sleep(0.2)
            
            indicator_data = {}
            
            # Method 1: Get ALL text content from chart labels (Buy, Sell, open_long, etc.)
            # These are the colored labels your indicator places on the chart
            all_labels = await self.page.locator('text=/buy|sell|open_long|open_short|close_long|close_short/i').all_text_contents()
            if all_labels:
                indicator_data['labels'] = all_labels
            
            # Method 2: Read from indicator legend (top-left of chart)
            legend_values = await self.page.locator('[class*="valuesWrapper"], [class*="legend"], [data-name="legend"]').all_text_contents()
            if legend_values:
                indicator_data['legend'] = legend_values
            
            # Method 3: Get all visible text that might contain signals
            # Look for elements with signal text
            signal_elements = await self.page.evaluate('''() => {
                const signals = [];
                const elements = document.querySelectorAll('*');
                const keywords = ['buy', 'sell', 'open_long', 'open_short', 'close_long', 'close_short'];
                
                elements.forEach(el => {
                    const text = el.innerText || el.textContent || '';
                    keywords.forEach(keyword => {
                        if (text.toLowerCase().includes(keyword) && text.length < 50) {
                            signals.push(text.trim());
                        }
                    });
                });
                return [...new Set(signals)]; // Remove duplicates
            }''')
            if signal_elements:
                indicator_data['signals'] = signal_elements
            
            # Method 4: Check for recently appeared labels (last candle signals)
            # Get the rightmost signal labels which are the most recent
            recent_signals = await self.page.evaluate('''() => {
                const canvas = document.querySelector('canvas');
                if (!canvas) return [];
                
                // Get all elements positioned near the right side of the chart
                const signals = [];
                const elements = document.querySelectorAll('[class*="label"], [class*="text"], div, span');
                const viewportWidth = window.innerWidth;
                
                elements.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const text = (el.innerText || '').toLowerCase().trim();
                    
                    // Check if it's a signal and positioned in the visible chart area
                    if (rect.right > viewportWidth * 0.7 && rect.width > 0) {
                        if (text.includes('buy') || text.includes('sell') || 
                            text.includes('open_long') || text.includes('open_short') ||
                            text.includes('close_long') || text.includes('close_short')) {
                            signals.push({
                                text: text,
                                x: rect.x,
                                right: rect.right
                            });
                        }
                    }
                });
                
                // Sort by x position (rightmost = most recent)
                signals.sort((a, b) => b.right - a.right);
                return signals.slice(0, 5); // Get 5 most recent
            }''')
            if recent_signals:
                indicator_data['recent'] = recent_signals
            
            return indicator_data
            
        except Exception as e:
            print(f"Error reading indicators: {e}")
            return {}
    
    async def get_signal(self):
        """
        Analyze your custom indicator to determine buy/sell signal.
        Detects: Buy, open_long, Sell, open_short, close_long, close_short
        """
        indicator_values = await self.get_indicator_values()
        
        signal = {
            'timestamp': datetime.now().isoformat(),
            'action': 'HOLD',  # BUY, SELL, CLOSE_LONG, CLOSE_SHORT, or HOLD
            'symbol': 'XAUUSD',
            'indicator_data': indicator_values,
            'confidence': 0,
            'raw_signal': None
        }
        
        # Convert all indicator data to lowercase string for searching
        all_text = json.dumps(indicator_values).lower()
        
        # Check recent signals first (most reliable - rightmost on chart)
        recent = indicator_values.get('recent', [])
        if recent:
            latest_signal = recent[0].get('text', '').lower() if isinstance(recent[0], dict) else str(recent[0]).lower()
            signal['raw_signal'] = latest_signal
            
            # Check for BUY signals
            if any(s in latest_signal for s in BUY_SIGNALS):
                signal['action'] = 'BUY'
                signal['confidence'] = 90
                
            # Check for SELL signals
            elif any(s in latest_signal for s in SELL_SIGNALS):
                signal['action'] = 'SELL'
                signal['confidence'] = 90
                
            # Check for CLOSE signals
            elif any(s in latest_signal for s in CLOSE_LONG_SIGNALS):
                signal['action'] = 'CLOSE_LONG'
                signal['confidence'] = 90
                
            elif any(s in latest_signal for s in CLOSE_SHORT_SIGNALS):
                signal['action'] = 'CLOSE_SHORT'
                signal['confidence'] = 90
        
        # Fallback: check all text if no recent signals found
        if signal['action'] == 'HOLD':
            # Check for BUY signals
            if any(s in all_text for s in BUY_SIGNALS):
                signal['action'] = 'BUY'
                signal['confidence'] = 70
                
            # Check for SELL signals  
            elif any(s in all_text for s in SELL_SIGNALS):
                signal['action'] = 'SELL'
                signal['confidence'] = 70
        
        return signal
    
    async def take_screenshot(self, filename="chart_screenshot.png"):
        """Take a screenshot of the current chart"""
        await self.page.screenshot(path=filename, full_page=False)
        print(f"✓ Screenshot saved: {filename}")
    
    async def setup_plus500(self):
        """
        Open and login to Plus500 Futures in a separate browser tab
        """
        try:
            # Open Plus500 in a new page
            self.plus500_page = await self.context.new_page()
            await self.plus500_page.goto(PLUS500_URL, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(5)
            
            print("📱 Plus500 Futures page opened")
            print("\n⚠️  Please login manually to Plus500:")
            print("   1. Enter your email and password in the browser")
            print("   2. Solve CAPTCHA if it appears")
            print("   3. After login, navigate to Metals > Gold")
            print("   4. Make sure '1 Ounce Gold' is visible in the list")
            print("   5. Then press Enter here to continue...")
            
            # Wait for manual login and setup
            await asyncio.to_thread(input)
            
            print("✓ Plus500 ready for trading")
            return True
            
        except Exception as e:
            print(f"✗ Failed to setup Plus500: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def click_plus500_buy(self):
        """Click BUY button in Plus500 Futures - using recorded selectors"""
        try:
            if not self.plus500_page:
                print("⚠️ Plus500 not connected!")
                return False
            
            # Bring Plus500 tab to focus
            await self.plus500_page.bring_to_front()
            await asyncio.sleep(0.3)
            
            # Click BUY button on Gold instrument (recorded selector)
            # This clicks the second button (index 1) on the Gold instrument row
            await self.plus500_page.locator("#instrumentsRepeater div").filter(
                has_text="1 Ounce Gold"
            ).get_by_role("button").nth(1).click()
            await asyncio.sleep(1)
            
            # Set to Limit order
            await self.plus500_page.get_by_text("Limit", exact=True).click()
            await asyncio.sleep(0.5)
            
            # Enable Take Profit
            await self.plus500_page.get_by_role("switch", name=" Take Profit").check()
            await asyncio.sleep(0.5)
            
            # Set Take Profit amount to 10.00
            await self.plus500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)
            
            # Accept terms if needed
            try:
                accept_btn = self.plus500_page.get_by_role("button", name="Accept")
                if await accept_btn.is_visible(timeout=1000):
                    await accept_btn.click()
                    await asyncio.sleep(0.5)
            except:
                pass
            
            # Place Buy Order
            await self.plus500_page.get_by_role("button", name="Place Buy Order").click()
            await asyncio.sleep(1)
            
            print("✅ BUY order placed on Plus500!")
            
            # Switch back to TradingView
            await self.page.bring_to_front()
            return True
            
        except Exception as e:
            print(f"❌ Failed to click BUY: {e}")
            await self.page.bring_to_front()
            return False
    
    async def click_plus500_sell(self):
        """Click SELL button in Plus500 Futures - using recorded selectors"""
        try:
            if not self.plus500_page:
                print("⚠️ Plus500 not connected!")
                return False
            
            # Bring Plus500 tab to focus
            await self.plus500_page.bring_to_front()
            await asyncio.sleep(0.3)
            
            # Click SELL button (short button) on Gold instrument
            await self.plus500_page.locator("div:nth-child(9) > .short-button > .buySellButton").click()
            await asyncio.sleep(1)
            
            # Set to Limit order
            await self.plus500_page.get_by_text("Limit", exact=True).click()
            await asyncio.sleep(0.5)
            
            # Enable Take Profit
            await self.plus500_page.get_by_role("switch", name=" Take Profit").check()
            await asyncio.sleep(0.5)
            
            # Set Take Profit amount to 10.00
            await self.plus500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)
            
            # Place Sell Order
            await self.plus500_page.get_by_role("button", name="Place Sell Order").click()
            await asyncio.sleep(1)
            
            print("✅ SELL order placed on Plus500!")
            
            # Switch back to TradingView
            await self.page.bring_to_front()
            return True
            
        except Exception as e:
            print(f"❌ Failed to click SELL: {e}")
            await self.page.bring_to_front()
            return False
    
    async def execute_trade(self, signal):
        """Execute trade on Plus500 based on signal"""
        action = signal['action']
        
        if action == 'BUY':
            return await self.click_plus500_buy()
        elif action == 'SELL':
            return await self.click_plus500_sell()
        elif action == 'CLOSE_LONG':
            # Close long = Sell
            return await self.click_plus500_sell()
        elif action == 'CLOSE_SHORT':
            # Close short = Buy
            return await self.click_plus500_buy()
        
        return False
        
    async def monitor_signals(self, interval_seconds=1, auto_trade=True):
        """
        Continuously monitor for trading signals every second.
        
        Args:
            interval_seconds: How often to check for signals (default: 1 second)
            auto_trade: If True, automatically execute trades on 5paisa
        """
        print(f"\n🔄 Starting signal monitoring (checking every {interval_seconds}s)...")
        print("Press Ctrl+C to stop\n")
        
        signal_count = 0
        trade_count = 0
        
        while True:
            try:
                signal = await self.get_signal()
                timestamp = datetime.now().strftime("%H:%M:%S")
                signal_count += 1
                
                # Only print if there's an actionable signal or every 10 checks
                if signal['action'] != 'HOLD':
                    print(f"\n{'='*50}")
                    print(f"🚨 [{timestamp}] SIGNAL: {signal['action']}")
                    print(f"   Raw: {signal.get('raw_signal', 'N/A')}")
                    print(f"   Confidence: {signal['confidence']}%")
                    print(f"{'='*50}")
                    
                    # Check if this is a new signal (not duplicate)
                    if self.last_signal != signal['action']:
                        self.last_signal = signal['action']
                        
                        if auto_trade and signal['action'] in ['BUY', 'SELL', 'CLOSE_LONG', 'CLOSE_SHORT']:
                            success = await self.execute_trade(signal)
                            if success:
                                trade_count += 1
                                print(f"📊 Total trades executed: {trade_count}")
                    else:
                        print("   (Duplicate signal - skipping)")
                        
                elif signal_count % 10 == 0:
                    # Print status every 10 checks
                    print(f"[{timestamp}] Monitoring... (checked {signal_count} times, {trade_count} trades)")
                    
                await asyncio.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                print(f"\n⏹ Monitoring stopped. Total checks: {signal_count}, Trades: {trade_count}")
                break
            except Exception as e:
                print(f"Error during monitoring: {e}")
                await asyncio.sleep(interval_seconds)
    
    async def close(self):
        """Close the browser"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("✓ Browser closed")


# Example callback for 5paisa integration
async def on_signal_detected(signal):
    """
    This function is called when a BUY/SELL signal is detected.
    Connect this to 5paisa API for automated order execution.
    """
    print(f"\n🚨 SIGNAL DETECTED: {signal['action']}")
    print(f"   Symbol: {signal['symbol']}")
    print(f"   Time: {signal['timestamp']}")
    print(f"   Confidence: {signal['confidence']}%")


def start_keep_alive():
    """Background thread to prevent system sleep/lock"""
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    ES_AWAYMODE_REQUIRED = 0x00000040
    while True:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
        import time; time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
        import time; time.sleep(30)


async def main():
    """Main function to run the TradingView + Plus500 automation"""
    # Start keep-alive to prevent sleep/lock
    t = threading.Thread(target=start_keep_alive, daemon=True)
    t.start()
    print("🔋 Keep-Alive ACTIVE - System will NOT sleep or lock")
    
    tv = TradingViewAutomation()
    
    try:
        print("\n" + "="*50)
        print("📊 TRADINGVIEW + PLUS500 AUTO-TRADER")
        print("="*50 + "\n")
        
        # Start browser (set headless=True for background execution)
        await tv.start_browser(headless=False)
        
        # Login to TradingView (manual login)
        print("Step 1: Opening TradingView Chart")
        login_success = await tv.login_tradingview()
        if not login_success:
            print("Failed to open TradingView. Exiting.")
            return
        
        # Chart is already loaded, no need to open again
        print("✓ Chart is ready")
        
        # Take a screenshot of the chart
        await tv.take_screenshot("tradingview_chart.png")
        
        # Setup Plus500 (opens in new tab)
        print("\n" + "-"*50)
        print("Step 2: Setting up Plus500 Futures...")
        print("-"*50)
        
        # Open Plus500 and login
        plus500_success = await tv.setup_plus500()
        if not plus500_success:
            print("Failed to setup Plus500. Exiting.")
            return
        
        # Get current signal as a test
        print("\n📈 Step 3: Testing signal detection...")
        signal = await tv.get_signal()
        print(f"Current Signal: {signal['action']}")
        print(f"Raw Signal: {signal.get('raw_signal', 'N/A')}")
        print(f"Indicator Data: {signal.get('indicator_data', {})}")
        
        # Start continuous monitoring (every 1 second)
        print("\n" + "="*50)
        print("🚀 Step 4: STARTING AUTO-TRADING")
        print(f"   - Checking chart every {CHECK_INTERVAL} second(s)")
        print("   - Buy/open_long → Click BUY on Plus500")
        print("   - Sell/open_short → Click SELL on Plus500")
        print("="*50)
        
        await tv.monitor_signals(interval_seconds=CHECK_INTERVAL, auto_trade=True)
        
    except KeyboardInterrupt:
        print("\n\n⏹ Stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await tv.close()


if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════╗
    ║     TRADINGVIEW + PLUS500 AUTO-TRADING BOT           ║
    ║                                                      ║
    ║  Monitors your TradingView indicators and            ║
    ║  automatically executes trades on Plus500 Futures    ║
    ║                                                      ║
    ║  Signals:                                            ║
    ║    • Buy / open_long  → BUY on Plus500              ║
    ║    • Sell / open_short → SELL on Plus500            ║
    ║    • close_long → SELL (close position)             ║
    ║    • close_short → BUY (close position)             ║
    ╚══════════════════════════════════════════════════════╝
    """)
    asyncio.run(main())
