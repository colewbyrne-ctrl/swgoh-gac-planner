import argparse
import asyncio

from scrape_defense import scrape_defense
from scrape_counters import (
    add_leader_repeat_counts,
    build_counter_urls,
    print_repeat_summary,
    scrape_all_counters,
)
from scrape_roster import scrape_roster
from make_strategy import (
    build_roster_set,
    choose_strategy,
    dedupe_defenses_by_leader,
    print_strategy,
    save_strategy,
)


DEFENSE_FILE = "defense_teams.csv"
COUNTERS_FILE = "counter_results.csv"
MY_ROSTER_FILE = "roster_units.csv"
ENEMY_ROSTER_FILE = "enemy_roster_units.csv"
DEFAULT_MY_PLAYER_ID = "848865876"
DEFAULT_ENEMY_PLAYER_ID = "721192678"


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
        help="Number of enemy GAC history links to inspect.",
    )
    parser.add_argument(
        "--debug-roster",
        action="store_true",
        help="Print detailed roster page debug output while scraping rosters.",
    )

    return parser.parse_args()


async def run_pipeline(args: argparse.Namespace) -> None:
    enemy_history_url = f"https://swgoh.gg/p/{args.enemy_player_id}/gac-history/"

    print("\n=== 1. Scraping enemy defenses ===")
    defense_df = await scrape_defense(
        enemy_history_url,
        player_id=args.enemy_player_id,
        history_limit=args.history_limit,
    )
    defense_df.to_csv(DEFENSE_FILE, index=False)
    print(f"Saved {len(defense_df)} defense rows to {DEFENSE_FILE}")

    print("\n=== 2. Scraping counters ===")
    character_urls, ship_urls, repeat_counts = build_counter_urls(defense_df)
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
    )
    warnings["duplicate_defenses"].extend(duplicate_warnings)

    save_strategy(strategy_df)
    print_strategy(strategy_df, warnings)


def main() -> None:
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
