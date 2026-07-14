"""Browser automation module - CDP-based browser control.

A simplified, direct API for launching Chromium-based browsers and performing
actions via the Chrome DevTools Protocol (CDP). No event bus, no LLM, no agent.

Key Components:
    - BrowserConfig: Configuration for browser launch/connection
    - BrowserSession: Core browser session (start/stop/navigate/screenshot)
    - Page: Page-level operations (navigate, evaluate, query_selector)
    - Element: DOM element interactions (click, type, get info)
    - Mouse: Raw mouse operations (click coordinates, scroll, drag)

Usage:
    from src.browser import BrowserConfig, BrowserSession

    config = BrowserConfig(headless=True)
    session = BrowserSession(config=config)
    await session.start()
    await session.navigate("https://example.com")

    page = await session.get_current_page()
    element = await page.query_selector("h1")
    text = await element.get_text()
    print(text)

    await session.stop()
"""

from .config import BrowserChannel, BrowserConfig, ViewportSize, ProxySettings, find_chrome_executable
from .element import Element
from .mouse import Mouse
from .page import Page
from .session import BrowserSession

__all__ = [
    'BrowserConfig',
    'BrowserChannel',
    'BrowserSession',
    'Element',
    'Mouse',
    'Page',
    'ProxySettings',
    'ViewportSize',
    'find_chrome_executable',
]
