#!/usr/bin/env python3
"""
Auto Playlist Scraper v2
Uses Selenium + CDP Network Interception to capture m3u8 streams.
Handles maskr.blog multi-step redirects with countdown timers.
"""

import re
import os
import sys
import json
import time
import random
import logging
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException,
    NoSuchWindowException
)
from webdriver_manager.chrome import ChromeDriverManager

# ─── Config ──────────────────────────────────────────────────────
SOURCE_URL = "https://www.tamiltvserial.com/all-bigg-boss-live-24-7/"
PLAYLIST_FILE = "playlist.m3u"
JSON_FILE = "playlist.json"
LOG_FILE = "log.txt"
REQUEST_TIMEOUT = 30
PAGE_WAIT = 15
PLAYER_WAIT = 20

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    ]
)
log = logging.getLogger(__name__)


# ─── Browser Setup ───────────────────────────────────────────────
def create_driver():
    """Create headless Chrome with CDP network logging enabled."""
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--disable-extensions')
    opts.add_argument('--disable-popup-blocking')
    opts.add_argument('--ignore-certificate-errors')
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    )
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    # Critical: enable performance logging for CDP network capture
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    try:
        svc = Service(ChromeDriverManager().install())
    except Exception:
        svc = Service('/usr/bin/chromedriver')

    driver = webdriver.Chrome(service=svc, options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    driver.set_page_load_timeout(60)
    return driver


# ─── CDP Network Capture ────────────────────────────────────────
def capture_m3u8_from_network(driver):
    """
    Parse Chrome DevTools Protocol performance logs.
    This catches EVERY network request the browser makes,
    including m3u8 URLs loaded by HLS.js / video.js players.
    """
    m3u8_urls = set()
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            try:
                msg = json.loads(entry['message'])
                inner = msg.get('message', {})
                method = inner.get('method', '')
                params = inner.get('params', {})

                # Capture request URLs
                if method in (
                    'Network.requestWillBeSent',
                    'Network.responseReceived',
                    'Network.loadingFinished'
                ):
                    url = ''
                    if method == 'Network.requestWillBeSent':
                        url = params.get('request', {}).get('url', '')
                    elif method == 'Network.responseReceived':
                        url = params.get('response', {}).get('url', '')

                    if '.m3u8' in url.lower():
                        m3u8_urls.add(url)
                        log.info(f"  🎯 CDP captured m3u8: {url}")

            except (json.JSONDecodeError, KeyError):
                continue
    except Exception as e:
        log.debug(f"  CDP log error: {e}")

    return list(m3u8_urls)


# ─── Page Source Extraction ─────────────────────────────────────
def extract_m3u8_from_source(driver):
    """Extract m3u8 URLs from page source + JS execution."""
    urls = set()
    pattern = re.compile(r'https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*', re.I)

    # 1) Raw page source
    try:
        src = driver.page_source
        for m in pattern.findall(src):
            urls.add(m.rstrip('",;\')}]\\'))
    except Exception:
        pass

    # 2) JavaScript: probe video players
    try:
        js_urls = driver.execute_script("""
            var results = [];
            // <video> elements
            document.querySelectorAll('video').forEach(function(v) {
                if (v.src && v.src.indexOf('.m3u8') !== -1) results.push(v.src);
                v.querySelectorAll('source').forEach(function(s) {
                    if (s.src && s.src.indexOf('.m3u8') !== -1) results.push(s.src);
                });
            });
            // HLS.js instances
            if (typeof Hls !== 'undefined' && Hls.isSupported()) {
                try {
                    document.querySelectorAll('video').forEach(function(v) {
                        if (v._hls && v._hls.url) results.push(v._hls.url);
                        if (v.hlsPlayer && v.hlsPlayer.url) results.push(v.hlsPlayer.url);
                    });
                } catch(e) {}
            }
            // video.js
            try {
                if (typeof videojs !== 'undefined') {
                    videojs.getAllPlayers().forEach(function(p) {
                        var s = p.currentSrc();
                        if (s && s.indexOf('.m3u8') !== -1) results.push(s);
                    });
                }
            } catch(e) {}
            // jwplayer
            try {
                if (typeof jwplayer !== 'undefined') {
                    var p = jwplayer();
                    if (p.getPlaylistItem) {
                        var item = p.getPlaylistItem();
                        if (item.file) results.push(item.file);
                        if (item.sources) item.sources.forEach(function(s){
                            if(s.file) results.push(s.file);
                        });
                    }
                }
            } catch(e) {}
            // flowplayer
            try {
                if (typeof flowplayer !== 'undefined') {
                    var fp = flowplayer();
                    if (fp.video && fp.video.src) results.push(fp.video.src);
                }
            } catch(e) {}
            // Global variables scan
            try {
                var keys = ['streamUrl','videoUrl','hlsUrl','m3u8Url','src','file','source'];
                keys.forEach(function(k) {
                    if (typeof window[k] === 'string' && window[k].indexOf('.m3u8') !== -1)
                        results.push(window[k]);
                });
            } catch(e) {}
            // Scan all script tags
            document.querySelectorAll('script').forEach(function(s) {
                if (s.textContent) {
                    var m = s.textContent.match(/https?:\\/\\/[^\\s'"<>]+\\.m3u8[^\\s'"<>]*/gi);
                    if (m) results = results.concat(m);
                }
            });
            return results;
        """)
        if js_urls:
            for u in js_urls:
                if '.m3u8' in u:
                    urls.add(u)
    except Exception as e:
        log.debug(f"  JS extraction error: {e}")

    return list(urls)


# ─── Handle Link Shortener Redirects ────────────────────────────
def handle_shortener_page(driver, wait):
    """
    Handle maskr.blog / similar link shortener pages.
    These typically have:
      - A countdown timer (5-15 seconds)
      - A "Continue" / "Get Link" button that appears after timer
      - Sometimes intermediate ad pages
    Returns True if we navigated somewhere new.
    """
    original_url = driver.current_url
    log.info(f"  Handling shortener page: {original_url}")

    # Wait for countdown to finish and button to appear
    button_selectors = [
        "a.btn-primary", "a.btn-success", "a.btn-danger",
        "button.btn-primary", "button.btn-success",
        "a.get-link", "a.continue", "a#continue",
        "a.redirect", "a.go-link", "a.skip",
        "#btn-main", "#get-link", "#continue-btn",
        "a[href*='get']", "a[href*='go']", "a[href*='continue']",
        ".btn-redirect", ".btn-get",
        "a.btn", "button.btn",
        # Generic fallback: any visible big button/link
        "a[role='button']", "button[role='button']",
    ]

    # Phase 1: Wait for countdown (up to 20 seconds)
    log.info("  Waiting for countdown / button to appear...")
    clicked = False
    for attempt in range(20):
        time.sleep(1)
        for selector in button_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed() and el.is_enabled():
                        text = el.text.strip().lower()
                        href = el.get_attribute('href') or ''
                        # Skip ad/social buttons
                        if any(skip in text for skip in [
                            'facebook', 'twitter', 'share', 'login', 'sign'
                        ]):
                            continue
                        if any(skip in href for skip in [
                            'facebook.com', 'twitter.com', 'instagram.com'
                        ]):
                            continue

                        log.info(f"  Clicking button: '{el.text.strip()}' -> {href}")
                        try:
                            el.click()
                            clicked = True
                        except ElementClickInterceptedException:
                            driver.execute_script("arguments[0].click();", el)
                            clicked = True
                        break
            except (StaleElementReferenceException, NoSuchElementException):
                continue
        if clicked:
            break

    if not clicked:
        log.info("  No button found, waiting for auto-redirect...")

    # Phase 2: Wait for URL to change (redirect)
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.current_url != original_url
        )
        log.info(f"  Redirected to: {driver.current_url}")
        return True
    except TimeoutException:
        log.info(f"  URL didn't change. Current: {driver.current_url}")
        # Check if new tabs opened
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            log.info(f"  Switched to new tab: {driver.current_url}")
            return True
        return False


# ─── Main Scraping Flow ─────────────────────────────────────────
def scrape():
    driver = create_driver()
    wait = WebDriverWait(driver, PAGE_WAIT)
    all_m3u8 = []

    try:
        # ── Step 1: Load source page, extract short links ──
        log.info(f"Loading source: {SOURCE_URL}")
        driver.get(SOURCE_URL)
        time.sleep(5)

        # Enable CDP network monitoring on this tab
        driver.execute_cdp_cmd('Network.enable', {})

        page_src = driver.page_source
        soup = BeautifulSoup(page_src, 'lxml')

        # Collect ALL href links
        short_links = []
        all_hrefs = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = urljoin(SOURCE_URL, href)
            if href.startswith('http'):
                all_hrefs.append(href)
                domain = urlparse(href).netloc.lower().replace('www.', '')
                # Detect short link domains
                if any(d in domain for d in [
                    'maskr.blog', 'shrinkme', 'shrinke', 'shortingly',
                    'linkshortify', 'bit.ly', 'tinyurl', 'cutt.ly',
                    'short.ink', 'tnshort', 'linkpays', 'earnl',
                    'adrinolinks', 'mdiskshortner', 'droplink',
                    'gplinks', 'tnlink', 'za.gl', 'exe.io'
                ]):
                    short_links.append(href)

        # Also check iframes on source page
        iframe_srcs = []
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if src.startswith('//'):
                src = 'https:' + src
            iframe_srcs.append(src)
            log.info(f"  Source iframe: {src}")

        # Check if m3u8 is directly on source page
        direct = extract_m3u8_from_source(driver)
        all_m3u8.extend(direct)
        net_m3u8 = capture_m3u8_from_network(driver)
        all_m3u8.extend(net_m3u8)

        log.info(f"Found {len(short_links)} short links, {len(iframe_srcs)} iframes")
        for sl in short_links:
            log.info(f"  Short: {sl}")

        # ── Step 2: Process iframe embeds from source page ──
        for iframe_url in iframe_srcs:
            if not iframe_url.startswith('http'):
                continue
            log.info(f"\nProcessing source iframe: {iframe_url}")
            try:
                driver.execute_script(f"window.open('{iframe_url}', '_blank');")
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(5)
                driver.execute_cdp_cmd('Network.enable', {})
                time.sleep(PLAYER_WAIT)

                all_m3u8.extend(extract_m3u8_from_source(driver))
                all_m3u8.extend(capture_m3u8_from_network(driver))

                driver.close()
                driver.switch_to.window(driver.window_handles[0])
            except Exception as e:
                log.warning(f"  Iframe error: {e}")
                try:
                    if len(driver.window_handles) > 1:
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass

        # ── Step 3: Process short links ──
        for short_url in short_links:
            log.info(f"\n{'='*50}")
            log.info(f"Processing short link: {short_url}")

            try:
                # Open short link in new tab
                driver.execute_script(f"window.open('{short_url}', '_blank');")
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(3)
                driver.execute_cdp_cmd('Network.enable', {})

                # Handle up to 3 redirect hops (shortener chains)
                for hop in range(4):
                    current = driver.current_url
                    log.info(f"  Hop {hop+1}: {current}")

                    # Check if we already landed on a video page
                    m3u8 = extract_m3u8_from_source(driver)
                    net = capture_m3u8_from_network(driver)
                    all_m3u8.extend(m3u8)
                    all_m3u8.extend(net)
                    if m3u8 or net:
                        log.info(f"  ✅ Found m3u8 at hop {hop+1}!")
                        break

                    # Check if this is a shortener page
                    domain = urlparse(current).netloc.lower()
                    is_shortener = any(d in domain for d in [
                        'maskr', 'shrink', 'short', 'link', 'bit.ly',
                        'tinyurl', 'cutt', 'tnlink', 'adrino', 'gplink',
                        'droplink', 'exe.io', 'za.gl'
                    ])

                    if is_shortener:
                        navigated = handle_shortener_page(driver, wait)
                        if not navigated:
                            log.info("  No further redirect, checking page...")
                            time.sleep(PLAYER_WAIT)
                            all_m3u8.extend(extract_m3u8_from_source(driver))
                            all_m3u8.extend(capture_m3u8_from_network(driver))
                            break
                        time.sleep(3)
                    else:
                        # Not a shortener - this should be the destination
                        log.info(f"  Destination page: {current}")
                        # Wait for video player to load
                        log.info(f"  Waiting {PLAYER_WAIT}s for player...")
                        time.sleep(PLAYER_WAIT)

                        all_m3u8.extend(extract_m3u8_from_source(driver))
                        all_m3u8.extend(capture_m3u8_from_network(driver))

                        # Try clicking play buttons
                        try:
                            play_btns = driver.find_elements(
                                By.CSS_SELECTOR,
                                ".vjs-big-play-button, .play-btn, "
                                "[class*='play'], button[aria-label*='Play']"
                            )
                            for btn in play_btns:
                                if btn.is_displayed():
                                    btn.click()
                                    log.info("  Clicked play button")
                                    time.sleep(10)
                                    all_m3u8.extend(capture_m3u8_from_network(driver))
                                    all_m3u8.extend(extract_m3u8_from_source(driver))
                                    break
                        except Exception:
                            pass

                        break

                # Handle any extra tabs that opened (ad popups)
                while len(driver.window_handles) > 2:
                    driver.switch_to.window(driver.window_handles[-1])
                    log.info(f"  Checking popup tab: {driver.current_url}")
                    time.sleep(3)
                    all_m3u8.extend(extract_m3u8_from_source(driver))
                    all_m3u8.extend(capture_m3u8_from_network(driver))
                    driver.close()

                # Close the short link tab
                driver.switch_to.window(driver.window_handles[-1])
                if driver.current_url != SOURCE_URL:
                    driver.close()
                driver.switch_to.window(driver.window_handles[0])

            except NoSuchWindowException:
                log.warning("  Window closed unexpectedly, recovering...")
                driver.switch_to.window(driver.window_handles[0])
            except Exception as e:
                log.error(f"  Short link error: {e}")
                try:
                    driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass

        # ── Step 4: If still nothing, try direct Selenium on source ──
        if not all_m3u8:
            log.info("\nNo m3u8 found yet. Deep-scanning source page...")
            driver.switch_to.window(driver.window_handles[0])
            driver.get(SOURCE_URL)
            time.sleep(5)
            driver.execute_cdp_cmd('Network.enable', {})

            # Click any visible links/buttons that might trigger the player
            try:
                clickable = driver.find_elements(
                    By.CSS_SELECTOR,
                    "a[href*='maskr'], a[href*='blog'], "
                    "a[href*='live'], a[href*='watch'], "
                    ".play-btn, [class*='play'], [class*='stream']"
                )
                for el in clickable[:3]:
                    if el.is_displayed():
                        log.info(f"  Clicking: {el.get_attribute('href')}")
                        el.click()
                        time.sleep(PLAYER_WAIT)
                        all_m3u8.extend(extract_m3u8_from_source(driver))
                        all_m3u8.extend(capture_m3u8_from_network(driver))
                        if all_m3u8:
                            break
                        driver.back()
                        time.sleep(3)
            except Exception as e:
                log.debug(f"  Click scan error: {e}")

    except Exception as e:
        log.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    # ── De-duplicate ──
    seen = set()
    unique = []
    for url in all_m3u8:
        url = url.strip().rstrip('",;\')}]\\')
        if '.m3u8' in url and url not in seen:
            seen.add(url)
            unique.append(url)

    return unique


# ─── Output ──────────────────────────────────────────────────────
def write_output(m3u8_links):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    # Load previous as fallback
    prev = []
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE) as f:
                prev = [s['url'] for s in json.load(f).get('streams', [])]
        except Exception:
            pass

    final = m3u8_links if m3u8_links else prev
    status = "live" if m3u8_links else "cached"

    with open(PLAYLIST_FILE, 'w') as f:
        f.write('#EXTM3U\n')
        f.write(f'# Updated: {ts} [{status}]\n')
        f.write(f'# Source: {SOURCE_URL}\n')
        f.write(f'# Streams: {len(final)}\n\n')
        for i, url in enumerate(final, 1):
            name = "Bigg Boss Live"
            if 'hindi' in url.lower():
                name += " Hindi"
            elif 'tamil' in url.lower():
                name += " Tamil"
            elif 'telugu' in url.lower():
                name += " Telugu"
            name += f" #{i}"
            f.write(f'#EXTINF:-1 tvg-name="{name}" group-title="Bigg Boss",{name}\n')
            f.write(f'{url}\n\n')

    with open(JSON_FILE, 'w') as f:
        json.dump({
            'updated': ts,
            'status': status,
            'source': SOURCE_URL,
            'total_streams': len(final),
            'streams': [{'id': i, 'url': u, 'name': f'Stream {i}'}
                        for i, u in enumerate(final, 1)]
        }, f, indent=2)

    log.info(f"\nWrote {len(final)} streams ({status}) to {PLAYLIST_FILE}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("=" * 60)
    log.info("Auto Playlist Scraper v2 (CDP Network Interception)")
    log.info("=" * 60)

    links = scrape()

    log.info(f"\n{'='*60}")
    log.info(f"RESULTS: {len(links)} m3u8 streams found")
    for i, u in enumerate(links, 1):
        log.info(f"  {i}. {u}")
    log.info(f"{'='*60}")

    write_output(links)
    log.info("Done!")
