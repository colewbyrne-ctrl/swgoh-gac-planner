import asyncio
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from bs4 import BeautifulSoup
import re
import pandas as pd
from urllib.parse import urlparse, parse_qs

def extract_stats(text: str) -> dict:
    seen_match = re.search(r"Seen\s+([\d,]+)", text)
    win_match = re.search(r"Win\s*%\s+(\d+(?:\.\d+)?)%", text)
    avg_match = re.search(r"Avg\s+(\d+(?:\.\d+)?)", text)

    return {
        "seen": int(seen_match.group(1).replace(",", "")) if seen_match else None,
        "win_percent": float(win_match.group(1)) if win_match else None,
        "avg_banners": float(avg_match.group(1)) if avg_match else None,
    }

def extract_units(container) -> list[str]:
    return [
        unit.get("data-unit-def-tooltip-app")
        for unit in container.select("[data-unit-def-tooltip-app]")
        if unit.get("data-unit-def-tooltip-app")
    ]
def looks_like_counter_row(div) -> bool:
    text = div.get_text(" ", strip=True)
    units = div.select("[data-unit-def-tooltip-app]")

    return (
        "Seen" in text
        and "Win %" in text
        and "Avg" in text
        and len(units) >= 1
    )
def has_counter_row_child(div) -> bool:
    for child in div.find_all("div", recursive=False):
        if looks_like_counter_row(child):
            return True
    return False

def parse_counter_page(html: str, url: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)

    # Example: /gac/counters/GLREY/
    path_parts = [part for part in parsed_url.path.split("/") if part]
    defense_leader = path_parts[-1] if path_parts else None

    season_id = query.get("season_id", [None])[0]

    rows = []

    for div in soup.find_all("div"):
        if not looks_like_counter_row(div):
            continue

        # Avoid collecting huge parent blocks when a smaller child row exists.
        if has_counter_row_child(div):
            continue

        text = div.get_text(" ", strip=True)
        stats = extract_stats(text)
        units = extract_units(div)

        if not units:
            continue

        rows.append({
            "defense_leader": defense_leader,
            "season_id": season_id,
            "units_in_row": units,
            "seen": stats["seen"],
            "win_percent": stats["win_percent"],
            "avg_banners": stats["avg_banners"],
            "raw_text": text,
        })

    return pd.DataFrame(rows)

async def scrape_counter(urls: list[str]) -> pd.DataFrame:
    
    options = ChromiumOptions()

    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(urls[0])

        # Find elements and interact with human-like timing
        star_button = await tab.find(
            tag_name='button',
            timeout=5,
            raise_exc=False
        )
        if not star_button:
            print("Ops! The button was not found.")
            return

        await asyncio.sleep(3)

        for url in urls:
            resp = await tab.request.get(url)
        
            df = parse_counter_page(resp.text, resp.url)
            print(df)

urls = ["https://swgoh.gg/gac/counters/JABBATHEHUTT/?season_id=CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_78",
        "https://swgoh.gg/gac/counters/GLREY/?season_id=CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_78"]
asyncio.run(scrape_counter(urls))

#https://swgoh.gg/p/848865876/characters/

#https://swgoh.gg/p/848865876/gac-history/    #need to grab the links to the team comps #then scrape all the characters listed