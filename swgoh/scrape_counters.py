import ast
import asyncio
import re
from urllib.parse import parse_qs, urlparse

import pandas as pd
from bs4 import BeautifulSoup
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

from .project_paths import csv_path, ensure_data_dirs, migrate_legacy_csvs

THREE_V_THREE_SEASON_ID = "CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_79"
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


def parse_unit_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(unit).strip() for unit in value if str(unit).strip()]

    if pd.isna(value):
        return []

    text = str(value).strip()
    if not text:
        return []

    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return [str(unit).strip() for unit in parsed if str(unit).strip()]
    except (ValueError, SyntaxError):
        pass

    return [
        unit.strip().strip("'\"[]")
        for unit in text.split(",")
        if unit.strip().strip("'\"[]")
    ]


def normalize_combat_type(value) -> str:
    return str(value).strip().lower()


def parse_counter_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")

    if (
        "data-unit-def-tooltip-app" in html
        and not soup.select_one("[data-unit-def-tooltip-app]")
    ):
        return BeautifulSoup(html, "html.parser")

    return soup


def looks_like_counter_row(div) -> bool:
    text = div.get_text(" ", strip=True)
    units = div.select("[data-unit-def-tooltip-app]")

    return (
        "Seen" in text
        and re.search(r"Win\s*%", text)
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
    soup = parse_counter_soup(html)

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
    soup = parse_counter_soup(html)

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
                and re.search(r"Win\s*%", text)
                and "Avg" in text
                and len(extract_units(div)) >= 2
            ):
                candidate_rows.append(div)

    print(f"Ship candidate rows found for {defense_leader}: {len(candidate_rows)}")

    for panel in candidate_rows:
        text = panel.get_text(" ", strip=True)

        if not (
            "Seen" in text
            and re.search(r"Win\s*%", text)
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


def normalize_html_result(result) -> str:
    """
    Convert Pydoll script/evaluate/page-source results into a real HTML string.
    """
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
            f"Result field type: {type(result.get('result'))}. "
            f"Result field preview: {str(result.get('result'))[:500]}"
        )

    raise TypeError(
        f"Expected rendered HTML to be str or dict, got {type(result)}"
    )


async def get_rendered_html(tab, url: str, wait_seconds: int = 2) -> str:
    """
    Loads a page normally in the browser and returns rendered DOM HTML.

    This is used for ship counter pages because tab.request.get(...) can return
    a small shell/403-style response without the rendered counter rows.
    """
    await tab.go_to(url)
    await asyncio.sleep(wait_seconds)

    execute_script = getattr(tab, "execute_script", None)

    if execute_script is not None:
        try:
            result = await execute_script("return document.documentElement.outerHTML")
            return normalize_html_result(result)
        except Exception as e:
            print("execute_script failed:", e)

    evaluate = getattr(tab, "evaluate", None)

    if evaluate is not None:
        try:
            result = await evaluate("document.documentElement.outerHTML")
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


async def get_rendered_html_with_human_check(
    tab,
    url: str,
    wait_seconds: int = 2,
    challenge_wait_seconds: int = CLOUDFLARE_WAIT_SECONDS,
) -> str:
    html = await get_rendered_html(tab, url, wait_seconds=wait_seconds)

    if not is_cloudflare_challenge(html):
        return html

    print(
        "\nCloudflare human check detected on counter page. "
        "Solve the checkbox in the browser window; retrying until it clears."
    )

    for elapsed in range(0, challenge_wait_seconds, 5):
        await asyncio.sleep(5)
        html = await get_rendered_html(tab, url, wait_seconds=1)

        if not is_cloudflare_challenge(html):
            print("Cloudflare check cleared. Continuing counter scrape.")
            return html

        print(
            "Still waiting on Cloudflare human check "
            f"({elapsed + 5}/{challenge_wait_seconds}s)."
        )

    raise RuntimeError(
        "Cloudflare human check did not clear before the wait timeout."
    )


async def fetch_character_counter_html(tab, url: str) -> str:
    """
    Try the fast request path first, then fall back to rendered DOM.
    """
    try:
        resp = await tab.request.get(url)
        html = resp.text

        if (
            "Seen" in html
            and "Win" in html
            and "data-unit-def-tooltip-app" in html
        ):
            return html

        print("Character request HTML did not contain counter rows. Falling back to rendered HTML.")
    except Exception as e:
        print(f"Character request failed, falling back to rendered HTML: {e}")

    return await get_rendered_html_with_human_check(tab, url, wait_seconds=4)


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

            try:
                html = await fetch_character_counter_html(tab, url)
            except Exception as e:
                print(f"Could not fetch character counter page: {e}")
                continue

            df = parse_character_counter_page(html, url)

            if df.empty and "season_id=" in url:
                print("No character rows parsed from request/rendered fetch. Retrying with a longer rendered wait.")
                try:
                    html = await get_rendered_html_with_human_check(tab, url, wait_seconds=7)
                    df = parse_character_counter_page(html, url)
                except Exception as e:
                    print(f"Long rendered retry failed for character counter page: {e}")

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

            try:
                html = await get_rendered_html_with_human_check(tab, url, wait_seconds=4)
            except Exception as e:
                print(f"Could not fetch ship counter page: {e}")
                continue

            df = parse_ship_counter_page(html, url)

            if df.empty:
                print("No ship rows parsed from first rendered fetch. Retrying with a longer rendered wait.")
                try:
                    html = await get_rendered_html_with_human_check(tab, url, wait_seconds=8)
                    df = parse_ship_counter_page(html, url)
                except Exception as e:
                    print(f"Long rendered retry failed for ship counter page: {e}")

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


def normalize_gac_format(gac_format: str, defense_df: pd.DataFrame) -> str:
    gac_format = str(gac_format or "all").strip().lower()

    if gac_format in {"3v3", "5v5"}:
        return gac_format

    if "match_format" not in defense_df.columns:
        return "all"

    match_formats = {
        str(value).strip().lower()
        for value in defense_df["match_format"].dropna()
        if str(value).strip().lower() in {"3v3", "5v5"}
    }

    if len(match_formats) == 1:
        return next(iter(match_formats))

    return "all"


def build_character_counter_url(leader: str, gac_format: str) -> str:
    base_url = f"https://swgoh.gg/gac/counters/{leader}/"

    if gac_format == "3v3":
        return f"{base_url}?season_id={THREE_V_THREE_SEASON_ID}"

    return base_url


def build_counter_urls(
    defense_df: pd.DataFrame,
    gac_format: str = "all",
) -> tuple[list[str], list[str], dict[tuple[str, str], int]]:
    gac_format = normalize_gac_format(gac_format, defense_df)
    character_urls = []
    ship_urls = []
    skipped_duplicate_counts = {}
    seen_teams = set()

    for index, row in defense_df.iterrows():
        leader = str(row["leader"]).strip()
        combat_type = normalize_combat_type(row["combat_type"])
        units = tuple(parse_unit_list(row.get("units", [])))
        leader_key = (combat_type, leader)
        team_key = (combat_type, leader, units)

        if team_key in seen_teams:
            skipped_duplicate_counts[leader_key] = skipped_duplicate_counts.get(leader_key, 0) + 1
            print(
                f"Skipping duplicate row {index}: "
                f"leader={leader}, combat_type={combat_type}, "
                f"duplicate team repeat #{skipped_duplicate_counts[leader_key]}"
            )
            continue

        seen_teams.add(team_key)

        print(f"Processing row {index}: leader={leader}, combat_type={combat_type}")

        if combat_type == "characters":
            character_urls.append(
                build_character_counter_url(leader, gac_format)
            )
        else:
            if combat_type != "ships":
                print(f"Unknown combat_type for row {index}: {row['combat_type']}. Treating as ships.")

            ship_urls.append(
                f"https://swgoh.gg/gac/ship-counters/{leader}/?season_id=CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_79"
            )

    return character_urls, ship_urls, skipped_duplicate_counts


def add_leader_repeat_counts(counter_df: pd.DataFrame, repeat_counts: dict[tuple[str, str], int]) -> pd.DataFrame:
    if counter_df.empty:
        return counter_df

    counter_df = counter_df.copy()

    counter_df["skipped_duplicate_team_count"] = counter_df.apply(
        lambda row: repeat_counts.get(
            (normalize_combat_type(row.get("combat_type")), str(row.get("defense_leader")).strip()),
            0,
        ),
        axis=1,
    )

    return counter_df


def print_repeat_summary(repeat_counts: dict[tuple[str, str], int]) -> None:
    print("\nDuplicate defense teams skipped:")

    if not repeat_counts:
        print("None")
        return

    for (combat_type, leader), repeat_count in sorted(repeat_counts.items()):
        print(f"- {combat_type} {leader}: {repeat_count} duplicate team repeat(s)")


def main() -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
    df = pd.read_csv(csv_path("defense_teams.csv"))

    character_urls, ship_urls, repeat_counts = build_counter_urls(df)
    print_repeat_summary(repeat_counts)

    counter_df = asyncio.run(
        scrape_all_counters(
            character_urls=character_urls,
            ship_urls=ship_urls,
        )
    )

    counter_df = add_leader_repeat_counts(counter_df, repeat_counts)

    print("\nFinal counter DataFrame:")
    print(counter_df)

    counter_df.to_csv(csv_path("counter_results.csv"), index=False)


if __name__ == "__main__":
    main()
