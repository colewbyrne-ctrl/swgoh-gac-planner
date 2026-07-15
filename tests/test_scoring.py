"""Tests for the counter scoring and roster-gating helpers.

These are the deterministic building blocks the beam search optimizes over:
how expensive a counter is to field (``unit_cost``/``counter_cost``), how
reliable it looks (``score_counter``), and whether the roster can even field
it (``roster_has_units``).
"""


import pandas as pd

from swgoh.make_strategy import (
    MIN_CHARACTER_RELIC_LEVEL,
    counter_cost,
    reliability_score_counter,
    roster_has_units,
    score_counter,
    unit_cost,
)


def make_unit(**overrides):
    row = {
        "stars": 7,
        "completion_percent": 100,
        "relic_level": 0,
        "zeta_count": 0,
        "ship_level": 0,
        "is_capital_ship": "false",
        "is_galactic_legend": "false",
        "has_ultimate": "false",
    }
    row.update(overrides)
    return row


def test_unit_cost_galactic_legend():
    roster = {"GLREY": make_unit(relic_level=9, zeta_count=6,
                                 is_galactic_legend="true", has_ultimate="true")}
    # 1 base + stars(7/7=1) + completion(100/100=1) + relic(9*0.7=6.3)
    # + zeta(6*0.25=1.5) + GL(10) + ultimate(2) = 22.8
    assert unit_cost("GLREY", "characters", roster) == 22.8


def test_unit_cost_missing_unit_is_zero():
    assert unit_cost("NOBODY", "characters", {}) == 0.0


def test_counter_cost_sums_units():
    roster = {
        "A": make_unit(stars=7, completion_percent=0, relic_level=0),  # 1 + 1 + 0 = 2
        "B": make_unit(stars=0, completion_percent=0, relic_level=0),  # 1 + 0 + 0 = 1
    }
    assert counter_cost(["A", "B"], "characters", roster) == 3.0


def test_score_counter_formula_with_no_roster_cost():
    counter = pd.Series({
        "win_percent": 90.0,
        "seen": 100,          # log10(100) = 2 -> *1.5 = 3.0
        "avg_banners": 60.0,  # min(60/60, 1.5) = 1.0
        "combat_type": "characters",
        "counter_units": ["A", "B"],
    })
    # cost is zero because the units are not in the roster lookup
    assert score_counter(counter, {}) == 90.0 + 3.0 + 1.0


def test_score_counter_subtracts_roster_cost():
    counter = pd.Series({
        "win_percent": 90.0,
        "seen": 100,
        "avg_banners": 0.0,
        "combat_type": "characters",
        "counter_units": ["A"],
    })
    roster = {"A": make_unit(stars=0, completion_percent=0)}  # cost = 1.0
    assert score_counter(counter, roster) == 90.0 + 3.0 - 1.0


def test_reliability_ignores_cost():
    counter = pd.Series({"win_percent": 80.0, "seen": 1, "avg_banners": 0.0})
    # log10(max(1,1)) = 0
    assert reliability_score_counter(counter) == 80.0


def test_roster_has_units_all_present_and_built():
    roster_set = {"LEAD", "SUP"}
    roster_by_unit = {
        "LEAD": make_unit(relic_level=MIN_CHARACTER_RELIC_LEVEL),
        "SUP": make_unit(relic_level=MIN_CHARACTER_RELIC_LEVEL),
    }
    ok, missing, underbuilt, note = roster_has_units(
        ["LEAD", "SUP"], "characters", roster_set, roster_by_unit)
    assert ok is True
    assert missing == [] and underbuilt == [] and note == ""


def test_roster_has_units_missing_leader():
    ok, missing, _, note = roster_has_units(
        ["LEAD", "SUP"], "characters", {"SUP"}, {"SUP": make_unit(relic_level=5)})
    assert ok is False
    assert "LEAD" in missing
    assert "leader" in note


def test_roster_has_units_underbuilt_character():
    roster_set = {"LEAD"}
    roster_by_unit = {"LEAD": make_unit(relic_level=MIN_CHARACTER_RELIC_LEVEL - 1)}
    ok, _, underbuilt, note = roster_has_units(
        ["LEAD"], "characters", roster_set, roster_by_unit)
    assert ok is False
    assert underbuilt and "relic" in note
