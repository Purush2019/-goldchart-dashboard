"""
Simple script to open Plus500 and help you find the Buy/Sell button selectors.
Use this to manually inspect the buttons with browser DevTools.
"""

import asyncio
from playwright.async_api import async_playwright

PLUS500_URL = "https://futures.plus500.com/trade"
PLUS500_USERNAME = "purushsubramani92@gmail.com"
PLUS500_PASSWORD = "Testing#2026"

async def inspect_plus500():
    async with async_playwright() as p:
        # Launch browser with DevTools
        browser = await p.chromium.launch(
            headless=False,
            args=['--start-maximized']
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )
        
        page = await context.new_page()
        
        print("\n" + "="*60)
        print("PLUS500 INSPECTOR")
        print("="*60)
        print("\n1. Opening Plus500...")
        
        await page.goto(PLUS500_URL, wait_until='networkidle')
        await asyncio.sleep(3)
        
        print("2. Login to Plus500 manually in the browser")
        print("3. Select GOLD (XAUUSD) for trading")
        print("\n4. TO FIND SELECTORS:")
        print("   - Right-click on the BUY button → Inspect")
        print("   - Look for attributes like:")
        print("     * class='...'")
        print("     * data-qa='...'")
        print("     * id='...'")
        print("   - Copy those and share them with me")
        print("\n5. Do the same for the SELL button")
        print("\n6. Press Enter here when done...")
        
        await asyncio.to_thread(input)
        
        print("\n✓ Closing browser...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_plus500())
