"""Tests for exempting a counter leader from the build-quality check.

A galactic legend carries a team even when the support slots are unfinished, so
teams they lead should stay on the board. The exemption waives the relic
minimum only -- ownership is still required, since a team you cannot field is
useless as a plan -- and it costs score, so a waived team never outranks an
equivalent fully built one.
"""

import pandas as pd
import pytest

from swgoh.make_strategy import (
    LEADER_EXEMPTIONS_FILE,
    MIN_CHARACTER_RELIC_LEVEL,
    UNDERBUILT_UNIT_SCORE_PENALTY,
    find_valid_counters_for_defense,
    load_leader_exemptions,
    roster_has_units,
    score_counter,
)
from swgoh.web import service

STRONG = MIN_CHARACTER_RELIC_LEVEL + 4
WEAK = MIN_CHARACTER_RELIC_LEVEL - 3


@pytest.fixture
def roster():
    units = {
        "GLLEIA": STRONG, "CARRY": STRONG, "SOLID": STRONG,
        "FILLER": WEAK, "FILLER2": WEAK,
    }
    by_unit = {
        unit: pd.Series({"base_id": unit, "relic_level": relic, "stars": 7,
                         "completion_percent": 80.0, "zeta_count": 1})
        for unit, relic in units.items()
    }
    return set(by_unit), by_unit


def test_underbuilt_support_blocks_a_team_without_an_exemption(roster):
    roster_set, by_unit = roster
    allowed, _, underbuilt, _ = roster_has_units(
        ["GLLEIA", "CARRY", "FILLER", "FILLER2"], "characters", roster_set, by_unit
    )
    assert allowed is False
    assert underbuilt


def test_exemption_lets_underbuilt_support_through(roster):
    roster_set, by_unit = roster
    allowed, _, underbuilt, note = roster_has_units(
        ["GLLEIA", "CARRY", "FILLER", "FILLER2"], "characters", roster_set, by_unit,
        waive_build_quality=True,
    )
    assert allowed is True
    assert "FILLER" in note and "leader exemption" in note
    assert underbuilt  # still reported, just not disqualifying


def test_exemption_does_not_conjure_units_you_do_not_own(roster):
    roster_set, by_unit = roster
    allowed, missing, _, note = roster_has_units(
        ["GLLEIA", "CARRY", "NOTOWNED", "FILLER"], "characters", roster_set, by_unit,
        waive_build_quality=True,
    )
    assert allowed is False
    assert missing == ["NOTOWNED"]
    assert "missing" in note


def test_exemption_never_rescues_a_leader_you_do_not_own(roster):
    roster_set, by_unit = roster
    allowed, _, _, note = roster_has_units(
        ["NOTOWNED", "CARRY", "SOLID"], "characters", roster_set, by_unit,
        waive_build_quality=True,
    )
    assert allowed is False
    assert "leader is missing" in note


def counters_frame(rows):
    df = pd.DataFrame(rows)
    df["counter_units"] = df["counter_units"].apply(list)
    return df


@pytest.fixture
def defense_and_counters():
    defense = pd.Series({"combat_type": "characters", "leader": "DEF_A"})
    counters = counters_frame([
        {"combat_type": "characters", "defense_leader": "DEF_A", "counter_leader": "GLLEIA",
         "counter_units": ["GLLEIA", "CARRY", "FILLER", "FILLER2"], "seen": 100, "win_percent": 95.0,
         "avg_banners": 60.0},
        {"combat_type": "characters", "defense_leader": "DEF_A", "counter_leader": "SOLID",
         "counter_units": ["SOLID"], "seen": 100, "win_percent": 95.0, "avg_banners": 60.0},
    ])
    return defense, counters


def valid_leaders(defense, counters, roster, exempt=None):
    roster_set, by_unit = roster
    from collections import defaultdict
    options = find_valid_counters_for_defense(
        defense, counters, roster_set, by_unit, defaultdict(list),
        exempt_leaders=exempt,
    )
    return options


def test_planner_skips_the_team_until_the_leader_is_exempt(defense_and_counters, roster):
    defense, counters = defense_and_counters
    assert [o["counter_leader"] for o in valid_leaders(defense, counters, roster)] == ["SOLID"]

    with_exemption = valid_leaders(defense, counters, roster, exempt={"GLLEIA"})
    assert {o["counter_leader"] for o in with_exemption} == {"GLLEIA", "SOLID"}


def test_a_waived_team_pays_a_penalty_per_weak_unit(defense_and_counters, roster):
    # Cost is about conserving good units, so an underbuilt team is cheap and would
    # otherwise gain score for being waived. The penalty prices each waived unit as
    # if it had just cleared the relic bar.
    defense, counters = defense_and_counters
    roster_set, by_unit = roster

    option = next(
        o for o in valid_leaders(defense, counters, roster, exempt={"GLLEIA"})
        if o["counter_leader"] == "GLLEIA"
    )
    unpenalised = score_counter(counters.iloc[0], by_unit)

    assert option["score"] == pytest.approx(unpenalised - UNDERBUILT_UNIT_SCORE_PENALTY * 2)


def test_exemption_only_applies_to_the_leader_named(defense_and_counters, roster):
    defense, counters = defense_and_counters
    leaders = {o["counter_leader"] for o in valid_leaders(defense, counters, roster, exempt={"CARRY"})}
    assert leaders == {"SOLID"}


# --- rule file and service layer -------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "team_lists").mkdir()
    service._cache.clear()
    yield tmp_path
    service._cache.clear()


def test_missing_file_means_no_exemptions(project):
    assert load_leader_exemptions() == set()


def test_exempting_a_leader_persists_it(project):
    message = service.exempt_leader("GLLEIA", "carries underbuilt support")
    assert "GLLEIA" in message
    assert load_leader_exemptions() == {"GLLEIA"}

    saved = pd.read_csv(LEADER_EXEMPTIONS_FILE)
    assert saved.loc[0, "reason"] == "carries underbuilt support"


def test_exempting_twice_does_not_duplicate(project):
    service.exempt_leader("GLLEIA", "")
    service.exempt_leader("GLLEIA", "")
    assert len(pd.read_csv(LEADER_EXEMPTIONS_FILE)) == 1


def test_a_blank_leader_is_rejected(project):
    assert "Provide a base ID" in service.exempt_leader("  ", "")
    assert load_leader_exemptions() == set()


def test_exemptions_are_listed_and_removable(project):
    service.exempt_leader("GLLEIA", "")
    rows = service.load_rule_rows("leader_exemptions")
    assert [row["leader"] for row in rows] == ["GLLEIA"]

    service.remove_rule("leader_exemptions", rows[0]["_row_index"])
    assert load_leader_exemptions() == set()
