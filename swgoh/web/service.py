"""Application logic behind the review UI.

This module is deliberately HTTP-free: it loads and rebuilds plans, manages the
persisted rule files (rejections, locks, reserved units), and controls the
scrape-and-plan subprocess. The FastAPI layer in ``app.py`` is a thin adapter
over these functions.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from swgoh.make_strategy import (
    LEADER_EXEMPTIONS_FILE,
    LOCKED_MATCHUPS_FILE,
    OFFENSE_TEAM_LOCKS_FILE,
    REJECTIONS_FILE,
    RESERVED_UNITS_FILE,
    THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE,
    build_roster_set,
    choose_strategy,
    counter_signature,
    dedupe_defenses_by_leader,
    find_valid_counters_for_defense,
    load_csvs,
    load_leader_exemptions,
    load_locked_matchup_signatures,
    load_offense_team_lock_signatures,
    load_rejected_counter_signatures,
    load_reserved_units,
    parse_unit_list,
    save_strategy,
)
from swgoh.plan_my_defense import (
    COUNTERS_FILE,
    DEFENSE_FILE,
    LIBRARY_FILE,
    ROSTER_FILE,
    STRATEGY_FILE,
    THREE_V_THREE_LIBRARY_FILE,
    load_csv,
    run_my_defense_planner,
)
from swgoh.project_paths import ensure_data_dirs, migrate_legacy_csvs

PIPELINE_SETTINGS_FILE = "pipeline_settings.json"
PIPELINE_LOG_FILE = "pipeline_run.log"

DEFAULT_PIPELINE_SETTINGS = {
    "my_player_id": "848865876",
    "enemy_player_id": "721192678",
    "history_limit": "3",
    "gac_format": "5v5",
    "counter_season_id": "",
}

STRATEGY_INPUT_FILES = [
    DEFENSE_FILE,
    COUNTERS_FILE,
    ROSTER_FILE,
    LOCKED_MATCHUPS_FILE,
    REJECTIONS_FILE,
    RESERVED_UNITS_FILE,
    OFFENSE_TEAM_LOCKS_FILE,
    THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE,
    LEADER_EXEMPTIONS_FILE,
]
DEFENSE_PLAN_INPUT_FILES = [
    ROSTER_FILE,
    STRATEGY_FILE,
    DEFENSE_FILE,
    COUNTERS_FILE,
    OFFENSE_TEAM_LOCKS_FILE,
    THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE,
    LIBRARY_FILE,
    THREE_V_THREE_LIBRARY_FILE,
    PIPELINE_SETTINGS_FILE,
]

REMOVABLE_RULE_FILES = {
    "locked_matchups": LOCKED_MATCHUPS_FILE,
    "offense_team_locks": OFFENSE_TEAM_LOCKS_FILE,
    "offense_team_locks_3v3": THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE,
    "strategy_rejections": REJECTIONS_FILE,
    "reserved_units": RESERVED_UNITS_FILE,
    "leader_exemptions": LEADER_EXEMPTIONS_FILE,
}


# --- small utilities -------------------------------------------------------


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def project_python() -> str:
    venv_python = Path(".venv") / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


# --- pipeline settings -----------------------------------------------------


def load_pipeline_settings() -> dict[str, str]:
    path = Path(PIPELINE_SETTINGS_FILE)
    if not path.exists():
        return DEFAULT_PIPELINE_SETTINGS.copy()

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_PIPELINE_SETTINGS.copy()

    settings = DEFAULT_PIPELINE_SETTINGS.copy()
    for key in settings:
        value = str(loaded.get(key, settings[key])).strip()
        settings[key] = value or settings[key]

    if settings["gac_format"] not in {"all", "3v3", "5v5"}:
        settings["gac_format"] = DEFAULT_PIPELINE_SETTINGS["gac_format"]

    return settings


def save_pipeline_settings(
    my_player_id: str,
    enemy_player_id: str,
    history_limit: str,
    gac_format: str,
    counter_season_id: str = "",
) -> dict[str, str]:
    settings = {
        "my_player_id": (my_player_id or "").strip() or DEFAULT_PIPELINE_SETTINGS["my_player_id"],
        "enemy_player_id": (enemy_player_id or "").strip()
        or DEFAULT_PIPELINE_SETTINGS["enemy_player_id"],
        "history_limit": (history_limit or "").strip()
        or DEFAULT_PIPELINE_SETTINGS["history_limit"],
        "gac_format": (gac_format or "").strip() or DEFAULT_PIPELINE_SETTINGS["gac_format"],
        "counter_season_id": (counter_season_id or "").strip(),
    }
    if settings["gac_format"] not in {"all", "3v3", "5v5"}:
        settings["gac_format"] = DEFAULT_PIPELINE_SETTINGS["gac_format"]
    try:
        int(settings["history_limit"])
    except ValueError:
        settings["history_limit"] = DEFAULT_PIPELINE_SETTINGS["history_limit"]

    Path(PIPELINE_SETTINGS_FILE).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


# --- pipeline subprocess control ------------------------------------------


@dataclass
class PipelineState:
    process: subprocess.Popen | None = None
    started_at: str | None = None
    command: list[str] | None = None
    cancelled: bool = False
    log_handle: object | None = None


_pipeline = PipelineState()


class PlanCache:
    """Rebuild plans only when their input files change on disk."""

    def __init__(self) -> None:
        self.strategy_signature: tuple | None = None
        self.strategy_df: pd.DataFrame | None = None
        self.strategy_warnings: dict[str, list[str]] | None = None
        self.defense_signature: tuple | None = None

    def clear(self) -> None:
        self.strategy_signature = None
        self.strategy_df = None
        self.strategy_warnings = None
        self.defense_signature = None


_cache = PlanCache()


def _close_log_handle(final_line: str = "") -> None:
    if _pipeline.log_handle is None:
        return

    try:
        if final_line:
            _pipeline.log_handle.write(final_line)
        _pipeline.log_handle.close()
    except (OSError, ValueError):
        pass

    _pipeline.log_handle = None


def start_pipeline(settings: dict[str, str]) -> str:
    if _pipeline.process is not None and _pipeline.process.poll() is None:
        return "Pipeline is already running."

    command = [
        project_python(),
        "-m",
        "swgoh.pipeline",
        "--my-player-id",
        settings["my_player_id"],
        "--enemy-player-id",
        settings["enemy_player_id"],
        "--history-limit",
        settings["history_limit"],
        "--gac-format",
        settings["gac_format"],
    ]

    if settings.get("counter_season_id"):
        command += ["--season-id", settings["counter_season_id"]]

    _close_log_handle()

    log_handle = Path(PIPELINE_LOG_FILE).open("w", encoding="utf-8")
    log_handle.write(f"Started {now_text()}\n")
    log_handle.write("Command: " + " ".join(command) + "\n\n")
    log_handle.flush()

    _pipeline.process = subprocess.Popen(
        command, cwd=Path.cwd(), stdout=log_handle, stderr=subprocess.STDOUT, text=True
    )
    _pipeline.started_at = now_text()
    _pipeline.command = command
    _pipeline.cancelled = False
    _pipeline.log_handle = log_handle
    _cache.clear()
    return "Pipeline started. Refresh for status."


def _kill_process_tree(process: subprocess.Popen) -> None:
    """
    Stop the pipeline and everything it spawned.

    The pipeline launches Chrome through pydoll, so terminating only the Python
    process would leave browser children running. Windows needs taskkill for
    that; elsewhere terminate/kill is enough.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def stop_pipeline() -> str:
    if _pipeline.process is None or _pipeline.process.poll() is not None:
        return "No pipeline is running."

    _kill_process_tree(_pipeline.process)
    _pipeline.cancelled = True
    _close_log_handle(f"\nCancelled from the web UI at {now_text()}.\n")
    _cache.clear()
    return "Pipeline cancelled."


def pipeline_status() -> dict[str, str | int | None]:
    if _pipeline.process is None:
        return {"state": "idle", "detail": "No pipeline started from this session.", "returncode": None}

    returncode = _pipeline.process.poll()
    if returncode is None:
        return {
            "state": "running",
            "detail": f"Running since {_pipeline.started_at}; pid {_pipeline.process.pid}",
            "returncode": None,
        }

    if _pipeline.cancelled:
        return {
            "state": "cancelled",
            "detail": f"Cancelled after starting at {_pipeline.started_at}.",
            "returncode": returncode,
        }

    return {
        "state": "complete" if returncode == 0 else "failed",
        "detail": f"Finished with exit code {returncode}.",
        "returncode": returncode,
    }


def read_log_tail(max_lines: int = 120) -> str:
    path = Path(PIPELINE_LOG_FILE)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


# --- plan rebuild (cached on file signatures) ------------------------------


def _file_signature(paths: list[str]) -> tuple:
    signature = []
    for path in paths:
        file_path = Path(path)
        if not file_path.exists():
            signature.append((path, None, None))
            continue
        stat = file_path.stat()
        signature.append((path, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def rebuild_strategy(force: bool = False) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    signature = _file_signature(STRATEGY_INPUT_FILES)
    if (
        not force
        and _cache.strategy_signature == signature
        and _cache.strategy_df is not None
        and _cache.strategy_warnings is not None
    ):
        return _cache.strategy_df.copy(), _cache.strategy_warnings

    defense_df, counters_df, roster_df = load_csvs()
    defense_df, duplicate_warnings = dedupe_defenses_by_leader(defense_df)
    roster_set, roster_by_unit = build_roster_set(roster_df)

    gac_format = load_pipeline_settings()["gac_format"]
    strategy_df, warnings = choose_strategy(
        defense_df,
        counters_df,
        roster_set,
        roster_by_unit,
        rejected_counters=load_rejected_counter_signatures(),
        reserved_units=load_reserved_units(),
        exempt_leaders=load_leader_exemptions(),
        locked_matchups=load_locked_matchup_signatures(),
        offense_team_locks=load_offense_team_lock_signatures(gac_format=gac_format),
    )
    warnings["duplicate_defenses"].extend(duplicate_warnings)
    save_strategy(strategy_df)

    _cache.strategy_signature = _file_signature(STRATEGY_INPUT_FILES)
    _cache.strategy_df = strategy_df.copy()
    _cache.strategy_warnings = warnings
    return strategy_df, warnings


def rebuild_defense_plan(force: bool = False) -> None:
    signature = _file_signature(DEFENSE_PLAN_INPUT_FILES)
    if not force and _cache.defense_signature == signature:
        return

    run_my_defense_planner(
        load_csv(ROSTER_FILE),
        load_csv(STRATEGY_FILE),
        load_csv(DEFENSE_FILE),
        load_csv(COUNTERS_FILE),
        gac_format=load_pipeline_settings()["gac_format"],
    )
    _cache.defense_signature = _file_signature(DEFENSE_PLAN_INPUT_FILES)


# --- rule-file mutations ---------------------------------------------------


def _append_csv_row(path: str, fieldnames: list[str], row: dict) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not file_path.exists() or file_path.stat().st_size == 0
    with file_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _offense_locks_file(gac_format: str) -> str:
    if gac_format == "3v3":
        return THREE_V_THREE_OFFENSE_TEAM_LOCKS_FILE
    return OFFENSE_TEAM_LOCKS_FILE


def _load_offense_team_lock_keys(gac_format: str) -> set[tuple[str, tuple[str, ...]]]:
    path = Path(_offense_locks_file(gac_format))
    if not path.exists():
        return set()
    locks_df = pd.read_csv(path)
    if not {"leader", "team_units"}.issubset(locks_df.columns):
        return set()
    return {
        (str(row.get("leader", "")).strip(), tuple(parse_unit_list(row.get("team_units", []))))
        for _, row in locks_df.iterrows()
    }


def reject_counter(
    combat_type: str,
    defense_leader: str,
    defense_name: str,
    counter_leader: str,
    counter_units_raw: str,
    reason: str,
) -> str:
    counter_units = parse_unit_list(counter_units_raw)
    signature = counter_signature(combat_type, defense_leader, counter_leader, counter_units)
    if signature not in load_rejected_counter_signatures():
        _append_csv_row(
            REJECTIONS_FILE,
            ["created_at", "combat_type", "defense_leader", "defense_name",
             "counter_leader", "counter_units", "reason"],
            {
                "created_at": now_text(),
                "combat_type": combat_type,
                "defense_leader": defense_leader,
                "defense_name": defense_name,
                "counter_leader": counter_leader,
                "counter_units": repr(counter_units),
                "reason": reason,
            },
        )
    return f"Rejected {counter_leader} into {defense_leader} and recalculated."


def lock_matchup(
    combat_type: str,
    defense_leader: str,
    defense_name: str,
    counter_leader: str,
    counter_units_raw: str,
    reason: str,
) -> str:
    counter_units = parse_unit_list(counter_units_raw)
    signature = counter_signature(combat_type, defense_leader, counter_leader, counter_units)
    if signature not in load_locked_matchup_signatures():
        _append_csv_row(
            LOCKED_MATCHUPS_FILE,
            ["created_at", "combat_type", "defense_leader", "defense_name",
             "counter_leader", "counter_units", "reason"],
            {
                "created_at": now_text(),
                "combat_type": combat_type,
                "defense_leader": defense_leader,
                "defense_name": defense_name,
                "counter_leader": counter_leader,
                "counter_units": repr(counter_units),
                "reason": reason,
            },
        )
    return f"Locked {counter_leader} into {defense_leader} and recalculated."


LOCK_FIELDNAMES = [
    "created_at", "combat_type", "defense_leader", "defense_name",
    "counter_leader", "counter_units", "reason",
]


def _rule_rows(path: str) -> list[dict]:
    """Every row of a rule file as plain dicts, or an empty list if unwritten."""
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size == 0:
        return []
    with file_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_rule_rows(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def counter_options_for_defense(combat_type: str, defense_leader: str) -> list[dict]:
    """Counter teams that could legally be assigned to one enemy defense.

    Runs the same filters the optimizer uses (roster ownership, rejections,
    reserved units, win/seen thresholds), so anything returned here is a pick
    the strategy build will actually honour. Each option is annotated with the
    defense currently using it, which is what makes a reassignment visible
    before the user commits to it.
    """
    combat_type = str(combat_type).strip()
    defense_leader = str(defense_leader).strip()

    defense_df, counters_df, roster_df = load_csvs()
    defense_df, _ = dedupe_defenses_by_leader(defense_df)
    if defense_df.empty:
        return []

    matches = defense_df[
        (defense_df["combat_type"].astype(str) == combat_type)
        & (defense_df["leader"].astype(str) == defense_leader)
    ]
    if matches.empty:
        return []

    roster_set, roster_by_unit = build_roster_set(roster_df)
    warnings = defaultdict(list)
    valid_counters = find_valid_counters_for_defense(
        matches.iloc[0],
        counters_df,
        roster_set,
        roster_by_unit,
        warnings,
        rejected_counters=load_rejected_counter_signatures(),
        reserved_units=load_reserved_units(),
        exempt_leaders=load_leader_exemptions(),
        offense_team_locks=load_offense_team_lock_signatures(
            gac_format=load_pipeline_settings()["gac_format"]
        ),
    )

    strategy_df, _ = rebuild_strategy()
    used_by = {}
    for record in strategy_df.to_dict("records"):
        counter_leader = str(record.get("chosen_counter_leader", "")).strip()
        if not counter_leader:
            continue
        key = (counter_leader, tuple(parse_unit_list(record.get("chosen_counter_units", []))))
        used_by[key] = str(record.get("defense_leader", ""))

    locked = load_locked_matchup_signatures()

    options = []
    for counter in valid_counters:
        counter_leader = str(counter.get("counter_leader", ""))
        units = parse_unit_list(counter.get("counter_units", []))
        assigned_to = used_by.get((counter_leader, tuple(units)), "")
        options.append({
            "counter_leader": counter_leader,
            "counter_units": units,
            "counter_units_repr": repr(units),
            "win_percent": counter.get("win_percent", ""),
            "seen": counter.get("seen", ""),
            "score": counter.get("score", ""),
            "assigned_to": assigned_to,
            "is_current": assigned_to == defense_leader,
            "is_locked": counter_signature(combat_type, defense_leader, counter_leader, units)
            in locked,
        })
    return options


def _defense_using_counter(
    combat_type: str, counter_leader: str, units_key: tuple[str, ...]
) -> str:
    """Which defense the current plan spends this exact counter team on."""
    strategy_df, _ = rebuild_strategy()
    for record in strategy_df.to_dict("records"):
        if str(record.get("combat_type", "")).strip() != combat_type:
            continue
        if str(record.get("chosen_counter_leader", "")).strip() != counter_leader:
            continue
        if tuple(parse_unit_list(record.get("chosen_counter_units", []))) == units_key:
            return str(record.get("defense_leader", ""))
    return ""


def _defense_has_counter(defense_leader: str) -> bool:
    strategy_df, _ = rebuild_strategy()
    for record in strategy_df.to_dict("records"):
        if str(record.get("defense_leader", "")).strip() == defense_leader:
            return bool(str(record.get("chosen_counter_leader", "")).strip())
    return False


def assign_counter(
    combat_type: str,
    defense_leader: str,
    defense_name: str,
    counter_leader: str,
    counter_units_raw: str,
    reason: str = "",
) -> str:
    """Pin a counter to a defense, taking the team off whatever else held it.

    Manual assignment is a locked matchup, so it reuses that file. Three stale
    rules are cleared first: an earlier manual pick for this defense, a lock
    holding this team against a *different* defense (the reassignment), and any
    rejection of this pairing, which would otherwise filter the pick straight
    back out.
    """
    counter_units = parse_unit_list(counter_units_raw)
    counter_leader = str(counter_leader).strip()
    if not counter_leader or not counter_units:
        return "No assignment made. Pick a counter team first."

    units_key = tuple(counter_units)
    previous_holder = _defense_using_counter(combat_type, counter_leader, units_key)
    kept_locks = []
    for row in _rule_rows(LOCKED_MATCHUPS_FILE):
        row_combat = str(row.get("combat_type", "")).strip()
        row_defense = str(row.get("defense_leader", "")).strip()
        row_counter = str(row.get("counter_leader", "")).strip()
        row_units = tuple(parse_unit_list(row.get("counter_units", "")))

        if row_combat == combat_type and row_defense == defense_leader:
            continue
        if row_combat == combat_type and (row_counter, row_units) == (counter_leader, units_key):
            continue
        kept_locks.append(row)

    kept_locks.append({
        "created_at": now_text(),
        "combat_type": combat_type,
        "defense_leader": defense_leader,
        "defense_name": defense_name,
        "counter_leader": counter_leader,
        "counter_units": repr(counter_units),
        "reason": reason or "Manual assignment",
    })
    _write_rule_rows(LOCKED_MATCHUPS_FILE, LOCK_FIELDNAMES, kept_locks)

    rejections = _rule_rows(REJECTIONS_FILE)
    kept_rejections = [
        row for row in rejections
        if not (
            str(row.get("combat_type", "")).strip() == combat_type
            and str(row.get("defense_leader", "")).strip() == defense_leader
            and str(row.get("counter_leader", "")).strip() == counter_leader
            and tuple(parse_unit_list(row.get("counter_units", ""))) == units_key
        )
    ]
    unrejected = len(kept_rejections) < len(rejections)
    if unrejected:
        _write_rule_rows(
            REJECTIONS_FILE,
            ["created_at", "combat_type", "defense_leader", "defense_name",
             "counter_leader", "counter_units", "reason"],
            kept_rejections,
        )

    message = f"Assigned {counter_leader} to {defense_leader}"
    if previous_holder and previous_holder != defense_leader:
        message += f", taken off the {previous_holder} defense"
    if unrejected:
        message += "; cleared an earlier rejection of this pairing"
    message += " and recalculated."

    if previous_holder and previous_holder != defense_leader:
        rebuild_strategy(force=True)
        if not _defense_has_counter(previous_holder):
            message += f" Warning: the {previous_holder} defense has no counter left."
    return message


def reserve_unit(unit: str, reason: str) -> str:
    unit = (unit or "").strip()
    if unit and unit not in load_reserved_units():
        _append_csv_row(
            RESERVED_UNITS_FILE,
            ["created_at", "unit", "reason"],
            {"created_at": now_text(), "unit": unit, "reason": reason},
        )
    return f"Reserved counters using {unit} and recalculated."


def exempt_leader(leader: str, reason: str) -> str:
    """Let a leader's teams through even when support units are underbuilt."""
    leader = (leader or "").strip()
    if not leader:
        return "No leader exempted. Provide a base ID."
    if leader not in load_leader_exemptions():
        _append_csv_row(
            LEADER_EXEMPTIONS_FILE,
            ["created_at", "leader", "reason"],
            {"created_at": now_text(), "leader": leader, "reason": reason},
        )
    return f"Exempted {leader}: teams they lead now ignore the relic minimum on support units."


def lock_offense_team(
    leader: str,
    team_units_raw: str,
    source_defense_leader: str,
    source_defense_name: str,
    reason: str,
    gac_format: str,
) -> str:
    team_units = parse_unit_list(team_units_raw)
    if not leader and team_units:
        leader = team_units[0]
    if not leader or not team_units:
        return "No offense-only team added. Provide a leader and units."

    lock_file = _offense_locks_file(gac_format)
    key = (leader, tuple(team_units))
    if key not in _load_offense_team_lock_keys(gac_format):
        _append_csv_row(
            lock_file,
            ["created_at", "leader", "team_units", "source_defense_leader",
             "source_defense_name", "reason"],
            {
                "created_at": now_text(),
                "leader": leader,
                "team_units": repr(team_units),
                "source_defense_leader": source_defense_leader or "manual",
                "source_defense_name": source_defense_name or "Manual entry",
                "reason": reason or "Always-offense team",
            },
        )
        rebuild_defense_plan(force=True)
    return f"Locked {leader} as an always-offense team in {lock_file}."


def remove_rule(rule_key: str, row_index: int) -> str:
    path = REMOVABLE_RULE_FILES.get(rule_key)
    if not path or not Path(path).exists():
        return "No rule removed."
    rules_df = pd.read_csv(path)
    if row_index not in rules_df.index:
        return "No rule removed."
    rules_df = rules_df.drop(index=row_index).reset_index(drop=True)
    rules_df.to_csv(path, index=False)
    if rule_key.startswith("offense_team_locks"):
        rebuild_defense_plan(force=True)
    return f"Removed saved rule from {path} and recalculated."


# --- read models for rendering ---------------------------------------------


def load_rule_rows(rule_key: str) -> list[dict]:
    path = REMOVABLE_RULE_FILES.get(rule_key)
    if not path or not Path(path).exists():
        return []
    df = load_csv(path)
    if df.empty:
        return []
    rows = []
    for index, row in df.iterrows():
        record = {k: ("" if pd.isna(v) else v) for k, v in row.to_dict().items()}
        record["_row_index"] = int(index)
        rows.append(record)
    return rows


def bootstrap() -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
