import argparse
import ast
import math
from pathlib import Path

import pandas as pd

from .project_paths import csv_path, ensure_data_dirs, migrate_legacy_csvs

ROSTER_FILE = csv_path("roster_units.csv")
STRATEGY_FILE = csv_path("strategy_plan.csv")
DEFENSE_FILE = csv_path("defense_teams.csv")
COUNTERS_FILE = csv_path("counter_results.csv")
LIBRARY_FILE = csv_path("defense_team_library.csv")
THREE_V_THREE_LIBRARY_FILE = csv_path("defense_team_library_3v3.csv")
OPTIONS_FILE = csv_path("my_defense_options.csv")
PLAN_FILE = csv_path("my_defense_plan.csv")
OFFENSE_TEAM_LOCKS_FILE = csv_path("offense_team_locks.csv")
THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE = csv_path("offense_team_locks_3v3.csv")
MAX_LIBRARY_TEAMS_PER_LEADER = 3
MIN_UNIT_CHANGE_FOR_DIFFERENT_TEAM = 3
MIN_CHARACTER_RELIC_LEVEL = 3


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


def safe_float(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def load_csv(path: str) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()

    return pd.read_csv(path)


def normalize_gac_format(gac_format: str = "all", defense_df: pd.DataFrame | None = None) -> str:
    gac_format = str(gac_format or "all").strip().lower()

    if gac_format in {"3v3", "5v5"}:
        return gac_format

    if defense_df is not None and "match_format" in defense_df.columns:
        formats = {
            str(value).strip().lower()
            for value in defense_df["match_format"].dropna()
            if str(value).strip().lower() in {"3v3", "5v5"}
        }

        if len(formats) == 1:
            return next(iter(formats))

    return "5v5"


def library_file_for_format(gac_format: str = "all", defense_df: pd.DataFrame | None = None) -> str:
    normalized_format = normalize_gac_format(gac_format, defense_df)

    if normalized_format == "3v3":
        return THREE_V_THREE_LIBRARY_FILE

    return LIBRARY_FILE


def normalize_inputs(
    roster_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    defense_df: pd.DataFrame,
    counters_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for df, column in [
        (strategy_df, "chosen_counter_units"),
        (defense_df, "units"),
        (counters_df, "defense_units"),
    ]:
        if not df.empty and column in df.columns:
            df[column] = df[column].apply(parse_unit_list)

    for df in [roster_df, counters_df]:
        for column in ["stars", "relic_level", "zeta_count", "ship_level", "completion_percent", "seen", "win_percent"]:
            if not df.empty and column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

    return roster_df, strategy_df, defense_df, counters_df


def build_used_offense_units(strategy_df: pd.DataFrame) -> set[str]:
    used_units = set()

    if strategy_df.empty or "chosen_counter_units" not in strategy_df.columns:
        return used_units

    for _, row in strategy_df.iterrows():
        if str(row.get("status", "")) != "assigned":
            continue

        for unit in row.get("chosen_counter_units", []):
            used_units.add(unit)

    return used_units


def offense_team_locks_file_for_format(gac_format: str = "5v5") -> str:
    if str(gac_format).strip().lower() == "3v3":
        return THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE

    return OFFENSE_TEAM_LOCKS_FILE


def load_offense_team_lock_units(
    path: str | None = None,
    gac_format: str = "5v5",
) -> set[str]:
    path = path or offense_team_locks_file_for_format(gac_format)
    locks_df = load_csv(path)

    if locks_df.empty or "team_units" not in locks_df.columns:
        return set()

    locked_units = set()

    for _, row in locks_df.iterrows():
        for unit in parse_unit_list(row.get("team_units", [])):
            locked_units.add(unit)

    return locked_units


def build_roster_lookup(roster_df: pd.DataFrame) -> dict[str, pd.Series]:
    if roster_df.empty or "base_id" not in roster_df.columns:
        return {}

    roster_df = roster_df.copy()
    roster_df["base_id"] = roster_df["base_id"].fillna("").astype(str)
    roster_df = roster_df[roster_df["base_id"] != ""]

    return {
        row["base_id"]: row
        for _, row in roster_df.iterrows()
    }


def add_library_entry(
    library: dict,
    combat_type: str,
    leader: str,
    units: list[str],
    source: str,
    seen: float = 0.0,
    win_percent: float | None = None,
    gac_format: str = "5v5",
) -> None:
    if not leader or not units:
        return

    clean_units = [str(unit).strip() for unit in units if str(unit).strip()]
    leader = str(leader).strip()
    support_units = sorted(unit for unit in clean_units if unit != leader)
    canonical_units = [leader] + support_units

    key = (
        str(combat_type).strip(),
        leader,
        tuple(canonical_units),
    )

    if not key[0] or not key[1] or not key[2]:
        return

    if key not in library:
        library[key] = {
            "combat_type": key[0],
            "leader": key[1],
            "team_units": canonical_units,
            "gac_format": gac_format,
            "source_notes": set(),
            "times_seen": 0,
            "total_counter_seen": 0.0,
            "counter_win_values": [],
        }

    row = library[key]
    row["source_notes"].add(source)
    row["times_seen"] += 1
    row["total_counter_seen"] += seen

    if win_percent is not None:
        row["counter_win_values"].append(win_percent)


def load_existing_library(library_file: str = LIBRARY_FILE, gac_format: str = "5v5") -> dict:
    library = {}
    existing_df = load_csv(library_file)

    if existing_df.empty:
        return library

    for _, row in existing_df.iterrows():
        units = parse_unit_list(row.get("team_units", []))
        source_notes = [
            source.strip()
            for source in str(row.get("source_notes", "existing_library")).split(",")
            if source.strip()
        ]

        add_library_entry(
            library,
            row.get("combat_type", ""),
            row.get("leader", ""),
            units,
            source_notes[0] if source_notes else "existing_library",
            safe_float(row.get("total_counter_seen"), 0.0),
            safe_float(row.get("avg_counter_win_percent"), None),
            str(row.get("gac_format", gac_format)).strip() or gac_format,
        )

        key = (str(row.get("combat_type", "")).strip(), str(row.get("leader", "")).strip(), tuple(units))
        if key in library:
            for source in source_notes[1:]:
                library[key]["source_notes"].add(source)

            library[key]["times_seen"] = max(
                int(safe_float(row.get("times_seen"), 1)),
                library[key]["times_seen"],
            )

    return library


def merge_libraries(primary: dict, fallback: dict) -> dict:
    """Merge two libraries, letting ``primary`` win on any shared team.

    ``fallback`` only contributes teams that ``primary`` never observed, so
    counts from the current run are authoritative and are never summed on top
    of a previous run's counts. Provenance (``source_notes``) is unioned.
    """

    def clone(entry: dict) -> dict:
        copied = dict(entry)
        copied["source_notes"] = set(entry["source_notes"])
        copied["counter_win_values"] = list(entry["counter_win_values"])
        return copied

    merged = {key: clone(entry) for key, entry in primary.items()}

    for key, entry in fallback.items():
        if key in merged:
            merged[key]["source_notes"] |= entry["source_notes"]
        else:
            merged[key] = clone(entry)

    return merged


def build_defense_library(
    defense_df: pd.DataFrame,
    counters_df: pd.DataFrame,
    gac_format: str = "all",
) -> pd.DataFrame:
    normalized_format = normalize_gac_format(gac_format, defense_df)
    library_file = library_file_for_format(normalized_format)

    # Counts derived purely from the current run. Rebuilding from the same
    # active_run data always yields the same numbers, so defensive rankings do
    # not drift when the planner is re-run for the same matchup.
    current: dict = {}

    if not defense_df.empty:
        for _, row in defense_df.iterrows():
            units = row.get("units", [])
            add_library_entry(
                current,
                row.get("combat_type", ""),
                row.get("leader", units[0] if units else ""),
                units,
                "defense_teams.csv",
                gac_format=normalized_format,
            )

    if not counters_df.empty and "defense_units" in counters_df.columns:
        for _, row in counters_df.iterrows():
            units = row.get("defense_units", [])
            leader = row.get("defense_leader", units[0] if units else "")
            add_library_entry(
                current,
                row.get("combat_type", ""),
                leader,
                units,
                "counter_results.csv",
                safe_float(row.get("seen"), 0.0),
                safe_float(row.get("win_percent"), None),
                normalized_format,
            )

    # Carry forward teams seen in earlier runs (e.g. against other opponents)
    # that the current run did not observe, without re-adding their counts.
    existing = load_existing_library(library_file, normalized_format)
    library = merge_libraries(current, existing)

    rows = []

    for row in library.values():
        win_values = row["counter_win_values"]
        avg_counter_win = sum(win_values) / len(win_values) if win_values else ""
        rows.append({
            "combat_type": row["combat_type"],
            "gac_format": row["gac_format"],
            "leader": row["leader"],
            "team_units": row["team_units"],
            "unit_count": len(row["team_units"]),
            "times_seen": row["times_seen"],
            "total_counter_seen": round(row["total_counter_seen"], 3),
            "avg_counter_win_percent": round(avg_counter_win, 3) if avg_counter_win != "" else "",
            "source_notes": ", ".join(sorted(row["source_notes"])),
        })

    library_df = pd.DataFrame(rows)

    if not library_df.empty:
        library_df = library_df.sort_values(
            by=["times_seen", "total_counter_seen"],
            ascending=[False, False],
        ).reset_index(drop=True)
        library_df = prune_library_variants(library_df)

    ensure_data_dirs()
    library_df.to_csv(library_file, index=False)
    print(f"Saved long-term defense team library to {library_file}")

    return library_df


def team_difference_count(units_a: list[str], units_b: list[str]) -> int:
    set_a = set(units_a)
    set_b = set(units_b)

    return max(
        len(set_a - set_b),
        len(set_b - set_a),
    )


def prune_library_variants(library_df: pd.DataFrame) -> pd.DataFrame:
    kept_rows = []

    for _, group_df in library_df.groupby(["combat_type", "leader"], sort=False):
        selected_units = []

        group_df = group_df.sort_values(
            by=["times_seen", "total_counter_seen"],
            ascending=[False, False],
        )

        for _, row in group_df.iterrows():
            units = parse_unit_list(row.get("team_units", []))

            if not selected_units:
                kept_rows.append(row.to_dict())
                selected_units.append(units)
                continue

            is_different = all(
                team_difference_count(units, existing_units) >= MIN_UNIT_CHANGE_FOR_DIFFERENT_TEAM
                for existing_units in selected_units
            )

            if not is_different:
                continue

            kept_rows.append(row.to_dict())
            selected_units.append(units)

            if len(selected_units) >= MAX_LIBRARY_TEAMS_PER_LEADER:
                break

    pruned_df = pd.DataFrame(kept_rows)

    if not pruned_df.empty:
        pruned_df = pruned_df.sort_values(
            by=["times_seen", "total_counter_seen"],
            ascending=[False, False],
        ).reset_index(drop=True)

    return pruned_df


def unit_strength(unit: str, combat_type: str, roster_by_unit: dict[str, pd.Series]) -> float:
    roster_row = roster_by_unit.get(unit)

    if roster_row is None:
        return 0.0

    stars = safe_float(roster_row.get("stars"), 0.0)
    completion = safe_float(roster_row.get("completion_percent"), 0.0)
    strength = (stars / 7.0) * 10.0 + completion / 10.0

    if combat_type == "ships":
        strength += safe_float(roster_row.get("ship_level"), 0.0) / 10.0
        if safe_bool(roster_row.get("is_capital_ship")):
            strength += 8.0
    else:
        strength += safe_float(roster_row.get("relic_level"), 0.0) * 3.0
        strength += safe_float(roster_row.get("zeta_count"), 0.0) * 1.2
        if safe_bool(roster_row.get("is_galactic_legend")):
            strength += 20.0
        if safe_bool(roster_row.get("has_ultimate")):
            strength += 5.0

    return strength


def can_field_team(units: list[str], combat_type: str, roster_by_unit: dict[str, pd.Series], used_offense_units: set[str]) -> tuple[bool, list[str], list[str]]:
    missing_units = []
    used_units = []

    for unit in units:
        if unit in used_offense_units:
            used_units.append(unit)
            continue

        roster_row = roster_by_unit.get(unit)
        if roster_row is None:
            missing_units.append(unit)
            continue

        relic_level = safe_float(roster_row.get("relic_level"), 0.0)
        if combat_type == "characters" and relic_level < MIN_CHARACTER_RELIC_LEVEL:
            missing_units.append(f"{unit} (relic {relic_level:g})")

    return not missing_units and not used_units, missing_units, used_units


def score_team(row: pd.Series, roster_by_unit: dict[str, pd.Series]) -> float:
    combat_type = str(row.get("combat_type", ""))
    units = parse_unit_list(row.get("team_units", []))
    total_strength = sum(unit_strength(unit, combat_type, roster_by_unit) for unit in units)
    avg_strength = total_strength / len(units) if units else 0.0
    popularity = math.log10(1.0 + safe_float(row.get("times_seen"), 0.0) + safe_float(row.get("total_counter_seen"), 0.0)) * 8.0
    avg_counter_win = safe_float(row.get("avg_counter_win_percent"), 90.0)
    counter_resistance = max(0.0, 100.0 - avg_counter_win) * 1.2

    return round(avg_strength + popularity + counter_resistance, 3)


def generate_defense_options(
    roster_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    library_df: pd.DataFrame,
    offense_locked_units: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    roster_by_unit = build_roster_lookup(roster_df)
    used_offense_units = build_used_offense_units(strategy_df)
    used_offense_units.update(offense_locked_units or set())
    option_rows = []

    for _, row in library_df.iterrows():
        units = parse_unit_list(row.get("team_units", []))
        combat_type = str(row.get("combat_type", "")).strip()
        can_field, missing_units, used_units = can_field_team(
            units,
            combat_type,
            roster_by_unit,
            used_offense_units,
        )

        if not can_field:
            continue

        option = row.to_dict()
        option["team_units"] = units
        option["excluded_offense_units"] = sorted(used_offense_units & set(units))
        option["perceived_strength"] = score_team(row, roster_by_unit)
        option_rows.append(option)

    options_df = pd.DataFrame(option_rows)

    if not options_df.empty:
        options_df = options_df.sort_values(
            by=["perceived_strength", "times_seen", "total_counter_seen"],
            ascending=[False, False, False],
        ).reset_index(drop=True)

    plan_df = choose_non_overlapping_defense_plan(options_df)

    return options_df, plan_df


def choose_non_overlapping_defense_plan(options_df: pd.DataFrame) -> pd.DataFrame:
    if options_df.empty:
        return pd.DataFrame()

    used_units = {
        "characters": set(),
        "ships": set(),
    }
    chosen_rows = []

    for _, row in options_df.iterrows():
        combat_type = str(row.get("combat_type", ""))
        units = set(parse_unit_list(row.get("team_units", [])))

        if units & used_units.setdefault(combat_type, set()):
            continue

        chosen_rows.append(row.to_dict())
        used_units[combat_type].update(units)

    return pd.DataFrame(chosen_rows)


def save_and_print_defense_options(options_df: pd.DataFrame, plan_df: pd.DataFrame) -> None:
    ensure_data_dirs()
    options_df.to_csv(OPTIONS_FILE, index=False)
    plan_df.to_csv(PLAN_FILE, index=False)

    print(f"\nSaved ranked defense options to {OPTIONS_FILE}")
    print(f"Saved non-overlapping defense plan to {PLAN_FILE}")

    print("\nTop potential defensive teams:")

    if options_df.empty:
        print("- No complete defensive teams available after excluding offense counters.")
        return

    for _, row in options_df.head(12).iterrows():
        print(
            f"- {row.get('leader', '')}: {row.get('team_units', [])} "
            f"(strength {row.get('perceived_strength', '')})"
        )


def run_my_defense_planner(
    roster_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    defense_df: pd.DataFrame,
    counters_df: pd.DataFrame,
    gac_format: str = "all",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    roster_df, strategy_df, defense_df, counters_df = normalize_inputs(
        roster_df,
        strategy_df,
        defense_df,
        counters_df,
    )
    library_df = build_defense_library(defense_df, counters_df, gac_format)
    options_df, plan_df = generate_defense_options(
        roster_df,
        strategy_df,
        library_df,
        offense_locked_units=load_offense_team_lock_units(gac_format=gac_format),
    )
    save_and_print_defense_options(options_df, plan_df)

    return library_df, options_df, plan_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank possible defensive teams from unused roster units.")
    parser.add_argument("--roster-file", default=ROSTER_FILE)
    parser.add_argument("--strategy-file", default=STRATEGY_FILE)
    parser.add_argument("--defense-file", default=DEFENSE_FILE)
    parser.add_argument("--counters-file", default=COUNTERS_FILE)
    parser.add_argument(
        "--gac-format",
        choices=["all", "3v3", "5v5"],
        default="all",
        help="Which long-term defense team library to update.",
    )

    return parser.parse_args()


def main() -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
    args = parse_args()
    roster_df = load_csv(args.roster_file)
    strategy_df = load_csv(args.strategy_file)
    defense_df = load_csv(args.defense_file)
    counters_df = load_csv(args.counters_file)

    run_my_defense_planner(
        roster_df,
        strategy_df,
        defense_df,
        counters_df,
        gac_format=args.gac_format,
    )


if __name__ == "__main__":
    main()
