# DarkMatter: Advanced Stealth Browser Automation Engine

DarkMatter is a high-fidelity automation framework designed for sophisticated web interaction and bot-detection evasion. Built on top of Playwright, it moves beyond simple script execution by simulating authentic human behavioral traces, masking hardware fingerprints, and modeling realistic network instability.

## Core Philosophy

Standard automation is often caught because it is "too perfect." DarkMatter adopts an adversarial approach to stealth: instead of just hiding the fact that it's a bot, it actively projects the identity of a human user operating under real-world conditions. This includes everything from the microscopic jitter in a mouse movement to the erratic latency of a residential ISP.

## Key Features

### 1. GhostLogic (Biometric Replay)
The heart of DarkMatter is its ability to record and replay high-resolution human interaction profiles.
- **Millisecond Precision:** Replays mouse movements, clicks, scrolls, and keystrokes with temporal accuracy.
- **Profile Synchronization:** Captures raw "biometric nodes" during manual sessions to create a reusable behavioral signature (`human_behavior_profile.json`).

### 2. HumanLogic (Synthetic Fallback)
When a recorded trace isn't available, DarkMatter generates synthetic behavior that mimics human imperfections.
- **Bezier Trajectories:** Mouse movements follow complex cubic bezier curves rather than linear paths, simulating the "wandering" motion of a human hand.
- **Typing Dynamics:** Simulates varying keystroke speeds and includes "human error" scenarios where the bot occasionally makes a typo and performs a backspace correction.
- **Hesitation Modeling:** Incorporates Gaussian-distributed pauses before interactions (e.g., hovering before clicking).

### 3. Deep Fingerprint Masking
DarkMatter injects a comprehensive suite of scripts to neutralize hardware-level identification:
- **WebGL & Canvas Noise:** Spoofs GPU renderers (Intel/AMD) and adds microscopic noise to canvas signatures to prevent hash-based tracking.
- **API Shielding:** Masks `navigator.webdriver`, spoofs `hardwareConcurrency` and `deviceMemory`, and simulates a plausible plugin array.
- **AudioContext & Permissions:** Injects noise into audio fingerprinting and patches the Permissions API to appear as a standard desktop browser.

### 4. Network Jitter & ISP Simulation
Most bot detectors flag data centers by analyzing the cleanliness of the connection. DarkMatter includes a "Lagos Mode" to simulate residential ISP instability:
- **Packet-Level Jitter:** Uses the Chrome DevTools Protocol (CDP) to inject latency spikes and bandwidth throttling.
- **TLS JA3 Mutation:** Blacklists certain cipher suites to alter the TLS handshake fingerprint, a common signal used by advanced WAFs (Web Application Firewalls).

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
The session will be saved to `human_behavior_profile.json`.

### 2. Running Automated Tasks
Once you have a profile, run the engine in auto mode. DarkMatter will replay your recorded movements to establish a high-trust session before proceeding.
```bash
python DarkMatter.py --mode auto
```

## Configuration

The script contains a `NETWORK_JITTER` toggle at the top of the file. 
- Set `NETWORK_JITTER = True` to enable residential ISP simulation (recommended for high-security targets).
- Set `NETWORK_JITTER = False` for high-speed, stable automation.

---

## Disclaimer

DarkMatter is intended for research, security testing, and educational purposes. Use this tool responsibly and in compliance with the Terms of Service of any website you interact with. The authors are not responsible for any misuse or consequences resulting from the deployment of this engine.
