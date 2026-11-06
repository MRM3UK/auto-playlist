#!/usr/bin/env python3
"""
Auto Playlist Scraper
Collects m3u8 stream links from tamiltvserial.com Bigg Boss Live page.
Resolves short links, follows redirects, and extracts .m3u8 URLs.
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

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

import cloudscraper

# ─── Configuration ───────────────────────────────────────────────
SOURCE_URL = "https://www.tamiltvserial.com/all-bigg-boss-live-24-7/"
PLAYLIST_FILE = "playlist.m3u"
JSON_FILE = "playlist.json"
LOG_FILE = "log.txt"
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
SELENIUM_TIMEOUT = 45
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# Known short link domains
SHORT_LINK_DOMAINS = [
    "maskr.blog", "shrinkme.io", "shrinke.me", "shortingly.me",
    "linkshortify.com", "bit.ly", "tinyurl.com", "cutt.ly",
    "short.ink", "tnshort.net", "linkpays.in", "earnl.ink"
]

# m3u8 URL pattern
M3U8_PATTERN = re.compile(
    r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*',
    re.IGNORECASE
)

# Stream domain patterns
STREAM_DOMAIN_PATTERN = re.compile(
    r'(stream\d*|live\d*|hls\d*|cdn\d*|media\d*)\.',
    re.IGNORECASE
)

# ─── Logging Setup ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


def get_random_ua():
    return random.choice(USER_AGENTS)


def get_session():
    """Create a requests session with headers."""
    session = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    session.headers.update({
        'User-Agent': get_random_ua(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    })
    return session


def get_chrome_driver():
    """Initialize headless Chrome driver."""
    if not SELENIUM_AVAILABLE:
        logger.warning("Selenium not available")
        return None

    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-images')
    options.add_argument('--window-size=1920,1080')
    options.add_argument(f'--user-agent={get_random_ua()}')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)

    # Enable network logging to capture m3u8 requests
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        # Hide webdriver
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return driver
    except Exception as e:
        logger.error(f"Failed to create Chrome driver: {e}")
        # Try system chrome
        try:
            service = Service('/usr/bin/chromedriver')
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            return driver
        except Exception as e2:
            logger.error(f"System chromedriver also failed: {e2}")
            return None


def is_short_link(url):
    """Check if URL is a known short link."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        for short_domain in SHORT_LINK_DOMAINS:
            if short_domain in domain:
                return True
        # Check for short path patterns
        if len(parsed.path.strip('/')) > 5 and len(parsed.path.strip('/')) < 30:
            if not any(ext in parsed.path for ext in ['.html', '.php', '.asp', '.jsp']):
                return True
    except Exception:
        pass
    return False


def extract_links_from_page(html, base_url=""):
    """Extract all links from HTML content."""
    links = []
    soup = BeautifulSoup(html, 'lxml')

    # Find all anchor tags
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            links.append(href)

    # Find links in iframes
    for iframe in soup.find_all('iframe', src=True):
        src = iframe['src']
        if src.startswith('//'):
            src = 'https:' + src
        elif src.startswith('/'):
            src = urljoin(base_url, src)
        if src.startswith('http'):
            links.append(src)

    # Find links in scripts
    for script in soup.find_all('script'):
        if script.string:
            urls = re.findall(r'https?://[^\s\'"<>]+', script.string)
            links.extend(urls)

    return links


def extract_m3u8_from_text(text):
    """Extract m3u8 URLs from any text content."""
    found = M3U8_PATTERN.findall(text)
    # Clean up URLs
    cleaned = []
    for url in found:
        # Remove trailing quotes, brackets, etc.
        url = url.rstrip('",;\')}]\\')
        if '.m3u8' in url:
            cleaned.append(url)
    return cleaned


def resolve_short_link_requests(url, session):
    """Resolve a short link using requests (follow redirects)."""
    try:
        logger.info(f"  Resolving short link (requests): {url}")
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        final_url = resp.url
        logger.info(f"  Final URL: {final_url}")

        # Check if the final page has m3u8 links
        m3u8_links = extract_m3u8_from_text(resp.text)
        if m3u8_links:
            return m3u8_links, final_url

        # Check page source for more links
        page_links = extract_links_from_page(resp.text, final_url)
        return [], final_url, page_links

    except Exception as e:
        logger.warning(f"  Failed to resolve {url}: {e}")
        return [], url, []


def resolve_with_selenium(url, driver):
    """Resolve a URL using Selenium and capture network requests for m3u8."""
    m3u8_links = []
    try:
        logger.info(f"  Loading in browser: {url}")
        driver.get(url)
        time.sleep(5)  # Wait for page load and JS execution

        # Check page source
        page_source = driver.page_source
        m3u8_links.extend(extract_m3u8_from_text(page_source))

        # Check network logs for m3u8 requests
        try:
            logs = driver.get_log('performance')
            for log_entry in logs:
                log_message = log_entry.get('message', '')
                m3u8_links.extend(extract_m3u8_from_text(log_message))
        except Exception:
            pass

        # Check all iframes
        iframes = driver.find_elements(By.TAG_NAME, 'iframe')
        for i, iframe in enumerate(iframes):
            try:
                src = iframe.get_attribute('src')
                if src:
                    logger.info(f"  Found iframe src: {src}")
                    m3u8_links.extend(extract_m3u8_from_text(src))

                    # Switch to iframe and check content
                    driver.switch_to.frame(iframe)
                    iframe_source = driver.page_source
                    m3u8_links.extend(extract_m3u8_from_text(iframe_source))

                    # Check for nested video elements
                    videos = driver.find_elements(By.TAG_NAME, 'video')
                    for video in videos:
                        video_src = video.get_attribute('src')
                        if video_src:
                            m3u8_links.extend(extract_m3u8_from_text(video_src))
                        # Check source elements inside video
                        sources = video.find_elements(By.TAG_NAME, 'source')
                        for source in sources:
                            source_src = source.get_attribute('src')
                            if source_src:
                                m3u8_links.extend(extract_m3u8_from_text(source_src))

                    driver.switch_to.default_content()
            except Exception as e:
                logger.debug(f"  Error processing iframe {i}: {e}")
                driver.switch_to.default_content()

        # Wait a bit more for dynamic content
        time.sleep(5)

        # Re-check page source after JS execution
        page_source = driver.page_source
        m3u8_links.extend(extract_m3u8_from_text(page_source))

        # Check network logs again
        try:
            logs = driver.get_log('performance')
            for log_entry in logs:
                log_message = log_entry.get('message', '')
                m3u8_links.extend(extract_m3u8_from_text(log_message))
        except Exception:
            pass

        # Execute JS to find video players
        try:
            js_result = driver.execute_script("""
                var urls = [];
                // Check for HLS.js
                if (typeof Hls !== 'undefined') {
                    document.querySelectorAll('video').forEach(function(v) {
                        if (v.hlsPlayer && v.hlsPlayer.url) urls.push(v.hlsPlayer.url);
                    });
                }
                // Check for video.js
                if (typeof videojs !== 'undefined') {
                    videojs.getAllPlayers().forEach(function(p) {
                        var src = p.currentSrc();
                        if (src) urls.push(src);
                    });
                }
                // Check jwplayer
                if (typeof jwplayer !== 'undefined') {
                    try {
                        var p = jwplayer();
                        if (p && p.getPlaylistItem) {
                            var item = p.getPlaylistItem();
                            if (item && item.file) urls.push(item.file);
                            if (item && item.sources) {
                                item.sources.forEach(function(s) { if(s.file) urls.push(s.file); });
                            }
                        }
                    } catch(e) {}
                }
                // Check all video elements
                document.querySelectorAll('video').forEach(function(v) {
                    if (v.src) urls.push(v.src);
                    v.querySelectorAll('source').forEach(function(s) {
                        if (s.src) urls.push(s.src);
                    });
                });
                // Check all script contents for m3u8
                document.querySelectorAll('script').forEach(function(s) {
                    if (s.textContent) {
                        var matches = s.textContent.match(/https?:\\/\\/[^\\s'"<>]+\\.m3u8[^\\s'"<>]*/gi);
                        if (matches) urls = urls.concat(matches);
                    }
                });
                return urls;
            """)
            if js_result:
                for url_found in js_result:
                    if url_found and '.m3u8' in url_found:
                        m3u8_links.append(url_found)
        except Exception as e:
            logger.debug(f"  JS execution error: {e}")

    except Exception as e:
        logger.error(f"  Selenium error for {url}: {e}")

    # De-duplicate
    m3u8_links = list(set(m3u8_links))
    return m3u8_links


def scrape_source_page(session):
    """Scrape the main source page for short links."""
    logger.info(f"Fetching source page: {SOURCE_URL}")
    short_links = []
    all_links = []

    try:
        resp = session.get(SOURCE_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        html = resp.text
        logger.info(f"Source page fetched: {len(html)} bytes")

        soup = BeautifulSoup(html, 'lxml')

        # Extract all links
        all_links = extract_links_from_page(html, SOURCE_URL)
        logger.info(f"Found {len(all_links)} total links on source page")

        # Filter for short links
        for link in all_links:
            if is_short_link(link):
                short_links.append(link)

        # Also look for specific patterns in the page
        # Sometimes links are in onclick attributes or data attributes
        for tag in soup.find_all(True):
            for attr_name, attr_value in tag.attrs.items():
                if isinstance(attr_value, str) and 'http' in attr_value:
                    urls = re.findall(r'https?://[^\s\'"<>]+', attr_value)
                    for url in urls:
                        if is_short_link(url) and url not in short_links:
                            short_links.append(url)

        # Check for m3u8 directly in source page
        direct_m3u8 = extract_m3u8_from_text(html)
        if direct_m3u8:
            logger.info(f"Found m3u8 directly in source page: {direct_m3u8}")

        logger.info(f"Found {len(short_links)} short links")
        for link in short_links:
            logger.info(f"  Short link: {link}")

        # Also extract iframe sources
        iframe_links = []
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if src.startswith('//'):
                src = 'https:' + src
            iframe_links.append(src)
            logger.info(f"  Iframe: {src}")

        return short_links, all_links, direct_m3u8, iframe_links

    except Exception as e:
        logger.error(f"Failed to fetch source page: {e}")
        return [], [], [], []


def process_intermediate_page(url, session, driver=None):
    """Process intermediate pages after resolving short links."""
    m3u8_links = []

    # First try with requests
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        final_url = resp.url
        html = resp.text
        logger.info(f"  Intermediate page loaded: {final_url} ({len(html)} bytes)")

        # Check for m3u8 in the page
        m3u8_links.extend(extract_m3u8_from_text(html))

        # Check for iframes that might contain the player
        soup = BeautifulSoup(html, 'lxml')
        iframe_srcs = []
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = urljoin(final_url, src)
            iframe_srcs.append(src)
            logger.info(f"    Found iframe in intermediate page: {src}")

        # Process iframes
        for iframe_src in iframe_srcs:
            try:
                iframe_resp = session.get(iframe_src, timeout=REQUEST_TIMEOUT)
                m3u8_links.extend(extract_m3u8_from_text(iframe_resp.text))
            except Exception as e:
                logger.debug(f"    Failed to fetch iframe: {e}")

    except Exception as e:
        logger.warning(f"  Requests failed for {url}: {e}")

    # If no m3u8 found, try with Selenium
    if not m3u8_links and driver:
        m3u8_links.extend(resolve_with_selenium(url, driver))

    return m3u8_links


def validate_m3u8(url, session):
    """Validate that an m3u8 URL is actually accessible."""
    try:
        resp = session.head(url, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            return True
        # Try GET if HEAD fails
        resp = session.get(url, timeout=15, stream=True)
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


def write_playlist(m3u8_links, metadata=None):
    """Write the m3u playlist file."""
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    with open(PLAYLIST_FILE, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        f.write(f'# Auto-generated playlist - Updated: {timestamp}\n')
        f.write(f'# Source: {SOURCE_URL}\n')
        f.write(f'# Total streams: {len(m3u8_links)}\n\n')

        for i, link in enumerate(m3u8_links, 1):
            # Try to determine a name from the URL
            parsed = urlparse(link)
            domain = parsed.netloc
            name = f"Bigg Boss Live Stream {i}"

            if 'hindi' in link.lower():
                name = f"Bigg Boss Live - Hindi (Stream {i})"
            elif 'tamil' in link.lower():
                name = f"Bigg Boss Live - Tamil (Stream {i})"
            elif 'telugu' in link.lower():
                name = f"Bigg Boss Live - Telugu (Stream {i})"

            f.write(f'#EXTINF:-1 tvg-name="{name}" group-title="Bigg Boss Live",{name}\n')
            f.write(f'{link}\n\n')

    logger.info(f"Playlist written to {PLAYLIST_FILE} with {len(m3u8_links)} streams")

    # Also write JSON metadata
    json_data = {
        'updated': timestamp,
        'source': SOURCE_URL,
        'total_streams': len(m3u8_links),
        'streams': []
    }
    for i, link in enumerate(m3u8_links, 1):
        json_data['streams'].append({
            'id': i,
            'url': link,
            'name': f"Bigg Boss Live Stream {i}",
            'added': timestamp
        })

    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2)

    logger.info(f"JSON metadata written to {JSON_FILE}")


def load_previous_links():
    """Load previously found m3u8 links from JSON file."""
    try:
        if os.path.exists(JSON_FILE):
            with open(JSON_FILE, 'r') as f:
                data = json.load(f)
                return [s['url'] for s in data.get('streams', [])]
    except Exception:
        pass
    return []


def main():
    logger.info("=" * 60)
    logger.info("Auto Playlist Scraper Started")
    logger.info("=" * 60)

    session = get_session()
    driver = None
    all_m3u8_links = []

    # Load previous links as fallback
    previous_links = load_previous_links()
    if previous_links:
        logger.info(f"Loaded {len(previous_links)} previous links as fallback")

    try:
        # Step 1: Scrape source page
        short_links, all_links, direct_m3u8, iframe_links = scrape_source_page(session)

        if direct_m3u8:
            all_m3u8_links.extend(direct_m3u8)

        # Step 2: Initialize Selenium if needed
        if short_links or iframe_links:
            driver = get_chrome_driver()
            if driver:
                logger.info("Chrome driver initialized")

        # Step 3: Process the source page with Selenium for dynamic content
        if driver and not all_m3u8_links:
            logger.info("Checking source page with Selenium...")
            selenium_links = resolve_with_selenium(SOURCE_URL, driver)
            all_m3u8_links.extend(selenium_links)

        # Step 4: Process iframe links
        for iframe_url in iframe_links:
            logger.info(f"Processing iframe: {iframe_url}")
            m3u8_found = process_intermediate_page(iframe_url, session, driver)
            all_m3u8_links.extend(m3u8_found)
            if m3u8_found:
                logger.info(f"  Found m3u8 in iframe: {m3u8_found}")

        # Step 5: Process short links
        for short_link in short_links:
            logger.info(f"Processing short link: {short_link}")

            # First resolve with requests
            try:
                resp = session.get(short_link, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                resolved_url = resp.url
                html = resp.text
                logger.info(f"  Resolved to: {resolved_url}")

                # Check for m3u8 in resolved page
                m3u8_found = extract_m3u8_from_text(html)
                if m3u8_found:
                    all_m3u8_links.extend(m3u8_found)
                    logger.info(f"  Found m3u8: {m3u8_found}")
                    continue

                # Check for more links/iframes in resolved page
                page_links = extract_links_from_page(html, resolved_url)
                soup = BeautifulSoup(html, 'lxml')

                # Process iframes in the resolved page
                for iframe in soup.find_all('iframe', src=True):
                    iframe_src = iframe['src']
                    if iframe_src.startswith('//'):
                        iframe_src = 'https:' + iframe_src
                    elif iframe_src.startswith('/'):
                        iframe_src = urljoin(resolved_url, iframe_src)

                    logger.info(f"  Found iframe in resolved page: {iframe_src}")
                    m3u8_found = process_intermediate_page(iframe_src, session, driver)
                    all_m3u8_links.extend(m3u8_found)

                # If still no m3u8, use Selenium on the resolved page
                if not m3u8_found and driver:
                    logger.info(f"  Trying Selenium on resolved page: {resolved_url}")
                    selenium_links = resolve_with_selenium(resolved_url, driver)
                    all_m3u8_links.extend(selenium_links)

            except Exception as e:
                logger.warning(f"  Error processing short link: {e}")

                # Fallback: try directly with Selenium
                if driver:
                    logger.info(f"  Fallback: Selenium on short link directly")
                    selenium_links = resolve_with_selenium(short_link, driver)
                    all_m3u8_links.extend(selenium_links)

            # Small delay between requests
            time.sleep(random.uniform(1, 3))

        # Step 6: Process remaining interesting links from source page
        if not all_m3u8_links:
            logger.info("No m3u8 found yet, checking all links from source page...")
            interesting_patterns = ['player', 'embed', 'watch', 'live', 'stream', 'video']
            for link in all_links:
                if any(pattern in link.lower() for pattern in interesting_patterns):
                    logger.info(f"Processing interesting link: {link}")
                    m3u8_found = process_intermediate_page(link, session, driver)
                    all_m3u8_links.extend(m3u8_found)
                    if m3u8_found:
                        break
                    time.sleep(1)

    except Exception as e:
        logger.error(f"Scraper error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # De-duplicate m3u8 links
    all_m3u8_links = list(dict.fromkeys(all_m3u8_links))

    # Validate m3u8 links
    if all_m3u8_links:
        logger.info(f"\nValidating {len(all_m3u8_links)} m3u8 links...")
        valid_links = []
        for link in all_m3u8_links:
            logger.info(f"  Validating: {link}")
            if validate_m3u8(link, session):
                valid_links.append(link)
                logger.info(f"    ✅ Valid")
            else:
                logger.info(f"    ❌ Invalid (keeping anyway)")
                valid_links.append(link)  # Keep even if validation fails

        all_m3u8_links = valid_links

    # If no new links found, keep previous ones
    if not all_m3u8_links and previous_links:
        logger.warning("No new m3u8 links found, keeping previous links")
        all_m3u8_links = previous_links

    # Write output
    if all_m3u8_links:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Total m3u8 streams found: {len(all_m3u8_links)}")
        for i, link in enumerate(all_m3u8_links, 1):
            logger.info(f"  {i}. {link}")
        logger.info(f"{'=' * 60}")

        write_playlist(all_m3u8_links)
    else:
        logger.warning("No m3u8 links found at all!")
        # Write empty playlist with error info
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        with open(PLAYLIST_FILE, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            f.write(f'# Auto-generated playlist - Updated: {timestamp}\n')
            f.write(f'# WARNING: No streams found in this update\n')
            f.write(f'# Source: {SOURCE_URL}\n')

        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'updated': timestamp,
                'source': SOURCE_URL,
                'total_streams': 0,
                'streams': [],
                'error': 'No streams found'
            }, f, indent=2)

    logger.info("\nScraper finished!")


if __name__ == '__main__':
    main()
