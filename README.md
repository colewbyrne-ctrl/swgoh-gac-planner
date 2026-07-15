# SWGOH GAC Strategy Planner

A planning tool for Grand Arena Championships (GAC) in *Star Wars: Galaxy of
Heroes*. It scrapes an opponent's recent defensive teams and the community
counter statistics from [SWGOH.GG](https://swgoh.gg), then solves a
**constrained assignment problem** to produce an offensive attack plan that
never reuses a unit — and ranks the defensive teams you can still field from
what's left.

The interesting part is not the scraping; it's the optimizer.

---

## The core problem

GAC is a resource-allocation puzzle. You face a set of enemy defensive teams and
must assign one *counter* team to each. The catch:

- **A unit can only attack once per round.** If two of your counters both need
  the same character, you can't run both.
- **Each defense has a different set of viable counters**, with different win
  rates, sample sizes, and roster costs.
- **Some defenses are "hard"** — only one team in your roster can beat them —
  while others are flexible with many options.

This is a bipartite assignment with a hard disjointness constraint (no shared
units) and a soft objective (maximize coverage, then total quality). A greedy
"pick each defense's best counter" strategy fails badly: a flexible defense will
happily grab a contested unit that a *constrained* defense was the only user of,
leaving that hard defense uncounterable.

### How it's solved

`swgoh/make_strategy.py` runs a **beam search** over assignments:

1. **Candidate generation** — for each enemy defense, find every counter that
   clears a win-rate floor and minimum sample size, that the roster can actually
   field (owned, built up enough), and that isn't manually rejected or reserved.
   Win/sample thresholds relax progressively when no strong counter exists.
2. **Scoring** — each counter is scored on win rate, sample size
   (log-scaled), average banners, and a *roster cost* penalty so premium
   units are preserved for where they're actually needed. Counters that are
   valid for many defenses get a **flexibility penalty**, and scarce defenses
   get an **assignment bonus** — both nudges push the search toward covering
   hard defenses first.
3. **Search** — defenses are ordered hardest-first, then a beam search
   (`BEAM_SEARCH_WIDTH = 1500`) explores assignment states, pruning to the top
   states ranked by *(defenses covered, total score, fewest units spent)*. Hard
   constraints (locked matchups, reserved units) are enforced during expansion.

Beam search was chosen over the alternatives as a deliberate trade-off:

| Approach | Why not |
| --- | --- |
| **Greedy** | Fast but strands hard defenses (the failure above). |
| **Exact ILP / max-weight matching** | Optimal, but the "no shared unit across *teams* of varying size" constraint isn't a clean bipartite matching, and pulling in a solver dependency was overkill for the instance sizes here (tens of defenses). |
| **Beam search** | Near-optimal, dependency-free, and easy to bias with soft scores. Width 1500 covers realistic GAC boards comfortably. |

The same "assign without overlap" idea runs again in reverse for defense:
`swgoh/plan_my_defense.py` ranks the teams you can field from units *not* spent
on offense and greedily selects a non-overlapping defensive lineup.

---

## Architecture

```
swgoh/
  scrape_defense.py     Enemy GAC defense history  (pydoll/Chromium + BeautifulSoup)
  scrape_counters.py    Community counter stats per defense leader
  scrape_roster.py      Roster: stars, relics, zetas, GL/ultimate flags, ships
  make_strategy.py      Candidate generation, scoring, beam-search assignment
  plan_my_defense.py    Ranks fieldable defensive teams from unused roster
  pipeline.py           Orchestrates scrape -> plan end to end
  project_paths.py      CSV data-store layout (active_run/ and team_lists/)
  web/                  FastAPI review UI (see below)
tests/                  pytest suite for the optimizer
```

Data is stored as plain CSVs — no database. `active_run/` holds per-run scraped
data and generated plans; `team_lists/` holds decisions that persist across runs
(rejections, locks, reserved units). There's no hosted service; everything runs
locally.

### Stack

- **Python 3.11+**, `pandas` for data wrangling and scoring
- **pydoll / Chromium + BeautifulSoup** for browser-driven scraping (with
  Cloudflare-challenge detection)
- **FastAPI + Jinja2** for the local review UI
- **pytest / ruff / mypy** for tests, linting, and type checking

---

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt        # or: pip install -e ".[dev]"
```

The scraper drives Chromium through `pydoll`, so the first scrape takes longer
while browser automation initializes.

## Usage

Run the full pipeline for a matchup:

```powershell
python -m swgoh.pipeline --my-player-id 848865876 --enemy-player-id 721192678 --history-limit 3 --gac-format 5v5
```

| Argument | Meaning |
| --- | --- |
| `--my-player-id` | Your SWGOH.GG player ID |
| `--enemy-player-id` | Opponent's player ID (their GAC history is scraped) |
| `--history-limit` | How many recent matching GAC matches to scrape |
| `--gac-format` | `all`, `3v3`, or `5v5` (skips the other format when set) |
| `--debug-roster` | Extra roster-scraping detail |

Rebuild just the attack plan after editing rule files (rejections, locks):

```powershell
python -m swgoh.make_strategy
```

Rebuild just the defensive options:

```powershell
python -m swgoh.plan_my_defense
```

If installed with `pip install -e .`, the console scripts `swgoh-pipeline`,
`swgoh-strategy`, `swgoh-defense`, and `swgoh-web` are equivalent.

## Review UI

```powershell
swgoh-web                      # or: uvicorn swgoh.web.app:app --port 8787
```

Then open <http://127.0.0.1:8787/>. The UI lets you:

- Run the scrape-and-plan pipeline from the browser and watch its log.
- **Reject** a counter that's wrong for your roster and recalculate.
- **Lock** a matchup you trust so the optimizer must keep it.
- **Reserve** individual units so they're never spent on offense.
- **Lock a whole team as always-offense** so it's excluded from defense planning.
- Review ranked defensive candidates at `/defense`.

All of these persist to CSV rule files under `team_lists/` and trigger a
recalculation.

## Development

```powershell
pytest            # run the optimizer test suite
ruff check .      # lint
mypy swgoh        # type-check
```

The tests focus on the parts worth trusting: the beam search's no-reuse and
coverage guarantees, the scoring formulas, roster gating, and the messy-CSV
parser. They use small synthetic fixtures so the assignment behavior is exact
and readable.

## Notes and caveats

- SWGOH.GG page structure changes over time; if a scrape returns empty data,
  check the selectors first.
- Counter data is historical and can mislead for low-sample teams — the planner
  penalizes low sample size but doesn't ignore it.
- Characters below relic 3 are treated as underbuilt and excluded.
- Rule files are intentionally plain CSVs so they can be inspected or edited
  outside the UI.
