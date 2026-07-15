from pathlib import Path

TEAM_LISTS_DIR = Path("team_lists")
ACTIVE_RUN_DIR = Path("active_run")

# Persistent Chrome profile shared by every scraper session. Reusing one profile
# keeps the Cloudflare `cf_clearance` cookie between runs, so the challenge only
# has to be solved once (until the cookie expires) instead of every session.
BROWSER_PROFILE_DIR = Path(".browser_profiles") / "swgoh_scraper"

TEAM_LIST_FILES = {
    "defense_team_library.csv",
    "defense_team_library_3v3.csv",
    "offense_team_locks.csv",
    "offense_team_locks_3v3.csv",
    "strategy_rejections.csv",
    "reserved_units.csv",
    "locked_matchups.csv",
}

ACTIVE_RUN_FILES = {
    "defense_teams.csv",
    "counter_results.csv",
    "roster_units.csv",
    "enemy_roster_units.csv",
    "strategy_plan.csv",
    "my_defense_options.csv",
    "my_defense_plan.csv",
}


def csv_path(filename: str) -> str:
    if filename in TEAM_LIST_FILES:
        return str(TEAM_LISTS_DIR / filename)

    if filename in ACTIVE_RUN_FILES:
        return str(ACTIVE_RUN_DIR / filename)

    return filename


def ensure_data_dirs() -> None:
    TEAM_LISTS_DIR.mkdir(exist_ok=True)
    ACTIVE_RUN_DIR.mkdir(exist_ok=True)


def browser_profile_dir() -> str:
    """Absolute path to the shared, persistent Chrome profile (created if needed)."""
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return str(BROWSER_PROFILE_DIR.resolve())


def migrate_legacy_csvs() -> None:
    ensure_data_dirs()

    for filename in sorted(TEAM_LIST_FILES | ACTIVE_RUN_FILES):
        old_path = Path(filename)
        new_path = Path(csv_path(filename))

        if old_path.exists() and not new_path.exists():
            old_path.replace(new_path)
