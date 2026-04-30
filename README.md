# DarkMatter: Advanced Stealth Browser Automation Engine

DarkMatter is a high-fidelity automation framework designed for sophisticated web interaction and bot-detection evasion. Built on top of Playwright, it moves beyond simple script execution by simulating authentic human behavioral traces, masking hardware fingerprints, and modeling realistic network instability.

## Core Philosophy

Standard automation is often caught because it is "too perfect." DarkMatter adopts an adversarial approach to stealth: instead of just hiding the fact that it's a bot, it actively projects the identity of a human user operating under real-world conditions. This includes everything from the microscopic jitter in a mouse movement to the erratic latency of a residential ISP.

## Key Features

### 1. GhostLogic (Biometric Replay)
The heart of DarkMatter is its ability to record and replay high-resolution human interaction profiles.
- **Millisecond Precision:** Replays mouse movements, clicks, scrolls, and keystrokes with temporal accuracy.
- **Profile Synchronization:** Captures raw "biometric nodes" during manual sessions to create a reusable behavioral signature (`human_behavior_profile.json`).
- **Viewport Consistency:** Each profile includes a randomized viewport with matching GPU vendor/renderer (Intel for laptops, AMD for desktops) for cross-signal coherence.

### 2. HumanLogic (Synthetic Fallback)
When a recorded trace isn't available, DarkMatter generates synthetic behavior that mimics human imperfections.
- **Bezier Trajectories:** Mouse movements follow complex cubic bezier curves rather than linear paths, simulating the "wandering" motion of a human hand.
- **Advanced Typing Dynamics:** Adjacent-key typo simulation using QWERTY layout, variable typing speed per key difficulty (easy/hard keys), realistic shift key handling, and think-time pauses after punctuation.
- **Hesitation Modeling:** Incorporates Gaussian-distributed pauses before interactions (e.g., hovering before clicking).
- **Scroll Simulation:** Variable-speed scrolling with reading pauses and content-density-based timing.
- **Tab Switching:** Simulates Alt+Tab away-and-back behavior with configurable duration.
- **Reading Pauses:** Duration calculated from text word count (200-250 WPM) for realistic content consumption.
- **Inactivity Patterns:** Brief cursor freezes and extended "coffee break" pauses to simulate user absence.

### 3. Deep Fingerprint Masking
DarkMatter injects a comprehensive suite of scripts to neutralize hardware-level identification:
- **WebGL & Canvas Noise:** Spoofs GPU renderers (Intel/AMD) matched to viewport profile and adds microscopic noise to canvas signatures to prevent hash-based tracking.
- **API Shielding:** Masks `navigator.webdriver` via prototype chain (iframe traversal protection), spoofs `hardwareConcurrency` and `deviceMemory` per viewport, and simulates a plausible plugin array.
- **AudioContext & Permissions:** Injects noise into audio fingerprinting and patches the Permissions API with realistic 500-2000ms response delays.
- **CDP Evasion:** Blocks detection via `Runtime.enable` checks, suppresses DevTools console messages, and neutralizes `chrome.devtools` properties.
- **toString Protection:** Preserves native code strings on wrapped functions to defeat prototype inspection.
- **Navigator.connection API:** Spoofs `effectiveType`, `rtt`, `downlink` coherent with network jitter settings.
- **Battery API:** Returns consistent randomized `getBattery()` values (charging state, level, times) per session.
- **MediaDevices API:** Spoofs `enumerateDevices()` with realistic audio/video input/output devices.
- **Font Enumeration:** Platform-matched font list (Windows/Mac/Linux) for `document.fonts.check()` responses.
- **WebGL Extensions:** GPU-matched extension lists (Intel: ~30, AMD: ~33) with proper `getExtension()` mock objects.
- **Fingerprint Validation:** Automatic consistency checks and auto-correction across viewport, GPU, UA, and hardware specs.

### 4. Network Jitter & ISP Simulation
Most bot detectors flag data centers by analyzing the cleanliness of the connection. DarkMatter includes a "Lagos Mode" to simulate residential ISP instability:
- **Packet-Level Jitter:** Uses the Chrome DevTools Protocol (CDP) to inject latency spikes and bandwidth throttling.
- **TLS JA3 Mutation:** Blacklists certain cipher suites to alter the TLS handshake fingerprint, a common signal used by advanced WAFs (Web Application Firewalls).

### 5. Session Persistence
DarkMatter maintains authentication state across sessions:
- **Cookie Management:** Automatically saves and restores cookies with deduplication.
- **localStorage Sync:** Per-page localStorage backup and restoration after navigation.
- **Named Sessions:** Support for multiple session profiles via `--session-name` flag.

### 6. Geographic Identity (GeoProfile)
Coherent timezone, locale, and language configuration:
- **18 Curated Profiles:** Pre-built combos (e.g., `Africa/Lagos` + `en-NG`, `America/New_York` + `en-US`) to prevent clock/timezone mismatches.
- **Auto-Resolution:** Infers locale from timezone and vice versa.
- **Override Support:** Arbitrary `--timezone` and `--locale` flags with mismatch warnings.

### 7. Proxy Rotation
- **File-Based Proxies:** Load proxy list from file (`--proxy-file`), one per line.
- **Rotation:** Round-robin and random selection strategies.
- **Auth Support:** Parses `protocol://user:pass@host:port` format.
- **Fallback:** Auto-retries without proxy on connection failure.

### 8. User-Agent Management
- **Built-in Pool:** 30+ realistic UAs (Chrome 120-128 on Windows/Mac/Linux + Edge).
- **Viewport Matching:** Auto-selects UA matching GPU vendor and viewport size.
- **Custom Override:** `--user-agent` flag for exact UA string.

### 9. Observability
Structured logging for debugging and monitoring:
- **Configurable Levels:** DEBUG, INFO, WARNING, ERROR via `--log-level` flag.
- **Dual Output:** Logs to both console and `darkmatter.log` file.
- **Detailed Metrics:** Viewport selection, session save/load counts, behavior execution times.

### 10. Detection Scoring Suite
Standalone test runner (`detection_scoring.py`) that scores stealth effectiveness:
- **bot.sannysoft.com** — Table-based bot detection pass/fail
- **CreepJS** — Fingerprint integrity and trust score
- **FingerprintJS** — Visitor ID and confidence parsing
- **bot.incolumitas.com** — Bot probability scoring
- **arh.antoinevastel.com** — Headless detection
- **Output:** Colored terminal summary + JSON report with per-site scores and overall grade (A+ to F).

---

## Installation

### Prerequisites
- Python 3.10+
- Playwright

### Setup
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd DarkMatter
   ```
2. Install dependencies:
   ```bash
   pip install playwright
   playwright install chromium
   ```

## Usage

DarkMatter operates in two primary modes: **Manual (Recording)** and **Auto (Playback)**.

### 1. Recording a Behavioral Profile
To create a "Ghost Profile," run the engine in manual mode. A browser window will open; interact with the target site naturally for 60 seconds.
```bash
python DarkMatter.py --mode manual
```
The session will be saved to `human_behavior_profile.json` with an associated viewport profile.

### 2. Running Automated Tasks
Once you have a profile, run the engine in auto mode. DarkMatter will replay your recorded movements to establish a high-trust session before proceeding.
```bash
python DarkMatter.py --mode auto
```

### 3. Using Different Browser Channels
DarkMatter can use different Chrome/Edge channels (stable, beta, dev, canary) to vary browser fingerprints:
```bash
# Use Chrome Beta
python DarkMatter.py --mode auto --channel chrome-beta

# Use Microsoft Edge
python DarkMatter.py --mode auto --channel msedge

# Combine with other options
python DarkMatter.py --mode auto --channel chrome-canary --session-name test1
```

**Note:** Install the channel first via Playwright:
```bash
playwright install chrome-beta
playwright install msedge
```

### 4. Advanced Options
```bash
python DarkMatter.py --mode auto --session-name mysession --log-level DEBUG --typing-style slow --channel chrome-beta
```

**Available Arguments:**

| Flag | Description | Default |
|------|-------------|--------|
| `--mode` | `manual` (record) or `auto` (playback) | `manual` |
| `--session-name` | Session persistence name | `default` |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `--typing-style` | `natural`, `fast`, `slow` | `natural` |
| `--channel` | Browser channel (e.g. `chrome-beta`, `msedge`) | system |
| `--headless` | Run in headless mode | `false` |
| `--url` | Target URL | `https://bot.sannysoft.com` |
| `--duration` | Recording duration in seconds | `60` |
| `--proxy-file` | Path to proxy list file | none |
| `--user-agent` | Custom User-Agent string | auto |
| `--timezone` | Override timezone (e.g. `America/New_York`) | random |
| `--locale` | Override locale (e.g. `en-US`) | auto |

**Note:** Using `--channel` requires the corresponding Playwright channel installation:
```bash
playwright install chrome-beta
playwright install msedge
```

### 5. Detection Scoring
Run the standalone detection test suite to measure stealth effectiveness:
```bash
python detection_scoring.py
python detection_scoring.py --channel chrome-beta --headless
python detection_scoring.py --proxy-file proxies.txt --timeout-multiplier 2.0
python detection_scoring.py --output report.json
```
The suite tests against 5 detection services and outputs a colored terminal summary with an overall grade (A+ to F), plus a JSON report file.

## Configuration

The script contains a `NETWORK_JITTER` toggle at the top of the file. 
- Set `NETWORK_JITTER = True` to enable residential ISP simulation (recommended for high-security targets).
- Set `NETWORK_JITTER = False` for high-speed, stable automation.

### Proxy File Format
Create a text file with one proxy per line:
```
# Lines starting with # are ignored
socks5://user:pass@proxy1.example.com:1080
http://proxy2.example.com:8080
https://user:pass@proxy3.example.com:443
```

---

## Disclaimer

DarkMatter is intended for research, security testing, and educational purposes. Use this tool responsibly and in compliance with the Terms of Service of any website you interact with. The authors are not responsible for any misuse or consequences resulting from the deployment of this engine.
