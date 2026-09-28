"""Lightweight User-Agent parsing (no external dependency).

Extracts browser, version, operating system and device type. This is
best-effort label extraction for a session log, not general-purpose device
detection — the IP side of the same log lives in `app.core.http.ip`.
"""

import re

_BOTS = ("bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests", "httpx")

_BROWSERS = (
    ("Edge", ("edg/", "edgios/", "edga/"), r"edg(?:ios|a)?/([\d.]+)"),
    ("Opera", ("opr/", "opera"), r"(?:opr|opera)[ /]([\d.]+)"),
    ("Chrome", ("chrome/", "crios/"), r"(?:chrome|crios)/([\d.]+)"),
    ("Firefox", ("firefox/", "fxios/"), r"(?:firefox|fxios)/([\d.]+)"),
    ("Safari", ("safari/",), r"version/([\d.]+)"),
    ("curl", ("curl/",), r"curl/([\d.]+)"),
    ("wget", ("wget/",), r"wget/([\d.]+)"),
)

_SYSTEMS = (
    ("Windows", ("windows nt",), r"windows nt ([\d.]+)"),
    ("Android", ("android",), r"android ([\d.]+)"),
    ("iOS", ("iphone", "ipad", "ipod"), r"os ([\d_]+)"),
    ("macOS", ("mac os x", "macintosh"), r"mac os x ([\d_]+)"),
    ("Linux", ("linux",), None),
    ("ChromeOS", ("cros",), None),
)


def parse_user_agent(user_agent: str | None) -> dict:
    """Return browser/os/device details parsed from a User-Agent header."""
    ua = user_agent or ""
    lowered = ua.lower()

    browser, browser_version = "Unknown", None
    for name, needles, version_re in _BROWSERS:
        if any(needle in lowered for needle in needles):
            browser = name
            match = re.search(version_re, lowered)
            if match:
                browser_version = match.group(1)
            break

    os_name, os_version = "Unknown", None
    for name, needles, version_re in _SYSTEMS:
        if any(needle in lowered for needle in needles):
            os_name = name
            if version_re:
                match = re.search(version_re, lowered)
                if match:
                    os_version = match.group(1).replace("_", ".")
            break

    if "ipad" in lowered or "tablet" in lowered:
        device_type = "Tablet"
    elif any(token in lowered for token in ("mobile", "iphone", "android", "ipod")):
        device_type = "Mobile"
    elif "mozilla/5.0" in lowered or "macintosh" in lowered or "windows" in lowered:
        device_type = "Desktop"
    else:
        device_type = "Unknown"

    is_bot = any(token in lowered for token in _BOTS)
    if is_bot:
        device_type = "Bot"

    return {
        "browser": browser,
        "browser_version": browser_version,
        "os": os_name,
        "os_version": os_version,
        "device_type": device_type,
        "is_bot": is_bot,
    }
