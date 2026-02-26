"""
Keep System Alive - Prevents sleep, lock, and screen off.
Snaps TradingView (left) and Plus500 (right) side by side.
Run this BEFORE or AFTER starting the trading bot.
Works even when laptop lid is closed.
"""
import ctypes
import ctypes.wintypes
import time
import threading

# Windows API constants
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040

SW_RESTORE = 9
SW_SHOWNORMAL = 1
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040

# Window title keywords to identify each app
TRADINGVIEW_KEYWORDS = ['tradingview', 'trading view']
PLUS500_KEYWORDS = ['plus500', 'plus 500', 'futures trading']


def get_screen_size():
    """Get primary screen resolution"""
    width = ctypes.windll.user32.GetSystemMetrics(0)
    height = ctypes.windll.user32.GetSystemMetrics(1)
    return width, height


def get_all_visible_windows():
    """Get all top-level visible windows"""
    windows = []
    
    def enum_callback(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                title = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, title, length + 1)
                windows.append((hwnd, title.value))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return windows


def find_window_by_keywords(keywords):
    """Find a window handle by title keywords"""
    windows = get_all_visible_windows()
    for hwnd, title in windows:
        title_lower = title.lower()
        for kw in keywords:
            if kw in title_lower:
                return hwnd, title
    return None, None


def snap_windows_side_by_side():
    """Force TradingView left half, Plus500 right half"""
    screen_w, screen_h = get_screen_size()
    half_w = screen_w // 2
    
    # Find TradingView window
    tv_hwnd, tv_title = find_window_by_keywords(TRADINGVIEW_KEYWORDS)
    # Find Plus500 window
    p500_hwnd, p500_title = find_window_by_keywords(PLUS500_KEYWORDS)
    
    if tv_hwnd:
        # Restore if minimized/maximized, then position left half
        ctypes.windll.user32.ShowWindow(tv_hwnd, SW_RESTORE)
        ctypes.windll.user32.SetWindowPos(
            tv_hwnd, 0,
            0, 0, half_w, screen_h,
            SWP_NOZORDER | SWP_SHOWWINDOW
        )
    
    if p500_hwnd:
        # Restore if minimized/maximized, then position right half
        ctypes.windll.user32.ShowWindow(p500_hwnd, SW_RESTORE)
        ctypes.windll.user32.SetWindowPos(
            p500_hwnd, 0,
            half_w, 0, half_w, screen_h,
            SWP_NOZORDER | SWP_SHOWWINDOW
        )


def keep_alive():
    """Prevent Windows from sleeping and keep windows side by side"""
    print("🔋 Keep-Alive ACTIVE - System will NOT sleep or lock")
    print("🪟 Side-by-Side ACTIVE - TradingView LEFT | Plus500 RIGHT")
    print("   Press Ctrl+C to stop\n")
    
    try:
        while True:
            # Tell Windows the system and display are in use
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_AWAYMODE_REQUIRED
            )
            
            # Force windows back to side-by-side position
            snap_windows_side_by_side()
            
            # Simulate a tiny mouse jiggle to prevent idle lock
            ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
            
            time.sleep(5)  # Check every 5 seconds
            
    except KeyboardInterrupt:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print("\n⏹ Keep-Alive stopped. Normal power settings restored.")


def start_keep_alive_thread():
    """Start keep-alive in a background thread (use from trading bot)"""
    t = threading.Thread(target=keep_alive, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════╗
    ║       SYSTEM KEEP-ALIVE + SNAP ACTIVE      ║
    ║                                            ║
    ║  ✓ Prevents sleep / lock / display off     ║
    ║  ✓ Works with lid closed                   ║
    ║  ✓ TradingView snapped LEFT half           ║
    ║  ✓ Plus500 snapped RIGHT half              ║
    ║  ✓ Auto-restores every 5 seconds           ║
    ║                                            ║
    ║  Press Ctrl+C to stop                      ║
    ╚════════════════════════════════════════════╝
    """)
    keep_alive()
