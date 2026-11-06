#!/usr/bin/env python3
"""
Auto Playlist Scraper v3
- Resolves short links with requests (NO browser on ad pages)
- Extracts redirect targets from HTML via regex
- Only opens the FINAL video page in Selenium
- Blocks ads via CDP to prevent hangs
- Captures m3u8 via CDP network interception
"""

import re
import os
import sys
import json
import time
import logging
import requests
import cloudscraper
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException,
    NoSuchWindowException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager

# ─── Config ──────────────────────────────────────────────────────
SOURCE_URL = "https://www.tamiltvserial.com/all-bigg-boss-live-24-7/"
PLAYLIST_FILE = "playlist.m3u"
JSON_FILE = "playlist.json"
LOG_FILE = "log.txt"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    ]
)
log = logging.getLogger(__name__)

SHORTENER_DOMAINS = [
    'maskr.blog', 'shrinkme', 'shrinke', 'shortingly', 'linkshortify',
    'bit.ly', 'tinyurl', 'cutt.ly', 'short.ink', 'tnshort', 'linkpays',
    'earnl', 'adrinolinks', 'mdiskshortner', 'droplink', 'gplinks',
    'tnlink', 'za.gl', 'exe.io', 'ouo.io', 'ouo.press', 'adf.ly',
    'bc.vc', 'shorte.st', 'linkvertise', 'boost.ink', 'shrinkforearn',
    'indianshortner', 'dulink', 'krownlinks', 'gyanilinks', 'mplaylink',
    'rslinks', 'techymozo', 'atglinks', 'inshortner', 'linkbnao',
    'htpmovies', 'cinevood', 'skymovieshd', 'filmywap', 'vegamovies',
]

# Patterns to find redirect URLs inside shortener HTML pages
REDIRECT_PATTERNS = [
    # meta refresh
    re.compile(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\d+;\s*url=([^"\'>\s]+)', re.I),
    # window.location assignments
    re.compile(r'window\.location\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.location\.replace\s*\(\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.open\s*\(\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'location\.href\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'location\.replace\s*\(\s*["\']([^"\']+)["\']', re.I),
    # setTimeout redirects
    re.compile(r'setTimeout\s*\([^,]+,\s*\d+\s*\)\s*;?\s*window\.location\s*=\s*["\']([^"\']+)["\']', re.I),
    # data attributes
    re.compile(r'data-href=["\']([^"\']+)["\']', re.I),
    re.compile(r'data-url=["\']([^"\']+)["\']', re.I),
    # countdown link patterns
    re.compile(r'id=["\'](?:countdown|timer|redirect|continue|skip)["\'][^>]+href=["\']([^"\']+)["\']', re.I),
    re.compile(r'class=["\'][^"\']*(?:countdown|timer|redirect|continue|skip)[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', re.I),
    # generic href in buttons/links with redirect-like text
    re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(?:[^<]*(?:continue|skip|proceed|get\s*link|click\s*here|go)[^<]*)</a>', re.I),
]

M3U8_RE = re.compile(r'https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*', re.I)


# ─── Helpers ─────────────────────────────────────────────────────
def is_shortener(url):
    domain = urlparse(url).netloc.lower().replace('www.', '')
    return any(s in domain for s in SHORTENER_DOMAINS)


def get_session():
    s = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    return s


def extract_redirect_url(html, base_url):
    """Extract the redirect target from a shortener page's HTML."""
    for pattern in REDIRECT_PATTERNS:
        matches = pattern.findall(html)
        for m in matches:
            m = m.strip()
            if m.startswith('//'):
                m = 'https:' + m
            elif m.startswith('/'):
                m = urljoin(base_url, m)
            if m.startswith('http') and m != base_url and not is_shortener(m):
                log.info(f"    Extracted redirect: {m}")
                return m
            elif m.startswith('http') and m != base_url:
                log.info(f"    Extracted redirect (still shortener): {m}")
                return m
    return None


def resolve_short_link(url, session, max_hops=8):
    """
    Resolve a short link to its final destination using ONLY requests.
    Never opens the shortener in a browser.
    """
    visited = set()
    current = url

    for hop in range(max_hops):
        if current in visited:
            log.info(f"    Loop detected at hop {hop+1}, stopping")
            break
        visited.add(current)

        domain = urlparse(current).netloc.lower()
        log.info(f"    Hop {hop+1}: {current} [{domain}]")

        if not is_shortener(current):
            log.info(f"    ✅ Reached destination: {current}")
            return current

        try:
            # Try following server-side redirects first
            resp = session.get(current, timeout=15, allow_redirects=True)
            current = resp.url
            log.info(f"    After redirects: {current}")

            if not is_shortener(current):
                log.info(f"    ✅ Reached destination via redirect: {current}")
                return current

            # Still on a shortener — extract JS redirect from HTML
            html = resp.text
            redirect = extract_redirect_url(html, current)
            if redirect and redirect not in visited:
                current = redirect
                continue
            else:
                # Try BeautifulSoup for button/link extraction
                soup = BeautifulSoup(html, 'lxml')
                found = False
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = urljoin(current, href)
                    text = a.get_text(strip=True).lower()
                    if href.startswith('http') and href not in visited:
                        if any(kw in text for kw in ['continue', 'skip', 'get link', 'go', 'proceed', 'click']):
                            log.info(f"    Found button link: {href}")
                            current = href
                            found = True
                            break
                if not found:
                    log.info(f"    ⚠️ Cannot resolve further at: {current}")
                    return current

        except requests.exceptions.Timeout:
            log.warning(f"    Timeout at hop {hop+1}")
            return current
        except Exception as e:
            log.warning(f"    Error at hop {hop+1}: {e}")
            return current

        time.sleep(1)

    return current


# ─── Browser ─────────────────────────────────────────────────────
def create_driver():
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
    opts.add_argument('--page-load-strategy=eager')  # KEY: don't wait for ads/images
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    )
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    try:
        svc = Service(ChromeDriverManager().install())
    except Exception:
        svc = Service('/usr/bin/chromedriver')

    driver = webdriver.Chrome(service=svc, options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    driver.set_page_load_timeout(30)  # Shorter timeout

    # Block ads/trackers/images via CDP to prevent hangs
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.execute_cdp_cmd('Network.setBlockedURLs', {
            'urls': [
                '*doubleclick.net*', '*googlesyndication*', '*googleads*',
                '*facebook.com/tr*', '*analytics*', '*adsystem*',
                '*popads*', '*popcash*', '*propellerads*', '*exoclick*',
                '*juicyads*', '*trafficjunky*', '*adnxs*', '*taboola*',
                '*outbrain*', '*mgid*', '*revcontent*', '*content.ad*',
                '*pushengage*', '*onesignal*', '*pushwoosh*',
                '*.gif', '*.png', '*.jpg', '*.jpeg', '*.webp', '*.svg',
                '*.woff', '*.woff2', '*.ttf', '*.eot',
            ]
        })
    except Exception as e:
        log.debug(f"CDP block setup: {e}")

    return driver


def capture_m3u8_cdp(driver):
    """Extract m3u8 URLs from CDP performance logs."""
    urls = set()
    try:
        for entry in driver.get_log('performance'):
            try:
                msg = json.loads(entry['message'])
                inner = msg.get('message', {})
                method = inner.get('method', '')
                params = inner.get('params', {})
                url = ''
                if method == 'Network.requestWillBeSent':
                    url = params.get('request', {}).get('url', '')
                elif method == 'Network.responseReceived':
                    url = params.get('response', {}).get('url', '')
                if '.m3u8' in url.lower():
                    urls.add(url)
                    log.info(f"  🎯 CDP: {url}")
            except (json.JSONDecodeError, KeyError):
                continue
    except Exception:
        pass
    return list(urls)


def extract_m3u8_js(driver):
    """Extract m3u8 URLs from page source + JS player probing."""
    urls = set()
    # Page source
    try:
        for m in M3U8_RE.findall(driver.page_source):
            urls.add(m.rstrip('",;\')}]\\'))
    except Exception:
        pass
    # JS probing
    try:
        js = driver.execute_script("""
            var r = [];
            document.querySelectorAll('video').forEach(function(v) {
                if (v.src && v.src.indexOf('.m3u8')!==-1) r.push(v.src);
                v.querySelectorAll('source').forEach(function(s) {
                    if (s.src && s.src.indexOf('.m3u8')!==-1) r.push(s.src);
                });
            });
            try { if(typeof Hls!=='undefined') document.querySelectorAll('video').forEach(function(v){
                if(v._hls&&v._hls.url) r.push(v._hls.url);
            }); } catch(e){}
            try { if(typeof videojs!=='undefined') videojs.getAllPlayers().forEach(function(p){
                var s=p.currentSrc(); if(s&&s.indexOf('.m3u8')!==-1) r.push(s);
            }); } catch(e){}
            try { if(typeof jwplayer!=='undefined'){var p=jwplayer();
                if(p.getPlaylistItem){var i=p.getPlaylistItem();
                if(i.file) r.push(i.file);
                if(i.sources) i.sources.forEach(function(s){if(s.file)r.push(s.file);});
            }}} catch(e){}
            document.querySelectorAll('script').forEach(function(s){
                if(s.textContent){var m=s.textContent.match(/https?:\\/\\/[^\\s'"<>]+\\.m3u8[^\\s'"<>]*/gi);
                if(m) r=r.concat(m);}
            });
            return r;
        """)
        if js:
            for u in js:
                if '.m3u8' in u:
                    urls.add(u)
    except Exception:
        pass
    return list(urls)


def load_video_page(driver, url):
    """Load a video page and capture m3u8 streams."""
    m3u8 = []
    log.info(f"  Loading video page: {url}")
    try:
        driver.get(url)
    except TimeoutException:
        log.info("  Page load timeout (eager), continuing anyway...")
    except WebDriverException as e:
        log.warning(f"  Page load error: {e}")
        return m3u8

    # Wait for player to initialize
    for i in range(4):
        time.sleep(5)
        m3u8.extend(capture_m3u8_cdp(driver))
        m3u8.extend(extract_m3u8_js(driver))
        if m3u8:
            break

    # Try clicking play buttons
    if not m3u8:
        try:
            for sel in [".vjs-big-play-button", ".play-btn", "[class*='play']",
                        "button[aria-label*='Play']", ".jw-icon-display",
                        ".fp-ui", "#play-btn", ".play-button"]:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    if btn.is_displayed():
                        try:
                            btn.click()
                        except ElementClickInterceptedException:
                            driver.execute_script("arguments[0].click();", btn)
                        log.info(f"  Clicked play: {sel}")
                        time.sleep(10)
                        m3u8.extend(capture_m3u8_cdp(driver))
                        m3u8.extend(extract_m3u8_js(driver))
                        if m3u8:
                            return list(set(m3u8))
                        break
        except Exception:
            pass

    # Check iframes on the video page
    if not m3u8:
        try:
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                src = iframe.get_attribute('src') or ''
                if src and 'http' in src:
                    log.info(f"  Video page iframe: {src}")
                    if '.m3u8' in src:
                        m3u8.append(src)
                    else:
                        # Load iframe src directly
                        try:
                            driver.execute_script(f"window.open('{src}','_blank');")
                            driver.switch_to.window(driver.window_handles[-1])
                            time.sleep(10)
                            m3u8.extend(capture_m3u8_cdp(driver))
                            m3u8.extend(extract_m3u8_js(driver))
                            driver.close()
                            driver.switch_to.window(driver.window_handles[0])
                        except Exception:
                            try:
                                driver.switch_to.window(driver.window_handles[0])
                            except Exception:
                                pass
                if m3u8:
                    break
        except Exception:
            pass

    return list(set(m3u8))


# ─── Main ────────────────────────────────────────────────────────
def scrape():
    session = get_session()
    all_m3u8 = []

    # ── Phase 1: Get links from source page ──
    log.info(f"Fetching source: {SOURCE_URL}")
    try:
        resp = session.get(SOURCE_URL, timeout=20)
        html = resp.text
        soup = BeautifulSoup(html, 'lxml')

        short_links = []
        iframe_srcs = []
        direct_m3u8 = []

        # Collect short links
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = urljoin(SOURCE_URL, href)
            if href.startswith('http') and is_shortener(href):
                short_links.append(href)

        # Collect iframes
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if src.startswith('//'):
                src = 'https:' + src
            if src.startswith('http'):
                iframe_srcs.append(src)

        # Check for m3u8 directly
        direct_m3u8 = M3U8_RE.findall(html)
        direct_m3u8 = [u.rstrip('",;\')}]\\') for u in direct_m3u8]

        log.info(f"  Short links: {len(short_links)}")
        log.info(f"  Iframes: {len(iframe_srcs)}")
        log.info(f"  Direct m3u8: {len(direct_m3u8)}")
        all_m3u8.extend(direct_m3u8)

    except Exception as e:
        log.error(f"Source fetch failed: {e}")
        return []

    # ── Phase 2: Resolve short links with requests (NO browser) ──
    destinations = []
    for sl in short_links:
        log.info(f"\nResolving: {sl}")
        dest = resolve_short_link(sl, session)
        if dest and dest != sl and not is_shortener(dest):
            destinations.append(dest)
            log.info(f"  ✅ Destination: {dest}")
        elif dest:
            log.info(f"  ⚠️ Partial resolve: {dest}")
            destinations.append(dest)

    # ── Phase 3: Open destinations in browser to capture m3u8 ──
    driver = None
    pages_to_check = iframe_srcs + destinations

    if pages_to_check:
        log.info(f"\n{'='*50}")
        log.info(f"Opening {len(pages_to_check)} pages in browser for m3u8 capture")
        try:
            driver = create_driver()
        except Exception as e:
            log.error(f"Failed to create driver: {e}")
            return list(set(all_m3u8))

        for page_url in pages_to_check:
            if all_m3u8:
                # Already found streams, but keep going to find more
                pass
            log.info(f"\n{'─'*40}")
            found = load_video_page(driver, page_url)
            all_m3u8.extend(found)
            if found:
                log.info(f"  ✅ Found {len(found)} m3u8 from {page_url}")

        # ── Phase 4: If nothing found, try source page in browser ──
        if not all_m3u8:
            log.info("\nFallback: loading source page in browser...")
            found = load_video_page(driver, SOURCE_URL)
            all_m3u8.extend(found)

        try:
            driver.quit()
        except Exception:
            pass

    return list(set(all_m3u8))


# ─── Output ──────────────────────────────────────────────────────
def write_output(m3u8_links):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

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
            for lang in ['hindi', 'tamil', 'telugu', 'malayalam', 'kannada', 'bengali']:
                if lang in url.lower():
                    name += f" {lang.title()}"
                    break
            name += f" #{i}"
            f.write(f'#EXTINF:-1 tvg-name="{name}" group-title="Bigg Boss",{name}\n')
            f.write(f'{url}\n\n')

    with open(JSON_FILE, 'w') as f:
        json.dump({
            'updated': ts, 'status': status, 'source': SOURCE_URL,
            'total_streams': len(final),
            'streams': [{'id': i, 'url': u} for i, u in enumerate(final, 1)]
        }, f, indent=2)

    log.info(f"\n✅ Wrote {len(final)} streams ({status})")


if __name__ == '__main__':
    log.info("=" * 60)
    log.info("Auto Playlist Scraper v3")
    log.info("=" * 60)
    links = scrape()
    log.info(f"\nRESULTS: {len(links)} streams")
    for i, u in enumerate(links, 1):
        log.info(f"  {i}. {u}")
    write_output(links)
    log.info("Done!")
