# Bonus Token Arbitrage Finder

A Python terminal tool that finds **profit-boost token** arbitrage opportunities for **NHL, NFL, MLB, and NBA** on FanDuel and DraftKings, using [The Odds API](https://the-odds-api.com/) and [Polymarket](https://polymarket.com/). It supports **1-3 leg parlays** and sizes sequential hedges that lock in profit no matter how the legs resolve.

You pick a league, the number of legs, your token's sportsbook, the boost percentage, and your max stake. The tool then:

1. Pulls FanDuel + DraftKings odds for the league
2. **Automatically** pulls Polymarket hedge odds for every matched game (no manual slugs)
3. Finds the best profit-boost parlay (1-3 legs) and sizes sequential hedges that lock in profit
4. Prints a terminal report with the bet and hedge instructions

## Features

- **Scan-the-board flow**: a single streamlined run finds the best opportunity automatically
- **1-3 leg parlays** via the leg-count prompt / `--legs` flag (most profit-boost tokens require 3 legs)
- **Profit-boost tokens only** (the most common bonus-token type)
- **Polymarket always auto-pulled**: events are auto-discovered by league and matched to sportsbook games by team names and start time, then moneyline / spreads / totals hedge odds are attached
- Scans all guaranteed markets (`h2h`, `spreads`, `totals`) by default; spreads/totals use exact opposite-line hedges
- Hedges use the best available odds across sportsbooks and Polymarket
- API credit tracking and JSON response caching
- `--dry-run` mode with bundled sample data for all four leagues

## How parlay hedging works

For an N-leg parlay, the tool sizes one hedge per leg, placed **sequentially**: each next hedge is only placed if the parlay is still alive (the previous legs won). The solver (`bonusarb/arb/sequential.py`) sizes the N hedge stakes so all N+1 resolution paths — each leg being the first loser, plus the all-win path — yield the same locked profit, which it verifies within $0.05. `bonusarb/schedule.py` enforces that legs resolve one after another with a gap, so same-time games are excluded automatically.

Caveats:

- The guarantee assumes later legs' hedge odds hold until you place them. Same-day legs are low-risk; multi-day legs carry odds-movement risk.
- Each leg adds vig, so the boost must overcome compounded vig. Three legs is usually the sweet spot — more isn't always better (irrelevant here since we cap at 3).
- Worst-case capital = token stake + all N hedge stakes (reported as `max_cash_needed`). No bankroll cap is applied by default.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

On Windows, use the project virtual environment (`.venv`) so dependencies install cleanly.

Add your Odds API key to `.env`:

```env
ODDS_API_KEY=your_api_key_here
```

## Usage

Guided mode (default) — five prompts: league, number of legs, token book, boost %, max stake:

```bash
python -m bonusarb
```

You can pre-fill any setting with flags; only the remaining fields will be prompted:

```bash
python -m bonusarb --sport basketball_nba --legs 3 --token-book fanduel
```

Fully non-interactive mode (all required flags must be provided):

```bash
python -m bonusarb --batch --sport basketball_nba --token-book fanduel \
  --legs 3 --boost 0.30 --max-stake 25
```

Dry-run with bundled sample data (no API credits):

```bash
python -m bonusarb --batch --dry-run --sport basketball_nba --token-book fanduel \
  --legs 3 --boost 0.50 --max-stake 25
```

Useful flags:

- `--sport` (one of `icehockey_nhl`, `americanfootball_nfl`, `baseball_mlb`, `basketball_nba`)
- `--legs` (1-3, default 3 — the number of parlay legs)
- `--token-book` (`fanduel` or `draftkings`)
- `--boost` (profit boost percentage as a decimal, e.g. `0.30`)
- `--max-stake`
- `--bankroll` (optional hedge bankroll cap; unlimited if omitted)
- `--min-gap-minutes`
- `--market` (single or comma-separated: `h2h`, `spreads`, `totals`; default is all)
- `--batch` (fully non-interactive)
- `--interactive` (force prompts even when all flags are set)
- `--no-fetch` (cache only)
- `--dry-run`
- `--recheck` (force refresh odds before solving)
- `--save` (save the top plan to `history/`)

## Tests

```bash
pytest
```

## Notes

- This tool is informational only. It does not place bets.
- Free-tier Odds API access does not include player props.
- Spreads and totals can create line mismatch or push risk; the optimizer restricts them to exact opposite-line hedges and excludes whole-number lines.
- Polymarket is hedge-only and read-only. Events are auto-discovered per league (supported tag ids are in `SPORT_TAG_IDS` in `bonusarb/polymarket/discovery.py`). Auto-discovered mappings are **unverified** and raise a "verify settlement" warning — always confirm the Polymarket market settles exactly like your sportsbook bet. Polymarket prices are top-of-book and may have limited liquidity.
- Verify token eligibility, odds movement, and local betting rules before placing any wagers.
