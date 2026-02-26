import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime

# Plus500 Futures Credentials
PLUS500_URL = "https://futures.plus500.com/trade"
PLUS500_USERNAME = "purushsubramani92@gmail.com"
PLUS500_PASSWORD = "Testing#2026"

# Signal check interval (in seconds)
CHECK_INTERVAL = 1  # Check every 1 second

# Signal keywords from your indicator
BUY_SIGNALS = ['buy', 'open_long']
SELL_SIGNALS = ['sell', 'open_short']
CLOSE_LONG_SIGNALS = ['close_long']
CLOSE_SHORT_SIGNALS = ['close_short']


class TradingBot:
    def __init__(self):
        self.browser = None
        self.plus500_page = None
        self.context = None
        self.last_signal = None
        
    async def start_browser(self):
        """Start browser for Plus500 only"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=['--start-maximized']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )
        print("✓ Browser started")
        
    async def setup_plus500(self):
        """Open and login to Plus500"""
        try:
            self.plus500_page = await self.context.new_page()
            await self.plus500_page.goto(PLUS500_URL, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            
            print("\n📱 Plus500 Futures page opened - Starting auto-login...")
            
            try:
                # Close "new user" popup if exists
                try:
                    cancel_btn = self.plus500_page.locator("#newUserCancel")
                    if await cancel_btn.is_visible(timeout=2000):
                        await cancel_btn.click()
                        await asyncio.sleep(0.5)
                except:
                    pass
                
                # Fill email
                await self.plus500_page.get_by_role("textbox", name="Email").fill(PLUS500_USERNAME)
                await asyncio.sleep(0.5)
                
                # Fill password
                await self.plus500_page.get_by_role("textbox", name="Password").fill(PLUS500_PASSWORD)
                await asyncio.sleep(0.5)
                
                # Click Log in
                await self.plus500_page.get_by_role("button", name="Log in").click()
                await asyncio.sleep(3)
                
                print("✓ Login submitted")
                print("⚠️  If CAPTCHA appears, please solve it manually")
                await asyncio.sleep(5)
                
                # Click "Allow" for notifications if prompted
                try:
                    allow_btn = self.plus500_page.get_by_role("button", name="Allow")
                    if await allow_btn.is_visible(timeout=3000):
                        await allow_btn.click()
                        await asyncio.sleep(1)
                except:
                    pass
                
                # Navigate to Metals > Gold
                print("📊 Selecting GOLD instrument...")
                await self.plus500_page.locator("#categories").get_by_text("Metals").click()
                await asyncio.sleep(2)
                
                print("✓ Plus500 logged in and GOLD selected")
                
            except Exception as e:
                print(f"⚠️ Auto-login issue: {e}")
                print("   Please complete login manually...")
            
            print("\n⚠️  Verify Plus500 is ready with GOLD selected")
            print("   Press Enter to continue...")
            await asyncio.to_thread(input)
            
            return True
            
        except Exception as e:
            print(f"✗ Failed to setup Plus500: {e}")
            return False
    
    async def get_tradingview_signal(self):
        """
        Monitor TradingView chart that's ALREADY OPEN in your browser.
        This checks the clipboard or looks for signals in open windows.
        """
        # For now, manual input - you can enhance this later
        print("\n📊 Checking for signals...")
        print("   Current action: Monitoring TradingView...")
        
        # TODO: You can implement clipboard monitoring or screen capture here
        # For now, return HOLD
        signal = {
            'timestamp': datetime.now().isoformat(),
            'action': 'HOLD',
            'symbol': 'XAUUSD',
            'confidence': 0
        }
        
        return signal
    
    async def manual_trade_input(self):
        """
        Simple manual input for testing.
        Type 'buy' or 'sell' to execute trades.
        """
        print("\n" + "="*60)
        print("MANUAL TRADE MODE")
        print("="*60)
        print("\nCommands:")
        print("  buy   - Execute BUY on Plus500")
        print("  sell  - Execute SELL on Plus500")
        print("  quit  - Stop the bot")
        print("\nType command and press Enter:")
        
        while True:
            try:
                command = await asyncio.to_thread(input, "> ")
                command = command.strip().lower()
                
                if command == 'quit':
                    break
                elif command == 'buy':
                    await self.click_plus500_buy()
                elif command == 'sell':
                    await self.click_plus500_sell()
                else:
                    print("Unknown command. Use: buy, sell, or quit")
                    
            except KeyboardInterrupt:
                break
    
    async def click_plus500_buy(self):
        """Execute BUY on Plus500"""
        try:
            if not self.plus500_page:
                print("⚠️ Plus500 not connected!")
                return False
            
            await self.plus500_page.bring_to_front()
            await asyncio.sleep(0.3)
            
            # Click BUY button
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
            
            # Set Take Profit
            await self.plus500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)
            
            # Accept terms
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
            return True
            
        except Exception as e:
            print(f"❌ Failed to BUY: {e}")
            return False
    
    async def click_plus500_sell(self):
        """Execute SELL on Plus500"""
        try:
            if not self.plus500_page:
                print("⚠️ Plus500 not connected!")
                return False
            
            await self.plus500_page.bring_to_front()
            await asyncio.sleep(0.3)
            
            # Click SELL button
            await self.plus500_page.locator("div:nth-child(9) > .short-button > .buySellButton").click()
            await asyncio.sleep(1)
            
            # Set to Limit order
            await self.plus500_page.get_by_text("Limit", exact=True).click()
            await asyncio.sleep(0.5)
            
            # Enable Take Profit
            await self.plus500_page.get_by_role("switch", name=" Take Profit").check()
            await asyncio.sleep(0.5)
            
            # Set Take Profit
            await self.plus500_page.get_by_role("textbox").nth(3).fill("10.00")
            await asyncio.sleep(0.5)
            
            # Place Sell Order
            await self.plus500_page.get_by_role("button", name="Place Sell Order").click()
            await asyncio.sleep(1)
            
            print("✅ SELL order placed on Plus500!")
            return True
            
        except Exception as e:
            print(f"❌ Failed to SELL: {e}")
            return False
    
    async def close(self):
        """Close browser"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("✓ Browser closed")


async def main():
    """Main function"""
    bot = TradingBot()
    
    try:
        print("""
    ╔══════════════════════════════════════════════════════╗
    ║           PLUS500 AUTO-TRADING BOT                   ║
    ║                                                      ║
    ║  1. Open TradingView manually in your browser        ║
    ║  2. This bot will only control Plus500               ║
    ║  3. Type 'buy' or 'sell' to execute trades           ║
    ╚══════════════════════════════════════════════════════╝
        """)
        
        # Start browser and setup Plus500
        await bot.start_browser()
        await bot.setup_plus500()
        
        # Manual trade mode
        await bot.manual_trade_input()
        
    except KeyboardInterrupt:
        print("\n⏹ Stopped by user")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
