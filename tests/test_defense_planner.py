"""Tests for the defensive-team planner.

After offense is committed, the planner ranks the defensive teams the roster can
still field. A team is fieldable only if every unit is owned, built up enough,
and not already spent on offense.
"""

import pandas as pd

from swgoh.make_strategy import dedupe_defenses_by_leader
from swgoh.plan_my_defense import (
    MIN_CHARACTER_RELIC_LEVEL,
    build_defense_library,
    can_field_team,
    choose_non_overlapping_defense_plan,
    merge_libraries,
    team_difference_count,
)


def make_row(**overrides):
    row = {"relic_level": MIN_CHARACTER_RELIC_LEVEL, "stars": 7}
    row.update(overrides)
    return row


def test_can_field_team_happy_path():
    roster = {"A": make_row(), "B": make_row()}
    ok, missing, used = can_field_team(["A", "B"], "characters", roster, set())
    assert ok is True and missing == [] and used == []


def test_can_field_team_blocks_offense_committed_unit():
    roster = {"A": make_row(), "B": make_row()}
    ok, missing, used = can_field_team(["A", "B"], "characters", roster, {"B"})
    assert ok is False
    assert used == ["B"] and missing == []


def test_can_field_team_flags_missing_and_underbuilt():
    roster = {"A": make_row(relic_level=MIN_CHARACTER_RELIC_LEVEL - 1)}
    ok, missing, used = can_field_team(["A", "GONE"], "characters", roster, set())
    assert ok is False
    assert "GONE" in missing
    assert any(m.startswith("A ") for m in missing)  # underbuilt annotated with relic


def test_team_difference_count():
    assert team_difference_count(["A", "B", "C"], ["A", "B", "C"]) == 0
    assert team_difference_count(["A", "B", "C"], ["A", "X", "Y"]) == 2


def test_choose_non_overlapping_defense_plan_skips_unit_conflicts():
    options = pd.DataFrame([
        {"combat_type": "characters", "leader": "T1", "team_units": ["A", "B"]},
        {"combat_type": "characters", "leader": "T2", "team_units": ["B", "C"]},  # shares B
        {"combat_type": "characters", "leader": "T3", "team_units": ["D", "E"]},
    ])
    plan = choose_non_overlapping_defense_plan(options)
    leaders = list(plan["leader"])
    assert leaders == ["T1", "T3"]  # T2 dropped because B already used


def _library_inputs():
    defense_df = pd.DataFrame([
        {"combat_type": "characters", "leader": "GLLEIA",
         "units": ["GLLEIA", "CAPTAINREX"], "match_format": "5v5"},
    ])
    counters_df = pd.DataFrame([
        {"combat_type": "characters", "defense_leader": "GLLEIA",
         "defense_units": ["GLLEIA", "CAPTAINREX"], "seen": 40000, "win_percent": 80.0},
    ])
    return defense_df, counters_df


def test_build_defense_library_is_idempotent(tmp_path, monkeypatch):
    # Isolate the on-disk library the rebuild reads/writes.
    monkeypatch.chdir(tmp_path)
    defense_df, counters_df = _library_inputs()

    first = build_defense_library(defense_df.copy(), counters_df.copy(), "5v5")
    second = build_defense_library(defense_df.copy(), counters_df.copy(), "5v5")

    # Re-running for the same matchup must not inflate the accumulated counts.
    cols = ["leader", "times_seen", "total_counter_seen"]
    pd.testing.assert_frame_equal(
        first[cols].sort_values("leader").reset_index(drop=True),
        second[cols].sort_values("leader").reset_index(drop=True),
    )


def test_merge_libraries_current_wins_and_carries_forward():
    current = {("characters", "A", ("A",)): {
        "combat_type": "characters", "leader": "A", "team_units": ["A"],
        "gac_format": "5v5", "source_notes": {"run2"}, "times_seen": 1,
        "total_counter_seen": 5.0, "counter_win_values": [80.0]}}
    existing = {
        ("characters", "A", ("A",)): {
            "combat_type": "characters", "leader": "A", "team_units": ["A"],
            "gac_format": "5v5", "source_notes": {"run1"}, "times_seen": 9,
            "total_counter_seen": 999.0, "counter_win_values": [10.0]},
        ("characters", "B", ("B",)): {
            "combat_type": "characters", "leader": "B", "team_units": ["B"],
            "gac_format": "5v5", "source_notes": {"run1"}, "times_seen": 3,
            "total_counter_seen": 7.0, "counter_win_values": [50.0]},
    }
    merged = merge_libraries(current, existing)
    # Shared team A keeps the current run's counts (not the old inflated ones)...
    assert merged[("characters", "A", ("A",))]["total_counter_seen"] == 5.0
    assert merged[("characters", "A", ("A",))]["source_notes"] == {"run1", "run2"}
    # ...and team B, unseen this run, is carried forward.
    assert ("characters", "B", ("B",)) in merged


def test_dedupe_defenses_prefers_more_complete_team():
    defense_df = pd.DataFrame([
        {"combat_type": "characters", "leader": "L", "units": ["L", "x"]},
        {"combat_type": "characters", "leader": "L", "units": ["L", "x", "y"]},
    ])
    deduped, warnings = dedupe_defenses_by_leader(defense_df)
    assert len(deduped) == 1
    assert deduped.iloc[0]["units"] == ["L", "x", "y"]
    assert any("more units" in w for w in warnings)
