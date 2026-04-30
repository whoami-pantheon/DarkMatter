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

### 4. Network Jitter & ISP Simulation
Most bot detectors flag data centers by analyzing the cleanliness of the connection. DarkMatter includes a "Lagos Mode" to simulate residential ISP instability:
- **Packet-Level Jitter:** Uses the Chrome DevTools Protocol (CDP) to inject latency spikes and bandwidth throttling.
- **TLS JA3 Mutation:** Blacklists certain cipher suites to alter the TLS handshake fingerprint, a common signal used by advanced WAFs (Web Application Firewalls).

### 5. Session Persistence
DarkMatter maintains authentication state across sessions:
- **Cookie Management:** Automatically saves and restores cookies with deduplication.
- **localStorage Sync:** Per-page localStorage backup and restoration after navigation.
- **Named Sessions:** Support for multiple session profiles via `--session-name` flag.

### 6. Observability
Structured logging for debugging and monitoring:
- **Configurable Levels:** DEBUG, INFO, WARNING, ERROR via `--log-level` flag.
- **Dual Output:** Logs to both console and `darkmatter.log` file.
- **Detailed Metrics:** Viewport selection, session save/load counts, behavior execution times.

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

### 3. Advanced Options
```bash
python DarkMatter.py --mode auto --session-name mysession --log-level DEBUG --typing-style slow
```

**Available Arguments:**
- `--mode`: `manual` (record) or `auto` (playback)
- `--session-name`: Name for session persistence (cookies/localStorage). Default: `default`
- `--log-level`: Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Default: `INFO`
- `--typing-style`: Typing speed profile (`natural`, `fast`, `slow`). Default: `natural`

## Configuration

The script contains a `NETWORK_JITTER` toggle at the top of the file. 
- Set `NETWORK_JITTER = True` to enable residential ISP simulation (recommended for high-security targets).
- Set `NETWORK_JITTER = False` for high-speed, stable automation.

---

## Disclaimer

DarkMatter is intended for research, security testing, and educational purposes. Use this tool responsibly and in compliance with the Terms of Service of any website you interact with. The authors are not responsible for any misuse or consequences resulting from the deployment of this engine.
