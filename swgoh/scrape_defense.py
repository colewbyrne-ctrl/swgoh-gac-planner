import asyncio
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from pydoll.browser import Chrome

from .browser_setup import build_scraper_options
from .project_paths import csv_path, ensure_data_dirs, migrate_legacy_csvs

BASE_URL = "https://swgoh.gg"
MAX_PARSE_FAILURES = 6
CLOUDFLARE_WAIT_SECONDS = 120


def is_cloudflare_challenge(html: str) -> bool:
    clean_html = str(html or "").lower()

    return any(
        marker in clean_html
        for marker in [
            "verify you are human",
            "checking if the site connection is secure",
            "cf-turnstile",
            "cf-chl",
            "challenge-platform",
        ]
    )


async def fetch_with_human_check(
    tab,
    url: str,
    wait_seconds: int = CLOUDFLARE_WAIT_SECONDS,
) -> str:
    resp = await tab.request.get(url)

    if not is_cloudflare_challenge(resp.text):
        return resp.text

    print(
        "\nCloudflare human check detected. "
        "Solve the checkbox in the browser window; retrying after it clears."
    )
    await tab.go_to(url)

    for elapsed in range(0, wait_seconds, 5):
        await asyncio.sleep(5)
        resp = await tab.request.get(url)

        if not is_cloudflare_challenge(resp.text):
            print("Cloudflare check cleared. Continuing defense scrape.")
            return resp.text

        print(f"Still waiting on Cloudflare human check ({elapsed + 5}/{wait_seconds}s).")

    raise RuntimeError(
        "Cloudflare human check did not clear before the wait timeout."
    )


def normalize_html_result(result) -> str:
    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        for key in ["value", "data"]:
            value = result.get(key)
            if isinstance(value, str):
                return value

        inner = result.get("result")

        if isinstance(inner, str):
            return inner

        if isinstance(inner, dict):
            value = inner.get("value")
            if isinstance(value, str):
                return value

            inner_inner = inner.get("result")

            if isinstance(inner_inner, str):
                return inner_inner

            if isinstance(inner_inner, dict):
                value = inner_inner.get("value")
                if isinstance(value, str):
                    return value

        raise TypeError(
            f"Could not extract HTML string from dict. "
            f"Top-level keys: {list(result.keys())}. "
            f"Result field type: {type(result.get('result'))}."
        )

    raise TypeError(
        f"Expected rendered HTML to be str or dict, got {type(result)}"
    )


async def get_rendered_html(tab, url: str, wait_seconds: int = 4) -> str:
    await tab.go_to(url)
    await asyncio.sleep(wait_seconds)

    execute_script = getattr(tab, "execute_script", None)

    if execute_script is not None:
        try:
            result = await execute_script(
                "return document.documentElement.outerHTML"
            )
            return normalize_html_result(result)
        except Exception as e:
            print("execute_script failed:", e)

    evaluate = getattr(tab, "evaluate", None)

    if evaluate is not None:
        try:
            result = await evaluate(
                "document.documentElement.outerHTML"
            )
            return normalize_html_result(result)
        except Exception as e:
            print("evaluate failed:", e)

    get_page_source = getattr(tab, "get_page_source", None)

    if get_page_source is not None:
        try:
            result = await get_page_source()
            return normalize_html_result(result)
        except Exception as e:
            print("get_page_source failed:", e)

    raise RuntimeError("Could not get rendered HTML from this Pydoll tab.")


async def get_rendered_element_html(
    tab,
    url: str,
    selector: str,
    wait_seconds: int = 5,
) -> str | None:
    await tab.go_to(url)
    await asyncio.sleep(wait_seconds)

    script = (
        "const el = document.querySelector("
        f"{selector!r}"
        "); return el ? el.outerHTML : '';"
    )

    execute_script = getattr(tab, "execute_script", None)

    if execute_script is not None:
        try:
            html = normalize_html_result(await execute_script(script))
            return html or None
        except Exception as e:
            print("targeted execute_script failed:", e)

    evaluate = getattr(tab, "evaluate", None)

    if evaluate is not None:
        try:
            html = normalize_html_result(await evaluate(script))
            return html or None
        except Exception as e:
            print("targeted evaluate failed:", e)

    return None


async def fetch_match_html(tab, url: str, wait_seconds: int = 5) -> str:
    try:
        resp = await tab.request.get(url)
        html = resp.text

        if "battles-defense" in html:
            return html

        print(
            "Request HTML did not contain battles-defense. "
            "Falling back to rendered DOM."
        )
    except Exception as e:
        print("Request match fetch failed. Falling back to rendered DOM:", e)

    battles_html = await get_rendered_element_html(
        tab,
        url,
        "#battles-defense",
        wait_seconds=wait_seconds,
    )

    if battles_html:
        print("Using targeted rendered #battles-defense HTML.")
        return f'<div id="rendered-gac-match">{battles_html}</div>'

    print("Targeted #battles-defense lookup failed. Falling back to full rendered DOM.")
    return await get_rendered_html(tab, url, wait_seconds=wait_seconds)


def detect_gac_format(text: str) -> str | None:
    clean_text = " ".join(str(text).split()).lower()

    if re.search(r"\b3\s*(v|vs\.?|versus)\s*3\b", clean_text):
        return "3v3"

    if re.search(r"\b5\s*(v|vs\.?|versus)\s*5\b", clean_text):
        return "5v5"

    return None


def infer_gac_format_from_defenses(defenses: list[dict]) -> str | None:
    character_counts = [
        int(defense.get("unit_count", 0))
        for defense in defenses
        if defense.get("combat_type") == "characters"
    ]

    if not character_counts:
        return None

    max_character_count = max(character_counts)

    if max_character_count <= 3:
        return "3v3"

    if max_character_count >= 5:
        return "5v5"

    return None


def extract_gac_history_matches(html: str, player_id: str) -> list[dict]:
    """
    Extracts GAC history battle links from a player's GAC history page.

    Example matched link:
    /p/848865876/gac-history/O1780434000000/1/
    """
    soup = BeautifulSoup(html, "lxml")

    pattern = re.compile(
        rf"^/p/{player_id}/gac-history/O\d+/\d+/$"
    )

    matches = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if pattern.match(href):
            full_url = urljoin(BASE_URL, href)

            if full_url in seen_urls:
                continue

            context_text = ""
            context = a

            for _ in range(4):
                context_text = context.get_text(" ", strip=True)
                detected_format = detect_gac_format(context_text)

                if detected_format:
                    break

                if context.parent is None:
                    break

                context = context.parent

            matches.append({
                "url": full_url,
                "format": detect_gac_format(context_text),
                "context": context_text,
            })
            seen_urls.add(full_url)
    return matches


def extract_first_gac_history_links(html: str, player_id: str, limit: int = 6) -> list[str]:
    return [
        match["url"]
        for match in extract_gac_history_matches(html, player_id)[:limit]
    ]


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
    name_div = side.select_one(
        ".gac-counters-battle-summary__side-name, "
        ".battle-team-name, "
        ".team-name"
    )

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


def extract_battle_containers(battles_container) -> list:
    battles = battles_container.select(".gac-counters-battle-summary")

    if battles:
        return battles

    return [
        panel
        for panel in battles_container.select(".panel")
        if (
            panel.select_one("[data-unit-def-tooltip-app]")
            and "text-center" not in panel.get("class", [])
        )
    ]


def candidate_side_containers(battle) -> list:
    selectors = [
        ".gac-counters-battle-summary__side",
        ".gac-battle-side",
        ".battle-side",
        ".battle-team",
        ".gac-battle-team",
        ".team",
        ".squad",
    ]
    candidates = []
    seen = set()

    for selector in selectors:
        for container in battle.select(selector):
            units = extract_unit_ids(container)

            if not units:
                continue

            key = id(container)

            if key in seen:
                continue

            candidates.append(container)
            seen.add(key)

    return candidates


def extract_defense_side(battle):
    defense_side = battle.select_one(
        ".gac-counters-battle-summary__side--defense"
    )

    if defense_side is not None:
        return defense_side

    candidates = candidate_side_containers(battle)

    if len(candidates) >= 2:
        return candidates[-1]

    return None


def extract_defense_sides_from_gac_html(html: str) -> list[dict]:
    """
    Extracts defense-side teams/fleets from the battles-defense tab.

    Important:
    SWGOH.GG has separate battle tabs. The battles-defense tab is the section
    labeled like "Player A's attacks / Player B's defenses", which is the
    opponent defense data we want. Do not infer the split from ship/character
    ordering because ship battles can appear between character battles.
    """
    soup = BeautifulSoup(html, "lxml")

    defense_entries = []

    battles_container = soup.select_one("#battles-defense")

    if battles_container is None:
        raise ValueError("Could not find #battles-defense tab in GAC history HTML.")

    battles = extract_battle_containers(battles_container)

    if not battles:
        return []

    for battle_index, battle in enumerate(battles, start=1):
        stats = extract_battle_stats(battle)

        defense_side = extract_defense_side(battle)

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

    if not defense_entries:
        raise ValueError(
            "Found #battles-defense tab and battle panels, but could not identify defense-side units."
        )

    return defense_entries


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
    1. Extract defense-side entries from the battles-defense tab.
    2. Dedupe repeated attempts by leader/capital ship.
    """
    wanted_entries = extract_defense_sides_from_gac_html(html)

    unique_wanted_entries = dedupe_defenses_by_leader(wanted_entries)

    return unique_wanted_entries


async def scrape_defense(
    url: str,
    player_id: str,
    history_limit: int = 1,
    gac_format: str = "all",
) -> pd.DataFrame:
    """
    Scrapes recent GAC history links, then extracts the wanted player's defense teams
    from each match page.
    """
    gac_format = str(gac_format or "all").lower()

    if gac_format not in {"all", "3v3", "5v5"}:
        raise ValueError("gac_format must be one of: all, 3v3, 5v5")

    options = build_scraper_options()

    all_rows = []

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(url)

        await asyncio.sleep(3)

        history_html = await fetch_with_human_check(tab, url)

        history_matches = extract_gac_history_matches(history_html, player_id)
        accepted_count = 0
        parse_failures = 0

        for match in history_matches:
            if accepted_count >= history_limit:
                break

            link = match["url"]
            history_format = match.get("format")

            if gac_format != "all" and history_format and history_format != gac_format:
                print(
                    f"Skipping {link}: wanted {gac_format}, "
                    f"found {history_format}"
                )
                continue

            match_html = await fetch_match_html(tab, link)

            try:
                defenses = extract_wanted_defense_teams(match_html)
            except ValueError as e:
                parse_failures += 1
                print(
                    f"Skipping {link}: could not parse defense battle tab "
                    f"({parse_failures}/{MAX_PARSE_FAILURES} failures): {e}"
                )

                if parse_failures >= MAX_PARSE_FAILURES:
                    print(
                        f"Stopping defense scrape after {MAX_PARSE_FAILURES} "
                        "parse failures."
                    )
                    break

                continue

            match_format = history_format or infer_gac_format_from_defenses(defenses)

            if gac_format != "all" and match_format != gac_format:
                print(
                    f"Skipping {link}: wanted {gac_format}, "
                    f"found {match_format or 'unknown'}"
                )
                continue

            accepted_count += 1

            print(f"\nDefenses from {link} ({match_format or 'unknown format'}):")

            for defense in defenses:
                print(
                    defense["combat_type"],
                    defense["name"],
                    defense["units"]
                )

                all_rows.append({
                    "match_url": link,
                    "match_format": match_format,
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

        if accepted_count < history_limit:
            print(
                f"\nOnly found {accepted_count} matching GAC history matches "
                f"for format {gac_format}."
            )

    df = pd.DataFrame(all_rows)

    return df


def main() -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
    df = asyncio.run(
        scrape_defense(
            "https://swgoh.gg/p/848865876/gac-history/",
            player_id="848865876",
            history_limit=3,
            gac_format="3v3",
        )
    )

    print("\nFinal DataFrame:")
    print(df)
    df.to_csv(csv_path("defense_teams.csv"), index=False)


if __name__ == "__main__":
    main()
