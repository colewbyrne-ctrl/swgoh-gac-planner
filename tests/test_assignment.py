"""Tests for the global counter-assignment beam search.

The planner solves a constrained assignment problem: assign one counter team to
each enemy defense such that no character/ship is reused across two attacks,
while maximizing the number of defenses covered (and then total score). A greedy
per-defense pick can strand a defense that has only one viable counter; the beam
search is supposed to let a *flexible* defense give up a contested unit so a
*constrained* defense can still be covered.
"""

import pandas as pd

from swgoh.make_strategy import (
    build_roster_set,
    choose_strategy,
    find_best_assignment,
)


def defense_item(index, leader, counters, locked=None, combat_type="characters"):
    return {
        "index": index,
        "defense": pd.Series({"combat_type": combat_type, "leader": leader}),
        "valid_counters": counters,
        "locked_counter": locked,
    }


def counter(leader, units, score):
    return {"counter_leader": leader, "counter_units": units, "score": score,
            "combat_type": "characters"}


def test_beam_search_never_reuses_a_unit():
    # Both defenses' best-scoring counter needs unit X; only one may use it.
    options = [
        defense_item(0, "DA", [counter("CX", ["X", "a1"], 10)]),          # only choice needs X
        defense_item(1, "DB", [counter("CXB", ["X", "b1"], 100),          # high score, needs X
                               counter("CY", ["Y", "b2"], 5)]),           # low score, frees X
    ]

    assignment = find_best_assignment(options)

    # Every unit is owned by at most one attack.
    all_units = [u for c in assignment.values() for u in c["counter_units"]]
    assert len(all_units) == len(set(all_units))


def test_beam_search_prefers_covering_more_defenses_over_raw_score():
    options = [
        defense_item(0, "DA", [counter("CX", ["X", "a1"], 10)]),
        defense_item(1, "DB", [counter("CXB", ["X", "b1"], 100),
                               counter("CY", ["Y", "b2"], 5)]),
    ]

    assignment = find_best_assignment(options)

    # Both defenses covered, even though it means DB drops its 100-point counter.
    assert set(assignment) == {0, 1}
    assert assignment[0]["counter_leader"] == "CX"
    assert assignment[1]["counter_leader"] == "CY"


def test_locked_counter_is_forced():
    forced = counter("LOCKED", ["Z", "z1"], 1)
    options = [
        defense_item(0, "DA", [counter("CX", ["X"], 999)], locked=forced),
    ]

    assignment = find_best_assignment(options)

    assert assignment[0]["counter_leader"] == "LOCKED"


# --- end-to-end through choose_strategy -----------------------------------


def roster_frame(units):
    return pd.DataFrame([
        {
            "base_id": u,
            "stars": 7,
            "completion_percent": 100,
            "relic_level": 5,
            "zeta_count": 0,
            "ship_level": 0,
            "is_capital_ship": "false",
            "is_galactic_legend": "false",
            "has_ultimate": "false",
        }
        for u in units
    ])


def test_choose_strategy_covers_both_defenses_without_reuse():
    defense_df = pd.DataFrame([
        {"combat_type": "characters", "leader": "DA", "name": "DA team",
         "units": ["DA", "d1"], "match_format": "5v5"},
        {"combat_type": "characters", "leader": "DB", "name": "DB team",
         "units": ["DB", "d2"], "match_format": "5v5"},
    ])
    counters_df = pd.DataFrame([
        # DA can only be answered with a team that uses X
        {"combat_type": "characters", "defense_leader": "DA", "counter_leader": "CX",
         "counter_units": ["X", "a1"], "defense_units": ["DA", "d1"],
         "seen": 50, "win_percent": 90.0, "avg_banners": 40.0},
        # DB's strongest answer also uses X, but a weaker Y answer exists
        {"combat_type": "characters", "defense_leader": "DB", "counter_leader": "CXB",
         "counter_units": ["X", "b1"], "defense_units": ["DB", "d2"],
         "seen": 100, "win_percent": 95.0, "avg_banners": 45.0},
        {"combat_type": "characters", "defense_leader": "DB", "counter_leader": "CY",
         "counter_units": ["Y", "b2"], "defense_units": ["DB", "d2"],
         "seen": 50, "win_percent": 90.0, "avg_banners": 40.0},
    ])
    roster = roster_frame(["X", "a1", "b1", "Y", "b2"])
    roster_set, roster_by_unit = build_roster_set(roster)

    plan_df, _ = choose_strategy(defense_df, counters_df, roster_set, roster_by_unit)

    by_leader = {r["defense_leader"]: r for _, r in plan_df.iterrows()}
    assert by_leader["DA"]["status"] == "assigned"
    assert by_leader["DB"]["status"] == "assigned"
    # DA keeps its only option; DB gives up the X-counter and takes CY.
    assert by_leader["DA"]["chosen_counter_leader"] == "CX"
    assert by_leader["DB"]["chosen_counter_leader"] == "CY"

    used = [u for r in by_leader.values() for u in r["chosen_counter_units"]]
    assert used.count("X") == 1  # X assigned to exactly one attack
