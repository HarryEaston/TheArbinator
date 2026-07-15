"""End-to-end optimizer tests using dry-run sample data."""

from datetime import datetime

from bonusarb.arb.optimizer import _candidate_stakes, find_best_plans
from bonusarb.models import BookmakerOdds, Game, Market, Outcome, TokenConstraint, TokenType
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.schedule import matchup_key


def test_candidate_stakes_never_exceed_fractional_max_stake():
    # CAD→USD can leave a fractional max_stake (e.g. 21.167). Rounding that up
    # to 21.17 would then fail token_constraints_satisfied and wipe every plan.
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="draftkings",
        min_legs=1,
        max_stake=21.167097299501727,
        boost_pct=0.50,
    )
    stakes = _candidate_stakes(token, bankroll=None)
    assert stakes
    assert all(stake <= token.max_stake for stake in stakes)
    assert stakes[0] == 21.16


def test_dry_run_finds_guaranteed_plan():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=1,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans
    assert plans[0].locked_profit > 0
    assert plans[0].is_guaranteed


def test_dry_run_finds_3leg_plan():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=3,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=3,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans
    plan = plans[0]
    assert len(plan.legs) == 3
    assert plan.locked_profit > 0
    assert plan.is_guaranteed
    # Each leg must come from a distinct matchup (teams + start time).
    matchup_keys = set()
    games_by_id = {game.id: game for game in games}
    for leg in plan.legs:
        game = games_by_id[leg.game_id]
        matchup_keys.add(matchup_key(game))
    assert len(matchup_keys) == 3
    # Every resolution path (N losers + the all-win path) must lock equal profit.
    assert plan.is_guaranteed


def test_min_legs_rejects_shorter_combo():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=3,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    # Asking for 2 legs while the token requires 3 must yield no plans.
    plans = find_best_plans(
        games,
        token,
        leg_count=2,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans == []


def _mlb_game(
    game_id: str,
    *,
    home: str,
    away: str,
    commence: str,
) -> Game:
    return Game(
        id=game_id,
        sport_key="baseball_mlb",
        sport_title="MLB",
        commence_time=datetime.fromisoformat(commence.replace("Z", "+00:00")),
        home_team=home,
        away_team=away,
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome(home, 2.0),
                            Outcome(away, 1.8),
                        ),
                    ),
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome(home, 1.91, 1.5),
                            Outcome(away, 1.93, -1.5),
                        ),
                    ),
                },
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome(home, 1.95),
                            Outcome(away, 1.85),
                        ),
                    ),
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome(home, 1.89, 1.5),
                            Outcome(away, 1.95, -1.5),
                        ),
                    ),
                },
            ),
        },
    )


def test_optimizer_rejects_same_matchup_twice():
    """Duplicate API events or mixed markets on one game cannot parlay together."""
    brewers_a = _mlb_game(
        "brewers-dup-a",
        home="St. Louis Cardinals",
        away="Milwaukee Brewers",
        commence="2026-07-07T23:00:00Z",
    )
    brewers_b = _mlb_game(
        "brewers-dup-b",
        home="St. Louis Cardinals",
        away="Milwaukee Brewers",
        commence="2026-07-07T23:00:00Z",
    )
    other = _mlb_game(
        "other-game",
        home="Chicago Cubs",
        away="New York Mets",
        commence="2026-07-08T02:00:00Z",
    )
    games = [brewers_a, brewers_b, other]
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=2,
        max_stake=25,
        boost_pct=0.30,
        sport_key="baseball_mlb",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=2,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
        market_key="h2h,spreads,totals",
    )
    games_by_id = {game.id: game for game in games}
    for plan in plans:
        seen: set = set()
        for leg in plan.legs:
            key = matchup_key(games_by_id[leg.game_id])
            assert key not in seen
            seen.add(key)
