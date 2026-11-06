#!/usr/bin/env python3
"""
Auto Playlist Scraper v4
- page-load-strategy=none (never hangs)
- allow_redirects=False (correct shortener resolution)
- CDP ad blocking before page load
- Smart iframe filtering (skip ad sync frames)
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
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
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
]

# Ad/tracking domains to block in CDP
AD_DOMAINS = [
    '*doubleclick*', '*googlesyndication*', '*googleads*', '*google-analytics*',
    '*facebook.com/tr*', '*facebook.net*', '*analytics*', '*adsystem*',
    '*popads*', '*popcash*', '*propellerads*', '*exoclick*', '*juicyads*',
    '*trafficjunky*', '*adnxs*', '*taboola*', '*outbrain*', '*mgid*',
    '*revcontent*', '*content.ad*', '*pushengage*', '*onesignal*',
    '*sync.*', '*prebid*', '*omni-dex*', '*eskimi*', '*missena*',
    '*ck-ie*', '*receptivity*', '*admatic*', '*smaato*', '*kueezrtb*',
    '*criteo*', '*rubicon*', '*pubmatic*', '*openx*', '*indexexchange*',
    '*amazon-adsystem*', '*scorecardresearch*', '*quantserve*',
    '*bluekai*', '*agkn*', '*bidswitch*', '*casalemedia*', '*contextweb*',
    '*smartadserver*', '*teads*', '*spotxchange*', '*adsrvr*',
    '*adform*', '*stickyadstv*', '*simpli.fi*', '*zemanta*',
    '*moatads*', '*serving-sys*', '*advertising.com*', '*aol.com*',
    '*yieldmo*', '*sharethrough*', '*triplelift*', '*gumgum*',
    '*3lift.com*', '*adsymptotic*', '*rlcdn*', '*pippio*',
    '*crwdcntrl*', '*demdex*', '*everesttech*', '*mathtag*',
    '*tapad*', '*turn.com*', '*undertone*', '*vindicosuite*',
    '*mixpo*', '*flashtalking*', '*media.net*', '*dotomi*',
]

# Iframe URL patterns that are definitely NOT video players
AD_IFRAME_PATTERNS = [
    'sync', 'prebid', 'pixel', 'tracking', 'analytics', 'ads',
    'doubleclick', 'googlesyndication', 'facebook', 'criteo',
    'taboola', 'outbrain', 'mgid', 'revcontent', 'omni-dex',
    'eskimi', 'missena', 'ck-ie', 'receptivity', 'admatic',
    'smaato', 'kueezrtb', 'rubicon', 'pubmatic', 'openx',
    'cookie', 'gdpr', 'consent', 'adnxs', 'bidswitch',
    'casalemedia', 'contextweb', 'smartadserver', 'teads',
    'spotxchange', 'adsrvr', 'adform', 'stickyadstv',
]

# Iframe URL patterns that ARE likely video players
VIDEO_IFRAME_PATTERNS = [
    'player', 'embed', 'video', 'stream', 'play', 'watch',
    'youtube', 'vimeo', 'dailymotion', 'jwplayer', 'flowplayer',
    'videojs', 'hls', 'm3u8', 'iframe.php', 'embed.php',
    'zoloj', 'streamtape', 'mixdrop', 'dood', 'voe',
    'filemoon', 'vidhide', 'streamwish', 'vidmoly',
]

M3U8_RE = re.compile(r'https?://[^\s\'"<>\\]+\.m3u8[^\s\'"<>\\]*', re.I)

REDIRECT_PATTERNS = [
    re.compile(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\d+;\s*url=([^"\'>\s]+)', re.I),
    re.compile(r'window\.location\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.location\.replace\s*\(\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.open\s*\(\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'location\.href\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'location\.replace\s*\(\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'data-href=["\']([^"\']+)["\']', re.I),
    re.compile(r'data-url=["\']([^"\']+)["\']', re.I),
    re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(?:[^<]*(?:continue|skip|proceed|get\s*link|click|go\s*to)[^<]*)</a>', re.I),
    re.compile(r'var\s+(?:url|link|href|redirect|target|destination)\s*=\s*["\']([^"\']+)["\']', re.I),
    re.compile(r'href=["\']([^"\']+)["\'][^>]*id=["\'](?:btn|continue|skip|get|go|redirect|download)["\']', re.I),
    re.compile(r'id=["\'](?:btn|continue|skip|get|go|redirect|download)["\'][^>]*href=["\']([^"\']+)["\']', re.I),
]


# ─── Helpers ─────────────────────────────────────────────────────
def is_shortener(url):
    domain = urlparse(url).netloc.lower().replace('www.', '')
    return any(s in domain for s in SHORTENER_DOMAINS)


def is_ad_iframe(url):
    url_lower = url.lower()
    return any(p in url_lower for p in AD_IFRAME_PATTERNS)


def is_video_iframe(url):
    url_lower = url.lower()
    return any(p in url_lower for p in VIDEO_IFRAME_PATTERNS)


def clean_url(url):
    """Strip Google tracking wrappers and clean up URLs."""
    if not url:
        return url
    # Unwrap Google redirect URLs
    if 'google.com/url' in url or 'google.com/imgres' in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'url' in params:
            url = params['url'][0]
            log.info(f"    Unwrapped Google redirect → {url}")
    # Remove tracking params
    url = url.split('#')[0]
    return url.strip()


def get_session():
    s = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    s.headers.update({
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/131.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    return s


def extract_redirect_url(html, base_url):
    """Extract redirect target from shortener page HTML."""
    for pattern in REDIRECT_PATTERNS:
        for m in pattern.findall(html):
            m = clean_url(m)
            if m.startswith('//'):
                m = 'https:' + m
            elif m.startswith('/'):
                m = urljoin(base_url, m)
            if m.startswith('http') and m != base_url:
                return m
    return None


def resolve_short_link(url, session, max_hops=10):
    """
    Resolve shortener chain using requests ONLY.
    Uses allow_redirects=False to avoid following wrong redirects.
    """
    visited = set()
    current = clean_url(url)

    for hop in range(max_hops):
        if current in visited:
            log.info(f"    Loop at hop {hop+1}")
            break
        visited.add(current)

        current = clean_url(current)
        domain = urlparse(current).netloc.lower()
        log.info(f"    Hop {hop+1}: {current}")

        if not is_shortener(current):
            log.info(f"    ✅ Destination: {current}")
            return current

        try:
            # DO NOT follow redirects automatically
            resp = session.get(current, timeout=15, allow_redirects=False)

            # Handle server-side redirect
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location', '')
                location = clean_url(location)
                if location:
                    if location.startswith('/'):
                        location = urljoin(current, location)
                    log.info(f"    302 → {location}")
                    current = location
                    continue

            # Parse HTML for JS-based redirect
            if resp.status_code == 200:
                html = resp.text
                redirect = extract_redirect_url(html, current)
                if redirect:
                    redirect = clean_url(redirect)
                    log.info(f"    JS redirect → {redirect}")
                    current = redirect
                    continue

                # Try BeautifulSoup for button links
                soup = BeautifulSoup(html, 'lxml')
                for a in soup.find_all('a', href=True):
                    href = clean_url(a['href'])
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = urljoin(current, href)
                    text = a.get_text(strip=True).lower()
                    if (href.startswith('http')
                            and href not in visited
                            and any(kw in text for kw in [
                                'continue', 'skip', 'get link',
                                'go', 'proceed', 'click here'
                            ])):
                        log.info(f"    Button → {href}")
                        current = href
                        break
                else:
                    log.info(f"    ⚠️ Stuck at: {current}")
                    return current

        except requests.exceptions.Timeout:
            log.warning(f"    Timeout at hop {hop+1}")
            return current
        except Exception as e:
            log.warning(f"    Error: {e}")
            return current

        time.sleep(0.5)

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
    opts.add_argument('--ignore-certificate-errors')
    # KEY: never wait for page load — prevents ALL timeout hangs
    opts.add_argument('--page-load-strategy=none')
    opts.add_argument('--js-flags=--max-old-space-size=256')
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
    # No page load timeout needed with strategy=none, but set as safety net
    driver.set_page_load_timeout(20)

    # Enable CDP and block ads BEFORE any navigation
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls': AD_DOMAINS})
        log.info(f"  CDP: blocked {len(AD_DOMAINS)} ad URL patterns")
    except Exception as e:
        log.warning(f"  CDP setup error: {e}")

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
    """Extract m3u8 from page source + JS player probing."""
    urls = set()
    try:
        for m in M3U8_RE.findall(driver.page_source):
            urls.add(m.rstrip('",;\')}]\\'))
    except Exception:
        pass
    try:
        js = driver.execute_script("""
            var r = [];
            document.querySelectorAll('video').forEach(function(v) {
                if (v.src && v.src.indexOf('.m3u8')!==-1) r.push(v.src);
                v.querySelectorAll('source').forEach(function(s) {
                    if (s.src && s.src.indexOf('.m3u8')!==-1) r.push(s.src);
                });
            });
            try { if(typeof Hls!=='undefined') document.querySelectorAll('video')
                .forEach(function(v){ if(v._hls&&v._hls.url) r.push(v._hls.url); });
            } catch(e){}
            try { if(typeof videojs!=='undefined') videojs.getAllPlayers()
                .forEach(function(p){ var s=p.currentSrc();
                if(s&&s.indexOf('.m3u8')!==-1) r.push(s); });
            } catch(e){}
            try { if(typeof jwplayer!=='undefined'){ var p=jwplayer();
                if(p.getPlaylistItem){ var i=p.getPlaylistItem();
                if(i.file) r.push(i.file);
                if(i.sources) i.sources.forEach(function(s){
                    if(s.file) r.push(s.file); }); }}
            } catch(e){}
            document.querySelectorAll('script').forEach(function(s){
                if(s.textContent){ var m=s.textContent.match(
                    /https?:\\/\\/[^\\s'"<>]+\\.m3u8[^\\s'"<>]*/gi);
                if(m) r=r.concat(m); }
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


def safe_get(driver, url):
    """Navigate to URL with strategy=none. Never blocks."""
    log.info(f"  Navigating: {url}")
    try:
        driver.get(url)
    except TimeoutException:
        log.info("  Nav timeout, stopping load...")
        try:
            driver.execute_cdp_cmd('Page.stopLoading', {})
        except Exception:
            pass
    except WebDriverException as e:
        log.warning(f"  Nav error: {e}")
        try:
            driver.execute_cdp_cmd('Page.stopLoading', {})
        except Exception:
            pass


def wait_and_collect(driver, seconds=20, interval=3):
    """Wait for player to load, collecting m3u8 URLs periodically."""
    urls = []
    elapsed = 0
    while elapsed < seconds:
        time.sleep(interval)
        elapsed += interval
        urls.extend(capture_m3u8_cdp(driver))
        urls.extend(extract_m3u8_js(driver))
        if urls:
            log.info(f"  Found m3u8 after {elapsed}s")
            break
        log.info(f"  Waiting... ({elapsed}s/{seconds}s)")
    return list(set(urls))


def try_click_play(driver):
    """Try clicking play buttons to trigger HLS.js."""
    selectors = [
        ".vjs-big-play-button", ".play-btn", ".jw-icon-display",
        "[class*='play-button']", "[class*='play-btn']",
        "button[aria-label*='Play']", "#play-btn", ".fp-ui",
        ".mejs-overlay-button", ".plyr__control",
    ]
    for sel in selectors:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in btns:
                if btn.is_displayed():
                    try:
                        btn.click()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", btn)
                    log.info(f"  ▶ Clicked: {sel}")
                    return True
        except Exception:
            continue
    return False


def load_video_page(driver, url):
    """Load a video page and capture m3u8 streams."""
    m3u8 = []
    safe_get(driver, url)

    # Wait for DOM to be ready (manual check since strategy=none)
    for _ in range(10):
        time.sleep(1)
        try:
            ready = driver.execute_script("return document.readyState")
            if ready in ('interactive', 'complete'):
                break
        except Exception:
            pass

    # Collect m3u8
    m3u8.extend(wait_and_collect(driver, seconds=15, interval=3))

    # Try clicking play
    if not m3u8:
        if try_click_play(driver):
            m3u8.extend(wait_and_collect(driver, seconds=15, interval=3))

    # Check iframes (ONLY video-looking ones)
    if not m3u8:
        try:
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                src = iframe.get_attribute('src') or ''
                if not src or 'http' not in src:
                    continue
                if is_ad_iframe(src):
                    log.debug(f"  Skip ad iframe: {src[:80]}")
                    continue
                if is_video_iframe(src) or not is_ad_iframe(src):
                    log.info(f"  Video iframe: {src}")
                    if '.m3u8' in src:
                        m3u8.append(src)
                        break
                    # Open iframe in new tab
                    try:
                        driver.execute_script(
                            f"window.open('{src}','_blank');"
                        )
                        driver.switch_to.window(driver.window_handles[-1])
                        m3u8.extend(wait_and_collect(driver, seconds=12, interval=3))
                        if not m3u8:
                            try_click_play(driver)
                            m3u8.extend(wait_and_collect(driver, seconds=10, interval=3))
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                    except Exception:
                        try:
                            if len(driver.window_handles) > 1:
                                driver.close()
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

    # ── Phase 1: Scrape source page ──
    log.info(f"Fetching source: {SOURCE_URL}")
    try:
        resp = session.get(SOURCE_URL, timeout=20)
        html = resp.text
        soup = BeautifulSoup(html, 'lxml')

        short_links = []
        iframe_srcs = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = urljoin(SOURCE_URL, href)
            if href.startswith('http') and is_shortener(href):
                short_links.append(href)

        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if src.startswith('//'):
                src = 'https:' + src
            if src.startswith('http') and not is_ad_iframe(src):
                iframe_srcs.append(src)

        # Direct m3u8 in source
        for m in M3U8_RE.findall(html):
            all_m3u8.append(m.rstrip('",;\')}]\\'))

        log.info(f"  Short links: {len(short_links)}")
        for sl in short_links:
            log.info(f"    {sl}")
        log.info(f"  Iframes: {len(iframe_srcs)}")
        log.info(f"  Direct m3u8: {len(all_m3u8)}")

    except Exception as e:
        log.error(f"Source fetch failed: {e}")
        return []

    # ── Phase 2: Resolve short links (requests only, NO browser) ──
    destinations = []
    for sl in short_links:
        log.info(f"\nResolving: {sl}")
        dest = resolve_short_link(sl, session)
        dest = clean_url(dest)
        if dest and dest != sl:
            destinations.append(dest)
            log.info(f"  → {dest}")

    # ── Phase 3: Open pages in browser ──
    pages_to_check = []
    # Add non-shortener iframe sources first
    for src in iframe_srcs:
        if not is_shortener(src) and not is_ad_iframe(src):
            pages_to_check.append(src)
    # Add resolved destinations
    for dest in destinations:
        if dest not in pages_to_check:
            pages_to_check.append(dest)

    if not pages_to_check and not all_m3u8:
        pages_to_check.append(SOURCE_URL)

    if pages_to_check:
        log.info(f"\n{'='*50}")
        log.info(f"Opening {len(pages_to_check)} pages in browser")
        driver = None
        try:
            driver = create_driver()
        except Exception as e:
            log.error(f"Driver creation failed: {e}")
            return list(set(all_m3u8))

        for page_url in pages_to_check:
            log.info(f"\n{'─'*40}")
            found = load_video_page(driver, page_url)
            all_m3u8.extend(found)
            if found:
                log.info(f"  ✅ {len(found)} m3u8 from {page_url}")

        # Fallback: source page in browser
        if not all_m3u8:
            log.info("\nFallback: source page in browser...")
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
            for lang in ['hindi', 'tamil', 'telugu', 'malayalam', 'kannada']:
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
    log.info("Auto Playlist Scraper v4")
    log.info("=" * 60)
    links = scrape()
    log.info(f"\nRESULTS: {len(links)} streams")
    for i, u in enumerate(links, 1):
        log.info(f"  {i}. {u}")
    write_output(links)
    log.info("Done!")
