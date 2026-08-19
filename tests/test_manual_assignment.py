"""Tests for manually assigning a counter from the attack screen.

Manual assignment is a locked matchup written from the UI, so these tests check
both halves: that the picker only offers counters the optimizer would accept,
and that committing one takes the team off whichever defense previously held it.
"""

import csv

import pandas as pd
import pytest

from swgoh.make_strategy import LOCKED_MATCHUPS_FILE, REJECTIONS_FILE
from swgoh.web import service


def counter_row(defense_leader, counter_leader, counter_units, seen=50, win=95.0):
    return {
        "combat_type": "characters",
        "defense_leader": defense_leader,
        "season_id": "SEASON_1",
        "counter_leader": counter_leader,
        "counter_units": repr(counter_units),
        "defense_units": repr([defense_leader, defense_leader + "_ALLY"]),
        "seen": seen,
        "win_percent": win,
        "avg_banners": 60.0,
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A miniature project tree: two defenses, three possible counters."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "active_run").mkdir()
    (tmp_path / "team_lists").mkdir()

    defenses = pd.DataFrame([
        {
            "combat_type": "characters",
            "name": "Defense A",
            "leader": "DEF_A",
            "units": repr(["DEF_A", "DEF_A_ALLY"]),
        },
        {
            "combat_type": "characters",
            "name": "Defense B",
            "leader": "DEF_B",
            "units": repr(["DEF_B", "DEF_B_ALLY"]),
        },
    ])
    defenses.to_csv("active_run/defense_teams.csv", index=False)

    # SHARED counters both defenses; ALT only counters A, SOLO only counters B.
    counters = pd.DataFrame([
        counter_row("DEF_A", "SHARED", ["SHARED", "SHARED_ALLY"], win=99.0),
        counter_row("DEF_A", "ALT", ["ALT", "ALT_ALLY"], win=90.0),
        counter_row("DEF_B", "SHARED", ["SHARED", "SHARED_ALLY"], win=99.0),
        counter_row("DEF_B", "SOLO", ["SOLO", "SOLO_ALLY"], win=88.0),
    ])
    counters.to_csv("active_run/counter_results.csv", index=False)

    roster = pd.DataFrame([
        {"player_id": 1, "combat_type": "characters", "name": unit, "base_id": unit,
         "stars": 7, "relic_level": 9.0, "zeta_count": 1, "completion_percent": 100.0}
        for unit in ["SHARED", "SHARED_ALLY", "ALT", "ALT_ALLY", "SOLO", "SOLO_ALLY"]
    ])
    roster.to_csv("active_run/roster_units.csv", index=False)

    service._cache.clear()
    yield tmp_path
    service._cache.clear()


def plan_by_defense() -> dict[str, str]:
    strategy_df, _ = service.rebuild_strategy(force=True)
    return {
        str(row["defense_leader"]): str(row["chosen_counter_leader"])
        for _, row in strategy_df.iterrows()
    }


def lock_rows() -> list[dict]:
    with open(LOCKED_MATCHUPS_FILE, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_options_only_offer_counters_for_that_defense(project):
    leaders = {
        option["counter_leader"]
        for option in service.counter_options_for_defense("characters", "DEF_A")
    }
    assert leaders == {"SHARED", "ALT"}


def test_options_flag_the_team_another_defense_is_using(project):
    # SHARED is the strongest option for both, so exactly one defense holds it.
    holder = next(
        leader for leader, counter in plan_by_defense().items() if counter == "SHARED"
    )
    other = "DEF_B" if holder == "DEF_A" else "DEF_A"

    options = {
        option["counter_leader"]: option
        for option in service.counter_options_for_defense("characters", other)
    }
    assert options["SHARED"]["assigned_to"] == holder
    assert options["SHARED"]["is_current"] is False


def test_options_mark_the_current_assignment(project):
    plan = plan_by_defense()
    options = {
        option["counter_leader"]: option
        for option in service.counter_options_for_defense("characters", "DEF_A")
    }
    assert options[plan["DEF_A"]]["is_current"] is True


def test_options_are_empty_for_an_unknown_defense(project):
    assert service.counter_options_for_defense("characters", "NOT_A_DEFENSE") == []


def test_assignment_is_honoured_by_the_rebuild(project):
    service.assign_counter("characters", "DEF_A", "Defense A", "ALT", repr(["ALT", "ALT_ALLY"]))
    assert plan_by_defense()["DEF_A"] == "ALT"


def test_assigning_a_used_team_takes_it_off_the_other_defense(project):
    service.assign_counter(
        "characters", "DEF_B", "Defense B", "SHARED", repr(["SHARED", "SHARED_ALLY"])
    )
    message = service.assign_counter(
        "characters", "DEF_A", "Defense A", "SHARED", repr(["SHARED", "SHARED_ALLY"])
    )

    assert "taken off the DEF_B defense" in message
    plan = plan_by_defense()
    assert plan["DEF_A"] == "SHARED"
    assert plan["DEF_B"] == "SOLO"
    assert len(lock_rows()) == 1


def test_reassigning_a_defense_replaces_its_earlier_pick(project):
    service.assign_counter("characters", "DEF_A", "Defense A", "ALT", repr(["ALT", "ALT_ALLY"]))
    service.assign_counter(
        "characters", "DEF_A", "Defense A", "SHARED", repr(["SHARED", "SHARED_ALLY"])
    )

    rows = lock_rows()
    assert len(rows) == 1
    assert rows[0]["counter_leader"] == "SHARED"
    assert plan_by_defense()["DEF_A"] == "SHARED"


def test_assignment_clears_a_rejection_of_the_same_pairing(project):
    service.reject_counter(
        "characters", "DEF_A", "Defense A", "ALT", repr(["ALT", "ALT_ALLY"]), ""
    )
    assert "ALT" not in {
        option["counter_leader"]
        for option in service.counter_options_for_defense("characters", "DEF_A")
    }

    message = service.assign_counter(
        "characters", "DEF_A", "Defense A", "ALT", repr(["ALT", "ALT_ALLY"])
    )

    assert "cleared an earlier rejection" in message
    assert pd.read_csv(REJECTIONS_FILE).empty
    assert plan_by_defense()["DEF_A"] == "ALT"


def test_assignment_leaves_unrelated_locks_alone(project):
    service.assign_counter("characters", "DEF_B", "Defense B", "SOLO", repr(["SOLO", "SOLO_ALLY"]))
    service.assign_counter("characters", "DEF_A", "Defense A", "ALT", repr(["ALT", "ALT_ALLY"]))

    holders = {row["defense_leader"]: row["counter_leader"] for row in lock_rows()}
    assert holders == {"DEF_A": "ALT", "DEF_B": "SOLO"}


def test_empty_pick_is_rejected(project):
    assert "Pick a counter team first" in service.assign_counter(
        "characters", "DEF_A", "Defense A", "", "", ""
    )


def test_message_names_the_defense_that_lost_the_team(project):
    # Nothing is locked, so this holder comes from the plan, not a rule file.
    holder = next(
        leader for leader, counter in plan_by_defense().items() if counter == "SHARED"
    )
    other = "DEF_B" if holder == "DEF_A" else "DEF_A"

    message = service.assign_counter(
        "characters", other, f"Defense {other[-1]}", "SHARED", repr(["SHARED", "SHARED_ALLY"])
    )

    assert f"taken off the {holder} defense" in message


def test_stranding_a_defense_is_called_out(project):
    # Strip DEF_B's fallback so losing SHARED leaves it with nothing.
    counters = pd.read_csv("active_run/counter_results.csv")
    counters[counters["counter_leader"] != "SOLO"].to_csv(
        "active_run/counter_results.csv", index=False
    )
    service._cache.clear()
    assert plan_by_defense()["DEF_B"] == "SHARED"

    message = service.assign_counter(
        "characters", "DEF_A", "Defense A", "SHARED", repr(["SHARED", "SHARED_ALLY"])
    )

    assert "taken off the DEF_B defense" in message
    assert "the DEF_B defense has no counter left" in message
    assert plan_by_defense() == {"DEF_A": "SHARED", "DEF_B": ""}
