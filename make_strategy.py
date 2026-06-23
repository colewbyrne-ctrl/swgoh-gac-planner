import ast
import math
import pandas as pd


DEFENSE_FILE = "defense_teams.csv"
COUNTERS_FILE = "counter_results.csv"
ROSTER_FILE = "roster_units.csv"
OUTPUT_FILE = "strategy_plan.csv"

MIN_WIN_PERCENT = 85
MIN_SEEN = 10
FALLBACK_MIN_SEEN = 1
MAX_COUNTER_OPTIONS_PER_DEFENSE = 15


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
        stars = safe_float(roster_row.get("stars") if roster_row is not None else None)

        if combat_type == "characters" and stars is not None and stars < 7:
            underbuilt_units.append(f"{unit} ({stars:g} stars)")

    if not units:
        return False, missing_units, underbuilt_units, "counter has no units"

    counter_leader = units[0]

    if counter_leader in missing_units:
        return False, missing_units, underbuilt_units, "counter leader is missing from roster"

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


def roster_strength_bonus(
    units: list[str],
    combat_type: str,
    roster_by_unit: dict[str, pd.Series],
) -> float:
    if not units:
        return 0.0

    bonuses = []

    for unit in units:
        roster_row = roster_by_unit.get(unit)
        if roster_row is None:
            continue

        stars = safe_float(roster_row.get("stars"), 0.0)
        completion = safe_float(roster_row.get("completion_percent"), 0.0)

        if combat_type == "ships":
            ship_level = safe_float(roster_row.get("ship_level"), 0.0)
            unit_bonus = (stars / 7.0) * 1.2
            unit_bonus += (completion / 100.0) * 1.0
            unit_bonus += (ship_level / 100.0) * 0.8
        else:
            relic_level = safe_float(roster_row.get("relic_level"), 0.0)
            zeta_count = safe_float(roster_row.get("zeta_count"), 0.0)
            unit_bonus = (stars / 7.0) * 0.8
            unit_bonus += min(relic_level, 9.0) / 9.0 * 1.2
            unit_bonus += min(zeta_count, 6.0) / 6.0 * 0.8
            unit_bonus += (completion / 100.0) * 0.8

        bonuses.append(unit_bonus)

    if not bonuses:
        return 0.0

    return sum(bonuses) / len(bonuses)


def score_counter(counter: pd.Series, roster_by_unit: dict[str, pd.Series]) -> float:
    win_percent = safe_float(counter.get("win_percent"), 0.0)
    seen = safe_float(counter.get("seen"), 0.0)
    avg_banners = safe_float(counter.get("avg_banners"), 0.0)
    combat_type = str(counter.get("combat_type", ""))
    counter_units = counter.get("counter_units", [])

    score = win_percent

    # Reliability matters, but the win rate remains the main signal.
    score += min(math.log10(max(seen, 1.0)), 3.0) * 1.5
    score += roster_strength_bonus(counter_units, combat_type, roster_by_unit)

    if avg_banners > 0:
        score += min(avg_banners / 60.0, 1.5)

    return round(score, 3)


def find_valid_counters_for_defense(
    defense: pd.Series,
    counters_df: pd.DataFrame,
    roster_set: set[str],
    roster_by_unit: dict[str, pd.Series],
    warnings: dict[str, list[str]],
) -> list[dict]:
    combat_type = str(defense.get("combat_type", ""))
    leader = str(defense.get("leader", ""))

    required_columns = {"combat_type", "defense_leader", "counter_units", "seen", "win_percent"}
    if not required_columns.issubset(counters_df.columns):
        missing = sorted(required_columns - set(counters_df.columns))
        warnings["missing_columns"].append(f"counter_results.csv missing columns: {missing}")
        return []

    possible = counters_df[
        (counters_df["combat_type"].astype(str) == combat_type)
        & (counters_df["defense_leader"].astype(str) == leader)
        & (counters_df["win_percent"].fillna(0) >= MIN_WIN_PERCENT)
        & (counters_df["seen"].fillna(0) >= MIN_SEEN)
    ]

    low_sample_fallback = False

    if possible.empty:
        possible = counters_df[
            (counters_df["combat_type"].astype(str) == combat_type)
            & (counters_df["defense_leader"].astype(str) == leader)
            & (counters_df["win_percent"].fillna(0) >= MIN_WIN_PERCENT)
            & (counters_df["seen"].fillna(0) >= FALLBACK_MIN_SEEN)
        ]
        low_sample_fallback = not possible.empty

        if low_sample_fallback:
            warnings["low_sample_fallback"].append(
                f"{leader}: no counters had seen >= {MIN_SEEN}; allowing seen >= {FALLBACK_MIN_SEEN}"
            )

    valid_counters = []

    for _, counter in possible.iterrows():
        counter_units = counter.get("counter_units", [])
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

            warnings["missing_roster"].append(
                f"{leader}: skipped {counter.get('counter_leader', '')} because "
                + "; ".join(skipped_reason)
            )
            continue

        counter_dict = counter.to_dict()
        counter_dict["score"] = score_counter(counter, roster_by_unit)
        counter_dict["roster_note"] = roster_note

        if low_sample_fallback:
            low_sample_note = f"low sample size: seen {counter.get('seen', '')}"
            counter_dict["roster_note"] = "; ".join(
                note for note in [counter_dict["roster_note"], low_sample_note]
                if note
            )

        if roster_note:
            warnings["soft_roster_notes"].append(
                f"{leader}: allowing {counter.get('counter_leader', '')} with note: {roster_note}"
            )

        valid_counters.append(counter_dict)

    valid_counters.sort(key=lambda row: row["score"], reverse=True)
    return dedupe_counter_options(valid_counters)


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
    deduped.sort(key=lambda row: row["score"], reverse=True)

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
        "score": counter.get("score", ""),
        "status": "assigned",
        "reason": reason,
    }


def choose_strategy(
    defense_df: pd.DataFrame,
    counters_df: pd.DataFrame,
    roster_set: set[str],
    roster_by_unit: dict[str, pd.Series],
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    warnings = {
        "no_valid_counter": [],
        "missing_roster": [],
        "soft_roster_notes": [],
        "low_sample_fallback": [],
        "unit_overlap": [],
        "missing_columns": [],
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
        )
        defense_options.append({
            "index": defense_index,
            "defense": defense,
            "valid_counters": valid_counters,
        })

    # Hardest defenses first for the search order, but choose the final plan
    # globally so flexible teams can give up counters to teams with fewer options.
    defense_options.sort(
        key=lambda item: (
            len(item["valid_counters"]),
            str(item["defense"].get("combat_type", "")),
            str(item["defense"].get("leader", "")),
        )
    )

    for item in defense_options:
        item["valid_counters"] = item["valid_counters"][:MAX_COUNTER_OPTIONS_PER_DEFENSE]

    best_assignment = find_best_assignment(defense_options)
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
                overlap_notes = describe_overlap_reasons(
                    leader,
                    valid_counters,
                    used_units.get(combat_type, set()),
                )
                warnings["unit_overlap"].extend(overlap_notes)
                planned_rows_by_index[item["index"]] = empty_plan_row(
                    defense,
                    "blocked_by_unit_overlap",
                    "valid counters existed, but every global assignment option reused units",
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


def find_best_assignment(defense_options: list[dict]) -> dict[int, dict]:
    best_score = (-1, -1.0)
    best_assignment = {}

    def search(position: int, used_units: dict[str, set], assignment: dict[int, dict], total_score: float) -> None:
        nonlocal best_score, best_assignment

        assigned_count = len(assignment)
        remaining = len(defense_options) - position

        if assigned_count + remaining < best_score[0]:
            return

        if position == len(defense_options):
            candidate_score = (assigned_count, round(total_score, 3))

            if candidate_score > best_score:
                best_score = candidate_score
                best_assignment = assignment.copy()

            return

        item = defense_options[position]
        combat_type = str(item["defense"].get("combat_type", ""))
        current_used = used_units.setdefault(combat_type, set())

        for counter in item["valid_counters"]:
            counter_units = set(counter.get("counter_units", []))

            if counter_units & current_used:
                continue

            assignment[item["index"]] = counter
            next_used_units = {
                key: value.copy()
                for key, value in used_units.items()
            }
            next_used_units.setdefault(combat_type, set()).update(counter_units)

            search(
                position + 1,
                next_used_units,
                assignment,
                total_score + safe_float(counter.get("score"), 0.0),
            )

            assignment.pop(item["index"], None)

        search(position + 1, used_units, assignment, total_score)

    search(
        position=0,
        used_units={"characters": set(), "ships": set()},
        assignment={},
        total_score=0.0,
    )

    return best_assignment


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
                f"({row.get('win_percent', '')}% win, seen {row.get('seen', '')}, score {row.get('score', '')})"
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
    defense_df, counters_df, roster_df = load_csvs()
    defense_df, duplicate_warnings = dedupe_defenses_by_leader(defense_df)
    roster_set, roster_by_unit = build_roster_set(roster_df)

    strategy_df, warnings = choose_strategy(
        defense_df,
        counters_df,
        roster_set,
        roster_by_unit,
    )
    warnings["duplicate_defenses"].extend(duplicate_warnings)

    save_strategy(strategy_df)
    print_strategy(strategy_df, warnings)


if __name__ == "__main__":
    main()
