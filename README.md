# Bonus Token Arbitrage Finder

A Python terminal tool that finds **profit-boost token** arbitrage opportunities for **NHL, NFL, MLB, NBA, WNBA, and UFC** on FanDuel, DraftKings, BetMGM, and theScore Bet, using [The Odds API](https://the-odds-api.com/) and [Polymarket](https://polymarket.com/). It supports **1-4 leg parlays** and sizes sequential hedges that lock in profit no matter how the legs resolve.

You pick a league, the number of legs, your token's sportsbook, the boost percentage, and your max stake. The tool then:

1. Pulls FanDuel + DraftKings + BetMGM + theScore Bet odds for the league (or fight card, for UFC)
2. **Automatically** pulls Polymarket hedge odds for every matched game (no manual slugs)
3. Finds the best profit-boost parlay (1-4 legs) and sizes sequential hedges that lock in profit
4. Prints a terminal report with the bet and hedge instructions

## Features

- **Scan-the-board flow**: a single streamlined run finds the best opportunity automatically
- **1-4 leg parlays** via the leg-count prompt / `--legs` flag (most profit-boost tokens require 3 legs)
- **Profit-boost tokens only** (the most common bonus-token type)
- **Polymarket always auto-pulled**: events are auto-discovered by league and matched to sportsbook games by team/player names and start time, then moneyline / spreads / totals hedge odds are attached
- Scans all guaranteed markets (`h2h`, `spreads`, `totals`) by default; spreads/totals use exact opposite-line hedges (UFC is moneyline/`h2h` only — it has no spreads/totals markets on the covered books)
- Hedges use the best available odds across sportsbooks and Polymarket
- API credit tracking and JSON response caching
- `--dry-run` mode with bundled sample data for all six leagues

## How parlay hedging works

For an N-leg parlay, the tool sizes one hedge per leg, placed **sequentially**: each next hedge is only placed if the parlay is still alive (the previous legs won). The solver (`bonusarb/arb/sequential.py`) sizes the N hedge stakes so all N+1 resolution paths — each leg being the first loser, plus the all-win path — yield the same locked profit, which it verifies within $0.05. `bonusarb/schedule.py` enforces that legs resolve one after another with a gap, so same-time games are excluded automatically.

Caveats:

- The guarantee assumes later legs' hedge odds hold until you place them. Same-day legs are low-risk; multi-day legs carry odds-movement risk.
- Each leg adds vig, so the boost must overcome compounded vig. Three legs is usually the sweet spot — more isn't always better (we cap at 4).
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

- `--sport` (one of `icehockey_nhl`, `americanfootball_nfl`, `baseball_mlb`, `basketball_nba`, `basketball_wnba`, `mma_mixed_martial_arts`)
- `--legs` (1-4, default 3 — the number of parlay legs)
- `--token-book` (`fanduel`, `draftkings`, `betmgm`, or `espnbet` / `thescore` for theScore Bet)
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
- `--usd` (treat `--max-stake` / `--bankroll` as USD; skip FX conversion)
- `--fx-rate` (manual CAD-per-USD rate; skips live fetch; stakes default to CAD)

## Tests

```bash
pytest
```

## Auto Polymarket hedging (`python -m bonusarb auto`)

The default command finds parlays and prints hedge instructions you place by
hand. The `auto` subcommand goes one step further: after you pick a parlay and
place it on the sportsbook, it **places the sequential Polymarket hedges for
you**, live-tracks each leg's resolution, and places the next hedge only when
the previous leg has won — so you can walk away.

```bash
python -m bonusarb auto --sport basketball_nba --legs 3 --token-book fanduel --boost 0.30 --max-stake 25
```

It reuses the same scan as the default command, then keeps only parlays whose
**every hedge is on Polymarket** (the only venue it can auto-trade). You pick a
plan, confirm you've placed the sportsbook bet by hand, and the monitor loop
takes over.

### Safety model

- **Paper mode is the default.** It simulates fills at the live price and never
  touches your wallet or the CLOB. Pass `--live` to arm real order placement.
- **Locked-profit floor.** Before placing each hedge it re-prices at the live
  CLOB quote and re-sizes the remaining hedges; if the recomputed locked profit
  falls below `AUTO_MIN_LOCKED_PROFIT_FLOOR` it **pauses and alerts** rather
  than placing a bad hedge.
- **Max slippage.** Live orders are marketable limit orders at
  `live_price * (1 + AUTO_MAX_SLIPPAGE)` (FAK/IOC), so you never chase the book.
  Partial fills pause and alert.
- **Never double-places.** State is persisted to `state/parlay_*.json` after
  every transition with a per-leg order-id ledger, so a crash or Ctrl+C can be
  resumed with `auto --resume` without re-placing anything.
- **Multiple parlays at once.** Nothing ties the monitor loop to a single
  parlay globally — run `auto` again in another terminal to hedge a second
  (or third) parlay in parallel. Use `auto --list` to see every in-progress
  parlay's id, and `auto --resume <PARLAY_ID>` to resume a specific one if a
  terminal closes (plain `--resume` resumes only the most recently created
  parlay).
- **Telegram alerts** on every placement, fill, resolution, completion, and
  pause/error (configure `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`).

### Resolution tracking

It watches each leg by polling the **hedge token's** live CLOB price with
**asymmetric thresholds** (defaults: `AUTO_LOST_THRESHOLD=0.97`,
`AUTO_WON_THRESHOLD=0.99`):

- Hedge price **>= 0.97** → leg **lost** (parlay dead, stop)
- Hedge price **<= 0.01** → leg **won** (parlay alive, place next hedge)

The won side requires higher certainty before placing the next hedge (reversal
risk is costly); the lost side can act sooner (a false stop only misses profit).
This fires **before** official Polymarket settlement so the next hedge is placed
in time when the next leg is already underway.

### First-run setup

1. Add wallet + Telegram credentials to `.env` (see `.env.example`):
   - `POLYMARKET_PRIVATE_KEY` — your browser wallet's private key
   - `POLYMARKET_FUNDER_ADDRESS` — your Polymarket proxy / Safe address
   - `POLYMARKET_SIGNATURE_TYPE` — `2` for Gnosis Safe (browser deposits),
     `1` for POLY_PROXY
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
2. Verify connectivity and the signature/funder setup:
   ```bash
   python -m bonusarb auto --check
   ```
   Confirm the printed signer/funder match your Polymarket deposit and that the
   USDC balance covers your planned hedges. If the check fails with a signature
   error, flip `POLYMARKET_SIGNATURE_TYPE` between 1 and 2 and retry.
3. Run a paper dry-run end-to-end, then `--live` once you trust it.

### Auto-specific flags

- `--live` / `--paper` (paper is default)
- `--resume [PARLAY_ID]` (resume an in-progress parlay; with no id, resumes
  the most recent one in `state/`)
- `--list` (list in-progress parlays and their ids, then exit)
- `--check` (verify wallet + signature setup, then exit)
- `--profit-floor`, `--slippage`, `--poll-interval`, `--won-threshold`,
  `--lost-threshold` (override the `AUTO_*` env defaults)
- `--usd`, `--fx-rate` (stakes default to CAD; Polymarket hedges sized in USD/USDC)

## Notes

- The default `python -m bonusarb` command remains informational only and does not place bets. The `auto` subcommand places real Polymarket orders when run with `--live`.
- Free-tier Odds API access does not include player props.
- Spreads and totals can create line mismatch or push risk; the optimizer restricts them to exact opposite-line hedges and excludes whole-number lines.
- Polymarket is hedge-only in the scan. Auto-hedging only places orders on Polymarket, so `auto` surfaces only parlays whose every hedge is on Polymarket. Auto-discovered mappings are **unverified** — always confirm the Polymarket market settles exactly like your sportsbook bet. Polymarket hedge odds are **post–sports-taker-fee** (top-of-book) and may have limited liquidity.
- Polymarket markets with less than `POLYMARKET_MIN_VOLUME_USD` (default $10,000) in all-time trading volume are excluded as hedges, checked per-market (moneyline, and each individual spread/total line) rather than per-event, since a game's moneyline can be liquid while its spread/totals lines are not. This filters out markets like a $160-volume moneyline that looks attractive on paper but can't absorb a real hedge stake. Lower this via `.env` if you want to allow thinner markets (at your own risk); a scan warning reports how many markets were skipped.
- Games that have already started (`commence_time` in the past) are automatically excluded from every scan. This avoids pairing stale pre-game sportsbook odds with live Polymarket prices for in-progress games.
- Verify token eligibility, odds movement, and local betting rules before placing any wagers.
- **Ontario / Canada:** [The Odds API](https://the-odds-api.com/) has no Ontario region. This tool requests `fanduel`, `draftkings`, `betmgm`, and `espnbet` (theScore Bet) by bookmaker key. theScore Bet lives in the API's `us2` region but is still fetched in the same request. Lines may differ slightly from what you see in Ontario apps — always confirm odds before betting.
