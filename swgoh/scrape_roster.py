import asyncio
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from pydoll.browser import Chrome

from .browser_setup import build_scraper_options

BASE_URL = "https://swgoh.gg"


def normalize_html_result(result) -> str:
    """
    Convert Pydoll script/evaluate/page-source results into a real HTML string.

    Handles shapes like:
    - "<html>...</html>"
    - {"result": {"value": "<html>...</html>"}}
    - {"result": {"result": {"value": "<html>...</html>"}}}
    - {"id": ..., "result": {"type": "string", "value": "<html>...</html>"}}
    - {"id": ..., "result": "<html>...</html>"}
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


async def get_rendered_html(tab, url: str, wait_seconds: int = 4) -> str:
    """
    Loads a page in the browser and returns rendered DOM HTML.
    Only used as fallback when request HTML does not contain the roster cards.
    """
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


async def fetch_roster_html(tab, url: str, wait_seconds: int = 4) -> str:
    """
    Try simple request HTML first.
    If it contains roster cards, use it.
    Otherwise fall back to rendered DOM.
    """
    try:
        resp = await tab.request.get(url)
        html = resp.text

        if (
            "unit-card-grid__cell" in html
            or "js-unit-search__result" in html
            or "data-unit-name" in html
        ):
            print("Using request HTML for:", url)
            return html

        print("Request HTML did not contain roster cards. Falling back to rendered DOM.")
    except Exception as e:
        print("Request fetch failed. Falling back to rendered DOM:", e)

    return await get_rendered_html(tab, url, wait_seconds=wait_seconds)


def clean_text(text: str | None) -> str | None:
    if text is None:
        return None

    return re.sub(r"\s+", " ", text).strip()


def extract_base_id_from_href(href: str | None) -> str | None:
    """
    Example:
    /p/848865876/unit/GRANDMASTERLUKE/ -> GRANDMASTERLUKE
    """
    if not href:
        return None

    match = re.search(r"/unit/([^/]+)/", href)

    if not match:
        return None

    return match.group(1)


def extract_active_stars(card) -> int | None:
    """
    Counts active stars in rarity-range.
    Inactive stars have rarity-range__star--inactive.
    """
    stars = card.select(".rarity-range__star")

    if not stars:
        return None

    active_count = 0

    for star in stars:
        classes = star.get("class", [])

        if "rarity-range__star--inactive" not in classes:
            active_count += 1

    return active_count


def extract_first_number(container) -> int | None:
    """
    Extracts first integer from a small SVG/text badge.
    Useful for zeta count, relic level, ship level, etc.
    """
    if container is None:
        return None

    text = container.get_text(" ", strip=True)
    match = re.search(r"\d+", text)

    if not match:
        return None

    return int(match.group(0))


def extract_completion_percent(card) -> float | None:
    """
    Extracts the percent shown in unit-card__extra, e.g. 94%.
    """
    extra = card.select_one(".unit-card__extra")

    if not extra:
        return None

    text = extra.get_text(" ", strip=True)

    match = re.search(r"(\d+(?:\.\d+)?)%", text)

    if not match:
        return None

    return float(match.group(1))


def extract_image_url(card) -> str | None:
    """
    Handles character portrait style URLs like:
    --character-portrait--image-url: url(...)
    Also handles ship img src.
    """

    portrait_with_style = card.select_one("[style*='image-url']")

    if portrait_with_style:
        style = portrait_with_style.get("style", "")
        match = re.search(r"url\(([^)]+)\)", style)

        if match:
            return match.group(1).strip("'\"")

    img = card.select_one(".ship-portrait__img")

    if img and img.get("src"):
        return img.get("src")

    return None


def extract_alignment(card) -> int | None:
    """
    Extracts alignment number from classes like:
    character-portrait--alignment-2
    ship-portrait--alignment-3
    """
    class_text = " ".join(
        " ".join(el.get("class", []))
        for el in card.select("[class]")
    )

    match = re.search(r"alignment-(\d+)", class_text)

    if not match:
        return None

    return int(match.group(1))


def parse_unit_card(cell, combat_type: str, player_id: str) -> dict:
    """
    Parses one unit-card-grid__cell into a row.
    Works for both characters and ships where possible.
    """
    name = cell.get("data-unit-name")
    tags_raw = cell.get("data-unit-tags")

    tags = []

    if tags_raw:
        tags = [tag.strip() for tag in tags_raw.split(",") if tag.strip()]

    link = cell.select_one("a[href*='/unit/']")
    href = link.get("href") if link else None
    full_url = urljoin(BASE_URL, href) if href else None
    base_id = extract_base_id_from_href(href)

    unit_card = cell.select_one(".unit-card") or cell
    class_text = " ".join(unit_card.get("class", []))

    portrait = (
        cell.select_one(".character-portrait")
        or cell.select_one(".ship-portrait")
    )

    portrait_class_text = ""

    if portrait:
        portrait_class_text = " ".join(portrait.get("class", []))

    zeta_count = extract_first_number(cell.select_one(".character-portrait__zeta"))
    relic_level = extract_first_number(cell.select_one(".character-portrait__relic"))
    ship_level = extract_first_number(cell.select_one(".ship-portrait__level"))

    stars = extract_active_stars(cell)
    completion_percent = extract_completion_percent(cell)
    image_url = extract_image_url(cell)
    alignment = extract_alignment(cell)

    is_galactic_legend = (
        "Galactic Legend" in tags
        or "unit-card--is-galactic-legend" in class_text
        or "character-portrait--is-galactic-legend" in portrait_class_text
    )

    has_ultimate = (
        "character-portrait--has-ultimate" in portrait_class_text
        or "relic-badge--has-ultimate" in str(cell)
    )

    is_capital_ship = (
        "ship-portrait--is-capital-ship" in portrait_class_text
        or "Capital Ship" in tags
    )

    if not name:
        name_el = cell.select_one(".unit-card__name")
        name = clean_text(name_el.get_text(" ", strip=True)) if name_el else None

    return {
        "player_id": player_id,
        "combat_type": combat_type,
        "name": clean_text(name),
        "base_id": base_id,
        "tags": ",".join(tags),
        "alignment": alignment,
        "stars": stars,
        "zeta_count": zeta_count,
        "relic_level": relic_level,
        "ship_level": ship_level,
        "completion_percent": completion_percent,
        "is_galactic_legend": is_galactic_legend,
        "has_ultimate": has_ultimate,
        "is_capital_ship": is_capital_ship,
        "unit_url": full_url,
        "image_url": image_url,
    }


def parse_roster_page(
    html: str,
    url: str,
    player_id: str,
    combat_type: str
) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    rows = []

    cells = soup.select(".unit-card-grid__cell.js-unit-search__result")

    if not cells:
        cells = soup.select(".js-unit-search__result")

    print(f"{combat_type} roster cells found:", len(cells))

    for cell in cells:
        row = parse_unit_card(
            cell=cell,
            combat_type=combat_type,
            player_id=player_id,
        )

        if not row["base_id"] and not row["name"]:
            continue

        rows.append(row)

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.drop_duplicates(
        subset=[
            "player_id",
            "combat_type",
            "base_id",
        ]
    )

    return df


def debug_roster_page(html: str, url: str, combat_type: str) -> None:
    soup = BeautifulSoup(html, "lxml")

    print(f"\n========== {combat_type.upper()} ROSTER DEBUG ==========")
    print("URL:", url)
    print("HTML length:", len(html))
    print("Contains unit-card-grid:", "unit-card-grid" in html)
    print("Contains js-unit-search__result:", "js-unit-search__result" in html)
    print("Contains data-unit-name:", "data-unit-name" in html)
    print("Contains /unit/:", "/unit/" in html)
    print("unit-card-grid cells:", len(soup.select(".unit-card-grid__cell")))
    print("js-unit-search results:", len(soup.select(".js-unit-search__result")))
    print("character portraits:", len(soup.select(".character-portrait")))
    print("ship portraits:", len(soup.select(".ship-portrait")))

    cells = soup.select(".unit-card-grid__cell.js-unit-search__result")

    if not cells:
        cells = soup.select(".js-unit-search__result")

    print("\nFirst few cells:")

    for i, cell in enumerate(cells[:5], start=1):
        print("-" * 80)
        print("Cell:", i)
        print("data-unit-name:", cell.get("data-unit-name"))
        print("data-unit-tags:", cell.get("data-unit-tags"))

        link = cell.select_one("a[href*='/unit/']")
        print("href:", link.get("href") if link else None)

        units_text = cell.get_text(" ", strip=True)
        print("text:", units_text[:300])

    print(f"========== END {combat_type.upper()} ROSTER DEBUG ==========\n")


async def scrape_roster(player_id: str, debug: bool = True) -> pd.DataFrame:
    options = build_scraper_options()

    character_url = f"https://swgoh.gg/p/{player_id}/characters/"
    ship_url = f"https://swgoh.gg/p/{player_id}/ships/"

    all_dfs = []

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        pages = [
            ("characters", character_url),
            ("ships", ship_url),
        ]

        for combat_type, url in pages:
            print(f"\nScraping {combat_type} roster page: {url}")

            html = await fetch_roster_html(tab, url, wait_seconds=4)

            if debug:
                debug_roster_page(html, url, combat_type)

            df = parse_roster_page(
                html=html,
                url=url,
                player_id=player_id,
                combat_type=combat_type,
            )

            if df.empty:
                print(f"No {combat_type} roster units parsed.")
            else:
                print(df[[
                    "combat_type",
                    "name",
                    "base_id",
                    "stars",
                    "relic_level",
                    "ship_level",
                    "zeta_count",
                    "completion_percent",
                ]].head(20))

                all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    roster_df = pd.concat(all_dfs, ignore_index=True)

    return roster_df


def main() -> None:
    roster_df = asyncio.run(
        scrape_roster(
            player_id="848865876",
            debug=True,
        )
    )

    print("\nFinal roster DataFrame:")
    print(roster_df)

    roster_df.to_csv("roster_units.csv", index=False)


if __name__ == "__main__":
    main()
