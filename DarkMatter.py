#Script to avoid bot-detection in browser

import asyncio
import random
import json
import os
import math
import time
import argparse
import logging
from playwright.async_api import async_playwright

# Logging setup
logger = logging.getLogger('DarkMatter')

# True  = Simulate Lagos ISP instability (latency spikes)
# False = Full speed connection
NETWORK_JITTER = True    

TRAINING_DATA_FILE = "human_behavior_profile.json"
SESSIONS_DIR = "sessions"

# Common desktop resolutions with realistic market share
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
    """Manages viewport randomization and device consistency."""
    
    @staticmethod
    def generate_viewport_profile():
        """Generate a consistent viewport profile with matching hardware signature."""
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
        """Generate JavaScript to inject consistent viewport properties."""
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
    """Manages session persistence (cookies and localStorage)."""
    
    def __init__(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
    
    def _get_cookie_path(self, profile_name):
        return os.path.join(SESSIONS_DIR, f"{profile_name}_cookies.json")
    
    def _get_storage_path(self, profile_name):
        return os.path.join(SESSIONS_DIR, f"{profile_name}_storage.json")
    
    async def save_session(self, context, profile_name):
        """Save cookies and storage state."""
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
        """Load cookies and storage state."""
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
        """Restore localStorage for a specific page after navigation."""
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

class GhostLogic:
    """Manages the recording and replaying of authentic human biometric traces."""
    @staticmethod
    def save_trace(data, viewport_profile=None):
        """Save trace data with viewport profile for consistency."""
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
        """Replays biological traces with millisecond precision."""
        if not trace_data: 
            logger.warning("[PLAYBACK] No trace data provided")
            return
        logger.info(f"[PLAYBACK] Replaying {len(trace_data)} biometric nodes...")
        
        trace_start_time = trace_data[0]['t']
        playback_start_time = time.time() * 1000 # Standardize to ms
        
        last_x, last_y = 200, 200 # Default fallback
        
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
                # Model human reaction latency distributions (Gaussian clusters)
                # Mean latency ~120ms, stddev ~30ms
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
    """Synthetic behavior for non-replayed automated tasks (fallback)."""
    
    # QWERTY keyboard layout for adjacent-key typo simulation
    QWERTY_LAYOUT = [
        ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
        ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']'],
        ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'"],
        ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/']
    ]
    
    # Typing speed per key difficulty (mean delay in seconds)
    KEY_SPEED = {
        'easy': 0.06,    # asdf home row
        'medium': 0.08,  # nearby keys
        'hard': 0.12,    # stretch keys (q, z, p)
    }
    
    HARD_KEYS = {'q', 'z', 'p', '1', '0', '-', '=', '[', ']', ';', "'", ',', '.', '/'}
    EASY_KEYS = {'a', 's', 'd', 'f', 'j', 'k', 'l'}
    
    @staticmethod
    def _get_adjacent_keys(char):
        """Get adjacent keys on QWERTY keyboard for realistic typos."""
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
        """Get realistic typing delay based on key difficulty."""
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
        if not steps: steps = random.randint(45, 85)
        # Gaussian distribution for hesitate-before-move
        await asyncio.sleep(max(0.0, random.gauss(0.25, 0.1)))
        
        # Decide if we overshoot (misclick simulation)
        overshoot = random.random() > 0.85
        actual_target_x = target_x + random.randint(-25, 25) if overshoot else target_x
        actual_target_y = target_y + random.randint(-25, 25) if overshoot else target_y
        
        # Cubic bezier for more human-like "wandering" (DOM avoidance simulation)
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
        """Simulate human-like scrolling with variable speed and pauses."""
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
        """Simulate Alt+Tab switching away and back."""
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
        """Pause to simulate reading content. Duration based on text density."""
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
        """Simulate extended away-from-keyboard time."""
        duration = random.uniform(*duration_range)
        logger.info(f"[AFK] Coffee break for {duration:.1f}s")
        await asyncio.sleep(duration)

    @staticmethod
    async def inactivity_pattern(page, duration_range=(0.5, 3.0)):
        """Simulate brief inactivity mid-task (cursor freeze)."""
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
        """Simulates human typing with adjacent-key typos and variable speed."""
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
    """Simulates Lagos-specific network latency and jitter via CDP (packet level)."""
    client = await context.new_cdp_session(context.pages[0] if context.pages else await context.new_page())
    
    # Base latency (80ms - 200ms), 20Mbps down, 5Mbps up
    latency = random.uniform(80, 200)
    download_throughput = 20 * 1024 * 1024 / 8
    upload_throughput = 5 * 1024 * 1024 / 8
    
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

async def launch_stealth_engine(mode="manual", session_name="default", typing_style="natural", channel=None):
    # Initialize session manager
    session_mgr = SessionManager()
    
    # Load or generate viewport profile
    if mode == "auto" and os.path.exists(TRAINING_DATA_FILE):
        _, viewport_profile = GhostLogic.load_trace()
    else:
        viewport_profile = ViewportManager.generate_viewport_profile()
    
    logger.info(f"[VIEWPORT] Using {viewport_profile['width']}x{viewport_profile['height']} "
                f"({viewport_profile['vendor'].split()[-1].strip('()')})")
    
    async with async_playwright() as p:
        user_data_dir = "/mnt/chrome-profile"
        # Let Playwright derive its native UA to ensure perfect browser matching 
        # (critical for JS engine signatures)
        # Prepare launch options
        launch_opts = {
            'user_data_dir': user_data_dir,
            'headless': False,
            'viewport': {'width': viewport_profile['width'], 'height': viewport_profile['height']},
            'timezone_id': "Africa/Lagos",
            'locale': "en-GB",
            'args': [
                "--start-maximized",
                "--no-sandbox", # User requested to keep
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-webrtc", # Prevent WebRTC IP leaks
                "--cipher-suite-blacklist=0xc02f,0xc02b", # TLS Ja3 Hash Mutation
            ]
        }
        
        # Add channel if specified (chrome, chrome-beta, chrome-dev, chrome-canary)
        if channel:
            launch_opts['channel'] = channel
            logger.info(f"[CHANNEL] Using Chrome channel: {channel}")
        
        context = await p.chromium.launch_persistent_context(**launch_opts)
        
        # Load session cookies if available
        await session_mgr.load_session(context, session_name)

        # 0. Background noise / Pre-warming
        logger.info("[STEALTH] Injecting background noise and pre-warming history...")
        bg_page = context.pages[0] if context.pages else await context.new_page()
        # Non-blocking background navigation to standard site to build realistic history / network noise
        asyncio.create_task(bg_page.goto("https://en.wikipedia.org/wiki/Special:Random"))

        page = await context.new_page()

        # 1. Network Jitter Logic (If enabled)
        if NETWORK_JITTER:
            logger.info("[NETWORK] Jitter Active: Simulating packet-level unstable connection via CDP.")
            await apply_network_jitter(context)

        # 2. Deep Integrity Injection (WebGL, Canvas Noise, Navigator, Enhanced Detection Evasion)
        await page.add_init_script(f"""
            // Dynamic WebGL Masking - Use viewport profile values
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

            // Canvas Noise Injection
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

            // Navigator/Shields with iframe protection
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {viewport_profile['hardware_concurrency']} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {viewport_profile['device_memory']} }});
            
            // Deep WebDriver protection - prevent iframe traversal detection
            const proto = navigator.__proto__;
            Object.defineProperty(proto, 'webdriver', {{ get: () => undefined }});
            
            // Protect against toString detection on prototypes
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = function() {{
                if (this === WebGLRenderingContext.prototype.getParameter ||
                    this === WebGL2RenderingContext.prototype.getParameter ||
                    this === AudioBuffer.prototype.getChannelData) {{
                    return originalToString.call(this).replace(/native code/, 'native code');
                }}
                return originalToString.call(this);
            }};

            // Plausible Plugin Array Simulation
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

            // AudioContext Noise Injection
            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function() {{
                const results_1 = originalGetChannelData.apply(this, arguments);
                for (let i = 0; i < results_1.length; i += 100) {{
                    results_1[i] = results_1[i] + (Math.random() * 0.0000001);
                }}
                return results_1;
            }};

            // Permissions API with realistic response delays
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

            // Chrome Runtime & Extension Noise
            window.chrome = {{
                app: {{ isInstalled: false }},
                webstore: {{ onInstall: {{}}, onDownloadProgress: {{}} }},
                runtime: {{ PlatformOs: 'win', PlatformArch: 'x86-64', PlatformNaclArch: 'x86-64', RequestUpdateCheckStatus: 'throttled', OnInstalledReason: 'install', OnRestartRequiredReason: 'app_update' }}
            }};
            window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {{ supportsFiber: true, renderers: new Map() }};
            
            // CDP / DevTools detection evasion
            // Prevent detection via Runtime.enable checks
            const cdpProps = ['__commandLineAPI', 'cdp', 'chrome.devtools'];
            cdpProps.forEach(prop => {{
                Object.defineProperty(window, prop, {{
                    get: () => undefined,
                    set: () => {{}},
                    configurable: false
                }});
            }});
            
            // Hide notification of DevTools open
            const originalNotify = console.debug;
            console.debug = function(...args) {{
                if (args.length > 0 && typeof args[0] === 'string' && args[0].includes('DevTools')) return;
                return originalNotify.apply(this, args);
            }};
        """)

        # Add viewport consistency script to all pages
        viewport_script = ViewportManager.get_viewport_init_script(viewport_profile)
        await page.add_init_script(viewport_script)

        if mode == "manual":
            # 3. Recording Mode (Manual)
            logger.info("--- [REC] MANUAL MODE ACTIVE ---")
            trace = []
            await page.expose_function("recordEvent", lambda e: trace.append(e))
            
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
            
            await page.goto("https://bot.sannysoft.com")
            logger.info("Recording started. Interact naturally for 60 seconds...")
            await asyncio.sleep(60) 
            GhostLogic.save_trace(trace, viewport_profile)
        
        else:
            # 4. Playback Mode (Automated)
            logger.info("--- [PLAY] AUTOMATED MODE ACTIVE ---")
            trace_data, loaded_viewport = GhostLogic.load_trace()
            
            # Restore localStorage after navigation
            await page.goto("https://bot.sannysoft.com")
            await session_mgr.restore_storage_for_page(page, session_name, page_index=0)
            
            # Configure typing style
            typing_rates = {
                'natural': 0.02,
                'fast': 0.01,
                'slow': 0.04
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
            
            # Demonstrate new HumanLogic features
            logger.debug("[DEMO] Demonstrating enhanced human behaviors...")
            await HumanLogic.scroll_page(page, direction="down", intensity="small")
            await HumanLogic.reading_pause(page, text_content="Sample text for reading simulation calculation.")

        logger.info("Engine shutting down in 5 seconds...")
        
        # Save session before closing
        if mode == "auto":
            await session_mgr.save_session(context, session_name)
        
        await asyncio.sleep(5)
        await context.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DarkMatter Stealth Automation Engine")
    parser.add_argument("--mode", choices=["manual", "auto"], default="manual", help="Run mode: manual (record profile) or auto (playback bot)")
    parser.add_argument("--session-name", default="default", help="Name for session persistence (cookies/localStorage)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO", help="Logging verbosity level")
    parser.add_argument("--typing-style", choices=["natural", "fast", "slow"], default="natural", help="Typing speed profile")
    parser.add_argument("--channel", choices=["chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"], default=None, help="Browser channel to use (requires Playwright channel installation)")
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
    
    asyncio.run(launch_stealth_engine(mode=args.mode, session_name=args.session_name, typing_style=args.typing_style, channel=args.channel))