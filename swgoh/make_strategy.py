import ast
import math
from pathlib import Path

import pandas as pd

from .project_paths import csv_path, ensure_data_dirs, migrate_legacy_csvs

DEFENSE_FILE = csv_path("defense_teams.csv")
COUNTERS_FILE = csv_path("counter_results.csv")
ROSTER_FILE = csv_path("roster_units.csv")
OUTPUT_FILE = csv_path("strategy_plan.csv")
REJECTIONS_FILE = csv_path("strategy_rejections.csv")
RESERVED_UNITS_FILE = csv_path("reserved_units.csv")
LOCKED_MATCHUPS_FILE = csv_path("locked_matchups.csv")
OFFENSE_TEAM_LOCKS_FILE = csv_path("offense_team_locks.csv")
THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE = csv_path("offense_team_locks_3v3.csv")

MIN_WIN_PERCENT = 85
SHIP_MIN_WIN_PERCENT = 80
MIN_CHARACTER_RELIC_LEVEL = 3
MIN_SEEN = 10
FALLBACK_MIN_SEEN_VALUES = [5, 1]
MIN_WIN_PERCENT_FLOOR = 70
WIN_PERCENT_STEP = 5
RELAXED_SEEN_SCORE_PENALTY = 2.5
RELAXED_WIN_SCORE_PENALTY = 1.5
MAX_COUNTER_OPTIONS_PER_DEFENSE = 30
OFFENSE_LOCK_SCORE_BONUS = 15.0
FLEXIBILITY_SCORE_PENALTY = 3.0
BEAM_SEARCH_WIDTH = 1500
SCARCE_DEFENSE_ASSIGNMENT_BONUS = 50.0


def counter_signature(
    combat_type: str,
    defense_leader: str,
    counter_leader: str,
    counter_units: list[str],
) -> tuple[str, str, str, tuple[str, ...]]:
    return (
        str(combat_type).strip(),
        str(defense_leader).strip(),
        str(counter_leader).strip(),
        tuple(str(unit).strip() for unit in counter_units if str(unit).strip()),
    )


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
        if isinstance(parsed, list):
            return [str(unit).strip() for unit in parsed if str(unit).strip()]
        if isinstance(parsed, tuple):
            return [str(unit).strip() for unit in parsed if str(unit).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
    except (ValueError, SyntaxError):
        pass

    if "," in text:
        return [
            unit.strip().strip("'\"[]")
            for unit in text.split(",")
            if unit.strip().strip("'\"[]")
        ]

    return [text.strip("'\"[]")]


def load_csvs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    defense_df = pd.read_csv(DEFENSE_FILE)
    counters_df = pd.read_csv(COUNTERS_FILE)
    roster_df = pd.read_csv(ROSTER_FILE)

    for df, column in [
        (defense_df, "units"),
        (counters_df, "counter_units"),
        (counters_df, "defense_units"),
    ]:
        if column in df.columns:
            df[column] = df[column].apply(parse_unit_list)

    for df in [counters_df, roster_df]:
        for column in ["seen", "win_percent", "avg_banners", "stars", "relic_level", "zeta_count", "completion_percent", "ship_level"]:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

    return defense_df, counters_df, roster_df


def load_rejected_counter_signatures(path: str = REJECTIONS_FILE) -> set[tuple[str, str, str, tuple[str, ...]]]:
    if not Path(path).exists():
        return set()

    rejections_df = pd.read_csv(path)
    required_columns = {"combat_type", "defense_leader", "counter_leader", "counter_units"}

    if not required_columns.issubset(rejections_df.columns):
        return set()

    return {
        counter_signature(
            row.get("combat_type", ""),
            row.get("defense_leader", ""),
            row.get("counter_leader", ""),
            parse_unit_list(row.get("counter_units", [])),
        )
        for _, row in rejections_df.iterrows()
    }


def load_locked_matchup_signatures(path: str = LOCKED_MATCHUPS_FILE) -> set[tuple[str, str, str, tuple[str, ...]]]:
    if not Path(path).exists():
        return set()

    locks_df = pd.read_csv(path)
    required_columns = {"combat_type", "defense_leader", "counter_leader", "counter_units"}

    if not required_columns.issubset(locks_df.columns):
        return set()

    return {
        counter_signature(
            row.get("combat_type", ""),
            row.get("defense_leader", ""),
            row.get("counter_leader", ""),
            parse_unit_list(row.get("counter_units", [])),
        )
        for _, row in locks_df.iterrows()
    }


def load_reserved_units(path: str = RESERVED_UNITS_FILE) -> set[str]:
    if not Path(path).exists():
        return set()

    reserved_df = pd.read_csv(path)

    if "unit" not in reserved_df.columns:
        return set()

    return {
        str(unit).strip()
        for unit in reserved_df["unit"].fillna("")
        if str(unit).strip()
    }


def offense_team_signature(
    leader: str,
    team_units: list[str],
) -> tuple[str, tuple[str, ...]]:
    return (
        str(leader).strip(),
        tuple(str(unit).strip() for unit in team_units if str(unit).strip()),
    )


def load_offense_team_lock_signatures(
    path: str = OFFENSE_TEAM_LOCKS_FILE,
    gac_format: str = "5v5",
) -> set[tuple[str, tuple[str, ...]]]:
    if gac_format == "3v3" and path == OFFENSE_TEAM_LOCKS_FILE:
        path = THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE

    if not Path(path).exists():
        return set()

    locks_df = pd.read_csv(path)
    required_columns = {"leader", "team_units"}

    if not required_columns.issubset(locks_df.columns):
        return set()

    return {
        offense_team_signature(
            row.get("leader", ""),
            parse_unit_list(row.get("team_units", [])),
        )
        for _, row in locks_df.iterrows()
    }


def dedupe_defenses_by_leader(defense_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if defense_df.empty or not {"combat_type", "leader"}.issubset(defense_df.columns):
        return defense_df, []

    warnings = []
    best_rows = {}
    order = []

    for index, defense in defense_df.iterrows():
        key = (
            str(defense.get("combat_type", "")).strip(),
            str(defense.get("leader", "")).strip(),
        )

        if key not in best_rows:
            best_rows[key] = (index, defense)
            order.append(key)
            continue

        best_index, best_defense = best_rows[key]
        current_units = defense.get("units", [])
        best_units = best_defense.get("units", [])

        warnings.append(
            f"{key[1]}: duplicate defense leader skipped from row {index}; "
            f"using row {best_index}"
        )

        if len(current_units) > len(best_units):
            best_rows[key] = (index, defense)
            warnings[-1] = (
                f"{key[1]}: duplicate defense leader found at row {index}; "
                f"using it instead of row {best_index} because it has more units"
            )

    deduped_rows = [
        best_rows[key][1]
        for key in order
    ]

    return pd.DataFrame(deduped_rows).reset_index(drop=True), warnings


def build_roster_set(roster_df: pd.DataFrame) -> tuple[set[str], dict[str, pd.Series]]:
    if "base_id" not in roster_df.columns:
        return set(), {}

    roster_df = roster_df.copy()
    roster_df["base_id"] = roster_df["base_id"].fillna("").astype(str)
    roster_df = roster_df[roster_df["base_id"] != ""]

    roster_set = set(roster_df["base_id"])
    roster_by_unit = {
        row["base_id"]: row
        for _, row in roster_df.iterrows()
    }

    return roster_set, roster_by_unit


def roster_has_units(
    units: list[str],
    combat_type: str,
    roster_set: set[str],
    roster_by_unit: dict[str, pd.Series],
) -> tuple[bool, list[str], list[str], str]:
    missing_units = []
    underbuilt_units = []

    for unit in units:
        if unit not in roster_set:
            missing_units.append(unit)
            continue

        roster_row = roster_by_unit.get(unit)
        relic_level = safe_float(
            roster_row.get("relic_level") if roster_row is not None else None,
            0.0,
        )

        if combat_type == "characters" and relic_level < MIN_CHARACTER_RELIC_LEVEL:
            underbuilt_units.append(f"{unit} (relic {relic_level:g})")

    if not units:
        return False, missing_units, underbuilt_units, "counter has no units"

    counter_leader = units[0]

    if counter_leader in missing_units:
        return False, missing_units, underbuilt_units, "counter leader is missing from roster"

    if missing_units:
        return False, missing_units, underbuilt_units, "one or more counter units are missing from roster"

    if underbuilt_units:
        return (
            False,
            missing_units,
            underbuilt_units,
            f"one or more character units are below relic {MIN_CHARACTER_RELIC_LEVEL}",
        )

    issue_count = len(missing_units) + len(underbuilt_units)

    if issue_count > 1:
        return False, missing_units, underbuilt_units, "more than one counter unit is missing or underbuilt"

    note_parts = []

    if missing_units:
        note_parts.append(f"missing support unit {missing_units[0]}")

    if underbuilt_units:
        note_parts.append(f"underbuilt unit {underbuilt_units[0]}")

    return True, missing_units, underbuilt_units, "; ".join(note_parts)


def safe_float(value, default=None):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def unit_cost(unit: str, combat_type: str, roster_by_unit: dict[str, pd.Series]) -> float:
    roster_row = roster_by_unit.get(unit)

    if roster_row is None:
        return 0.0

    stars = safe_float(roster_row.get("stars"), 0.0)
    completion = safe_float(roster_row.get("completion_percent"), 0.0)
    cost = 1.0 + (stars / 7.0) + (completion / 100.0)

    if combat_type == "ships":
        ship_level = safe_float(roster_row.get("ship_level"), 0.0)
        cost += ship_level / 25.0

        if str(roster_row.get("is_capital_ship", "")).lower() == "true":
            cost += 5.0
    else:
        relic_level = safe_float(roster_row.get("relic_level"), 0.0)
        zeta_count = safe_float(roster_row.get("zeta_count"), 0.0)
        cost += relic_level * 0.7
        cost += zeta_count * 0.25

        if str(roster_row.get("is_galactic_legend", "")).lower() == "true":
            cost += 10.0

        if str(roster_row.get("has_ultimate", "")).lower() == "true":
            cost += 2.0

    return cost


def counter_cost(
    units: list[str],
    combat_type: str,
    roster_by_unit: dict[str, pd.Series],
) -> float:
    if not units:
        return 0.0

    return sum(
        unit_cost(unit, combat_type, roster_by_unit)
        for unit in units
    )


def score_counter(counter: pd.Series, roster_by_unit: dict[str, pd.Series]) -> float:
    win_percent = safe_float(counter.get("win_percent"), 0.0)
    seen = safe_float(counter.get("seen"), 0.0)
    avg_banners = safe_float(counter.get("avg_banners"), 0.0)
    combat_type = str(counter.get("combat_type", ""))
    counter_units = counter.get("counter_units", [])
    cost = counter_cost(counter_units, combat_type, roster_by_unit)

    # Valid counters have already cleared the win-rate floor. From there,
    # preserve premium teams by preferring the cheapest reliable answer.
    score = win_percent
    score += min(math.log10(max(seen, 1.0)), 3.0) * 1.5

    if avg_banners > 0:
        score += min(avg_banners / 60.0, 1.5)

    score -= cost

    return round(score, 3)


def reliability_score_counter(counter: pd.Series) -> float:
    win_percent = safe_float(counter.get("win_percent"), 0.0)
    seen = safe_float(counter.get("seen"), 0.0)
    avg_banners = safe_float(counter.get("avg_banners"), 0.0)

    score = win_percent
    score += min(math.log10(max(seen, 1.0)), 3.0) * 1.5

    if avg_banners > 0:
        score += min(avg_banners / 60.0, 1.5)

    return round(score, 3)


def min_win_percent_for_combat_type(combat_type: str) -> int:
    if str(combat_type).strip().lower() == "ships":
        return SHIP_MIN_WIN_PERCENT

    return MIN_WIN_PERCENT


def fallback_win_percent_values_for_combat_type(combat_type: str) -> list[int]:
    base_min_win = min_win_percent_for_combat_type(combat_type)
    values = []

    for min_win in range(base_min_win, MIN_WIN_PERCENT_FLOOR - 1, -WIN_PERCENT_STEP):
        values.append(min_win)

    return values


def infer_gac_format_from_defense_df(defense_df: pd.DataFrame) -> str:
    if "match_format" not in defense_df.columns:
        return "5v5"

    formats = {
        str(value).strip().lower()
        for value in defense_df["match_format"].dropna()
        if str(value).strip().lower() in {"3v3", "5v5"}
    }

    if len(formats) == 1:
        return next(iter(formats))

    return "5v5"


def find_counter_rows_at_seen_threshold(
    counters_df: pd.DataFrame,
    combat_type: str,
    leader: str,
    min_win_percent: int,
    min_seen: int,
) -> pd.DataFrame:
    return counters_df[
        (counters_df["combat_type"].astype(str) == combat_type)
        & (counters_df["defense_leader"].astype(str) == leader)
        & (counters_df["win_percent"].fillna(0) >= min_win_percent)
        & (counters_df["seen"].fillna(0) >= min_seen)
    ]


def relaxed_threshold_score_penalty(min_seen: int, min_win_threshold: int, normal_min_win: int) -> float:
    seen_penalty_steps = 0 if min_seen >= MIN_SEEN else 1 if min_seen >= 5 else 2
    win_penalty_steps = max(0, normal_min_win - min_win_threshold) / WIN_PERCENT_STEP

    return round(
        seen_penalty_steps * RELAXED_SEEN_SCORE_PENALTY
        + win_penalty_steps * RELAXED_WIN_SCORE_PENALTY,
        3,
    )


def find_valid_counters_for_defense(
    defense: pd.Series,
    counters_df: pd.DataFrame,
    roster_set: set[str],
    roster_by_unit: dict[str, pd.Series],
    warnings: dict[str, list[str]],
    rejected_counters: set[tuple[str, str, str, tuple[str, ...]]] | None = None,
    reserved_units: set[str] | None = None,
    offense_team_locks: set[tuple[str, tuple[str, ...]]] | None = None,
) -> list[dict]:
    combat_type = str(defense.get("combat_type", ""))
    leader = str(defense.get("leader", ""))
    min_win_percent = min_win_percent_for_combat_type(combat_type)
    rejected_counters = rejected_counters or set()
    reserved_units = reserved_units or set()
    offense_team_locks = offense_team_locks or set()

    required_columns = {"combat_type", "defense_leader", "counter_units", "seen", "win_percent"}
    if not required_columns.issubset(counters_df.columns):
        missing = sorted(required_columns - set(counters_df.columns))
        warnings["missing_columns"].append(f"counter_results.csv missing columns: {missing}")
        return []

    leader_rows = counters_df[
        (counters_df["combat_type"].astype(str) == combat_type)
        & (counters_df["defense_leader"].astype(str) == leader)
    ]

    if leader_rows.empty:
        warnings["missing_counter_data"].append(
            f"{leader}: no rows found in counter_results.csv for this defense leader"
        )
        return []

    def build_valid_counters_at_threshold(
        min_seen: int,
        min_win_threshold: int,
    ) -> tuple[list[dict], dict[str, list[str]], bool]:
        possible = find_counter_rows_at_seen_threshold(
            counters_df,
            combat_type,
            leader,
            min_win_threshold,
            min_seen,
        )
        low_sample_fallback = min_seen < MIN_SEEN
        local_warnings = {
            "rejected_counters": [],
            "reserved_units": [],
            "missing_roster": [],
            "soft_roster_notes": [],
        }
        valid_counters = []

        for _, counter in possible.iterrows():
            counter_units = counter.get("counter_units", [])
            counter_leader = str(counter.get("counter_leader", ""))
            signature = counter_signature(combat_type, leader, counter_leader, counter_units)

            if signature in rejected_counters:
                local_warnings["rejected_counters"].append(
                    f"{leader}: skipped rejected counter {counter_leader} {counter_units}"
                )
                continue

            reserved_overlap = sorted(set(counter_units) & reserved_units)

            if reserved_overlap:
                local_warnings["reserved_units"].append(
                    f"{leader}: skipped {counter_leader} because unit-specific rejected units are present: {reserved_overlap}"
                )
                continue

            has_units, missing_units, underbuilt_units, roster_note = roster_has_units(
                counter_units,
                combat_type,
                roster_set,
                roster_by_unit,
            )

            if not has_units:
                skipped_reason = []
                if missing_units:
                    skipped_reason.append(f"missing {missing_units}")
                if underbuilt_units:
                    skipped_reason.append(f"underbuilt {underbuilt_units}")
                if not skipped_reason:
                    skipped_reason.append(roster_note)

                local_warnings["missing_roster"].append(
                    f"{leader}: skipped {counter.get('counter_leader', '')} because "
                    + "; ".join(skipped_reason)
                )
                continue

            counter_dict = counter.to_dict()
            counter_dict["counter_cost"] = round(
                counter_cost(counter_units, combat_type, roster_by_unit),
                3,
            )
            counter_dict["reliability_score"] = reliability_score_counter(counter)
            score = score_counter(counter, roster_by_unit)
            offense_locked = offense_team_signature(counter_leader, counter_units) in offense_team_locks

            if offense_locked:
                score += OFFENSE_LOCK_SCORE_BONUS

            fallback_penalty = relaxed_threshold_score_penalty(
                min_seen,
                min_win_threshold,
                min_win_percent,
            )
            if fallback_penalty:
                score -= fallback_penalty

            counter_dict["score"] = round(score, 3)
            counter_dict["offense_locked"] = offense_locked
            counter_dict["roster_note"] = roster_note

            if fallback_penalty:
                counter_dict["roster_note"] = "; ".join(
                    note for note in [
                        counter_dict["roster_note"],
                        f"relaxed threshold penalty: -{fallback_penalty:g}",
                    ]
                    if note
                )

            if offense_locked:
                counter_dict["roster_note"] = "; ".join(
                    note for note in [counter_dict["roster_note"], "preferred by offense lock"]
                    if note
                )

            if low_sample_fallback:
                low_sample_note = f"low sample size: seen {counter.get('seen', '')}"
                counter_dict["roster_note"] = "; ".join(
                    note for note in [counter_dict["roster_note"], low_sample_note]
                    if note
                )

            if min_win_threshold < min_win_percent:
                low_win_note = (
                    f"relaxed win threshold: {counter.get('win_percent', '')}% win "
                    f"is below normal {min_win_percent}% threshold"
                )
                counter_dict["roster_note"] = "; ".join(
                    note for note in [counter_dict["roster_note"], low_win_note]
                    if note
                )

            if roster_note:
                local_warnings["soft_roster_notes"].append(
                    f"{leader}: allowing {counter.get('counter_leader', '')} with note: {roster_note}"
                )

            valid_counters.append(counter_dict)

        valid_counters.sort(
            key=lambda row: (
                row["reliability_score"],
                row["score"],
            ),
            reverse=True,
        )
        return dedupe_counter_options(valid_counters), local_warnings, not possible.empty

    seen_thresholds = [MIN_SEEN] + FALLBACK_MIN_SEEN_VALUES
    threshold_profiles = [
        (min_win_threshold, min_seen)
        for min_win_threshold in fallback_win_percent_values_for_combat_type(combat_type)
        for min_seen in seen_thresholds
    ]
    last_warnings = None
    saw_any_statistical_rows = False
    valid_counter_groups = []
    seen_counter_keys = set()

    for min_win_threshold, min_seen in threshold_profiles:
        valid_counters, local_warnings, had_rows = build_valid_counters_at_threshold(
            min_seen,
            min_win_threshold,
        )
        saw_any_statistical_rows = saw_any_statistical_rows or had_rows
        last_warnings = local_warnings

        if not valid_counters:
            continue

        new_valid_counters = []
        for counter in valid_counters:
            key = (
                str(counter.get("counter_leader", "")),
                tuple(counter.get("counter_units", [])),
            )
            if key in seen_counter_keys:
                continue
            seen_counter_keys.add(key)
            new_valid_counters.append(counter)

        if not new_valid_counters:
            continue

        valid_counter_groups.append((min_win_threshold, min_seen, new_valid_counters, local_warnings))

    if valid_counter_groups:
        merged_valid_counters = []
        merged_warnings = {
            "rejected_counters": [],
            "reserved_units": [],
            "missing_roster": [],
            "soft_roster_notes": [],
        }

        for min_win_threshold, min_seen, valid_counters, local_warnings in valid_counter_groups:
            if min_seen < MIN_SEEN:
                warnings["low_sample_fallback"].append(
                    f"{leader}: allowing backup counters at seen >= {min_seen}"
                )
            if min_win_threshold < min_win_percent:
                warnings["low_sample_fallback"].append(
                    f"{leader}: allowing backup counters with win >= {min_win_threshold}%"
                )

            for warning_key, warning_values in local_warnings.items():
                merged_warnings[warning_key].extend(warning_values)
            merged_valid_counters.extend(valid_counters)

        for warning_key, warning_values in merged_warnings.items():
            warnings[warning_key].extend(warning_values)

        merged_valid_counters.sort(
            key=lambda row: (
                row["reliability_score"],
                row["score"],
            ),
            reverse=True,
        )
        return dedupe_counter_options(merged_valid_counters)

    if last_warnings is not None:
        for warning_key, warning_values in last_warnings.items():
            warnings[warning_key].extend(warning_values)

    if not saw_any_statistical_rows:
        warnings["missing_counter_data"].append(
            f"{leader}: no counters met win >= {MIN_WIN_PERCENT_FLOOR} even after seen fallback"
        )

    return []


def dedupe_counter_options(valid_counters: list[dict]) -> list[dict]:
    best_by_units = {}
    order = []

    for counter in valid_counters:
        key = (
            counter.get("counter_leader", ""),
            tuple(counter.get("counter_units", [])),
        )

        if key not in best_by_units:
            best_by_units[key] = counter
            order.append(key)
            continue

        if safe_float(counter.get("score"), 0.0) > safe_float(best_by_units[key].get("score"), 0.0):
            best_by_units[key] = counter

    deduped = [
        best_by_units[key]
        for key in order
    ]
    deduped.sort(
        key=lambda row: (
            row["reliability_score"],
            row["score"],
        ),
        reverse=True,
    )

    return deduped


def empty_plan_row(defense: pd.Series, status: str, reason: str) -> dict:
    return {
        "defense_name": defense.get("name", ""),
        "defense_leader": defense.get("leader", ""),
        "combat_type": defense.get("combat_type", ""),
        "defense_units": defense.get("units", []),
        "chosen_counter_leader": "",
        "chosen_counter_units": [],
        "win_percent": "",
        "seen": "",
        "avg_banners": "",
        "counter_cost": "",
        "score": "",
        "status": status,
        "reason": reason,
    }


def assigned_plan_row(defense: pd.Series, counter: dict) -> dict:
    reason = "reliable counter found"

    if counter.get("roster_note"):
        reason += f"; note: {counter.get('roster_note')}"

    return {
        "defense_name": defense.get("name", ""),
        "defense_leader": defense.get("leader", ""),
        "combat_type": defense.get("combat_type", ""),
        "defense_units": defense.get("units", []),
        "chosen_counter_leader": counter.get("counter_leader", ""),
        "chosen_counter_units": counter.get("counter_units", []),
        "win_percent": counter.get("win_percent", ""),
        "seen": counter.get("seen", ""),
        "avg_banners": counter.get("avg_banners", ""),
        "counter_cost": counter.get("counter_cost", ""),
        "score": counter.get("score", ""),
        "status": "assigned",
        "reason": reason,
    }


def choose_strategy(
    defense_df: pd.DataFrame,
    counters_df: pd.DataFrame,
    roster_set: set[str],
    roster_by_unit: dict[str, pd.Series],
    rejected_counters: set[tuple[str, str, str, tuple[str, ...]]] | None = None,
    reserved_units: set[str] | None = None,
    locked_matchups: set[tuple[str, str, str, tuple[str, ...]]] | None = None,
    offense_team_locks: set[tuple[str, tuple[str, ...]]] | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    locked_matchups = locked_matchups or set()
    offense_team_locks = offense_team_locks or set()
    warnings = {
        "no_valid_counter": [],
        "missing_roster": [],
        "soft_roster_notes": [],
        "rejected_counters": [],
        "reserved_units": [],
        "locked_matchups": [],
        "low_sample_fallback": [],
        "unit_overlap": [],
        "missing_columns": [],
        "missing_counter_data": [],
        "duplicate_defenses": [],
    }

    defense_options = []

    for defense_index, defense in defense_df.iterrows():
        valid_counters = find_valid_counters_for_defense(
            defense,
            counters_df,
            roster_set,
            roster_by_unit,
            warnings,
            rejected_counters=rejected_counters,
            reserved_units=reserved_units,
            offense_team_locks=offense_team_locks,
        )
        locked_counter = find_locked_counter_for_defense(defense, valid_counters, locked_matchups)

        if locked_counter is not None:
            valid_counters = [locked_counter]
            warnings["locked_matchups"].append(
                f"{defense.get('leader', '')}: locked to {locked_counter.get('counter_leader', '')}"
            )

        defense_options.append({
            "index": defense_index,
            "defense": defense,
            "valid_counters": valid_counters,
            "locked_counter": locked_counter,
        })

    # Hardest defenses first for the search order, but choose the final plan
    # globally so flexible teams can give up counters to teams with fewer options.
    defense_options.sort(
        key=lambda item: (
            0 if item.get("locked_counter") is not None else 1,
            len(item["valid_counters"]),
            str(item["defense"].get("combat_type", "")),
            str(item["defense"].get("leader", "")),
        )
    )

    apply_counter_flexibility_penalties(defense_options)

    for item in defense_options:
        item["valid_counters"] = select_search_options(item["valid_counters"])
        item["valid_counters"] = sort_assignment_options(item["valid_counters"])

    best_assignment = find_best_assignment(defense_options)
    assignment_unit_owners = build_assignment_unit_owners(defense_options, best_assignment)
    planned_rows_by_index = {}
    used_units = {
        "characters": set(),
        "ships": set(),
    }

    for item in defense_options:
        defense = item["defense"]
        combat_type = str(defense.get("combat_type", ""))
        leader = str(defense.get("leader", ""))
        valid_counters = item["valid_counters"]
        chosen_counter = best_assignment.get(item["index"])

        if chosen_counter is None:
            if not valid_counters:
                warnings["no_valid_counter"].append(f"{leader}: no counter met thresholds and roster requirements")
                planned_rows_by_index[item["index"]] = empty_plan_row(
                    defense,
                    "no_valid_counter",
                    "no counter met win, seen, and roster requirements",
                )
            else:
                blocker_reasons = [
                    counter_block_reason(counter, assignment_unit_owners)
                    for counter in valid_counters[:5]
                ]
                specific_reason = (
                    "valid counters existed, but assignment conflicts blocked them; "
                    + " | ".join(blocker_reasons)
                )
                overlap_notes = describe_overlap_reasons(
                    leader,
                    valid_counters,
                    used_units.get(combat_type, set()),
                )
                warnings["unit_overlap"].extend(overlap_notes)
                planned_rows_by_index[item["index"]] = empty_plan_row(
                    defense,
                    "blocked_by_unit_overlap",
                    specific_reason,
                )
            continue

        overlap_notes = describe_overlap_reasons(
            leader,
            valid_counters,
            used_units.get(combat_type, set()),
            stop_at_counter=chosen_counter,
        )
        warnings["unit_overlap"].extend(overlap_notes)
        used_units.setdefault(combat_type, set()).update(chosen_counter.get("counter_units", []))
        planned_rows_by_index[item["index"]] = assigned_plan_row(defense, chosen_counter)

    plan_rows = [
        planned_rows_by_index[index]
        for index in defense_df.index
    ]

    return pd.DataFrame(plan_rows), warnings


def find_locked_counter_for_defense(
    defense: pd.Series,
    valid_counters: list[dict],
    locked_matchups: set[tuple[str, str, str, tuple[str, ...]]],
) -> dict | None:
    combat_type = str(defense.get("combat_type", ""))
    leader = str(defense.get("leader", ""))

    for counter in valid_counters:
        signature = counter_signature(
            combat_type,
            leader,
            counter.get("counter_leader", ""),
            counter.get("counter_units", []),
        )

        if signature in locked_matchups:
            return counter

    return None


def counter_team_key(counter: dict) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(counter.get("combat_type", "")),
        str(counter.get("counter_leader", "")),
        tuple(counter.get("counter_units", [])),
    )


def apply_counter_flexibility_penalties(defense_options: list[dict]) -> None:
    defenses_by_counter = {}

    for item in defense_options:
        defense_index = item["index"]

        for counter in item["valid_counters"]:
            key = counter_team_key(counter)
            defenses_by_counter.setdefault(key, set()).add(defense_index)

    for item in defense_options:
        for counter in item["valid_counters"]:
            flexibility_count = len(defenses_by_counter.get(counter_team_key(counter), set()))
            penalty = max(0, flexibility_count - 1) * FLEXIBILITY_SCORE_PENALTY

            counter["flexibility_count"] = flexibility_count
            counter["flexibility_penalty"] = round(penalty, 3)

            if penalty:
                original_score = safe_float(counter.get("score"), 0.0)
                counter["score"] = round(original_score - penalty, 3)
                counter["roster_note"] = "; ".join(
                    note
                    for note in [
                        counter.get("roster_note", ""),
                        f"flexibility penalty: valid for {flexibility_count} defenses",
                    ]
                    if note
                )

def build_assignment_unit_owners(
    defense_options: list[dict],
    assignment: dict[int, dict],
) -> dict[str, str]:
    owners = {}

    defenses_by_index = {
        item["index"]: item["defense"]
        for item in defense_options
    }

    for assigned_index, assigned_counter in assignment.items():
        defense = defenses_by_index.get(assigned_index)
        defense_leader = (
            str(defense.get("leader", assigned_index))
            if defense is not None
            else str(assigned_index)
        )
        counter_leader = str(assigned_counter.get("counter_leader", ""))

        for unit in assigned_counter.get("counter_units", []):
            owners[str(unit)] = f"{defense_leader} using {counter_leader}"

    return owners


def counter_block_reason(counter: dict, unit_owners: dict[str, str]) -> str:
    counter_leader = str(counter.get("counter_leader", ""))
    overlap = [
        unit
        for unit in counter.get("counter_units", [])
        if str(unit) in unit_owners
    ]

    if not overlap:
        return f"{counter_leader} was not selected by the global optimizer"

    owner_notes = [
        f"{unit} already assigned to {unit_owners[str(unit)]}"
        for unit in overlap
    ]

    return f"{counter_leader} blocked: " + "; ".join(owner_notes)


def defense_assignment_bonus(item: dict) -> float:
    valid_count = len(item.get("valid_counters", []))

    if valid_count <= 0:
        return 0.0

    return round(SCARCE_DEFENSE_ASSIGNMENT_BONUS / valid_count, 3)


def assignment_score_for_counter(item: dict, counter: dict) -> float:
    return safe_float(counter.get("score"), 0.0) + defense_assignment_bonus(item)


def find_best_assignment(defense_options: list[dict]) -> dict[int, dict]:
    states = [
        {
            "assignment": {},
            "used_units": {"characters": set(), "ships": set()},
            "score": 0.0,
        }
    ]

    def state_rank(state: dict) -> tuple[int, float, float]:
        return (
            len(state["assignment"]),
            round(safe_float(state.get("score"), 0.0), 3),
            -sum(len(units) for units in state["used_units"].values()),
        )

    for item in defense_options:
        combat_type = str(item["defense"].get("combat_type", ""))
        counters_to_try = item["valid_counters"]

        if item.get("locked_counter") is not None:
            counters_to_try = [item["locked_counter"]]

        next_states = []
        blocked_count = 0
        tried_count = 0

        for state in states:
            current_used = state["used_units"].setdefault(combat_type, set())
            for counter in counters_to_try:
                tried_count += 1
                counter_units = set(counter.get("counter_units", []))
                overlap = sorted(counter_units & current_used)

                if overlap:
                    blocked_count += 1
                    continue

                next_assignment = state["assignment"].copy()
                next_assignment[item["index"]] = counter
                next_used_units = {
                    key: value.copy()
                    for key, value in state["used_units"].items()
                }
                next_used_units.setdefault(combat_type, set()).update(counter_units)
                next_states.append({
                    "assignment": next_assignment,
                    "used_units": next_used_units,
                    "score": safe_float(state.get("score"), 0.0) + assignment_score_for_counter(item, counter),
                })

            if item.get("locked_counter") is None:
                next_states.append({
                    "assignment": state["assignment"].copy(),
                    "used_units": {
                        key: value.copy()
                        for key, value in state["used_units"].items()
                    },
                    "score": state["score"],
                })

        next_states.sort(key=state_rank, reverse=True)
        states = next_states[:BEAM_SEARCH_WIDTH]

    if not states:
        return {}

    states.sort(key=state_rank, reverse=True)
    best_state = states[0]

    return best_state["assignment"]


def select_search_options(valid_counters: list[dict]) -> list[dict]:
    if len(valid_counters) <= MAX_COUNTER_OPTIONS_PER_DEFENSE:
        return valid_counters

    by_reliability = sorted(
        valid_counters,
        key=lambda row: (
            safe_float(row.get("reliability_score"), 0.0),
            safe_float(row.get("score"), 0.0),
        ),
        reverse=True,
    )
    by_value = sorted(
        valid_counters,
        key=lambda row: (
            safe_float(row.get("score"), 0.0),
            safe_float(row.get("reliability_score"), 0.0),
        ),
        reverse=True,
    )

    selected = []
    seen_keys = set()

    for counter in by_reliability[:MAX_COUNTER_OPTIONS_PER_DEFENSE]:
        key = (counter.get("counter_leader", ""), tuple(counter.get("counter_units", [])))
        if key not in seen_keys:
            selected.append(counter)
            seen_keys.add(key)

    for counter in by_value[:MAX_COUNTER_OPTIONS_PER_DEFENSE]:
        key = (counter.get("counter_leader", ""), tuple(counter.get("counter_units", [])))
        if key not in seen_keys:
            selected.append(counter)
            seen_keys.add(key)

    selected.sort(
        key=lambda row: (
            safe_float(row.get("reliability_score"), 0.0),
            safe_float(row.get("score"), 0.0),
        ),
        reverse=True,
    )

    return selected


def sort_assignment_options(valid_counters: list[dict]) -> list[dict]:
    return sorted(
        valid_counters,
        key=lambda row: (
            safe_float(row.get("score"), 0.0),
            safe_float(row.get("reliability_score"), 0.0),
            safe_float(row.get("seen"), 0.0),
        ),
        reverse=True,
    )


def option_debug_label(counter: dict) -> str:
    return (
        f"{counter.get('counter_leader', '')}"
        f"(score={counter.get('score', '')}, "
        f"rel={counter.get('reliability_score', '')}, "
        f"units={counter.get('counter_units', [])})"
    )


def describe_overlap_reasons(
    leader: str,
    valid_counters: list[dict],
    used_units: set,
    stop_at_counter: dict | None = None,
) -> list[str]:
    notes = []

    for counter in valid_counters:
        if stop_at_counter is not None and counter is stop_at_counter:
            break

        overlap = sorted(set(counter.get("counter_units", [])) & used_units)

        if overlap:
            notes.append(
                f"{leader}: skipped {counter.get('counter_leader', '')} due to overlap {overlap}"
            )

    return notes


def save_strategy(strategy_df: pd.DataFrame) -> None:
    ensure_data_dirs()
    strategy_df.to_csv(OUTPUT_FILE, index=False)


def print_strategy(strategy_df: pd.DataFrame, warnings: dict[str, list[str]]) -> None:
    print("\nRecommended strategy:")

    for _, row in strategy_df.iterrows():
        defense_label = f"{row.get('defense_name', '')} ({row.get('defense_leader', '')})"
        status = row.get("status", "")

        if status == "assigned":
            print(
                f"- {defense_label}: use {row.get('chosen_counter_leader', '')} "
                f"{row.get('chosen_counter_units', [])} "
                f"({row.get('win_percent', '')}% win, seen {row.get('seen', '')}, "
                f"cost {row.get('counter_cost', '')}, score {row.get('score', '')})"
            )
        else:
            print(f"- {defense_label}: {status} - {row.get('reason', '')}")

    print("\nWarnings:")
    warning_count = 0

    for warning_type, messages in warnings.items():
        if not messages:
            continue

        unique_messages = list(dict.fromkeys(messages))
        warning_count += len(unique_messages)
        print(f"\n{warning_type}:")

        for message in unique_messages[:25]:
            print(f"- {message}")

        if len(unique_messages) > 25:
            print(f"- ... {len(unique_messages) - 25} more")

    if warning_count == 0:
        print("- None")

    print(f"\nSaved strategy to {OUTPUT_FILE}")

    print("\nDefense leader -> counter leader:")
    for _, row in strategy_df.iterrows():
        defense_leader = row.get("defense_leader", "")
        counter_leader = row.get("chosen_counter_leader", "") or "NO COUNTER"
        print(f"- {defense_leader}: {counter_leader}")


def main() -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
    defense_df, counters_df, roster_df = load_csvs()
    gac_format = infer_gac_format_from_defense_df(defense_df)
    defense_df, duplicate_warnings = dedupe_defenses_by_leader(defense_df)
    roster_set, roster_by_unit = build_roster_set(roster_df)

    strategy_df, warnings = choose_strategy(
        defense_df,
        counters_df,
        roster_set,
        roster_by_unit,
        rejected_counters=load_rejected_counter_signatures(),
        reserved_units=load_reserved_units(),
        locked_matchups=load_locked_matchup_signatures(),
        offense_team_locks=load_offense_team_lock_signatures(gac_format=gac_format),
    )
    warnings["duplicate_defenses"].extend(duplicate_warnings)

    save_strategy(strategy_df)
    print_strategy(strategy_df, warnings)


if __name__ == "__main__":
    main()
