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
    units = []

    for unit in container.select("[data-unit-def-tooltip-app]"):
        unit_id = unit.get("data-unit-def-tooltip-app")

        if unit_id and unit_id not in units:
            units.append(unit_id)

    return units


def dedupe_units(units: list[str]) -> list[str]:
    seen = set()
    clean = []

    for unit in units:
        if unit not in seen:
            seen.add(unit)
            clean.append(unit)

    return clean


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


def split_units_around_stats(container) -> tuple[list[str], list[str]]:
    row_html = str(container)

    possible_markers = [
        "Win %",
        ">Win %<",
        "Seen",
        ">Seen<",
    ]

    stats_index = -1

    for marker in possible_markers:
        stats_index = row_html.find(marker)
        if stats_index != -1:
            break

    pattern = re.compile(
        r'data-unit-def-tooltip-app\s*=\s*["\']([^"\']+)["\']'
    )

    units_with_positions = []

    for match in pattern.finditer(row_html):
        unit_id = match.group(1)
        position = match.start()
        units_with_positions.append((unit_id, position))

    if stats_index == -1:
        return dedupe_units([unit for unit, _ in units_with_positions]), []

    counter_units = [
        unit for unit, position in units_with_positions
        if position < stats_index
    ]

    defense_units = [
        unit for unit, position in units_with_positions
        if position > stats_index
    ]

    return dedupe_units(counter_units), dedupe_units(defense_units)


def parse_character_counter_page(html: str, url: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)

    path_parts = [part for part in parsed_url.path.split("/") if part]
    defense_leader = path_parts[-1] if path_parts else None

    season_id = query.get("season_id", [None])[0]

    rows = []

    for div in soup.find_all("div"):
        if not looks_like_counter_row(div):
            continue

        if has_counter_row_child(div):
            continue

        text = div.get_text(" ", strip=True)
        stats = extract_stats(text)

        counter_units, defense_units = split_units_around_stats(div)

        if not counter_units:
            continue

        rows.append({
            "combat_type": "characters",
            "defense_leader": defense_leader,
            "season_id": season_id,
            "counter_leader": counter_units[0],
            "counter_units": counter_units,
            "defense_units": defense_units,
            "seen": stats["seen"],
            "win_percent": stats["win_percent"],
            "avg_banners": stats["avg_banners"],
            "raw_text": text,
            "source_url": url,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.drop_duplicates(
        subset=[
            "combat_type",
            "defense_leader",
            "season_id",
            "counter_leader",
            "seen",
            "win_percent",
            "avg_banners",
        ]
    )


def parse_ship_counter_page(html: str, url: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)

    path_parts = [part for part in parsed_url.path.split("/") if part]
    defense_leader = path_parts[-1] if path_parts else None

    season_id = query.get("season_id", [None])[0]
    rows = []

    candidate_rows = soup.select(".panel.panel--size-sm")

    if not candidate_rows:
        for div in soup.find_all("div"):
            text = div.get_text(" ", strip=True)

            if (
                "Seen" in text
                and "Win %" in text
                and "Avg" in text
                and len(extract_units(div)) >= 2
            ):
                candidate_rows.append(div)

    print(f"Ship candidate rows found for {defense_leader}: {len(candidate_rows)}")

    for index, panel in enumerate(candidate_rows, start=1):
        text = panel.get_text(" ", strip=True)

        if not (
            "Seen" in text
            and "Win %" in text
            and "Avg" in text
        ):
            continue

        stats = extract_stats(text)
        counter_units, defense_units = split_units_around_stats(panel)
        if not counter_units or not defense_units:
            continue

        rows.append({
            "combat_type": "ships",
            "defense_leader": defense_leader,
            "season_id": season_id,
            "counter_leader": counter_units[0],
            "counter_units": counter_units,
            "defense_units": defense_units,
            "seen": stats["seen"],
            "win_percent": stats["win_percent"],
            "avg_banners": stats["avg_banners"],
            "raw_text": text,
            "source_url": url,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.drop_duplicates(
        subset=[
            "combat_type",
            "defense_leader",
            "season_id",
            "counter_leader",
            "seen",
            "win_percent",
            "avg_banners",
        ]
    )


async def get_rendered_html(tab, url: str, wait_seconds: int = 5) -> str:
    """
    Loads a page normally in the browser and returns rendered DOM HTML.

    This is used for ship counter pages because tab.request.get(...) can return
    a small shell/403-style response without the rendered counter rows.
    """
    await tab.go_to(url)
    await asyncio.sleep(wait_seconds)

    try:
        return await tab.execute_script("return document.documentElement.outerHTML")
    except Exception:
        try:
            return await tab.evaluate("document.documentElement.outerHTML")
        except Exception:
            return await tab.get_page_source()


async def scrape_character_counters(urls: list[str]) -> pd.DataFrame:
    """
    Character counters keep the original behavior:
    use tab.request.get(url), then parse the response text.
    """
    if not urls:
        return pd.DataFrame()

    options = ChromiumOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")

    all_rows = []

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        await tab.go_to(urls[0])
        await asyncio.sleep(3)

        for url in urls:
            print(f"\nScraping CHARACTER counter page: {url}")

            resp = await tab.request.get(url)

            df = parse_character_counter_page(resp.text, url)

            if df.empty:
                print("No character rows parsed.")
            else:
                print(df[[
                    "combat_type",
                    "defense_leader",
                    "counter_leader",
                    "counter_units",
                    "defense_units",
                    "seen",
                    "win_percent",
                    "avg_banners",
                ]])
                all_rows.append(df)

    if not all_rows:
        return pd.DataFrame()

    return pd.concat(all_rows, ignore_index=True)


async def scrape_ship_counters(urls: list[str]) -> pd.DataFrame:
    """
    Ship counters use a separate browser flow:
    go_to(url), wait, then parse rendered DOM HTML.

    This avoids tab.request.get(url), which was returning a 403/shell page for ships.
    """
    if not urls:
        return pd.DataFrame()

    options = ChromiumOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")

    all_rows = []

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # Start directly on the first ship URL.
        await tab.go_to(urls[0])
        await asyncio.sleep(5)

        for url in urls:
            print(f"\nScraping SHIP counter page: {url}")

            resp = await tab.request.get(url)

            df = parse_ship_counter_page(resp.text, url)

            if df.empty:
                print("No ship rows parsed.")
            else:
                print(df[[
                    "combat_type",
                    "defense_leader",
                    "counter_leader",
                    "counter_units",
                    "defense_units",
                    "seen",
                    "win_percent",
                    "avg_banners",
                ]])
                all_rows.append(df)

    if not all_rows:
        return pd.DataFrame()

    return pd.concat(all_rows, ignore_index=True)


async def scrape_all_counters(character_urls: list[str], ship_urls: list[str]) -> pd.DataFrame:
    character_df = await scrape_character_counters(character_urls)
    ship_df = await scrape_ship_counters(ship_urls)

    dfs = []

    if not character_df.empty:
        dfs.append(character_df)

    if not ship_df.empty:
        dfs.append(ship_df)

    if not dfs:
        return pd.DataFrame()

    return pd.concat(dfs, ignore_index=True)


df = pd.read_csv("defense_teams.csv")

character_urls = []
ship_urls = []

for index, row in df.iterrows():
    leader = row["leader"]
    combat_type = row["combat_type"]

    print(f"Processing row {index}: leader={leader}, combat_type={combat_type}")

    if combat_type == "characters":
        character_urls.append(
            f"https://swgoh.gg/gac/counters/{leader}/"
        )
    else:
        ship_urls.append(
            f"https://swgoh.gg/gac/ship-counters/{leader}/?season_id=CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_79"
        )

counter_df = asyncio.run(
    scrape_all_counters(
        character_urls=character_urls,
        ship_urls=ship_urls,
    )
)

print("\nFinal counter DataFrame:")
print(counter_df)

counter_df.to_csv("counter_results.csv", index=False)