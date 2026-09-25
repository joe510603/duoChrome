"""Minimal browser fingerprint patch — enough to avoid the dumbest bot checks.

This is NOT full stealth (use playwright-stealth / puppeteer-extra-stealth for that).
duoChrome keeps it light on purpose: a few high-signal patches that work for most
mainstream sites (Douyin / Xiaohongshu / generic platforms).
"""
from __future__ import annotations

# Patch 1: navigator.webdriver → false (the #1 detection signal)
# Patch 2: chrome.runtime presence (so navigator.webdriver alone doesn't flag)
# Patch 3: Permissions API query result for notifications (default = 'denied', not 'prompt')
# Patch 4: Plugin / languages array length > 0 (headless Chrome often empty)
_JS = r"""
// 1. webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// 2. chrome.runtime stub
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
window.chrome.runtime.sendMessage = window.chrome.runtime.sendMessage || (() => undefined);
window.chrome.runtime.id = window.chrome.runtime.id || 'duochrome-stub';

// 3. permissions: notifications
const _origQuery = navigator.permissions && navigator.permissions.query;
if (_origQuery) {
    navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origQuery(params);
}

// 4. plugins / languages (headless chromium returns empty)
Object.defineProperty(navigator, 'languages', {
    get: () => Object.freeze(['zh-CN', 'zh', 'en-US', 'en'])
});
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]  // length > 0 is enough
});
"""


def stealth_script() -> str:
    """Return JS source to inject via context.add_init_script()."""
    return _JS