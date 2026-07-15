"""Shared Chrome/pydoll launch configuration for the scrapers.

All scrapers use one persistent profile so the Cloudflare `cf_clearance` cookie
is reused between runs (solve once, then skip the challenge until it expires).

pydoll already supplies ``--no-first-run`` / ``--no-default-browser-check`` by
default, so we only add our own flags here.
"""

from pydoll.browser.options import ChromiumOptions

from .project_paths import browser_profile_dir


def build_scraper_options() -> ChromiumOptions:
    options = ChromiumOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # Persist cookies (incl. Cloudflare clearance) across runs.
    options.add_argument(f"--user-data-dir={browser_profile_dir()}")
    return options
