#!/usr/bin/env python3
"""
DarkMatter Detection Scoring Suite

Standalone detection test runner that launches a DarkMatter-configured browser
and scores stealth effectiveness against multiple bot detection services.

Usage:
    python detection_scoring.py
    python detection_scoring.py --channel chrome-beta --headless
    python detection_scoring.py --proxy-file proxies.txt --timeout 30
"""

import asyncio
import argparse
import json
import logging
import os
import sys
import time
import random
from datetime import datetime
from playwright.async_api import async_playwright

# Reuse DarkMatter's stealth classes so the scoring browser has identical fingerprints
from DarkMatter import (
    ViewportManager, SessionManager, GeoProfile, ProxyManager,
    UserAgentManager, FingerprintValidator, NETWORK_JITTER,
    apply_network_jitter
)

logger = logging.getLogger('DarkMatter.Scoring')

# ─── Test Sites Configuration ───────────────────────────────────────────────
# Each site tests a different detection technique. Timeouts are per-site defaults (ms).

TEST_SITES = {
    'sannysoft': {
        'url': 'https://bot.sannysoft.com',
        'name': 'SannySoft Bot Detection',
        'category': 'bot_detection',
        'timeout': 15000,
    },
    'creepjs': {
        'url': 'https://abrahamjuliot.github.io/creepjs/',
        'name': 'CreepJS Fingerprint Integrity',
        'category': 'fingerprint',
        'timeout': 30000,
    },
    'creepjs_workers': {
        'url': 'https://nicedoc.io/nicedoc/creepjs/workers',
        'name': 'CreepJS Workers Test',
        'category': 'fingerprint',
        'timeout': 20000,
        'parent': 'creepjs',
    },
    'fingerprintjs': {
        'url': 'https://fingerprintjs.github.io/fingerprintjs/',
        'name': 'FingerprintJS Pro',
        'category': 'fingerprint',
        'timeout': 20000,
    },
    'incolumitas': {
        'url': 'https://bot.incolumitas.com/',
        'name': 'Incolumitas Bot Detection',
        'category': 'headless_detection',
        'timeout': 30000,
    },
    'areyouheadless': {
        'url': 'https://arh.antoinevastel.com/bots/areyouheadless',
        'name': 'Are You Headless?',
        'category': 'headless_detection',
        'timeout': 15000,
    },
}


# ─── Color Output Helpers ───────────────────────────────────────────────────

class Colors:
    """ANSI escape codes for colored terminal output."""
    PASS = '\033[92m'      # Green
    FAIL = '\033[91m'      # Red
    WARN = '\033[93m'      # Yellow
    INFO = '\033[94m'      # Blue
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    HEADER = '\033[95m'    # Magenta

def color_score(score, max_score=100):
    """Color a score green (>=80), yellow (>=50), or red (<50)."""
    if score >= 80:
        return f"{Colors.PASS}{score}/{max_score}{Colors.RESET}"
    elif score >= 50:
        return f"{Colors.WARN}{score}/{max_score}{Colors.RESET}"
    else:
        return f"{Colors.FAIL}{score}/{max_score}{Colors.RESET}"

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'═' * 60}{Colors.RESET}")
    print(f"{Colors.HEADER}{Colors.BOLD}  {text}{Colors.RESET}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'═' * 60}{Colors.RESET}")

def print_result(label, status, detail=""):
    """Print a single test result line with ✓/✗/~/? icon."""
    if status == "pass":
        icon = f"{Colors.PASS}✓{Colors.RESET}"
    elif status == "fail":
        icon = f"{Colors.FAIL}✗{Colors.RESET}"
    elif status == "warn":
        icon = f"{Colors.WARN}~{Colors.RESET}"
    else:
        icon = f"{Colors.DIM}?{Colors.RESET}"
    
    detail_str = f" {Colors.DIM}({detail}){Colors.RESET}" if detail else ""
    print(f"  {icon} {label}{detail_str}")


# ─── Individual Test Parsers ────────────────────────────────────────────────

async def test_sannysoft(page, timeout):
    """Parse bot.sannysoft.com HTML tables for pass/fail results.
    Score = number of passed rows out of total rows."""
    results = {'site': 'sannysoft', 'tests': [], 'score': 0, 'max_score': 0}
    
    try:
        await page.goto('https://bot.sannysoft.com', timeout=timeout, wait_until='networkidle')
        await asyncio.sleep(3)  # Wait for all client-side detection scripts to finish
        
        # Parse the results table
        rows = await page.evaluate("""() => {
            const results = [];
            const tables = document.querySelectorAll('table');
            tables.forEach(table => {
                const rows = table.querySelectorAll('tr');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const name = cells[0]?.innerText?.trim() || '';
                        const value = cells[1]?.innerText?.trim() || '';
                        const className = cells[1]?.className || '';
                        const passed = className.includes('passed') || 
                                       cells[1]?.style?.backgroundColor === 'green' ||
                                       cells[1]?.style?.backgroundColor === '#90EE90' ||
                                       cells[1]?.getAttribute('class')?.includes('passed');
                        const failed = className.includes('failed') || 
                                       cells[1]?.style?.backgroundColor === 'red' ||
                                       cells[1]?.style?.backgroundColor === '#FFB6C1' ||
                                       cells[1]?.getAttribute('class')?.includes('failed');
                        results.push({
                            name: name,
                            value: value,
                            passed: passed,
                            failed: failed
                        });
                    }
                });
            });
            return results;
        }""")
        
        passed = 0
        total = len(rows)
        for row in rows:
            status = 'pass' if row.get('passed') else ('fail' if row.get('failed') else 'unknown')
            results['tests'].append({
                'name': row.get('name', ''),
                'value': row.get('value', ''),
                'status': status
            })
            if status == 'pass':
                passed += 1
        
        results['score'] = passed
        results['max_score'] = total if total > 0 else 1
        
    except Exception as e:
        results['error'] = str(e)
        logger.error(f"[SANNYSOFT] Test failed: {e}")
    
    return results


async def test_creepjs(page, timeout):
    """Parse CreepJS trust score (0-100) and detect lie/trash flags.
    CreepJS is one of the most thorough fingerprint integrity checkers."""
    results = {'site': 'creepjs', 'tests': [], 'score': 0, 'max_score': 100}
    
    try:
        await page.goto('https://abrahamjuliot.github.io/creepjs/', timeout=timeout, wait_until='networkidle')
        await asyncio.sleep(10)  # CreepJS runs ~40 async tests; needs time to complete
        
        # Parse trust score and findings
        data = await page.evaluate("""() => {
            const results = {};
            
            // Get trust score from the page
            const scoreEl = document.querySelector('[class*="trust"]') || 
                           document.querySelector('.score') ||
                           document.querySelector('[data-score]');
            if (scoreEl) {
                results.trust_score = scoreEl.innerText || scoreEl.getAttribute('data-score') || '';
            }
            
            // Get all fingerprint sections
            const sections = document.querySelectorAll('.fingerprint-item, [class*="fp-"], section, .col-six');
            sections.forEach(section => {
                const title = section.querySelector('h3, h4, .title, strong')?.innerText?.trim();
                const value = section.querySelector('.value, .result, span')?.innerText?.trim();
                const status = section.className || '';
                if (title) {
                    results[title] = {
                        value: value || '',
                        is_lies: status.includes('lies') || status.includes('fail'),
                        is_trash: status.includes('trash')
                    };
                }
            });
            
            // Get lie detection results
            const lies = document.querySelectorAll('[class*="lies"], [class*="lie-"]');
            results.lies_detected = lies.length;
            
            // Get overall page text for additional parsing
            results.page_text = document.body?.innerText?.substring(0, 5000) || '';
            
            return results;
        }""")
        
        # Parse trust score
        trust_text = data.get('trust_score', '')
        try:
            # Extract number from trust score text
            score_num = ''.join(c for c in trust_text if c.isdigit() or c == '.')
            if score_num:
                results['score'] = min(100, int(float(score_num)))
        except (ValueError, TypeError):
            results['score'] = 0
        
        lies = data.get('lies_detected', 0)
        results['tests'].append({
            'name': 'Trust Score',
            'value': trust_text,
            'status': 'pass' if results['score'] >= 50 else 'fail'
        })
        results['tests'].append({
            'name': 'Lies Detected',
            'value': str(lies),
            'status': 'pass' if lies == 0 else 'fail'
        })
        
        # Parse sub-sections
        for key, val in data.items():
            if isinstance(val, dict) and 'value' in val:
                status = 'fail' if val.get('is_lies') or val.get('is_trash') else 'pass'
                results['tests'].append({
                    'name': key,
                    'value': val['value'][:80],
                    'status': status
                })
        
    except Exception as e:
        results['error'] = str(e)
        logger.error(f"[CREEPJS] Test failed: {e}")
    
    return results


async def test_fingerprintjs(page, timeout):
    """Check if FingerprintJS can generate a stable visitor ID.
    A successful ID generation means we look like a normal browser (score 80)."""
    results = {'site': 'fingerprintjs', 'tests': [], 'score': 0, 'max_score': 100}
    
    try:
        await page.goto('https://fingerprintjs.github.io/fingerprintjs/', timeout=timeout, wait_until='networkidle')
        await asyncio.sleep(5)
        
        data = await page.evaluate("""() => {
            const results = {};
            
            // Look for visitor ID
            const visitorEl = document.querySelector('.visitor-id, [class*="visitor"], .giant, code');
            if (visitorEl) {
                results.visitor_id = visitorEl.innerText?.trim() || '';
            }
            
            // Look for confidence
            const confidenceEls = document.querySelectorAll('.confidence, [class*="confidence"], td, .value');
            confidenceEls.forEach(el => {
                const text = el.innerText?.trim() || '';
                if (text.includes('%') || text.match(/0\\.\\d+/)) {
                    results.confidence = text;
                }
            });
            
            // Get component details
            const components = document.querySelectorAll('tr, .component, [class*="detail"]');
            results.components = [];
            components.forEach(comp => {
                const cells = comp.querySelectorAll('td');
                if (cells.length >= 2) {
                    results.components.push({
                        name: cells[0]?.innerText?.trim() || '',
                        value: cells[1]?.innerText?.trim()?.substring(0, 100) || ''
                    });
                }
            });
            
            results.page_text = document.body?.innerText?.substring(0, 3000) || '';
            
            return results;
        }""")
        
        visitor_id = data.get('visitor_id', 'N/A')
        confidence = data.get('confidence', 'N/A')
        
        results['tests'].append({
            'name': 'Visitor ID',
            'value': visitor_id[:32],
            'status': 'pass' if visitor_id and visitor_id != 'N/A' else 'warn'
        })
        results['tests'].append({
            'name': 'Confidence',
            'value': confidence,
            'status': 'pass' if confidence and confidence != 'N/A' else 'warn'
        })
        
        # If FingerprintJS generates an ID, we appear as a normal browser.
        # Failure to generate = something is fundamentally broken in our spoofing.
        if visitor_id and visitor_id != 'N/A':
            results['score'] = 80
        else:
            results['score'] = 30
        
        # Add component details
        for comp in data.get('components', [])[:15]:
            results['tests'].append({
                'name': comp['name'],
                'value': comp['value'],
                'status': 'pass'
            })
        
    except Exception as e:
        results['error'] = str(e)
        logger.error(f"[FINGERPRINTJS] Test failed: {e}")
    
    return results


async def test_incolumitas(page, timeout):
    """Parse bot.incolumitas.com bot probability (0-100%).
    Score is inverted: low bot probability = high stealth score."""
    results = {'site': 'incolumitas', 'tests': [], 'score': 0, 'max_score': 100}
    
    try:
        await page.goto('https://bot.incolumitas.com/', timeout=timeout, wait_until='networkidle')
        await asyncio.sleep(12)  # Runs ~20 behavioral and fingerprint tests; needs time
        
        data = await page.evaluate("""() => {
            const results = {};
            
            // Get bot probability
            const probEl = document.querySelector('#bot-probability, [class*="probability"], .score');
            if (probEl) {
                results.bot_probability = probEl.innerText?.trim() || '';
            }
            
            // Get detection details
            const detailEls = document.querySelectorAll('.test-result, tr, [class*="result"], [class*="test"]');
            results.details = [];
            detailEls.forEach(el => {
                const text = el.innerText?.trim();
                if (text && text.length > 3 && text.length < 200) {
                    results.details.push(text);
                }
            });
            
            // Get all test items with pass/fail
            const items = document.querySelectorAll('li, .check-item');
            results.checks = [];
            items.forEach(item => {
                const text = item.innerText?.trim();
                const cls = item.className || '';
                if (text && text.length > 2) {
                    results.checks.push({
                        text: text.substring(0, 150),
                        passed: cls.includes('pass') || cls.includes('good') || text.includes('✓'),
                        failed: cls.includes('fail') || cls.includes('bad') || text.includes('✗')
                    });
                }
            });
            
            results.page_text = document.body?.innerText?.substring(0, 5000) || '';
            
            return results;
        }""")
        
        # Parse bot probability
        prob_text = data.get('bot_probability', '')
        try:
            prob_num = ''.join(c for c in prob_text if c.isdigit() or c == '.')
            if prob_num:
                bot_prob = float(prob_num)
                # Convert: low bot probability = high stealth score
                results['score'] = max(0, min(100, int(100 - bot_prob)))
            else:
                results['score'] = 50  # Unknown
        except (ValueError, TypeError):
            results['score'] = 50
        
        results['tests'].append({
            'name': 'Bot Probability',
            'value': prob_text or 'Unable to parse',
            'status': 'pass' if results['score'] >= 70 else 'fail'
        })
        
        # Add check items
        for check in data.get('checks', [])[:20]:
            status = 'pass' if check.get('passed') else ('fail' if check.get('failed') else 'unknown')
            results['tests'].append({
                'name': check['text'][:60],
                'value': '',
                'status': status
            })
        
        # Fallback: look for explicit human/bot verdict in page text
        page_text = data.get('page_text', '').lower()
        if 'human' in page_text and 'not a bot' in page_text:
            results['score'] = max(results['score'], 80)
        elif 'bot detected' in page_text or 'automation' in page_text:
            results['score'] = min(results['score'], 30)
        
    except Exception as e:
        results['error'] = str(e)
        logger.error(f"[INCOLUMITAS] Test failed: {e}")
    
    return results


async def test_areyouheadless(page, timeout):
    """Binary test: detects if the browser is running in headless mode.
    Score 100 = not detected, 0 = headless detected."""
    results = {'site': 'areyouheadless', 'tests': [], 'score': 0, 'max_score': 100}
    
    try:
        await page.goto('https://arh.antoinevastel.com/bots/areyouheadless', timeout=timeout, wait_until='networkidle')
        await asyncio.sleep(3)
        
        data = await page.evaluate("""() => {
            const results = {};
            results.page_text = document.body?.innerText?.trim() || '';
            
            // Look for specific result elements
            const resultEl = document.querySelector('#result, .result, [class*="result"]');
            if (resultEl) {
                results.result = resultEl.innerText?.trim() || '';
            }
            
            // Check for headless indicators in page
            const allText = document.body?.innerText?.toLowerCase() || '';
            results.is_headless = allText.includes('headless') && !allText.includes('not headless');
            results.is_chrome_headless = allText.includes('chrome headless');
            results.passed = allText.includes('not headless') || allText.includes('you are not');
            
            return results;
        }""")
        
        is_headless_detected = data.get('is_headless', False) and not data.get('passed', False)
        passed = data.get('passed', False)
        result_text = data.get('result', data.get('page_text', '')[:200])
        
        if passed:
            results['score'] = 100
            status = 'pass'
        elif is_headless_detected:
            results['score'] = 0
            status = 'fail'
        else:
            results['score'] = 50
            status = 'unknown'
        
        results['tests'].append({
            'name': 'Headless Detection',
            'value': result_text[:100],
            'status': status
        })
        
        if data.get('is_chrome_headless'):
            results['tests'].append({
                'name': 'Chrome Headless Signature',
                'value': 'Detected',
                'status': 'fail'
            })
            results['score'] = min(results['score'], 20)
        
    except Exception as e:
        results['error'] = str(e)
        logger.error(f"[AREYOUHEADLESS] Test failed: {e}")
    
    return results


# ─── Test Runner ────────────────────────────────────────────────────────────

async def run_detection_suite(channel=None, headless=False, proxy_file=None, 
                               user_agent=None, timezone=None, locale=None,
                               timeout_multiplier=1.0, output_file=None):
    """Launch a DarkMatter-configured browser, visit each detection site, parse results,
    and output a colored terminal summary + JSON report with per-site scores and overall grade."""
    
    print_header("DarkMatter Detection Scoring Suite")
    print(f"  {Colors.DIM}Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}")
    
    # Build a coherent fingerprint profile (same logic as the main engine)
    geo = GeoProfile.resolve(timezone, locale)
    viewport_profile = ViewportManager.generate_viewport_profile()
    viewport_profile = FingerprintValidator.auto_correct(viewport_profile)
    
    if user_agent:
        selected_ua = user_agent
    else:
        selected_ua = UserAgentManager.get_matching(viewport_profile)
    
    FingerprintValidator.validate(viewport_profile, geo, selected_ua)
    
    proxy_mgr = ProxyManager(proxy_file) if proxy_file else None
    
    print(f"\n  {Colors.INFO}Configuration:{Colors.RESET}")
    print(f"    Viewport: {viewport_profile['width']}x{viewport_profile['height']}")
    print(f"    GPU: {viewport_profile['vendor']}")
    print(f"    Geo: {geo['region']} ({geo['timezone']})")
    print(f"    UA: {selected_ua[:55]}...")
    print(f"    Headless: {headless}")
    if channel:
        print(f"    Channel: {channel}")
    if proxy_mgr and proxy_mgr.has_proxies():
        print(f"    Proxies: {len(proxy_mgr.proxies)} loaded")
    
    all_results = {}
    overall_score = 0
    overall_max = 0
    
    try:
        async with async_playwright() as p:
            # Same stealth launch flags as the main engine
            launch_args = [
                "--start-maximized",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-webrtc",
                "--cipher-suite-blacklist=0xc02f,0xc02b",
            ]
            
            if headless:
                launch_args.append("--headless=new")
                launch_args.append("--disable-gpu")
            
            launch_opts = {
                'user_data_dir': "/mnt/chrome-profile-scoring",
                'headless': headless,
                'viewport': {'width': viewport_profile['width'], 'height': viewport_profile['height']},
                'timezone_id': geo['timezone'],
                'locale': geo['locale'],
                'user_agent': selected_ua,
                'args': launch_args
            }
            
            if channel:
                launch_opts['channel'] = channel
            
            if proxy_mgr and proxy_mgr.has_proxies():
                proxy_url = proxy_mgr.get_random()
                proxy_config = ProxyManager.parse_proxy_url(proxy_url)
                if proxy_config:
                    launch_opts['proxy'] = proxy_config
            
            context = await p.chromium.launch_persistent_context(**launch_opts)
            
            page = await context.new_page()
            
            # Inject the full DarkMatter stealth stack (WebGL, canvas, navigator, etc.)
            await _inject_stealth_scripts(page, viewport_profile, geo)
            
            # Override window.screen dimensions to match our viewport
            viewport_script = ViewportManager.get_viewport_init_script(viewport_profile)
            await page.add_init_script(viewport_script)
            
            # Run each detection test sequentially on the same page
            test_functions = {
                'sannysoft': test_sannysoft,
                'creepjs': test_creepjs,
                'fingerprintjs': test_fingerprintjs,
                'incolumitas': test_incolumitas,
                'areyouheadless': test_areyouheadless,
            }
            
            for site_key, test_fn in test_functions.items():
                site_config = TEST_SITES[site_key]
                site_timeout = int(site_config['timeout'] * timeout_multiplier)
                
                print(f"\n  {Colors.INFO}Testing:{Colors.RESET} {site_config['name']}")
                print(f"  {Colors.DIM}{site_config['url']}{Colors.RESET}")
                
                start_time = time.time()
                try:
                    result = await test_fn(page, site_timeout)
                except Exception as e:
                    result = {
                        'site': site_key,
                        'tests': [],
                        'score': 0,
                        'max_score': 100,
                        'error': str(e)
                    }
                elapsed = time.time() - start_time
                
                result['elapsed_seconds'] = round(elapsed, 2)
                all_results[site_key] = result
                
                # Print individual test results
                if result.get('error'):
                    print(f"  {Colors.FAIL}ERROR: {result['error'][:80]}{Colors.RESET}")
                else:
                    for test in result.get('tests', [])[:10]:
                        detail = test.get('value', '')[:60]
                        print_result(test['name'], test['status'], detail)
                
                score = result.get('score', 0)
                max_score = result.get('max_score', 100)
                overall_score += score
                overall_max += max_score
                
                print(f"  {Colors.BOLD}Score: {color_score(score, max_score)} "
                      f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}")
            
            await context.close()
    
    except Exception as e:
        logger.error(f"[SUITE] Fatal error: {e}")
        print(f"\n  {Colors.FAIL}FATAL ERROR: {e}{Colors.RESET}")
    
    # ─── Summary ─────────────────────────────────────────────────────────────
    
    print_header("Detection Score Summary")
    
    for site_key, result in all_results.items():
        site_name = TEST_SITES.get(site_key, {}).get('name', site_key)
        score = result.get('score', 0)
        max_score = result.get('max_score', 100)
        elapsed = result.get('elapsed_seconds', 0)
        error = result.get('error')
        
        if error:
            print(f"  {Colors.FAIL}✗{Colors.RESET} {site_name}: {Colors.FAIL}ERROR{Colors.RESET}")
        else:
            pct = int((score / max_score) * 100) if max_score > 0 else 0
            print(f"  {'✓' if pct >= 70 else '~' if pct >= 40 else '✗'} "
                  f"{site_name}: {color_score(pct)} "
                  f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}")
    
    # Overall
    if overall_max > 0:
        overall_pct = int((overall_score / overall_max) * 100)
    else:
        overall_pct = 0
    
    print(f"\n  {Colors.BOLD}Overall Stealth Score: {color_score(overall_pct)}{Colors.RESET}")
    
    grade = 'A+' if overall_pct >= 95 else 'A' if overall_pct >= 85 else 'B' if overall_pct >= 70 else 'C' if overall_pct >= 50 else 'D' if overall_pct >= 30 else 'F'
    grade_color = Colors.PASS if overall_pct >= 70 else Colors.WARN if overall_pct >= 50 else Colors.FAIL
    print(f"  {Colors.BOLD}Grade: {grade_color}{grade}{Colors.RESET}")
    print()
    
    # Save full results as JSON for programmatic analysis
    report = {
        'timestamp': datetime.now().isoformat(),
        'configuration': {
            'viewport': f"{viewport_profile['width']}x{viewport_profile['height']}",
            'gpu_vendor': viewport_profile['vendor'],
            'user_agent': selected_ua,
            'geo': geo,
            'headless': headless,
            'channel': channel
        },
        'results': all_results,
        'overall_score': overall_pct,
        'grade': grade
    }
    
    report_path = output_file or f"detection_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"  {Colors.DIM}Report saved: {report_path}{Colors.RESET}\n")
    except Exception as e:
        logger.error(f"Failed to save report: {e}")
    
    return report


async def _inject_stealth_scripts(page, viewport_profile, geo):
    """Inject all browser API spoofing scripts (mirrors DarkMatter.py's master init script).
    Covers: WebGL, Canvas, Navigator, Plugins, Audio, Permissions, Chrome runtime,
    CDP evasion, Connection, Battery, MediaDevices, Fonts, WebGL extensions."""
    
    # Connection API values must match NETWORK_JITTER setting
    if NETWORK_JITTER:
        conn_type = '3g'
        conn_rtt = random.randint(100, 300)
        conn_downlink = round(random.uniform(1.5, 5.0), 1)
    else:
        conn_type = '4g'
        conn_rtt = random.randint(20, 80)
        conn_downlink = round(random.uniform(10.0, 50.0), 1)
    
    # Randomize battery state for this scoring session
    battery_charging = 'true' if random.choice([True, False]) else 'false'
    battery_charging_time = 'Infinity' if battery_charging == 'false' else str(random.randint(1800, 7200))
    battery_discharging_time = str(random.randint(3600, 28800)) if battery_charging == 'false' else 'Infinity'
    battery_level = round(random.uniform(0.2, 1.0), 2)
    
    # Unique hex seed for fake device IDs
    media_seed = ''.join(random.choices('0123456789abcdef', k=16))
    
    # Common cross-platform fonts (simplified list for scoring; main engine has OS-specific lists)
    is_windows = 'Windows' in (page._impl_obj._browser_context._options.get('user_agent', '') or '')
    font_list = [
        'Arial', 'Arial Black', 'Calibri', 'Cambria', 'Comic Sans MS',
        'Consolas', 'Courier New', 'Georgia', 'Impact', 'Lucida Console',
        'Microsoft Sans Serif', 'Palatino Linotype', 'Segoe UI', 'Tahoma',
        'Times New Roman', 'Trebuchet MS', 'Verdana'
    ]
    
    # GPU-matched WebGL extensions (Intel has fewer than AMD/NVIDIA)
    is_intel = 'Intel' in viewport_profile.get('vendor', '')
    webgl_exts = [
        'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_color_buffer_half_float',
        'EXT_float_blend', 'EXT_frag_depth', 'EXT_shader_texture_lod',
        'EXT_texture_filter_anisotropic', 'OES_element_index_uint',
        'OES_standard_derivatives', 'OES_texture_float', 'OES_texture_float_linear',
        'OES_texture_half_float', 'OES_texture_half_float_linear',
        'OES_vertex_array_object', 'WEBGL_color_buffer_float',
        'WEBGL_compressed_texture_s3tc', 'WEBGL_debug_renderer_info',
        'WEBGL_depth_texture', 'WEBGL_draw_buffers', 'WEBGL_lose_context',
        'WEBGL_multi_draw'
    ]
    if not is_intel:
        webgl_exts.extend(['WEBGL_compressed_texture_astc', 'EXT_color_buffer_float', 'OES_draw_buffers_indexed'])
    
    languages_json = json.dumps(geo.get('languages', ['en-US', 'en']))
    font_json = json.dumps(font_list)
    ext_json = json.dumps(webgl_exts)
    
    # Single init script containing all stealth overrides
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
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        Object.defineProperty(navigator, 'languages', {{ get: () => {languages_json} }});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {viewport_profile['hardware_concurrency']} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {viewport_profile['device_memory']} }});
        const proto = navigator.__proto__;
        Object.defineProperty(proto, 'webdriver', {{ get: () => undefined }});
        
        // --- toString Protection (keeps 'native code' in Function.toString) ---
        const originalToString = Function.prototype.toString;
        Function.prototype.toString = function() {{
            if (this === WebGLRenderingContext.prototype.getParameter ||
                this === WebGL2RenderingContext.prototype.getParameter ||
                this === AudioBuffer.prototype.getChannelData) {{
                return originalToString.call(this).replace(/native code/, 'native code');
            }}
            return originalToString.call(this);
        }};
        
        // --- Plugin Simulation (headless Chrome has 0 by default) ---
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
        const originalGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function() {{
            const results_1 = originalGetChannelData.apply(this, arguments);
            for (let i = 0; i < results_1.length; i += 100) {{
                results_1[i] = results_1[i] + (Math.random() * 0.0000001);
            }}
            return results_1;
        }};
        
        // --- Permissions API (add realistic 500-2000ms delay) ---
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = function(parameters) {{
            const delay = Math.floor(Math.random() * 1500) + 500;
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
        window.chrome = {{
            app: {{ isInstalled: false }},
            webstore: {{ onInstall: {{}}, onDownloadProgress: {{}} }},
            runtime: {{ PlatformOs: 'win', PlatformArch: 'x86-64', PlatformNaclArch: 'x86-64', RequestUpdateCheckStatus: 'throttled', OnInstalledReason: 'install', OnRestartRequiredReason: 'app_update' }}
        }};
        window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {{ supportsFiber: true, renderers: new Map() }};
        
        // --- CDP / DevTools Evasion ---
        const cdpProps = ['__commandLineAPI', 'cdp', 'chrome.devtools'];
        cdpProps.forEach(prop => {{
            Object.defineProperty(window, prop, {{
                get: () => undefined,
                set: () => {{}},
                configurable: false
            }});
        }});
        const originalNotify = console.debug;
        console.debug = function(...args) {{
            if (args.length > 0 && typeof args[0] === 'string' && args[0].includes('DevTools')) return;
            return originalNotify.apply(this, args);
        }};
        
        // --- Navigator.connection API ---
        try {{
            const connectionProto = {{
                get effectiveType() {{ return '{conn_type}'; }},
                get rtt() {{ return {conn_rtt}; }},
                get downlink() {{ return {conn_downlink}; }},
                get saveData() {{ return false; }},
                get type() {{ return 'wifi'; }},
                addEventListener: function() {{}},
                removeEventListener: function() {{}}
            }};
            if (typeof NetworkInformation !== 'undefined') {{
                Object.setPrototypeOf(connectionProto, NetworkInformation.prototype);
            }}
            Object.defineProperty(navigator, 'connection', {{
                get: () => connectionProto, configurable: true
            }});
        }} catch(e) {{}}
        
        // --- Battery API ---
        if (navigator.getBattery) {{
            navigator.getBattery = function() {{
                return Promise.resolve({{
                    charging: {battery_charging},
                    chargingTime: {battery_charging_time},
                    dischargingTime: {battery_discharging_time},
                    level: {battery_level},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}}
                }});
            }};
        }}
        
        // --- MediaDevices API ---
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
            const fakeDevices = [
                {{ deviceId: '{media_seed}a1', kind: 'audioinput', label: 'Default - Built-in Audio', groupId: 'grp1' }},
                {{ deviceId: '{media_seed}o1', kind: 'audiooutput', label: 'Default - Speakers', groupId: 'grp2' }},
                {{ deviceId: '{media_seed}v1', kind: 'videoinput', label: 'HD Webcam C270', groupId: 'grp3' }}
            ];
            navigator.mediaDevices.enumerateDevices = function() {{
                return Promise.resolve(fakeDevices.map(d => ({{ ...d, toJSON: function() {{ return this; }} }})));
            }};
        }}
        
        // --- Font Enumeration ---
        const knownFonts = {font_json};
        if (document.fonts && document.fonts.check) {{
            const origCheck = document.fonts.check.bind(document.fonts);
            document.fonts.check = function(font, text) {{
                const fontFamily = font.split(',')[0].replace(/['"\\s]/g, '').toLowerCase();
                const isKnown = knownFonts.some(f => f.toLowerCase() === fontFamily);
                if (isKnown) return true;
                try {{ return origCheck(font, text); }} catch(e) {{ return false; }}
            }};
        }}
        
        // --- WebGL Extensions ---
        const supportedExtensions = {ext_json};
        const origGSE = WebGLRenderingContext.prototype.getSupportedExtensions;
        WebGLRenderingContext.prototype.getSupportedExtensions = function() {{ return supportedExtensions; }};
        const origGSE2 = WebGL2RenderingContext.prototype.getSupportedExtensions;
        WebGL2RenderingContext.prototype.getSupportedExtensions = function() {{ return supportedExtensions; }};
        const origGE = WebGLRenderingContext.prototype.getExtension;
        WebGLRenderingContext.prototype.getExtension = function(name) {{
            if (supportedExtensions.includes(name)) {{ try {{ return origGE.call(this, name); }} catch(e) {{ return {{}}; }} }}
            return null;
        }};
    """)


# ─── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # CLI interface: parse flags, configure logging, run the scoring suite
    parser = argparse.ArgumentParser(description="DarkMatter Detection Scoring Suite")
    parser.add_argument("--channel", choices=["chrome", "chrome-beta", "chrome-dev", "chrome-canary", 
                                                "msedge", "msedge-beta", "msedge-dev", "msedge-canary"],
                        default=None, help="Browser channel to use")
    parser.add_argument("--headless", action="store_true", default=False, help="Run in headless mode")
    parser.add_argument("--proxy-file", default=None, help="Path to proxy list file")
    parser.add_argument("--user-agent", default=None, help="Custom User-Agent string")
    parser.add_argument("--timezone", default=None, help="Override timezone (e.g. America/New_York)")
    parser.add_argument("--locale", default=None, help="Override locale (e.g. en-US)")
    parser.add_argument("--timeout-multiplier", type=float, default=1.0, help="Multiply default timeouts (e.g. 2.0 for slow connections)")
    parser.add_argument("--output", default=None, help="Output JSON report file path")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO", help="Logging verbosity")
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('darkmatter_scoring.log')
        ]
    )
    
    asyncio.run(run_detection_suite(
        channel=args.channel,
        headless=args.headless,
        proxy_file=args.proxy_file,
        user_agent=args.user_agent,
        timezone=args.timezone,
        locale=args.locale,
        timeout_multiplier=args.timeout_multiplier,
        output_file=args.output
    ))
