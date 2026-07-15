import argparse
import asyncio

from .make_strategy import (
    build_roster_set,
    choose_strategy,
    dedupe_defenses_by_leader,
    load_locked_matchup_signatures,
    load_offense_team_lock_signatures,
    load_rejected_counter_signatures,
    load_reserved_units,
    print_strategy,
    save_strategy,
)
from .plan_my_defense import run_my_defense_planner
from .project_paths import csv_path, ensure_data_dirs, migrate_legacy_csvs
from .scrape_counters import (
    add_leader_repeat_counts,
    build_counter_urls,
    print_repeat_summary,
    scrape_all_counters,
)
from .scrape_defense import scrape_defense
from .scrape_roster import scrape_roster

DEFENSE_FILE = csv_path("defense_teams.csv")
COUNTERS_FILE = csv_path("counter_results.csv")
MY_ROSTER_FILE = csv_path("roster_units.csv")
ENEMY_ROSTER_FILE = csv_path("enemy_roster_units.csv")
DEFAULT_MY_PLAYER_ID = "848865876"
DEFAULT_ENEMY_PLAYER_ID = "946418649"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape defenses, counters, rosters, and build a SWGOH strategy plan."
    )
    parser.add_argument(
        "--my-player-id",
        default=DEFAULT_MY_PLAYER_ID,
        help=(
            "Your SWGOH.GG player ID. "
            f"Defaults to {DEFAULT_MY_PLAYER_ID}."
        ),
    )
    parser.add_argument(
        "--enemy-player-id",
        default=DEFAULT_ENEMY_PLAYER_ID,
        help=(
            "Enemy SWGOH.GG player ID. Their GAC history is used for defenses. "
            f"Defaults to {DEFAULT_ENEMY_PLAYER_ID}."
        ),
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=3,
        help="Number of matching enemy GAC history matches to scrape.",
    )
    parser.add_argument(
        "--gac-format",
        choices=["all", "3v3", "5v5"],
        default="all",
        help=(
            "Which GAC format to scrape. If set to 3v3 or 5v5, the scraper "
            "skips intervening matches of the other format until it finds "
            "history-limit matching matches."
        ),
    )
    parser.add_argument(
        "--debug-roster",
        action="store_true",
        help="Print detailed roster page debug output while scraping rosters.",
    )

    return parser.parse_args()


async def run_pipeline(args: argparse.Namespace) -> None:
    migrate_legacy_csvs()
    ensure_data_dirs()
    enemy_history_url = f"https://swgoh.gg/p/{args.enemy_player_id}/gac-history/"

    print("\n=== 1. Scraping enemy defenses ===")
    defense_df = await scrape_defense(
        enemy_history_url,
        player_id=args.enemy_player_id,
        history_limit=args.history_limit,
        gac_format=args.gac_format,
    )
    defense_df.to_csv(DEFENSE_FILE, index=False)
    print(f"Saved {len(defense_df)} defense rows to {DEFENSE_FILE}")

    print("\n=== 2. Scraping counters ===")
    character_urls, ship_urls, repeat_counts = build_counter_urls(
        defense_df,
        gac_format=args.gac_format,
    )
    print_repeat_summary(repeat_counts)

    counter_df = await scrape_all_counters(
        character_urls=character_urls,
        ship_urls=ship_urls,
    )
    counter_df = add_leader_repeat_counts(counter_df, repeat_counts)
    counter_df.to_csv(COUNTERS_FILE, index=False)
    print(f"Saved {len(counter_df)} counter rows to {COUNTERS_FILE}")

    print("\n=== 3. Scraping your roster ===")
    my_roster_df = await scrape_roster(
        player_id=args.my_player_id,
        debug=args.debug_roster,
    )
    my_roster_df.to_csv(MY_ROSTER_FILE, index=False)
    print(f"Saved {len(my_roster_df)} roster rows to {MY_ROSTER_FILE}")

    print("\n=== 4. Scraping enemy roster ===")
    enemy_roster_df = await scrape_roster(
        player_id=args.enemy_player_id,
        debug=args.debug_roster,
    )
    enemy_roster_df.to_csv(ENEMY_ROSTER_FILE, index=False)
    print(f"Saved {len(enemy_roster_df)} enemy roster rows to {ENEMY_ROSTER_FILE}")

    print("\n=== 5. Building strategy plan ===")
    strategy_defense_df, duplicate_warnings = dedupe_defenses_by_leader(defense_df)
    roster_set, roster_by_unit = build_roster_set(my_roster_df)
    strategy_df, warnings = choose_strategy(
        strategy_defense_df,
        counter_df,
        roster_set,
        roster_by_unit,
        rejected_counters=load_rejected_counter_signatures(),
        reserved_units=load_reserved_units(),
        locked_matchups=load_locked_matchup_signatures(),
        offense_team_locks=load_offense_team_lock_signatures(gac_format=args.gac_format),
    )
    warnings["duplicate_defenses"].extend(duplicate_warnings)

    save_strategy(strategy_df)
    print_strategy(strategy_df, warnings)

    print("\n=== 6. Planning your defensive teams from unused roster ===")
    run_my_defense_planner(
        my_roster_df,
        strategy_df,
        defense_df,
        counter_df,
        gac_format=args.gac_format,
    )


def main() -> None:
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
