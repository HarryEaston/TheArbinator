"""Tests for the Polymarket hedge integration."""

from datetime import datetime

from bonusarb.arb.optimizer import enumerate_candidate_legs, find_best_plans
from bonusarb.models import BookmakerOdds, Game, Market, Outcome, TokenConstraint, TokenType
from bonusarb.odds_utils import decimal_to_american
from bonusarb.polymarket.client import (
    PolymarketClient,
    PolymarketMarket,
    effective_taker_share_price,
    share_price_to_decimal_odds,
)
from bonusarb.polymarket.mappings import (
    EventMapping,
    OutcomeMapping,
    find_mapping,
)
from bonusarb.polymarket.merge import merge_polymarket_odds


def _polymarket_game_samples(
    *,
    home_price: str = "0.6",
    away_price: str = "0.4",
    include_spreads: bool = True,
    include_totals: bool = True,
) -> dict:
    markets = [
        {
            "closed": False,
            "sportsMarketType": "moneyline",
            "outcomes": '["Celtics", "Lakers"]',
            "clobTokenIds": '["token-ml-celtics", "token-ml-lakers"]',
            "outcomePrices": f'[{home_price}, {away_price}]',
            "lastUpdate": "2026-03-01T00:00:00Z",
        }
    ]
    samples = {
        "event:nba-bos-lal-2026-03-01": [
            {
                "slug": "nba-bos-lal-2026-03-01",
                "gameId": 71000001,
                "closed": False,
            }
        ],
        "game_events:71000001": [
            {
                "slug": "nba-bos-lal-2026-03-01-more-markets",
                "closed": False,
                "markets": markets,
            }
        ],
        "price:token-ml-celtics": {"price": home_price},
        "price:token-ml-lakers": {"price": away_price},
    }

    if include_spreads:
        markets.append(
            {
                "closed": False,
                "sportsMarketType": "spreads",
                "groupItemTitle": "Celtics (-4.5)",
                "line": -4.5,
                "outcomes": '["Celtics", "Lakers"]',
                "clobTokenIds": '["token-spread-celtics", "token-spread-lakers"]',
                "outcomePrices": '["0.52", "0.48"]',
                "lastUpdate": "2026-03-01T00:00:00Z",
            }
        )
        samples["price:token-spread-celtics"] = {"price": "0.52"}
        samples["price:token-spread-lakers"] = {"price": "0.48"}

    if include_totals:
        markets.append(
            {
                "closed": False,
                "sportsMarketType": "totals",
                "groupItemTitle": "O/U 222.5",
                "line": 222.5,
                "outcomes": '["Over", "Under"]',
                "clobTokenIds": '["token-total-over", "token-total-under"]',
                "outcomePrices": '["0.5", "0.5"]',
                "lastUpdate": "2026-03-01T00:00:00Z",
            }
        )
        samples["price:token-total-over"] = {"price": "0.5"}
        samples["price:token-total-under"] = {"price": "0.5"}

    return samples


def _nba_game(
    home_team: str = "Celtics",
    away_team: str = "Lakers",
    commence: str = "2026-03-01T23:00:00+00:00",
    fanduel_outcomes: tuple[Outcome, ...] | None = None,
    draftkings_outcomes: tuple[Outcome, ...] | None = None,
    fanduel_spreads: tuple[Outcome, ...] | None = None,
    draftkings_spreads: tuple[Outcome, ...] | None = None,
    fanduel_totals: tuple[Outcome, ...] | None = None,
    draftkings_totals: tuple[Outcome, ...] | None = None,
) -> Game:
    fanduel_markets = {
        "h2h": Market(
            key="h2h",
            outcomes=fanduel_outcomes
            or (
                Outcome("Lakers", 3.2),
                Outcome("Celtics", 1.45),
            ),
        )
    }
    draftkings_markets = {
        "h2h": Market(
            key="h2h",
            outcomes=draftkings_outcomes
            or (
                Outcome("Lakers", 3.1),
                Outcome("Celtics", 1.45),
            ),
        )
    }
    if fanduel_spreads is not None:
        fanduel_markets["spreads"] = Market(key="spreads", outcomes=fanduel_spreads)
    if draftkings_spreads is not None:
        draftkings_markets["spreads"] = Market(key="spreads", outcomes=draftkings_spreads)
    if fanduel_totals is not None:
        fanduel_markets["totals"] = Market(key="totals", outcomes=fanduel_totals)
    if draftkings_totals is not None:
        draftkings_markets["totals"] = Market(key="totals", outcomes=draftkings_totals)

    return Game(
        id="celtics-lakers",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime.fromisoformat(commence),
        home_team=home_team,
        away_team=away_team,
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets=fanduel_markets,
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets=draftkings_markets,
            ),
        },
    )


def _mapping(
    home_team: str = "Celtics",
    away_team: str = "Lakers",
    verified: bool = False,
) -> EventMapping:
    return EventMapping(
        sport_key="basketball_nba",
        home_team=home_team,
        away_team=away_team,
        polymarket_event_slug="nba-bos-lal-2026-03-01",
        settlement="Auto-discovered from Polymarket; verify settlement rules match your bet.",
        outcomes=(
            OutcomeMapping("Celtics", "Celtics"),
            OutcomeMapping("Lakers", "Lakers"),
        ),
        commence_time=datetime.fromisoformat("2026-03-01T23:00:00+00:00"),
        verified=verified,
    )


def test_effective_taker_share_price():
    assert round(effective_taker_share_price(0.0875), 6) == round(0.0875 * 1.045625, 6)
    assert round(share_price_to_decimal_odds(0.0875), 2) == 10.93
    assert decimal_to_american(share_price_to_decimal_odds(0.0875)) == 993


def test_share_price_to_decimal_odds():
    assert round(share_price_to_decimal_odds(0.25), 4) == round(1 / effective_taker_share_price(0.25), 4)
    assert round(share_price_to_decimal_odds(0.5), 4) == round(1 / effective_taker_share_price(0.5), 4)
    assert round(share_price_to_decimal_odds(0.8), 4) == round(1 / effective_taker_share_price(0.8), 4)
    assert share_price_to_decimal_odds(0.5, fee_rate=0.0) == 2.0


def test_share_price_rejects_out_of_range():
    import pytest

    with pytest.raises(ValueError):
        share_price_to_decimal_odds(0.0)
    with pytest.raises(ValueError):
        share_price_to_decimal_odds(1.0)
    with pytest.raises(ValueError):
        share_price_to_decimal_odds(1.5)
    with pytest.raises(ValueError):
        effective_taker_share_price(0.0)


def test_find_mapping_matches_by_teams_and_sport():
    mapping = _mapping()
    game = _nba_game()
    assert find_mapping(game, [mapping]) is mapping
    other = _nba_game(home_team="Bucks", away_team="Knicks")
    assert find_mapping(other, [mapping]) is None


def test_polymarket_client_parses_stringified_gamma_fields_and_clob_prices():
    sample_market = {
        "key": "celtics-vs-lakers",
        "closed": False,
        "outcomes": '["Celtics", "Lakers"]',
        "clobTokenIds": '["token-celtics", "token-lakers"]',
        "outcomePrices": '[0.65, 0.35]',
        "lastUpdate": "2026-03-01T00:00:00Z",
    }
    sample_prices = {
        "price:token-celtics": {"price": "0.65"},
        "price:token-lakers": {"price": "0.35"},
    }
    samples = {**sample_prices, "market:celtics-vs-lakers": [sample_market]}
    client = PolymarketClient(dry_run=True, sample_markets=samples)

    market = client.get_market(
        "celtics-vs-lakers",
        outcome_by_team={"Celtics": "Celtics", "Lakers": "Lakers"},
        allow_fetch=False,
    )

    assert market is not None
    assert round(market.decimal_odds_for("Celtics"), 4) == round(
        share_price_to_decimal_odds(0.65), 4
    )
    assert round(market.decimal_odds_for("Lakers"), 4) == round(
        share_price_to_decimal_odds(0.35), 4
    )


def test_get_game_markets_parses_moneyline_spreads_and_totals():
    client = PolymarketClient(dry_run=True, sample_markets=_polymarket_game_samples())

    parsed = client.get_game_markets(
        "nba-bos-lal-2026-03-01",
        "Celtics",
        "Lakers",
        allow_fetch=False,
    )

    assert parsed is not None
    assert "h2h" in parsed.markets
    assert {o.name for o in parsed.markets["h2h"]} == {"Celtics", "Lakers"}
    assert round(parsed.markets["h2h"][0].decimal_odds, 3) == round(
        share_price_to_decimal_odds(0.6), 3
    )

    assert "spreads" in parsed.markets
    spread_points = {(o.name, o.point) for o in parsed.markets["spreads"]}
    assert ("Celtics", -4.5) in spread_points
    assert ("Lakers", 4.5) in spread_points

    assert "totals" in parsed.markets
    total_points = {(o.name, o.point) for o in parsed.markets["totals"]}
    assert ("Over", 222.5) in total_points
    assert ("Under", 222.5) in total_points


def test_merge_attaches_polymarket_hedge_odds():
    game = _nba_game()
    client = PolymarketClient(dry_run=True, sample_markets=_polymarket_game_samples())

    merged, warnings = merge_polymarket_odds(
        [game], [_mapping()], client, allow_fetch=False
    )

    polymarket = merged[0].bookmakers["polymarket"]
    assert "h2h" in polymarket.markets
    assert "spreads" in polymarket.markets
    assert "totals" in polymarket.markets
    h2h = polymarket.markets["h2h"]
    assert {o.name for o in h2h.outcomes} == {"Celtics", "Lakers"}
    assert any("liquidity" in w.lower() for w in warnings)
    assert any("fee" in w.lower() for w in warnings)


def test_merge_uses_unverified_mapping_with_warning():
    game = _nba_game()
    client = PolymarketClient(dry_run=True, sample_markets=_polymarket_game_samples())

    merged, warnings = merge_polymarket_odds(
        [game], [_mapping(verified=False)], client, allow_fetch=False
    )

    assert "polymarket" in merged[0].bookmakers
    assert any("unverified" in w.lower() for w in warnings)


def test_optimizer_picks_polymarket_moneyline_when_it_offers_better_hedge_odds():
    game = _nba_game()
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_polymarket_game_samples(home_price="0.55", away_price="0.45"),
    )

    merged, _warnings = merge_polymarket_odds(
        [game], [_mapping()], client, allow_fetch=False
    )

    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.40,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        merged,
        token,
        leg_count=1,
        hedge_books=("draftkings", "polymarket"),
        bankroll=1000,
        market_key="h2h",
    )

    assert plans, "Expected a guaranteed plan using the Polymarket hedge."
    leg = plans[0].legs[0]
    assert leg.hedge_book == "polymarket"
    assert plans[0].is_guaranteed


def test_spread_leg_can_use_polymarket_hedge_at_matching_line():
    samples = _polymarket_game_samples()
    samples["price:token-spread-lakers"] = {"price": "0.25"}
    game = _nba_game(
        fanduel_spreads=(
            Outcome("Celtics", 1.85, -4.5),
            Outcome("Lakers", 2.05, 4.5),
        ),
        draftkings_spreads=(
            Outcome("Celtics", 1.8, -4.5),
            Outcome("Lakers", 2.1, 4.5),
        ),
    )
    client = PolymarketClient(dry_run=True, sample_markets=samples)
    merged, _warnings = merge_polymarket_odds(
        [game], [_mapping()], client, allow_fetch=False
    )

    candidates = enumerate_candidate_legs(
        merged, "fanduel", ("draftkings", "polymarket"), "spreads"
    )
    celtics_legs = [leg for leg in candidates if leg.selection == "Celtics"]
    assert celtics_legs
    assert celtics_legs[0].hedge_book == "polymarket"


def test_polymarket_market_decimal_odds_for_missing_team_returns_none():
    market = PolymarketMarket(
        slug="x",
        outcomes=(("Celtics", 1.3, "t1"), ("Lakers", 4.3, "t2")),
    )
    assert market.decimal_odds_for("Knicks") is None
    assert market.decimal_odds_for("Celtics") == 1.3
