"""
appium_navigator.py — Automate KukuTV app navigation via Appium to trigger API calls
while mitmproxy captures the traffic.

Navigates through: Home → Categories → Shows → Episodes → Player
"""

import argparse
import os
import sys
import time

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from rich.console import Console

console = Console()


def get_driver(
    package: str,
    activity: str = "",
    appium_url: str = "http://127.0.0.1:4723",
    device_name: str = "emulator-5554",
    platform_version: str = "13",
    no_reset: bool = True,
) -> webdriver.Remote:
    """Create and return an Appium driver for KukuTV."""
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.device_name = device_name
    options.platform_version = platform_version
    options.app_package = package
    if activity:
        options.app_activity = activity
    options.no_reset = no_reset
    options.auto_grant_permissions = True
    options.new_command_timeout = 300

    console.print(f"[cyan]Connecting to Appium at {appium_url}...[/cyan]")
    driver = webdriver.Remote(appium_url, options=options)
    console.print("[green]✓ Connected to device[/green]")
    return driver


def wait_and_find(driver, by, value, timeout=10):
    """Wait for an element and return it."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def scroll_down(driver, times=3):
    """Scroll down the screen to load more content."""
    size = driver.get_window_size()
    start_x = size["width"] // 2
    start_y = int(size["height"] * 0.8)
    end_y = int(size["height"] * 0.2)
    for i in range(times):
        driver.swipe(start_x, start_y, start_x, end_y, duration=800)
        time.sleep(2)
        console.print(f"  Scrolled down ({i + 1}/{times})")


def tap_element_safe(driver, element, label="element"):
    """Tap an element with error handling."""
    try:
        element.click()
        console.print(f"  [green]✓ Tapped: {label}[/green]")
        time.sleep(3)
        return True
    except Exception as e:
        console.print(f"  [red]✗ Failed to tap {label}: {e}[/red]")
        return False


def explore_home(driver):
    """Explore the home screen — scroll and tap featured content."""
    console.print("\n[bold cyan]═══ Exploring Home Screen ═══[/bold cyan]")
    time.sleep(5)  # Let home load fully

    # Scroll to trigger lazy-loaded API calls
    scroll_down(driver, times=5)

    # Try tapping items on screen
    try:
        # Look for clickable items (cards, thumbnails, etc.)
        items = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.ImageView")
        console.print(f"  Found {len(items)} image elements")
        if len(items) > 2:
            tap_element_safe(driver, items[2], "content card")
            time.sleep(3)
            scroll_down(driver, times=2)  # Scroll detail page
            driver.back()
            time.sleep(2)
    except Exception as e:
        console.print(f"  [yellow]Home exploration note: {e}[/yellow]")


def explore_navigation_tabs(driver):
    """Tap through bottom navigation tabs to trigger different API endpoints."""
    console.print("\n[bold cyan]═══ Exploring Navigation Tabs ═══[/bold cyan]")

    # Common tab identifiers
    tab_keywords = ["home", "search", "explore", "discover", "library", "profile",
                     "categories", "browse", "my", "account", "downloads"]

    # Try finding tabs via various strategies
    for strategy in [
        (AppiumBy.CLASS_NAME, "android.widget.BottomNavigationItemView"),
        (AppiumBy.CLASS_NAME, "com.google.android.material.bottomnavigation.BottomNavigationItemView"),
        (AppiumBy.XPATH, "//android.widget.LinearLayout[@resource-id]//android.widget.FrameLayout"),
    ]:
        try:
            tabs = driver.find_elements(*strategy)
            if tabs:
                console.print(f"  Found {len(tabs)} navigation tabs")
                for i, tab in enumerate(tabs):
                    tap_element_safe(driver, tab, f"tab {i}")
                    time.sleep(3)
                    scroll_down(driver, times=2)
                break
        except Exception:
            continue


def explore_search(driver):
    """Use the search feature to trigger search APIs."""
    console.print("\n[bold cyan]═══ Exploring Search ═══[/bold cyan]")

    search_queries = ["drama", "comedy", "action", "romance", "trending"]

    try:
        # Try to find search icon/bar
        for selector in [
            (AppiumBy.ACCESSIBILITY_ID, "Search"),
            (AppiumBy.XPATH, "//*[contains(@content-desc, 'search') or contains(@content-desc, 'Search')]"),
            (AppiumBy.XPATH, "//*[contains(@resource-id, 'search')]"),
        ]:
            try:
                search_btn = driver.find_element(*selector)
                tap_element_safe(driver, search_btn, "search")
                break
            except Exception:
                continue

        time.sleep(2)

        # Type search queries
        for query in search_queries:
            try:
                search_input = driver.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText")
                search_input.clear()
                search_input.send_keys(query)
                time.sleep(3)  # Wait for search results API
                console.print(f"  Searched: '{query}'")
                scroll_down(driver, times=1)
            except Exception:
                break

        driver.back()
        time.sleep(2)
    except Exception as e:
        console.print(f"  [yellow]Search exploration note: {e}[/yellow]")


def explore_content_detail(driver):
    """Try to enter a content detail page and explore episodes/player."""
    console.print("\n[bold cyan]═══ Exploring Content Details ═══[/bold cyan]")

    try:
        # Find and tap a content item
        items = driver.find_elements(AppiumBy.CLASS_NAME, "android.view.ViewGroup")
        clickable = [i for i in items if i.get_attribute("clickable") == "true"]

        if clickable and len(clickable) > 3:
            tap_element_safe(driver, clickable[3], "content item")
            time.sleep(4)

            # Scroll detail page to load all info
            scroll_down(driver, times=3)

            # Try to find and tap episode list / play button
            for selector in [
                (AppiumBy.XPATH, "//*[contains(@text, 'Episode') or contains(@text, 'episode')]"),
                (AppiumBy.XPATH, "//*[contains(@text, 'Play') or contains(@text, 'play')]"),
                (AppiumBy.XPATH, "//*[contains(@content-desc, 'play') or contains(@content-desc, 'Play')]"),
            ]:
                try:
                    el = driver.find_element(*selector)
                    tap_element_safe(driver, el, el.text or "play/episode")
                    time.sleep(5)  # Let video APIs fire
                    break
                except Exception:
                    continue

            driver.back()
            time.sleep(2)
            driver.back()
            time.sleep(2)
    except Exception as e:
        console.print(f"  [yellow]Detail exploration note: {e}[/yellow]")


def dump_page_source(driver, label: str):
    """Save page source XML for analysis."""
    try:
        source = driver.page_source
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata", "page_sources")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{label}_{int(time.time())}.xml")
        with open(path, "w") as f:
            f.write(source)
        console.print(f"  [dim]Page source saved: {path}[/dim]")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Navigate KukuTV app via Appium")
    parser.add_argument("--package", default="com.kukufm.android", help="App package name")
    parser.add_argument("--activity", default="", help="Main activity (auto-detected if empty)")
    parser.add_argument("--appium-url", default="http://127.0.0.1:4723", help="Appium server URL")
    parser.add_argument("--device", default="emulator-5554", help="Device name")
    parser.add_argument("--platform-version", default="13")
    parser.add_argument("--dump-sources", action="store_true", help="Save page source XML at each step")
    args = parser.parse_args()

    driver = None
    try:
        driver = get_driver(
            package=args.package,
            activity=args.activity,
            appium_url=args.appium_url,
            device_name=args.device,
            platform_version=args.platform_version,
        )

        console.print(f"\n[bold green]KukuTV Navigator Started[/bold green]")
        console.print(f"Package: {args.package}\n")

        # Handle any initial popups/permissions
        time.sleep(5)
        try:
            allow_btns = driver.find_elements(
                AppiumBy.XPATH, "//*[contains(@text, 'Allow') or contains(@text, 'ALLOW')]"
            )
            for btn in allow_btns:
                btn.click()
                time.sleep(1)
        except Exception:
            pass

        if args.dump_sources:
            dump_page_source(driver, "home")

        # Run exploration routines
        explore_home(driver)
        explore_navigation_tabs(driver)
        explore_search(driver)
        explore_content_detail(driver)

        console.print("\n[bold green]✓ Navigation complete![/bold green]")
        console.print("Check metadata/captured_apis/ for intercepted API data.\n")

    except Exception as e:
        console.print(f"\n[red bold]Error: {e}[/red bold]")
        sys.exit(1)
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    main()
