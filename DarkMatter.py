# DarkMatter: Advanced Stealth Browser Automation Engine
# Evades bot detection by combining biometric replay, hardware fingerprint masking,
# API spoofing, and realistic network simulation via Playwright.

import asyncio
import random
import json
import os
import math
import time
import argparse
import logging
from playwright.async_api import async_playwright

# All DarkMatter modules log under this namespace (filterable via --log-level)
logger = logging.getLogger('DarkMatter')

# Toggle residential ISP simulation.
# True  = inject latency spikes and bandwidth throttling via CDP (mimics Lagos ISP)
# False = clean, full-speed connection (use for trusted/non-detection targets)
NETWORK_JITTER = True    

# Recorded biometric traces (mouse, keyboard, scroll) stored here for replay
TRAINING_DATA_FILE = "human_behavior_profile.json"
# Per-session cookies and localStorage snapshots
SESSIONS_DIR = "sessions"

# Common desktop resolutions weighted toward real-world market share.
# Used to randomize viewport per session so each run looks like a different machine.
DESKTOP_VIEWPORTS = [
    (1920, 1080),  # Full HD - most common
    (1366, 768),   # HD - laptops
    (1440, 900),   # MacBooks
    (1680, 1050),  # Larger monitors
    (1536, 864),   # Windows laptops
    (1280, 720),   # HD ready
    (1600, 900),   # Common mid-range
]

class ViewportManager:
    """Generates randomized but internally-consistent viewport + hardware profiles.
    Ensures GPU vendor, memory, and CPU cores all match the chosen screen size."""
    
    @staticmethod
    def generate_viewport_profile():
        """Generate a viewport profile where screen size, GPU, memory, and DPR are coherent."""
        width, height = random.choice(DESKTOP_VIEWPORTS)
        
        # Match renderer to viewport size for consistency
        # Smaller viewports = Intel integrated (laptops), larger = AMD discrete
        is_laptop_viewport = width <= 1440 and height <= 900
        
        if is_laptop_viewport:
            vendor = 'Google Inc. (Intel)'
            renderer = 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)'
            device_memory = random.choice([4, 8])
            hardware_concurrency = random.choice([4, 8])
        else:
            vendor = 'Google Inc. (AMD)'
            renderer = 'ANGLE (AMD, AMD Radeon Graphics (RADV RENOIR) OpenGL Engine)'
            device_memory = random.choice([8, 16])
            hardware_concurrency = random.choice([8, 16])
        
        # Calculate available screen (accounting for taskbar)
        avail_width = width
        avail_height = height - random.randint(40, 80)  # Taskbar
        
        return {
            'width': width,
            'height': height,
            'avail_width': avail_width,
            'avail_height': avail_height,
            'device_pixel_ratio': 1.0 if width <= 1366 else random.choice([1.0, 1.25]),
            'color_depth': 24,
            'vendor': vendor,
            'renderer': renderer,
            'device_memory': device_memory,
            'hardware_concurrency': hardware_concurrency,
        }
    
    @staticmethod
    def get_viewport_init_script(profile):
        """Generate JS to override window.screen and dimension properties.
        Prevents mismatch between Playwright viewport and JS-visible values."""
        return f"""
            Object.defineProperty(window.screen, 'width', {{ get: () => {profile['width']} }});
            Object.defineProperty(window.screen, 'height', {{ get: () => {profile['height']} }});
            Object.defineProperty(window.screen, 'availWidth', {{ get: () => {profile['avail_width']} }});
            Object.defineProperty(window.screen, 'availHeight', {{ get: () => {profile['avail_height']} }});
            Object.defineProperty(window.screen, 'colorDepth', {{ get: () => {profile['color_depth']} }});
            Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {profile['device_pixel_ratio']} }});
            Object.defineProperty(window.screen, 'pixelDepth', {{ get: () => {profile['color_depth']} }});
            
            // Match inner/outer dimensions
            Object.defineProperty(window, 'outerWidth', {{ get: () => {profile['width']} }});
            Object.defineProperty(window, 'outerHeight', {{ get: () => {profile['height']} }});
        """

class SessionManager:
    """Persists cookies and localStorage across runs to maintain login state.
    Saved per named profile in the sessions/ directory."""
    
    def __init__(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
    
    def _get_cookie_path(self, profile_name):
        return os.path.join(SESSIONS_DIR, f"{profile_name}_cookies.json")
    
    def _get_storage_path(self, profile_name):
        return os.path.join(SESSIONS_DIR, f"{profile_name}_storage.json")
    
    async def save_session(self, context, profile_name):
        """Save all cookies (deduplicated) and per-page localStorage to disk."""
        try:
            # Save cookies from all pages
            all_cookies = []
            for page in context.pages:
                cookies = await context.cookies()
                all_cookies.extend(cookies)
            
            # Deduplicate cookies
            seen = set()
            unique_cookies = []
            for cookie in all_cookies:
                key = (cookie.get('name'), cookie.get('domain'), cookie.get('path'))
                if key not in seen:
                    seen.add(key)
                    unique_cookies.append(cookie)
            
            cookie_path = self._get_cookie_path(profile_name)
            with open(cookie_path, 'w') as f:
                json.dump(unique_cookies, f, indent=2)
            
            # Save localStorage from each page
            storage_data = {}
            for i, page in enumerate(context.pages):
                try:
                    storage = await page.evaluate("""() => {{
                        const data = {{}};
                        for (let i = 0; i < localStorage.length; i++) {{
                            const key = localStorage.key(i);
                            data[key] = localStorage.getItem(key);
                        }}
                        return data;
                    }}""")
                    storage_data[f"page_{i}"] = {
                        'url': page.url,
                        'storage': storage
                    }
                except Exception as e:
                    logger.warning(f"Could not save storage for page {i}: {e}")
            
            storage_path = self._get_storage_path(profile_name)
            with open(storage_path, 'w') as f:
                json.dump(storage_data, f, indent=2)
            
            logger.info(f"[SESSION] Saved session '{profile_name}': {len(unique_cookies)} cookies, {len(storage_data)} pages")
            return True
        except Exception as e:
            logger.error(f"[SESSION] Failed to save session: {e}")
            return False
    
    async def load_session(self, context, profile_name):
        """Restore cookies into the browser context. localStorage is restored per-page after navigation."""
        try:
            # Load cookies
            cookie_path = self._get_cookie_path(profile_name)
            if os.path.exists(cookie_path):
                with open(cookie_path, 'r') as f:
                    cookies = json.load(f)
                await context.add_cookies(cookies)
                logger.info(f"[SESSION] Loaded {len(cookies)} cookies for '{profile_name}'")
            
            # Note: localStorage restoration happens per-page after navigation
            return True
        except Exception as e:
            logger.warning(f"[SESSION] Could not load session '{profile_name}': {e}")
            return False
    
    async def restore_storage_for_page(self, page, profile_name, page_index=0):
        """Inject saved localStorage key-value pairs into a page. Must be called after page.goto()."""
        try:
            storage_path = self._get_storage_path(profile_name)
            if not os.path.exists(storage_path):
                return False
            
            with open(storage_path, 'r') as f:
                storage_data = json.load(f)
            
            page_key = f"page_{page_index}"
            if page_key in storage_data:
                storage = storage_data[page_key].get('storage', {})
                for key, value in storage.items():
                    try:
                        await page.evaluate(f"""() => {{
                            localStorage.setItem({json.dumps(key)}, {json.dumps(value)});
                        }}""")
                    except Exception:
                        pass
                logger.debug(f"[SESSION] Restored {len(storage)} localStorage items for page {page_index}")
                return True
        except Exception as e:
            logger.debug(f"[SESSION] Could not restore storage: {e}")
        return False

class GeoProfile:
    """Curated timezone+locale+language combos for coherent geographic identity.
    Prevents clock/timezone/locale mismatches that bot detectors flag (e.g. JS Date
    returning UTC-5 but navigator.language saying 'ja-JP')."""
    
    # Each profile is a realistic combination as seen on real user machines
    PROFILES = [
        {'timezone': 'Africa/Lagos', 'locale': 'en-NG', 'languages': ['en-US', 'en'], 'region': 'Nigeria'},
        {'timezone': 'Africa/Johannesburg', 'locale': 'en-ZA', 'languages': ['en-ZA', 'en'], 'region': 'South Africa'},
        {'timezone': 'Africa/Nairobi', 'locale': 'en-KE', 'languages': ['en-KE', 'en'], 'region': 'Kenya'},
        {'timezone': 'America/New_York', 'locale': 'en-US', 'languages': ['en-US', 'en'], 'region': 'US East'},
        {'timezone': 'America/Chicago', 'locale': 'en-US', 'languages': ['en-US', 'en'], 'region': 'US Central'},
        {'timezone': 'America/Los_Angeles', 'locale': 'en-US', 'languages': ['en-US', 'en'], 'region': 'US West'},
        {'timezone': 'America/Toronto', 'locale': 'en-CA', 'languages': ['en-CA', 'en'], 'region': 'Canada'},
        {'timezone': 'America/Sao_Paulo', 'locale': 'pt-BR', 'languages': ['pt-BR', 'pt', 'en'], 'region': 'Brazil'},
        {'timezone': 'Europe/London', 'locale': 'en-GB', 'languages': ['en-GB', 'en'], 'region': 'UK'},
        {'timezone': 'Europe/Berlin', 'locale': 'de-DE', 'languages': ['de-DE', 'de', 'en'], 'region': 'Germany'},
        {'timezone': 'Europe/Paris', 'locale': 'fr-FR', 'languages': ['fr-FR', 'fr', 'en'], 'region': 'France'},
        {'timezone': 'Europe/Moscow', 'locale': 'ru-RU', 'languages': ['ru-RU', 'ru', 'en'], 'region': 'Russia'},
        {'timezone': 'Asia/Tokyo', 'locale': 'ja-JP', 'languages': ['ja', 'en'], 'region': 'Japan'},
        {'timezone': 'Asia/Shanghai', 'locale': 'zh-CN', 'languages': ['zh-CN', 'zh', 'en'], 'region': 'China'},
        {'timezone': 'Asia/Kolkata', 'locale': 'en-IN', 'languages': ['en-IN', 'hi', 'en'], 'region': 'India'},
        {'timezone': 'Asia/Dubai', 'locale': 'ar-AE', 'languages': ['ar-AE', 'ar', 'en'], 'region': 'UAE'},
        {'timezone': 'Asia/Singapore', 'locale': 'en-SG', 'languages': ['en-SG', 'en'], 'region': 'Singapore'},
        {'timezone': 'Australia/Sydney', 'locale': 'en-AU', 'languages': ['en-AU', 'en'], 'region': 'Australia'},
    ]
    
    # Fast lookup: timezone string -> full profile dict
    _TZ_MAP = {p['timezone']: p for p in PROFILES}
    
    @staticmethod
    def get_random():
        """Pick a random geo profile. All fields are pre-validated as coherent."""
        return random.choice(GeoProfile.PROFILES)
    
    @staticmethod
    def get_by_timezone(timezone_id):
        """Look up by exact timezone, fall back to same continent, then default to Lagos."""
        if timezone_id in GeoProfile._TZ_MAP:
            return GeoProfile._TZ_MAP[timezone_id]
        # Try to match by region prefix (e.g. America/ -> pick a US one)
        prefix = timezone_id.split('/')[0] if '/' in timezone_id else ''
        candidates = [p for p in GeoProfile.PROFILES if p['timezone'].startswith(prefix)]
        if candidates:
            chosen = random.choice(candidates)
            logger.warning(f"[GEO] No exact match for '{timezone_id}', using '{chosen['timezone']}'")
            return chosen
        # Absolute fallback
        logger.warning(f"[GEO] Unknown timezone '{timezone_id}', defaulting to Africa/Lagos")
        return GeoProfile.PROFILES[0]
    
    @staticmethod
    def resolve(timezone_override=None, locale_override=None):
        """Build final geo config: uses CLI overrides if given, otherwise picks randomly.
        Warns if timezone and locale don't match any known real-world pairing."""
        if timezone_override and locale_override:
            # Both specified: use as-is but warn about potential mismatch
            profile = GeoProfile.get_by_timezone(timezone_override)
            if profile['locale'] != locale_override:
                logger.warning(f"[GEO] Potential mismatch: timezone '{timezone_override}' "
                               f"typically uses locale '{profile['locale']}', not '{locale_override}'")
            return {
                'timezone': timezone_override,
                'locale': locale_override,
                'languages': profile['languages'],
                'region': profile.get('region', 'Custom')
            }
        elif timezone_override:
            profile = GeoProfile.get_by_timezone(timezone_override)
            return {**profile, 'timezone': timezone_override}
        elif locale_override:
            # Find profile matching locale
            for p in GeoProfile.PROFILES:
                if p['locale'] == locale_override:
                    return p
            logger.warning(f"[GEO] No profile for locale '{locale_override}', using random")
            profile = GeoProfile.get_random()
            return {**profile, 'locale': locale_override}
        else:
            return GeoProfile.get_random()

class ProxyManager:
    """Loads and rotates proxies from a text file. Supports round-robin and random selection.
    Format: one proxy per line as protocol://[user:pass@]host:port. Lines starting with # are skipped."""
    
    def __init__(self, proxy_file=None):
        self.proxies = []
        self._index = 0  # Tracks position for round-robin rotation
        if proxy_file:
            self.load_proxies(proxy_file)
    
    def load_proxies(self, filepath):
        """Read proxy URLs from file, skipping comments and blank lines."""
        try:
            with open(filepath, 'r') as f:
                lines = f.readlines()
            self.proxies = [line.strip() for line in lines if line.strip() and not line.strip().startswith('#')]
            logger.info(f"[PROXY] Loaded {len(self.proxies)} proxies from {filepath}")
        except FileNotFoundError:
            logger.error(f"[PROXY] Proxy file not found: {filepath}")
        except Exception as e:
            logger.error(f"[PROXY] Failed to load proxy file: {e}")
    
    def get_next(self):
        """Return next proxy via round-robin. Wraps around when the list is exhausted."""
        if not self.proxies:
            return None
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy
    
    def get_random(self):
        """Return a random proxy."""
        if not self.proxies:
            return None
        return random.choice(self.proxies)
    
    def has_proxies(self):
        return len(self.proxies) > 0
    
    @staticmethod
    def parse_proxy_url(proxy_url):
        """Convert 'protocol://user:pass@host:port' into Playwright's proxy config dict.
        Returns {'server': ..., 'username': ..., 'password': ...} or just {'server': ...}."""
        if not proxy_url:
            return None
        # Format: protocol://user:pass@host:port or protocol://host:port
        proxy_dict = {'server': proxy_url}
        if '@' in proxy_url:
            # Extract credentials
            proto_rest = proxy_url.split('://', 1)
            if len(proto_rest) == 2:
                protocol = proto_rest[0]
                creds_host = proto_rest[1]
                if '@' in creds_host:
                    creds, host_port = creds_host.rsplit('@', 1)
                    if ':' in creds:
                        username, password = creds.split(':', 1)
                        proxy_dict = {
                            'server': f"{protocol}://{host_port}",
                            'username': username,
                            'password': password
                        }
        return proxy_dict

class UserAgentManager:
    """Selects realistic User-Agent strings that match the viewport's hardware profile.
    Auto-selection avoids obvious mismatches (e.g. Mac UA on an Intel laptop viewport)."""
    
    # Real-world UAs from Chrome 120-128 across Windows/Mac/Linux + Edge variants.
    # Kept current to avoid age-based detection (outdated Chrome versions are flagged).
    BUILTIN_UAS = [
        # Windows - Chrome
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        # Windows 11
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.76 Safari/537.36',
        # Mac - Chrome
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        # Linux - Chrome
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        # Edge (Chromium-based)
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
    ]
    
    @staticmethod
    def get_random():
        """Get a random User-Agent from the built-in pool."""
        return random.choice(UserAgentManager.BUILTIN_UAS)
    
    @staticmethod
    def get_matching(viewport_profile):
        """Pick a UA consistent with the viewport's GPU and screen size.
        Intel GPU → Windows/Linux only. Laptop viewport → prefer Windows."""
        is_laptop = viewport_profile.get('width', 1920) <= 1440
        vendor = viewport_profile.get('vendor', '')
        
        if 'Intel' in vendor:
            # Intel = likely Windows laptop or Linux
            candidates = [ua for ua in UserAgentManager.BUILTIN_UAS 
                         if 'Windows' in ua or 'Linux' in ua]
        else:
            # AMD = could be any platform
            candidates = UserAgentManager.BUILTIN_UAS[:]
        
        if is_laptop:
            # Prefer Windows for laptops
            win_uas = [ua for ua in candidates if 'Windows' in ua]
            if win_uas:
                candidates = win_uas
        
        return random.choice(candidates) if candidates else random.choice(UserAgentManager.BUILTIN_UAS)

class FingerprintValidator:
    """Cross-checks viewport, GPU, UA, memory, and geo for internal consistency.
    Bot detectors flag contradictions (e.g. 4-core CPU with 32GB RAM, or Intel GPU on a Mac)."""
    
    KNOWN_RESOLUTIONS = set(DESKTOP_VIEWPORTS)
    
    @staticmethod
    def validate(viewport_profile, geo_profile=None, user_agent=None):
        """Run 8 consistency checks. Logs warnings for each issue found. Returns warning list."""
        warnings = []
        
        width = viewport_profile.get('width', 0)
        height = viewport_profile.get('height', 0)
        vendor = viewport_profile.get('vendor', '')
        hw_concurrency = viewport_profile.get('hardware_concurrency', 0)
        dev_memory = viewport_profile.get('device_memory', 0)
        dpr = viewport_profile.get('device_pixel_ratio', 1.0)
        
        # 1. Resolution must be in known list
        if (width, height) not in FingerprintValidator.KNOWN_RESOLUTIONS:
            warnings.append(f"Resolution {width}x{height} not in known desktop resolutions")
        
        # 2. Vendor/renderer must match viewport heuristic
        is_laptop = width <= 1440 and height <= 900
        if is_laptop and 'AMD' in vendor:
            warnings.append(f"Laptop viewport ({width}x{height}) with AMD GPU is unusual")
        elif not is_laptop and 'Intel' in vendor:
            warnings.append(f"Desktop viewport ({width}x{height}) with Intel GPU is unusual")
        
        # 3. hardwareConcurrency must be power of 2 and >= 2
        if hw_concurrency < 2 or (hw_concurrency & (hw_concurrency - 1)) != 0:
            warnings.append(f"hardwareConcurrency={hw_concurrency} is not a valid power of 2")
        
        # 4. deviceMemory must be in standard values
        valid_memory = {2, 4, 8, 16, 32}
        if dev_memory not in valid_memory:
            warnings.append(f"deviceMemory={dev_memory} is not a standard value ({valid_memory})")
        
        # 5. deviceMemory should be proportional to concurrency
        if hw_concurrency >= 16 and dev_memory < 8:
            warnings.append(f"High concurrency ({hw_concurrency}) with low memory ({dev_memory}GB) is suspicious")
        if hw_concurrency <= 4 and dev_memory >= 16:
            warnings.append(f"Low concurrency ({hw_concurrency}) with high memory ({dev_memory}GB) is suspicious")
        
        # 6. devicePixelRatio should match resolution
        if width <= 1366 and dpr > 1.0:
            warnings.append(f"Low resolution ({width}x{height}) with high DPR ({dpr}) is unusual")
        
        # 7. UA platform should match viewport/vendor
        if user_agent:
            ua_is_windows = 'Windows' in user_agent
            ua_is_mac = 'Macintosh' in user_agent
            ua_is_linux = 'Linux' in user_agent
            
            if ua_is_mac and 'Intel' in vendor and not is_laptop:
                warnings.append("Mac UA with Intel GPU on large viewport is unusual")
        
        # 8. Geo consistency check
        if geo_profile and user_agent:
            # Check language consistency
            if geo_profile.get('locale', '').startswith('ja') and 'Windows' not in user_agent and 'Macintosh' not in user_agent:
                warnings.append("Japanese locale on Linux is uncommon")
        
        # Log results
        if warnings:
            for w in warnings:
                logger.warning(f"[FINGERPRINT] {w}")
        else:
            logger.info("[FINGERPRINT] All consistency checks passed")
        
        return warnings
    
    @staticmethod
    def auto_correct(viewport_profile):
        """Fix obvious mismatches silently: swap GPU vendor if it contradicts screen size,
        reset DPR if too high for low-res screens. Returns corrected copy."""
        corrected = viewport_profile.copy()
        
        width = corrected.get('width', 1920)
        height = corrected.get('height', 1080)
        is_laptop = width <= 1440 and height <= 900
        
        # Fix vendor/renderer mismatch
        if is_laptop and 'AMD' in corrected.get('vendor', ''):
            corrected['vendor'] = 'Google Inc. (Intel)'
            corrected['renderer'] = 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)'
            corrected['device_memory'] = random.choice([4, 8])
            corrected['hardware_concurrency'] = random.choice([4, 8])
            logger.info("[FINGERPRINT] Auto-corrected: Switched to Intel for laptop viewport")
        elif not is_laptop and 'Intel' in corrected.get('vendor', ''):
            corrected['vendor'] = 'Google Inc. (AMD)'
            corrected['renderer'] = 'ANGLE (AMD, AMD Radeon Graphics (RADV RENOIR) OpenGL Engine)'
            corrected['device_memory'] = random.choice([8, 16])
            corrected['hardware_concurrency'] = random.choice([8, 16])
            logger.info("[FINGERPRINT] Auto-corrected: Switched to AMD for desktop viewport")
        
        # Fix DPR
        if width <= 1366 and corrected.get('device_pixel_ratio', 1.0) > 1.0:
            corrected['device_pixel_ratio'] = 1.0
            logger.info("[FINGERPRINT] Auto-corrected: Reset DPR to 1.0 for low-res viewport")
        
        return corrected

class GhostLogic:
    """Records and replays real human input (mouse, keyboard, scroll, focus) with ms precision.
    Traces are stored in human_behavior_profile.json alongside the viewport profile that was
    active during recording, so replay uses the same screen geometry."""
    @staticmethod
    def save_trace(data, viewport_profile=None):
        """Write recorded biometric nodes + viewport profile to disk as JSON."""
        save_data = {
            'trace': data,
            'viewport_profile': viewport_profile or ViewportManager.generate_viewport_profile()
        }
        with open(TRAINING_DATA_FILE, "w") as f:
            json.dump(save_data, f)
        logger.info(f"[BIO-SYNC] Profile Saved: {len(data)} nodes with viewport {save_data['viewport_profile']['width']}x{save_data['viewport_profile']['height']}")

    @staticmethod
    def load_trace():
        if os.path.exists(TRAINING_DATA_FILE):
            with open(TRAINING_DATA_FILE, "r") as f:
                data = json.load(f)
            # Backward compatibility: handle old format (just a list)
            if isinstance(data, list):
                return data, ViewportManager.generate_viewport_profile()
            return data.get('trace', []), data.get('viewport_profile', ViewportManager.generate_viewport_profile())
        return [], ViewportManager.generate_viewport_profile()

    @staticmethod
    async def playback(page, trace_data):
        """Replays recorded traces with absolute-time alignment to prevent drift.
        Each event is dispatched at the exact ms offset it was originally recorded."""
        if not trace_data: 
            logger.warning("[PLAYBACK] No trace data provided")
            return
        logger.info(f"[PLAYBACK] Replaying {len(trace_data)} biometric nodes...")
        
        trace_start_time = trace_data[0]['t']        # First event's original timestamp (ms)
        playback_start_time = time.time() * 1000       # Current wall clock in ms
        
        last_x, last_y = 200, 200  # Track cursor position for return value
        
        for node in trace_data:
            # Calculate absolute target time to prevent asyncio.sleep drift
            target_time = playback_start_time + (node['t'] - trace_start_time)
            current_time = time.time() * 1000
            wait_time = max(0, target_time - current_time)
            
            if wait_time > 0:
                await asyncio.sleep(wait_time / 1000)
            
            if node['type'] == 'mousemove':
                last_x, last_y = node['x'], node['y']
                await page.mouse.move(last_x, last_y)
            elif node['type'] == 'mousedown':
                # Add Gaussian jitter to click timing (mean 120ms, σ=30ms)
                # This models human motor cortex reaction time distribution
                latency = max(0.0, random.gauss(0.12, 0.03))
                await asyncio.sleep(latency) 
                await page.mouse.down()
            elif node['type'] == 'mouseup':
                await page.mouse.up()
            elif node['type'] == 'wheel':
                await page.mouse.wheel(node['deltaX'], node['deltaY'])
            elif node['type'] == 'keydown':
                await page.keyboard.down(node['key'])
            elif node['type'] == 'keyup':
                await page.keyboard.up(node['key'])
            elif node['type'] in ('focus', 'blur'):
                # Note: Exact synthetic replay of passive focus logic is context-dependent,
                # but we track the entropy for realistic session timespans.
                pass
            
        return last_x, last_y

class HumanLogic:
    """Generates synthetic human behavior when no recorded trace is available.
    Includes bezier mouse paths, realistic typing with typos, scrolling, tab switches,
    reading pauses, and inactivity patterns. Each action has built-in randomness."""
    
    # Full QWERTY layout for finding adjacent keys during typo simulation
    QWERTY_LAYOUT = [
        ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
        ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']'],
        ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'"],
        ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/']
    ]
    
    # Per-key typing speed: harder-to-reach keys take longer (backed by HCI research)
    KEY_SPEED = {
        'easy': 0.06,    # Home row (asdf, jkl) — fingers rest here
        'medium': 0.08,  # One row away — short reach
        'hard': 0.12,    # Corner keys (q, z, p, numbers) — full finger extension
    }
    
    # Categorized by physical reach distance on a standard keyboard
    HARD_KEYS = {'q', 'z', 'p', '1', '0', '-', '=', '[', ']', ';', "'", ',', '.', '/'}
    EASY_KEYS = {'a', 's', 'd', 'f', 'j', 'k', 'l'}
    
    @staticmethod
    def _get_adjacent_keys(char):
        """Find physically adjacent keys on QWERTY layout (horizontal + vertical neighbors).
        Used to generate typos that match real finger-slip patterns."""
        if not char.isalpha() and char not in '1234567890-=[];\',./':
            return [char]
        
        char_lower = char.lower()
        adjacents = [char_lower]  # Include original
        
        for row_idx, row in enumerate(HumanLogic.QWERTY_LAYOUT):
            if char_lower in row:
                col_idx = row.index(char_lower)
                # Add horizontal neighbors
                if col_idx > 0:
                    adjacents.append(row[col_idx - 1])
                if col_idx < len(row) - 1:
                    adjacents.append(row[col_idx + 1])
                # Add vertical neighbors (row above/below, offset for staggered keys)
                if row_idx > 0:
                    above_row = HumanLogic.QWERTY_LAYOUT[row_idx - 1]
                    # Approximate vertical alignment (keys are staggered)
                    above_idx = min(col_idx, len(above_row) - 1)
                    adjacents.append(above_row[above_idx])
                if row_idx < len(HumanLogic.QWERTY_LAYOUT) - 1:
                    below_row = HumanLogic.QWERTY_LAYOUT[row_idx + 1]
                    below_idx = min(col_idx, len(below_row) - 1)
                    adjacents.append(below_row[below_idx])
        
        # Return uppercase if original was uppercase
        if char.isupper():
            return [c.upper() for c in adjacents]
        return adjacents
    
    @staticmethod
    def _get_typing_delay(char):
        """Return delay in seconds based on how hard the key is to reach. Adds Gaussian noise."""
        char_lower = char.lower()
        if char_lower in HumanLogic.HARD_KEYS:
            base_delay = HumanLogic.KEY_SPEED['hard']
        elif char_lower in HumanLogic.EASY_KEYS:
            base_delay = HumanLogic.KEY_SPEED['easy']
        else:
            base_delay = HumanLogic.KEY_SPEED['medium']
        
        # Add Gaussian variance
        return max(0.02, random.gauss(base_delay, base_delay * 0.3))
    
    @staticmethod
    async def bezier_move(page, start_x, start_y, target_x, target_y, steps=None):
        """Move cursor along a cubic bezier curve with random control points.
        15% chance of overshooting and correcting (mimics real hand imprecision)."""
        if not steps: steps = random.randint(45, 85)
        # Brief hesitation before moving (human motor planning delay)
        await asyncio.sleep(max(0.0, random.gauss(0.25, 0.1)))
        
        # 15% chance: overshoot the target, then correct (misclick simulation)
        overshoot = random.random() > 0.85
        actual_target_x = target_x + random.randint(-25, 25) if overshoot else target_x
        actual_target_y = target_y + random.randint(-25, 25) if overshoot else target_y
        
        # Random control points create a curved, wandering path (not a straight line)
        control1_x = start_x + random.randint(-400, 400)
        control1_y = start_y + random.randint(-400, 400)
        control2_x = actual_target_x + random.randint(-400, 400)
        control2_y = actual_target_y + random.randint(-400, 400)
        
        pause_step = random.randint(int(steps * 0.3), int(steps * 0.7))
        
        for i in range(steps + 1):
            t = i / steps
            x = (1-t)**3*start_x + 3*(1-t)**2*t*control1_x + 3*(1-t)*t**2*control2_x + t**3*actual_target_x
            y = (1-t)**3*start_y + 3*(1-t)**2*t*control1_y + 3*(1-t)*t**2*control2_y + t**3*actual_target_y
            await page.mouse.move(x, y)
            
            # Micro-sleep for smooth movement
            await asyncio.sleep(random.uniform(0.002, 0.01))
            
            # Idle variance block (stopping the mouse briefly mid-flight while "reading")
            if i == pause_step and random.random() > 0.5:
                await asyncio.sleep(random.uniform(0.3, 0.8))

        if overshoot:
            # Corrective micro-movement back to intended target (Hesitation + Fix)
            await asyncio.sleep(max(0.0, random.gauss(0.15, 0.05)))
            await page.mouse.move(target_x, target_y)

    @staticmethod
    async def scroll_page(page, direction="down", intensity="medium", selector=None):
        """Scroll with variable speed, decelerating over time (humans slow down as they read).
        10% chance of a longer pause mid-scroll (simulating stopping to read)."""
        intensities = {
            "small": (100, 300, 15, 30),
            "medium": (300, 600, 25, 45),
            "large": (600, 1200, 40, 70)
        }
        min_scroll, max_scroll, min_steps, max_steps = intensities.get(intensity, intensities["medium"])
        
        total_scroll = random.randint(min_scroll, max_scroll)
        steps = random.randint(min_steps, max_steps)
        scroll_per_step = total_scroll // steps
        
        # Direction handling
        if direction == "up":
            scroll_per_step = -scroll_per_step
        
        # Optional: scroll within specific element
        scroll_target = f"document.querySelector('{selector}')" if selector else "window"
        
        for i in range(steps):
            # Variable scroll amount per step (human inconsistency)
            actual_scroll = int(scroll_per_step * random.uniform(0.7, 1.3))
            
            if selector:
                await page.evaluate(f"{scroll_target}.scrollTop += {actual_scroll}")
            else:
                await page.mouse.wheel(0, actual_scroll)
            
            # Pause between scrolls (faster at start, slower as "reading")
            progress = i / steps
            base_pause = 0.05 + (progress * 0.15)  # Slow down as we scroll
            await asyncio.sleep(random.uniform(base_pause * 0.5, base_pause * 1.5))
            
            # Occasional longer pause (reading simulation)
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.5, 1.5))
        
        logger.debug(f"[SCROLL] {direction} {total_scroll}px over {steps} steps")

    @staticmethod
    async def simulate_tab_switch(page, duration_range=(1, 3)):
        """Simulate Alt+Tab away and back. Duration is how long the user is 'away'.
        Creates realistic focus/blur events that bot detectors monitor."""
        duration = random.uniform(*duration_range)
        
        # Switch away (Alt+Tab)
        await page.keyboard.down("Alt")
        await page.keyboard.down("Tab")
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.keyboard.up("Tab")
        await page.keyboard.up("Alt")
        
        logger.debug(f"[TAB] Away for {duration:.1f}s")
        await asyncio.sleep(duration)
        
        # Switch back (Alt+Tab again)
        await page.keyboard.down("Alt")
        await page.keyboard.down("Tab")
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.keyboard.up("Tab")
        await page.keyboard.up("Alt")
        
        # Brief pause to "refocus"
        await asyncio.sleep(random.uniform(0.3, 0.7))

    @staticmethod
    async def reading_pause(page, element_selector=None, text_content=None):
        """Pause proportional to text length (200-250 WPM reading speed).
        Can extract text from a DOM element or accept it directly."""
        if element_selector:
            try:
                text = await page.evaluate(f"document.querySelector('{element_selector}')?.innerText || ''")
            except Exception:
                text = ""
        elif text_content:
            text = text_content
        else:
            text = ""
        
        # Estimate reading time (average adult: 200-250 WPM = 3-4 words/sec)
        word_count = len(text.split()) if text else random.randint(20, 100)
        reading_speed = random.uniform(2.5, 4.0)  # words per second
        base_reading_time = word_count / reading_speed
        
        # Add variance (skimming vs deep reading)
        actual_time = base_reading_time * random.uniform(0.6, 1.4)
        actual_time = max(0.5, min(actual_time, 30))  # Cap between 0.5-30s
        
        logger.debug(f"[READING] {word_count} words, pausing {actual_time:.1f}s")
        await asyncio.sleep(actual_time)

    @staticmethod
    async def coffee_break(duration_range=(10, 60)):
        """Simulate long AFK pause (10-60s). Useful between multi-page workflows."""
        duration = random.uniform(*duration_range)
        logger.info(f"[AFK] Coffee break for {duration:.1f}s")
        await asyncio.sleep(duration)

    @staticmethod
    async def inactivity_pattern(page, duration_range=(0.5, 3.0)):
        """Brief cursor freeze (0.5-3s) — human users often pause to think mid-action."""
        duration = random.uniform(*duration_range)
        # Get current mouse position
        try:
            pos = await page.evaluate("() => ({x: window.mouseX || 0, y: window.mouseY || 0})")
            x, y = pos.get('x', 100), pos.get('y', 100)
        except Exception:
            x, y = 100, 100
        
        logger.debug(f"[FREEZE] Cursor frozen at ({x}, {y}) for {duration:.1f}s")
        await asyncio.sleep(duration)

    @staticmethod
    async def type_text(page, text, typo_rate=0.02):
        """Type text character-by-character with realistic speed, shift handling,
        adjacent-key typos (typed then backspaced), and punctuation think-pauses."""
        for i, char in enumerate(text):
            # Check for shift key (capital letters or symbols)
            needs_shift = char.isupper() or char in '~!@#$%^&*()_+{}|:"<>?'
            
            # 2% chance of adjacent-key typo (reduced for shifted chars)
            actual_typo_rate = typo_rate * 0.5 if needs_shift else typo_rate
            
            if char.isalpha() and random.random() < actual_typo_rate:
                # Generate adjacent-key typo
                adjacent = HumanLogic._get_adjacent_keys(char.lower())
                typo = random.choice([k for k in adjacent if k != char.lower()]) if len(adjacent) > 1 else char.lower()
                if char.isupper():
                    typo = typo.upper()
                
                # Type the typo
                if needs_shift:
                    await page.keyboard.down("Shift")
                await page.keyboard.press(typo)
                if needs_shift:
                    await page.keyboard.up("Shift")
                
                # Realization pause (longer for more obvious mistakes)
                await asyncio.sleep(max(0.08, random.gauss(0.15, 0.05)))
                
                # Backspace
                await page.keyboard.press("Backspace")
                
                # Pause before correction
                await asyncio.sleep(max(0.1, random.gauss(0.2, 0.06)))
            
            # Type the correct character
            if needs_shift:
                await page.keyboard.down("Shift")
            await page.keyboard.press(char)
            if needs_shift:
                await page.keyboard.up("Shift")
            
            # Variable delay based on key difficulty
            delay = HumanLogic._get_typing_delay(char)
            
            # Longer pause after punctuation (think time)
            if char in '.!?;,' and i < len(text) - 1:
                delay += random.uniform(0.2, 0.5)
            
            await asyncio.sleep(delay)

async def apply_network_jitter(context):
    """Use Chrome DevTools Protocol to throttle network at the packet level.
    Models a Lagos residential ISP: 80-200ms base latency, 20Mbps down, 5Mbps up.
    10% chance of a severe congestion spike (300-800ms added, 90% bandwidth drop)."""
    client = await context.new_cdp_session(context.pages[0] if context.pages else await context.new_page())
    
    # Base conditions: typical Lagos residential fiber/4G
    latency = random.uniform(80, 200)              # Round-trip time in ms
    download_throughput = 20 * 1024 * 1024 / 8     # 20 Mbps -> bytes/sec
    upload_throughput = 5 * 1024 * 1024 / 8        # 5 Mbps -> bytes/sec
    
    if random.random() > 0.90: # 10% chance of a 'Lagos Spike' (congestion)
        latency += random.uniform(300, 800)
        download_throughput = download_throughput * 0.1 # Severe throttle
    
    await client.send('Network.enable')
    await client.send('Network.emulateNetworkConditions', {
        'offline': False,
        'downloadThroughput': download_throughput,
        'uploadThroughput': upload_throughput,
        'latency': latency
    })

async def launch_stealth_engine(mode="manual", session_name="default", typing_style="natural", 
                                channel=None, headless=False, target_url="https://bot.sannysoft.com",
                                duration=60, proxy_file=None, user_agent=None,
                                timezone_override=None, locale_override=None):
    """Main entry point. Sets up all stealth layers, launches browser, and runs in manual or auto mode.
    Manual = record a human trace. Auto = replay a trace to establish trust, then continue."""
    # Initialize session persistence (cookies + localStorage)
    session_mgr = SessionManager()
    proxy_mgr = ProxyManager(proxy_file) if proxy_file else None
    
    # Pick a coherent timezone/locale/language combo (or use CLI overrides)
    geo = GeoProfile.resolve(timezone_override, locale_override)
    logger.info(f"[GEO] Identity: {geo['region']} ({geo['timezone']}, {geo['locale']})")
    
    # In auto mode, reuse the viewport from the recorded trace for consistency.
    # In manual mode, generate a fresh random viewport.
    if mode == "auto" and os.path.exists(TRAINING_DATA_FILE):
        _, viewport_profile = GhostLogic.load_trace()
    else:
        viewport_profile = ViewportManager.generate_viewport_profile()
    
    # Fix any GPU/viewport mismatches before they get injected into the browser
    viewport_profile = FingerprintValidator.auto_correct(viewport_profile)
    
    # Pick a User-Agent that matches the viewport's GPU/screen (or use CLI override)
    if user_agent:
        selected_ua = user_agent
        logger.info(f"[UA] Using custom User-Agent")
    else:
        selected_ua = UserAgentManager.get_matching(viewport_profile)
        logger.info(f"[UA] Auto-selected: {selected_ua[:60]}...")
    
    # Final cross-check: warn about any remaining inconsistencies
    FingerprintValidator.validate(viewport_profile, geo, selected_ua)
    
    logger.info(f"[VIEWPORT] Using {viewport_profile['width']}x{viewport_profile['height']} "
                f"({viewport_profile['vendor'].split()[-1].strip('()')})")
    
    # Navigator.connection values must match network conditions.
    # If jitter is on (slow ISP), report 3g with high RTT. Otherwise, report fast 4g.
    if NETWORK_JITTER:
        connection_effective_type = '3g'
        connection_rtt = random.randint(100, 300)
        connection_downlink = round(random.uniform(1.5, 5.0), 1)
    else:
        connection_effective_type = '4g'
        connection_rtt = random.randint(20, 80)
        connection_downlink = round(random.uniform(10.0, 50.0), 1)
    
    # Randomize battery state once per session (stays constant throughout)
    battery_is_charging = random.choice([True, False])
    battery_charging = 'true' if battery_is_charging else 'false'
    battery_charging_time = 'Infinity' if not battery_is_charging else str(random.randint(1800, 7200))
    battery_discharging_time = str(random.randint(3600, 28800)) if not battery_is_charging else 'Infinity'
    battery_level = round(random.uniform(0.2, 1.0), 2)
    
    # Hex seed for generating stable fake device IDs (unique per session, not per page)
    media_device_seed = ''.join(random.choices('0123456789abcdef', k=16))
    
    # Font lists must match the OS in the User-Agent string.
    # Windows, Mac, and Linux each have distinct default font sets.
    is_windows_ua = 'Windows' in selected_ua
    is_mac_ua = 'Macintosh' in selected_ua
    if is_windows_ua:
        font_list = [
            'Arial', 'Arial Black', 'Calibri', 'Cambria', 'Cambria Math', 'Comic Sans MS',
            'Consolas', 'Constantia', 'Corbel', 'Courier New', 'Georgia', 'Impact',
            'Lucida Console', 'Lucida Sans Unicode', 'Microsoft Sans Serif', 'Palatino Linotype',
            'Segoe UI', 'Segoe UI Symbol', 'Segoe Print', 'Segoe Script', 'Tahoma',
            'Times New Roman', 'Trebuchet MS', 'Verdana', 'Webdings', 'Wingdings',
            'Yu Gothic', 'Malgun Gothic', 'Microsoft YaHei', 'SimSun', 'NSimSun',
            'Candara', 'Franklin Gothic Medium', 'Garamond', 'Sylfaen', 'MS Gothic',
            'MS PGothic', 'MS UI Gothic', 'Meiryo', 'Meiryo UI', 'PMingLiU',
            'MingLiU', 'Microsoft JhengHei', 'Leelawadee UI', 'Bahnschrift',
            'Ebrima', 'Gabriola', 'Nirmala UI', 'Sitka Text'
        ]
    elif is_mac_ua:
        font_list = [
            'Arial', 'Arial Black', 'Comic Sans MS', 'Courier New', 'Georgia',
            'Helvetica', 'Helvetica Neue', 'Impact', 'Lucida Grande', 'Monaco',
            'Palatino', 'Times New Roman', 'Trebuchet MS', 'Verdana',
            'American Typewriter', 'Andale Mono', 'Apple Chancery', 'Apple SD Gothic Neo',
            'Avenir', 'Avenir Next', 'Baskerville', 'Big Caslon', 'Bodoni 72',
            'Bradley Hand', 'Brush Script MT', 'Chalkboard', 'Chalkboard SE',
            'Cochin', 'Copperplate', 'Didot', 'Futura', 'Geneva', 'Gill Sans',
            'Hoefler Text', 'Iowan Old Style', 'Menlo', 'Optima', 'Papyrus',
            'Phosphate', 'Rockwell', 'San Francisco', 'Savoye LET', 'Seravek',
            'SignPainter', 'Skia', 'Snell Roundhand', 'STIXGeneral',
            'Sukhumvit Set', 'Superclarendon', 'Times', 'Zapfino'
        ]
    else:
        font_list = [
            'Arial', 'Comic Sans MS', 'Courier New', 'Georgia', 'Impact',
            'Times New Roman', 'Trebuchet MS', 'Verdana',
            'DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono', 'Liberation Sans',
            'Liberation Serif', 'Liberation Mono', 'Noto Sans', 'Noto Serif',
            'Noto Mono', 'Ubuntu', 'Ubuntu Mono', 'Cantarell', 'Droid Sans',
            'Droid Serif', 'Droid Sans Mono', 'Open Sans', 'Roboto',
            'Lato', 'Source Sans Pro', 'Source Code Pro', 'FreeSans',
            'FreeSerif', 'FreeMono', 'Nimbus Sans L', 'Nimbus Roman No9 L',
            'URW Gothic', 'Bitstream Vera Sans', 'Bitstream Vera Serif',
            'Bitstream Vera Sans Mono', 'Gentium', 'Inconsolata',
            'Hack', 'Fira Code', 'Fira Sans', 'PT Sans', 'PT Serif',
            'PT Mono', 'IBM Plex Sans', 'IBM Plex Serif', 'IBM Plex Mono'
        ]
    font_list_var = 'knownFonts'
    font_list_json = json.dumps(font_list)
    
    # WebGL extension lists differ by GPU. Intel integrated GPUs support fewer extensions
    # than AMD discrete GPUs. Mismatched lists are a known fingerprinting signal.
    is_intel_gpu = 'Intel' in viewport_profile.get('vendor', '')
    if is_intel_gpu:
        webgl_extensions = [
            'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
            'EXT_disjoint_timer_query', 'EXT_float_blend', 'EXT_frag_depth',
            'EXT_shader_texture_lod', 'EXT_texture_compression_bptc',
            'EXT_texture_compression_rgtc', 'EXT_texture_filter_anisotropic',
            'WEBKIT_EXT_texture_filter_anisotropic', 'EXT_sRGB',
            'KHR_parallel_shader_compile', 'OES_element_index_uint',
            'OES_fbo_render_mipmap', 'OES_standard_derivatives',
            'OES_texture_float', 'OES_texture_float_linear',
            'OES_texture_half_float', 'OES_texture_half_float_linear',
            'OES_vertex_array_object', 'WEBGL_color_buffer_float',
            'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
            'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders',
            'WEBGL_depth_texture', 'WEBGL_draw_buffers', 'WEBGL_lose_context',
            'WEBGL_multi_draw'
        ]
    else:
        webgl_extensions = [
            'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
            'EXT_disjoint_timer_query', 'EXT_float_blend', 'EXT_frag_depth',
            'EXT_shader_texture_lod', 'EXT_texture_compression_bptc',
            'EXT_texture_compression_rgtc', 'EXT_texture_filter_anisotropic',
            'WEBKIT_EXT_texture_filter_anisotropic', 'EXT_sRGB',
            'KHR_parallel_shader_compile', 'OES_element_index_uint',
            'OES_fbo_render_mipmap', 'OES_standard_derivatives',
            'OES_texture_float', 'OES_texture_float_linear',
            'OES_texture_half_float', 'OES_texture_half_float_linear',
            'OES_vertex_array_object', 'WEBGL_color_buffer_float',
            'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
            'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders',
            'WEBGL_depth_texture', 'WEBGL_draw_buffers', 'WEBGL_lose_context',
            'WEBGL_multi_draw', 'WEBGL_compressed_texture_astc',
            'EXT_color_buffer_float', 'OES_draw_buffers_indexed',
            'WEBGL_provoking_vertex'
        ]
    webgl_ext_var = 'supportedExtensions'
    webgl_extensions_json = json.dumps(webgl_extensions)
    
    try:
        async with async_playwright() as p:
            # Persistent profile dir preserves cache, history, and service workers across runs
            user_data_dir = "/mnt/chrome-profile"
            
            # Chrome launch flags for stealth and stability
            launch_args = [
                "--start-maximized",                            # Fill the screen naturally
                "--no-sandbox",                                 # Required in some environments
                "--disable-infobars",                           # Hide "Chrome is being controlled" bar
                "--disable-dev-shm-usage",                      # Prevent crashes in low-memory envs
                "--disable-webrtc",                             # Block WebRTC from leaking real IP
                "--cipher-suite-blacklist=0xc02f,0xc02b",       # Mutate TLS JA3 hash fingerprint
            ]
            
            if headless:
                launch_args.append("--headless=new")
                launch_args.append("--disable-gpu")
                logger.info("[MODE] Running in headless mode")
            
            launch_opts = {
                'user_data_dir': user_data_dir,
                'headless': headless,
                'viewport': {'width': viewport_profile['width'], 'height': viewport_profile['height']},
                'timezone_id': geo['timezone'],
                'locale': geo['locale'],
                'user_agent': selected_ua,
                'args': launch_args
            }
            
            # Use a specific Chrome/Edge channel (e.g. chrome-beta) to vary browser identity
            if channel:
                launch_opts['channel'] = channel
                logger.info(f"[CHANNEL] Using Chrome channel: {channel}")
            
            # Route all traffic through a proxy if one is available
            if proxy_mgr and proxy_mgr.has_proxies():
                proxy_url = proxy_mgr.get_random()
                proxy_config = ProxyManager.parse_proxy_url(proxy_url)
                if proxy_config:
                    launch_opts['proxy'] = proxy_config
                    logger.info(f"[PROXY] Using proxy: {proxy_config['server']}")
            
            # Try up to 3 times. On final failure with proxy, retry without it.
            context = None
            for attempt in range(3):
                try:
                    context = await p.chromium.launch_persistent_context(**launch_opts)
                    break
                except Exception as e:
                    logger.warning(f"[LAUNCH] Attempt {attempt + 1}/3 failed: {e}")
                    if attempt == 2:
                        # Last attempt: try without proxy
                        if 'proxy' in launch_opts:
                            logger.warning("[LAUNCH] Retrying without proxy...")
                            del launch_opts['proxy']
                            try:
                                context = await p.chromium.launch_persistent_context(**launch_opts)
                            except Exception as e2:
                                logger.error(f"[LAUNCH] Failed to launch browser: {e2}")
                                raise
                        else:
                            raise
                    await asyncio.sleep(1)
            
            if not context:
                logger.error("[LAUNCH] Could not create browser context")
                return
            
            # Restore cookies from previous sessions (maintains login state)
            await session_mgr.load_session(context, session_name)

            # 0. Background noise: visit a random Wikipedia page to create realistic
            # browsing history and DNS/TLS cache entries before the main navigation
            logger.info("[STEALTH] Pre-warming browser history with background navigation...")
            bg_page = context.pages[0] if context.pages else await context.new_page()
            asyncio.create_task(bg_page.goto("https://en.wikipedia.org/wiki/Special:Random"))

            page = await context.new_page()

            # 1. Network throttling via CDP (if enabled). Must be applied before page navigation.
            if NETWORK_JITTER:
                logger.info("[NETWORK] Jitter Active: Simulating packet-level unstable connection via CDP.")
                try:
                    await apply_network_jitter(context)
                except Exception as e:
                    logger.warning(f"[NETWORK] Failed to apply jitter: {e}")

            # 2. Master init script: all browser API spoofing injected before any page JS runs.
            #    Covers WebGL, Canvas, Navigator, Plugins, Audio, Permissions, Chrome runtime,
            #    CDP evasion, Connection, Battery, MediaDevices, Fonts, and WebGL extensions.
            await page.add_init_script(f"""
            // --- WebGL Vendor/Renderer Masking ---
            const vendor = '{viewport_profile['vendor']}';
            const renderer = '{viewport_profile['renderer']}';

            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {{
                if (p === 37445) return vendor;
                if (p === 37446) return renderer;
                return getParameter.apply(this, arguments);
            }};

            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(p) {{
                if (p === 37445) return vendor;
                if (p === 37446) return renderer;
                return getParameter2.apply(this, arguments);
            }};

            // --- Canvas Fingerprint Noise ---
            // Add ±1 to sparse pixels so canvas hashes change per session
            const wrap = (obj, prop, wrapper) => {{
                const fn = obj[prop];
                obj[prop] = function () {{ return wrapper.apply(this, [fn.bind(this), ...arguments]); }};
            }};
            wrap(CanvasRenderingContext2D.prototype, 'getImageData', (fn, ...args) => {{
                const img = fn(...args);
                for (let i = 0; i < img.data.length; i += 1024) {{
                    img.data[i] = img.data[i] + (Math.random() > 0.5 ? 1 : -1);
                }}
                return img;
            }});

            // --- Navigator Property Overrides ---
            // Hide webdriver flag, set languages/concurrency/memory to match our profile
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            Object.defineProperty(navigator, 'languages', {{ get: () => {json.dumps(geo['languages'])} }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {viewport_profile['hardware_concurrency']} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {viewport_profile['device_memory']} }});
            
            // Also patch the prototype chain so iframes can't detect webdriver via __proto__
            const proto = navigator.__proto__;
            Object.defineProperty(proto, 'webdriver', {{ get: () => undefined }});
            
            // Wrap Function.toString so our patched functions still report 'native code'
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = function() {{
                if (this === WebGLRenderingContext.prototype.getParameter ||
                    this === WebGL2RenderingContext.prototype.getParameter ||
                    this === AudioBuffer.prototype.getChannelData) {{
                    return originalToString.call(this).replace(/native code/, 'native code');
                }}
                return originalToString.call(this);
            }};

            // --- Plugin Simulation ---
            // Headless Chrome has 0 plugins by default; real Chrome always has the PDF plugin
            Object.defineProperty(navigator, 'plugins', {{
                get: () => {{
                    const pluginArray = Object.create(PluginArray.prototype);
                    const pdfPlugin = Object.create(Plugin.prototype);
                    Object.defineProperties(pdfPlugin, {{
                        name: {{ value: 'Chrome PDF Plugin' }},
                        filename: {{ value: 'internal-pdf-viewer' }},
                        description: {{ value: 'Portable Document Format' }},
                        length: {{ value: 1 }}
                    }});
                    Object.defineProperty(pluginArray, 0, {{ value: pdfPlugin }});
                    Object.defineProperty(pluginArray, 'length', {{ value: 1 }});
                    return pluginArray;
                }}
            }});

            // --- AudioContext Fingerprint Noise ---
            // Tiny noise on audio buffer data prevents hash-based audio fingerprinting
            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function() {{
                const results_1 = originalGetChannelData.apply(this, arguments);
                for (let i = 0; i < results_1.length; i += 100) {{
                    results_1[i] = results_1[i] + (Math.random() * 0.0000001);
                }}
                return results_1;
            }};

            // --- Permissions API ---
            // Real permission queries take 500-2000ms; instant responses flag automation
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = function(parameters) {{
                const delay = Math.floor(Math.random() * 1500) + 500;  // 500-2000ms delay
                return new Promise((resolve) => {{
                    setTimeout(() => {{
                        if (parameters.name === 'notifications') {{
                            resolve({{ state: Notification.permission }});
                        }} else {{
                            resolve(originalQuery(parameters));
                        }}
                    }}, delay);
                }});
            }};

            // --- Chrome Object Simulation ---
            // Headless Chrome lacks window.chrome; its absence is a top detection signal
            window.chrome = {{
                app: {{ isInstalled: false }},
                webstore: {{ onInstall: {{}}, onDownloadProgress: {{}} }},
                runtime: {{ PlatformOs: 'win', PlatformArch: 'x86-64', PlatformNaclArch: 'x86-64', RequestUpdateCheckStatus: 'throttled', OnInstalledReason: 'install', OnRestartRequiredReason: 'app_update' }}
            }};
            window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {{ supportsFiber: true, renderers: new Map() }};
            
            // --- CDP / DevTools Evasion ---
            // Block common checks for __commandLineAPI and chrome.devtools
            const cdpProps = ['__commandLineAPI', 'cdp', 'chrome.devtools'];
            cdpProps.forEach(prop => {{
                Object.defineProperty(window, prop, {{
                    get: () => undefined,
                    set: () => {{}},
                    configurable: false
                }});
            }});
            
            // Suppress console.debug messages that mention DevTools (used by some detectors)
            const originalNotify = console.debug;
            console.debug = function(...args) {{
                if (args.length > 0 && typeof args[0] === 'string' && args[0].includes('DevTools')) return;
                return originalNotify.apply(this, args);
            }};

            // --- Navigator.connection API ---
            // Must match NETWORK_JITTER setting: slow network = 3g, fast = 4g
            const connectionInfo = {{
                effectiveType: '{connection_effective_type}',
                rtt: {connection_rtt},
                downlink: {connection_downlink},
                saveData: false,
                type: 'wifi'
            }};
            const connectionProto = {{
                get effectiveType() {{ return connectionInfo.effectiveType; }},
                get rtt() {{ return connectionInfo.rtt; }},
                get downlink() {{ return connectionInfo.downlink; }},
                get saveData() {{ return connectionInfo.saveData; }},
                get type() {{ return connectionInfo.type; }},
                addEventListener: function() {{}},
                removeEventListener: function() {{}},
                get onchange() {{ return null; }},
                set onchange(v) {{}}
            }};
            Object.setPrototypeOf(connectionProto, NetworkInformation.prototype || Object.prototype);
            Object.defineProperty(navigator, 'connection', {{
                get: () => connectionProto,
                configurable: true
            }});

            // --- Battery API ---
            // Returns consistent values for the entire session duration
            const batteryInfo = {{
                charging: {battery_charging},
                chargingTime: {battery_charging_time},
                dischargingTime: {battery_discharging_time},
                level: {battery_level},
                addEventListener: function() {{}},
                removeEventListener: function() {{}},
                get onchargingchange() {{ return null; }},
                set onchargingchange(v) {{}},
                get onchargingtimechange() {{ return null; }},
                set onchargingtimechange(v) {{}},
                get ondischargingtimechange() {{ return null; }},
                set ondischargingtimechange(v) {{}},
                get onlevelchange() {{ return null; }},
                set onlevelchange(v) {{}}
            }};
            if (navigator.getBattery) {{
                navigator.getBattery = function() {{
                    return Promise.resolve(batteryInfo);
                }};
            }}

            // --- MediaDevices API ---
            // Spoofs enumerateDevices() with 5 realistic devices (2 audio in, 2 audio out, 1 webcam)
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                const fakeDevices = [
                    {{ deviceId: '{media_device_seed}a1', kind: 'audioinput', label: 'Default - Built-in Audio', groupId: 'grp1' }},
                    {{ deviceId: '{media_device_seed}a2', kind: 'audioinput', label: 'Communications - Built-in Audio', groupId: 'grp1' }},
                    {{ deviceId: '{media_device_seed}o1', kind: 'audiooutput', label: 'Default - Speakers (Realtek High Definition Audio)', groupId: 'grp2' }},
                    {{ deviceId: '{media_device_seed}o2', kind: 'audiooutput', label: 'Communications - Speakers', groupId: 'grp2' }},
                    {{ deviceId: '{media_device_seed}v1', kind: 'videoinput', label: 'HD Webcam C270 (046d:0825)', groupId: 'grp3' }}
                ];
                const origEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
                navigator.mediaDevices.enumerateDevices = function() {{
                    return Promise.resolve(fakeDevices.map(d => ({{
                        deviceId: d.deviceId,
                        kind: d.kind,
                        label: d.label,
                        groupId: d.groupId,
                        toJSON: function() {{ return this; }}
                    }})));
                }};
            }}

            // --- Font Enumeration ---
            // Override document.fonts.check() to report only fonts matching the spoofed OS
            const {font_list_var} = {font_list_json};
            if (document.fonts && document.fonts.check) {{
                const origCheck = document.fonts.check.bind(document.fonts);
                document.fonts.check = function(font, text) {{
                    const fontFamily = font.split(',')[0].replace(/['"\\s]/g, '').toLowerCase();
                    const isKnown = {font_list_var}.some(f => f.toLowerCase() === fontFamily);
                    if (isKnown) return true;
                    try {{ return origCheck(font, text); }} catch(e) {{ return false; }}
                }};
            }}

            // --- WebGL Extensions ---
            // Return a GPU-matched extension list; Intel has fewer than AMD
            const {webgl_ext_var} = {webgl_extensions_json};
            const origGetSupportedExtensions = WebGLRenderingContext.prototype.getSupportedExtensions;
            WebGLRenderingContext.prototype.getSupportedExtensions = function() {{
                return {webgl_ext_var};
            }};
            const origGetSupportedExtensions2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
            WebGL2RenderingContext.prototype.getSupportedExtensions = function() {{
                return {webgl_ext_var};
            }};
            const origGetExtension = WebGLRenderingContext.prototype.getExtension;
            WebGLRenderingContext.prototype.getExtension = function(name) {{
                if ({webgl_ext_var}.includes(name)) {{
                    try {{ return origGetExtension.call(this, name); }} catch(e) {{ return {{}}; }}
                }}
                return null;
            }};
            const origGetExtension2 = WebGL2RenderingContext.prototype.getExtension;
            WebGL2RenderingContext.prototype.getExtension = function(name) {{
                if ({webgl_ext_var}.includes(name)) {{
                    try {{ return origGetExtension2.call(this, name); }} catch(e) {{ return {{}}; }}
                }}
                return null;
            }};
            """)

            # Override window.screen properties so JS-visible dimensions match our viewport
            viewport_script = ViewportManager.get_viewport_init_script(viewport_profile)
            await page.add_init_script(viewport_script)

            if mode == "manual":
                # 3. Recording Mode: capture all user input events with ms timestamps.
                #    The trace is saved to disk and can be replayed in auto mode.
                logger.info("--- [REC] MANUAL MODE ACTIVE ---")
                trace = []
                await page.expose_function("recordEvent", lambda e: trace.append(e))
                
                # Inject event listeners for all input types (mouse, keyboard, wheel, focus)
                await page.add_init_script("""
                    const handler = (type, e) => {
                        const evt = {
                            type: type, 
                            t: Date.now()
                        };
                        
                        if (e instanceof MouseEvent) {
                            evt.x = e.clientX;
                            evt.y = e.clientY;
                            evt.button = e.button;
                        }
                        if (e instanceof KeyboardEvent) {
                            evt.key = e.key;
                        }
                        if (e instanceof WheelEvent) {
                            evt.deltaX = e.deltaX;
                            evt.deltaY = e.deltaY;
                        }
                        
                        window.recordEvent(evt);
                    };
                    
                    window.addEventListener('mousemove', e => handler('mousemove', e));
                    window.addEventListener('mousedown', e => handler('mousedown', e));
                    window.addEventListener('mouseup', e => handler('mouseup', e));
                    window.addEventListener('wheel', e => handler('wheel', e), {passive: true});
                    window.addEventListener('keydown', e => handler('keydown', e));
                    window.addEventListener('keyup', e => handler('keyup', e));
                    window.addEventListener('focus', e => handler('focus', e), true);
                    window.addEventListener('blur', e => handler('blur', e), true);
                """)
                
                try:
                    await page.goto(target_url, timeout=30000)
                except Exception as e:
                    logger.error(f"[NAV] Failed to navigate to {target_url}: {e}")
                    logger.info("[NAV] Retrying with extended timeout...")
                    try:
                        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
                    except Exception as e2:
                        logger.error(f"[NAV] Navigation failed: {e2}")
                logger.info(f"Recording started. Interact naturally for {duration} seconds...")
                await asyncio.sleep(duration) 
                GhostLogic.save_trace(trace, viewport_profile)
            
            else:
                # 4. Playback Mode: replay the recorded trace to build trust,
                #    then continue with synthetic human behaviors.
                logger.info("--- [PLAY] AUTOMATED MODE ACTIVE ---")
                trace_data, loaded_viewport = GhostLogic.load_trace()
                
                # Navigate first, then restore localStorage (requires same-origin context)
                try:
                    await page.goto(target_url, timeout=30000)
                except Exception as e:
                    logger.error(f"[NAV] Failed to navigate to {target_url}: {e}")
                    try:
                        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
                    except Exception as e2:
                        logger.error(f"[NAV] Navigation failed: {e2}")
                await session_mgr.restore_storage_for_page(page, session_name, page_index=0)
                
                # Set typo probability based on CLI typing style
                typing_rates = {
                    'natural': 0.02,  # 2% typo chance (realistic)
                    'fast': 0.01,     # 1% (fewer typos at speed)
                    'slow': 0.04      # 4% (more mistakes when cautious)
                }
                typo_rate = typing_rates.get(typing_style, 0.02)
                logger.info(f"[TYPING] Using '{typing_style}' style (typo rate: {typo_rate})")
                
                last_x, last_y = 200, 200
                if trace_data:
                    last_x, last_y = await GhostLogic.playback(page, trace_data)
                else:
                    logger.warning("[WARNING] No recording found. Falling back to Bezier movement.")
                    await HumanLogic.bezier_move(page, start_x=last_x, start_y=last_y, target_x=800, target_y=600)
                
                logger.info("[STATUS] Playback sequence complete.")
                
                # Post-playback: add some synthetic behavior to extend the session naturally
                logger.debug("[DEMO] Demonstrating enhanced human behaviors...")
                await HumanLogic.scroll_page(page, direction="down", intensity="small")
                await HumanLogic.reading_pause(page, text_content="Sample text for reading simulation calculation.")

            # Graceful cooldown: wait before closing so any pending requests complete
            logger.info("Engine shutting down in 5 seconds...")
            
            # Persist session state (cookies + localStorage) for future runs
            if mode == "auto":
                await session_mgr.save_session(context, session_name)
            
            await asyncio.sleep(5)
            await context.close()
    
    except KeyboardInterrupt:
        # Ctrl+C: save session before exiting so progress isn't lost
        logger.info("[SHUTDOWN] Interrupted by user. Saving session...")
        try:
            if context:
                await session_mgr.save_session(context, session_name)
                await context.close()
        except Exception:
            pass
    except Exception as e:
        # Unexpected crash: close browser cleanly, then re-raise for caller to handle
        logger.error(f"[FATAL] Unhandled error: {e}")
        try:
            if context:
                await context.close()
        except Exception:
            pass
        raise

if __name__ == "__main__":
    # CLI interface: parse all flags, configure logging, then launch the engine
    parser = argparse.ArgumentParser(description="DarkMatter Stealth Automation Engine")
    parser.add_argument("--mode", choices=["manual", "auto"], default="manual", help="Run mode: manual (record profile) or auto (playback bot)")
    parser.add_argument("--session-name", default="default", help="Name for session persistence (cookies/localStorage)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO", help="Logging verbosity level")
    parser.add_argument("--typing-style", choices=["natural", "fast", "slow"], default="natural", help="Typing speed profile")
    parser.add_argument("--channel", choices=["chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"], default=None, help="Browser channel to use (requires Playwright channel installation)")
    parser.add_argument("--headless", action="store_true", default=False, help="Run in headless mode")
    parser.add_argument("--url", default="https://bot.sannysoft.com", help="Target URL (default: https://bot.sannysoft.com)")
    parser.add_argument("--duration", type=int, default=60, help="Recording duration in seconds (default: 60)")
    parser.add_argument("--proxy-file", default=None, help="Path to proxy list file (one proxy per line)")
    parser.add_argument("--user-agent", default=None, help="Custom User-Agent string (overrides auto-selection)")
    parser.add_argument("--timezone", default=None, help="Override timezone (e.g. America/New_York)")
    parser.add_argument("--locale", default=None, help="Override locale (e.g. en-US)")
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('darkmatter.log')
        ]
    )
    
    asyncio.run(launch_stealth_engine(
        mode=args.mode, 
        session_name=args.session_name, 
        typing_style=args.typing_style, 
        channel=args.channel,
        headless=args.headless,
        target_url=args.url,
        duration=args.duration,
        proxy_file=args.proxy_file,
        user_agent=args.user_agent,
        timezone_override=args.timezone,
        locale_override=args.locale
    ))