import asyncio
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from bs4 import BeautifulSoup
import re
import pandas as pd
from urllib.parse import urljoin


BASE_URL = "https://swgoh.gg"


def extract_first_gac_history_links(html: str, player_id: str, limit: int = 6) -> list[str]:
    """
    Extracts the first GAC history battle links from a player's GAC history page.

    Example matched link:
    /p/848865876/gac-history/O1780434000000/1/
    """
    soup = BeautifulSoup(html, "lxml")

    pattern = re.compile(
        rf"^/p/{player_id}/gac-history/O\d+/\d+/$"
    )

    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if pattern.match(href):
            full_url = urljoin(BASE_URL, href)

            if full_url not in links:
                links.append(full_url)

            if len(links) == limit:
                break

    return links


def extract_unit_ids(container) -> list[str]:
    """
    Extract unit IDs from one specific container.

    Empty slots are ignored because they do not have data-unit-def-tooltip-app.
    Works for both characters and ships.
    """
    units = []

    for el in container.select("[data-unit-def-tooltip-app]"):
        unit_id = el.get("data-unit-def-tooltip-app")

        if unit_id and unit_id not in units:
            units.append(unit_id)

    return units


def extract_side_name(side) -> str | None:
    """
    Extracts the displayed team/fleet name.

    Examples:
    - Malevolence
    - Darth Revan
    - Qui-Gon Jinn
    """
    name_div = side.select_one(".gac-counters-battle-summary__side-name")

    if not name_div:
        return None

    text = name_div.get_text(" ", strip=True)

    # Remove button/link labels that appear beside the name.
    text = text.replace("Counters", "")
    text = text.replace("Insight", "")
    text = text.strip()

    return text or None


def is_ship_side(side) -> bool:
    """
    Detects whether this side is a ship/fleet battle.
    """
    return side.select_one(".gac-battle-portrait-layout--ship") is not None


def extract_battle_stats(battle) -> dict:
    """
    Extract battle-level stats like banners, attempt, outcome, zone, date.
    """
    stats = {}

    for stat in battle.select(".gac-counters-battle-summary__stat"):
        label_el = stat.select_one(".gac-counters-battle-summary__stat-label")
        value_el = stat.select_one(".gac-counters-battle-summary__stat-value")

        if not label_el or not value_el:
            continue

        label = label_el.get_text(" ", strip=True).lower().replace(" ", "_")
        value = value_el.get_text(" ", strip=True)

        stats[label] = value

    return stats


def extract_defense_sides_from_gac_html(html: str) -> list[dict]:
    """
    Extracts every defense-side team/fleet from the battle summaries.

    Important:
    This still includes both players' defense teams.
    Later, we find the ship -> character transition to keep only the wanted second section.
    """
    soup = BeautifulSoup(html, "lxml")

    defense_entries = []

    battles = soup.select(".gac-counters-battle-summary")

    for battle_index, battle in enumerate(battles, start=1):
        stats = extract_battle_stats(battle)

        defense_side = battle.select_one(
            ".gac-counters-battle-summary__side--defense"
        )

        if defense_side is None:
            continue

        units = extract_unit_ids(defense_side)

        if not units:
            continue

        combat_type = "ships" if is_ship_side(defense_side) else "characters"

        defense_entries.append({
            "battle_index": battle_index,
            "side": "defense",
            "name": extract_side_name(defense_side),
            "combat_type": combat_type,
            "units": units,
            "leader": units[0],
            "unit_count": len(units),
            "banners": stats.get("banners"),
            "attempt": stats.get("attempt"),
            "outcome": stats.get("outcome"),
            "zone": stats.get("zone"),
            "date": stats.get("date"),
        })

    return defense_entries


def take_second_player_defenses_after_first_ship_section(entries: list[dict]) -> list[dict]:
    """
    Definitive split rule based on page order.

    Expected page order:
    1. Player 1 character defenses
    2. Player 1 ship defenses
    3. Player 2 character defenses  <-- wanted info starts here
    4. Player 2 ship defenses

    So:
    - Wait until we see at least one ship defense.
    - Then the first character defense after that ship section starts player 2.
    """
    seen_ship_section = False

    for i, entry in enumerate(entries):
        if entry["combat_type"] == "ships":
            seen_ship_section = True
            continue

        if seen_ship_section and entry["combat_type"] == "characters":
            return entries[i:]

    raise ValueError(
        "Could not find the transition from first player's ships to second player's characters."
    )


def dedupe_defenses_by_leader(entries: list[dict]) -> list[dict]:
    """
    Dedupe by leader/capital ship instead of exact unit list.

    Why:
    The same defensive team/fleet can appear multiple times if it took multiple attempts.
    Ships especially may appear with different order or missing/dead units.

    Rule:
    - Use combat_type + leader/capital ship as the key.
    - If duplicate leader appears, keep the version with the most units.
    - Preserve first-seen order.
    """
    order = []
    best_by_key = {}

    for entry in entries:
        key = (
            entry["combat_type"],
            entry["leader"],
        )

        if key not in best_by_key:
            order.append(key)
            best_by_key[key] = entry
            continue

        current_best = best_by_key[key]

        if len(entry["units"]) > len(current_best["units"]):
            best_by_key[key] = entry

    return [best_by_key[key] for key in order]


def extract_wanted_defense_teams(html: str) -> list[dict]:
    """
    Full defense extraction pipeline:
    1. Extract every defense-side entry from the page.
    2. Find the boundary after player 1's ship section.
    3. Keep everything after that boundary.
    4. Dedupe repeated attempts by leader/capital ship.
    """
    all_entries = extract_defense_sides_from_gac_html(html)

    wanted_entries = take_second_player_defenses_after_first_ship_section(
        all_entries
    )

    unique_wanted_entries = dedupe_defenses_by_leader(wanted_entries)

    return unique_wanted_entries


async def scrape_defense(
    url: str,
    player_id: str,
    history_limit: int = 1,
) -> pd.DataFrame:
    """
    Scrapes recent GAC history links, then extracts the wanted player's defense teams
    from each match page.
    """
    options = ChromiumOptions()

    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")

    all_rows = []

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(url)

        await asyncio.sleep(3)

        resp = await tab.request.get(url)

        links = extract_first_gac_history_links(
            resp.text,
            player_id,
            limit=history_limit
        )

        print("GAC links found:")
        for link in links:
            print(link)

        for link in links:
            resp = await tab.request.get(link)

            defenses = extract_wanted_defense_teams(resp.text)

            print(f"\nDefenses from {link}:")

            for defense in defenses:
                print(
                    defense["combat_type"],
                    defense["name"],
                    defense["units"]
                )

                all_rows.append({
                    "match_url": link,
                    "combat_type": defense["combat_type"],
                    "name": defense["name"],
                    "leader": defense["leader"],
                    "units": defense["units"],
                    "unit_count": len(defense["units"]),
                    "banners": defense["banners"],
                    "attempt": defense["attempt"],
                    "outcome": defense["outcome"],
                    "zone": defense["zone"],
                    "date": defense["date"],
                })

    df = pd.DataFrame(all_rows)

    return df


df = asyncio.run(
    scrape_defense(
        "https://swgoh.gg/p/848865876/gac-history/",
        player_id="848865876",
        history_limit=1,
    )
)

print("\nFinal DataFrame:")
print(df)
df.to_csv("defense_teams.csv", index=False)