"""Tests for counter page URL building.

The scraper normally hits the site's default counter page (most recent
season). ``season_id`` lets a run be pinned to a specific season page, given
as a full URL, a season id, or just the season number.
"""

import pandas as pd

from swgoh.scrape_counters import (
    SHIP_SEASON_ID,
    THREE_V_THREE_SEASON_ID,
    build_character_counter_url,
    build_counter_urls,
    build_ship_counter_url,
    normalize_season_id,
)

SAMPLE_URL = (
    "https://swgoh.gg/gac/counters/GLREY/"
    "?season_id=CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_80"
)
SEASON_80 = "CHAMPIONSHIPS_GRAND_ARENA_GA2_EVENT_SEASON_80"


def test_blank_season_is_none():
    assert normalize_season_id("") is None
    assert normalize_season_id(None) is None
    assert normalize_season_id("   ") is None


def test_season_from_full_url():
    assert normalize_season_id(SAMPLE_URL) == SEASON_80


def test_season_from_bare_number():
    assert normalize_season_id("80") == SEASON_80


def test_season_id_passes_through():
    assert normalize_season_id(SEASON_80) == SEASON_80


def test_character_url_defaults_to_most_recent_page():
    assert build_character_counter_url("GLREY", "5v5") == "https://swgoh.gg/gac/counters/GLREY/"


def test_character_url_keeps_3v3_default_season():
    assert build_character_counter_url("GLREY", "3v3") == (
        f"https://swgoh.gg/gac/counters/GLREY/?season_id={THREE_V_THREE_SEASON_ID}"
    )


def test_character_url_override_wins_over_3v3_default():
    assert build_character_counter_url("GLREY", "3v3", "80") == SAMPLE_URL


def test_ship_url_defaults_to_ship_season():
    assert build_ship_counter_url("EXECUTRIX") == (
        f"https://swgoh.gg/gac/ship-counters/EXECUTRIX/?season_id={SHIP_SEASON_ID}"
    )


def test_ship_url_uses_override():
    assert build_ship_counter_url("EXECUTRIX", SAMPLE_URL) == (
        f"https://swgoh.gg/gac/ship-counters/EXECUTRIX/?season_id={SEASON_80}"
    )


def test_build_counter_urls_applies_season_to_both_types():
    defense_df = pd.DataFrame(
        [
            {"leader": "GLREY", "combat_type": "characters", "units": ["GLREY"]},
            {"leader": "EXECUTRIX", "combat_type": "ships", "units": ["EXECUTRIX"]},
        ]
    )

    character_urls, ship_urls, _ = build_counter_urls(
        defense_df,
        gac_format="5v5",
        season_id="80",
    )

    assert character_urls == [SAMPLE_URL]
    assert ship_urls == [
        f"https://swgoh.gg/gac/ship-counters/EXECUTRIX/?season_id={SEASON_80}"
    ]


def test_build_counter_urls_without_season_keeps_old_behavior():
    defense_df = pd.DataFrame(
        [
            {"leader": "GLREY", "combat_type": "characters", "units": ["GLREY"]},
            {"leader": "EXECUTRIX", "combat_type": "ships", "units": ["EXECUTRIX"]},
        ]
    )

    character_urls, ship_urls, _ = build_counter_urls(defense_df, gac_format="5v5")

    assert character_urls == ["https://swgoh.gg/gac/counters/GLREY/"]
    assert ship_urls == [
        f"https://swgoh.gg/gac/ship-counters/EXECUTRIX/?season_id={SHIP_SEASON_ID}"
    ]
