#Script to avoid bot-detection in browser

import asyncio
import random
import json
import os
import math
import time
import argparse
from playwright.async_api import async_playwright

# True  = Simulate Lagos ISP instability (latency spikes)
# False = Full speed connection
NETWORK_JITTER = True    

TRAINING_DATA_FILE = "human_behavior_profile.json"

class GhostLogic:
    """Manages the recording and replaying of authentic human biometric traces."""
    @staticmethod
    def save_trace(data):
        with open(TRAINING_DATA_FILE, "w") as f:
            json.dump(data, f)
        print(f"--- [BIO-SYNC] Profile Saved: {len(data)} nodes ---")

    @staticmethod
    def load_trace():
        if os.path.exists(TRAINING_DATA_FILE):
            with open(TRAINING_DATA_FILE, "r") as f:
                return json.load(f)
        return []

    @staticmethod
    async def playback(page, trace_data):
        """Replays biological traces with millisecond precision."""
        if not trace_data: return
        print(f"[PLAYBACK] Replaying {len(trace_data)} biometric nodes...")
        
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
    async def type_text(page, text):
        """Simulates human typing with variable speed and occasional typos/backspaces."""
        qwerty = "qwertyuiopasdfghjklzxcvbnm"
        for char in text:
            # 2% chance of generating a typo then backspacing
            if char.isalpha() and random.random() < 0.02:
                idx = qwerty.find(char.lower())
                typo = qwerty[idx - 1] if idx > 0 else qwerty[idx + 1] if idx < len(qwerty)-1 else char
                
                await page.keyboard.press(typo)
                await asyncio.sleep(max(0.0, random.gauss(0.12, 0.04))) # Realize mistake
                await page.keyboard.press("Backspace")
                await asyncio.sleep(max(0.0, random.gauss(0.15, 0.05))) # Pause before correct key

            await page.keyboard.press(char)
            # Gaussian delay between keystrokes (mean 80ms)
            await asyncio.sleep(max(0.0, random.gauss(0.08, 0.03)))

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

async def launch_stealth_engine(mode="manual"):
    async with async_playwright() as p:
        user_data_dir = "/mnt/chrome-profile"
        # Let Playwright derive its native UA to ensure perfect browser matching 
        # (critical for JS engine signatures)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={'width': 1920, 'height': 1080},
            timezone_id="Africa/Lagos",
            locale="en-GB",
            args=[
                "--start-maximized",
                "--no-sandbox", # User requested to keep
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-webrtc", # Prevent WebRTC IP leaks
                "--cipher-suite-blacklist=0xc02f,0xc02b", # TLS Ja3 Hash Mutation
            ]
        )

        # 0. Background noise / Pre-warming
        print("[STEALTH] Injecting background noise and pre-warming history...")
        bg_page = context.pages[0] if context.pages else await context.new_page()
        # Non-blocking background navigation to standard site to build realistic history / network noise
        asyncio.create_task(bg_page.goto("https://en.wikipedia.org/wiki/Special:Random"))

        page = await context.new_page()

        # 1. Network Jitter Logic (If enabled)
        if NETWORK_JITTER:
            print("[NETWORK] Jitter Active: Simulating packet-level unstable connection via CDP.")
            await apply_network_jitter(context)

        # 2. Deep Integrity Injection (WebGL, Canvas Noise, Navigator)
        await page.add_init_script("""
            // Dynamic WebGL Masking for Cross-Signal Coherence (Linux/Win/Mac)
            const isWindows = navigator.userAgent.includes('Windows');
            const vendor = isWindows ? 'Google Inc. (Intel)' : 'Google Inc. (AMD)';
            const renderer = isWindows 
                ? 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)'
                : 'ANGLE (AMD, AMD Radeon Graphics (RADV RENOIR) OpenGL Engine)';

            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return vendor;
                if (p === 37446) return renderer;
                return getParameter.apply(this, arguments);
            };

            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return vendor;
                if (p === 37446) return renderer;
                return getParameter2.apply(this, arguments);
            };

            // Canvas Noise Injection
            const wrap = (obj, prop, wrapper) => {
                const fn = obj[prop];
                obj[prop] = function () { return wrapper.apply(this, [fn.bind(this), ...arguments]); };
            };
            wrap(CanvasRenderingContext2D.prototype, 'getImageData', (fn, ...args) => {
                const img = fn(...args);
                for (let i = 0; i < img.data.length; i += 1024) {
                    img.data[i] = img.data[i] + (Math.random() > 0.5 ? 1 : -1);
                }
                return img;
            });

            // Navigator/Shields
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // Plausible Plugin Array Simulation
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const pluginArray = Object.create(PluginArray.prototype);
                    const pdfPlugin = Object.create(Plugin.prototype);
                    Object.defineProperties(pdfPlugin, {
                        name: { value: 'Chrome PDF Plugin' },
                        filename: { value: 'internal-pdf-viewer' },
                        description: { value: 'Portable Document Format' },
                        length: { value: 1 }
                    });
                    Object.defineProperty(pluginArray, 0, { value: pdfPlugin });
                    Object.defineProperty(pluginArray, 'length', { value: 1 });
                    return pluginArray;
                }
            });

            // AudioContext Noise Injection
            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function() {
                const results_1 = originalGetChannelData.apply(this, arguments);
                for (let i = 0; i < results_1.length; i += 100) {
                    results_1[i] = results_1[i] + (Math.random() * 0.0000001);
                }
                return results_1;
            };

            // Permissions API spoofing
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = parameters => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Chrome Runtime & Extension Noise
            window.chrome = {
                app: { isInstalled: false },
                webstore: { onInstall: {}, onDownloadProgress: {} },
                runtime: { PlatformOs: 'win', PlatformArch: 'x86-64', PlatformNaclArch: 'x86-64', RequestUpdateCheckStatus: 'throttled', OnInstalledReason: 'install', OnRestartRequiredReason: 'app_update' }
            };
            window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber: true, renderers: new Map() };
        """)

        if mode == "manual":
            # 3. Recording Mode (Manual)
            print("--- [REC] MANUAL MODE ACTIVE ---")
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
            print("Recording started. Interact naturally for 60 seconds...")
            await asyncio.sleep(60) 
            GhostLogic.save_trace(trace)
        
        else:
            # 4. Playback Mode (Automated)
            print("--- [PLAY] AUTOMATED MODE ACTIVE ---")
            trace_data = GhostLogic.load_trace()
            await page.goto("https://bot.sannysoft.com")
            
            last_x, last_y = 200, 200
            if trace_data:
                last_x, last_y = await GhostLogic.playback(page, trace_data)
            else:
                print("[WARNING] No recording found. Falling back to Bezier movement.")
                await HumanLogic.bezier_move(page, start_x=last_x, start_y=last_y, target_x=800, target_y=600)
            
            print("[STATUS] Playback sequence complete.")

        print("Engine shutting down in 5 seconds...")
        await asyncio.sleep(5)
        await context.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DarkMatter Stealth Automation Engine")
    parser.add_argument("--mode", choices=["manual", "auto"], default="manual", help="Run mode: manual (record profile) or auto (playback bot)")
    args = parser.parse_args()
    
    asyncio.run(launch_stealth_engine(mode=args.mode))