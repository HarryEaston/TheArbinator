"""Tests for Polymarket auto-discovery and MLB market parsing."""

from datetime import datetime, timedelta, timezone

from bonusarb.models import BookmakerOdds, Game, Market, Outcome
from bonusarb.polymarket.client import PolymarketClient
from bonusarb.polymarket.discovery import (
    SPORT_TAG_IDS,
    discover_event_mappings,
    list_discovered_events,
)


def _mlb_game(
    home_team: str = "Cincinnati Reds",
    away_team: str = "Baltimore Orioles",
    commence: str = "2026-07-03T23:10:00+00:00",
) -> Game:
    return Game(
        id=f"{away_team}-{home_team}",
        sport_key="baseball_mlb",
        sport_title="MLB",
        commence_time=datetime.fromisoformat(commence),
        home_team=home_team,
        away_team=away_team,
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(Outcome(away_team, 1.95), Outcome(home_team, 1.95)),
                    )
                },
            )
        },
    )


def _tag_events_sample(*events: dict, tag_id: int = 100381) -> dict:
    return {f"tag_events:{tag_id}": list(events)}


def _mlb_event(slug: str, away: str, home: str, start: str) -> dict:
    return {
        "slug": slug,
        "gameId": abs(hash(slug)) % 10_000_000,
        "startTime": start,
        "markets": [
            {
                "sportsMarketType": "moneyline",
                "outcomes": f'["{away}", "{home}"]',
                "clobTokenIds": '["a", "b"]',
                "outcomePrices": '["0.52", "0.48"]',
            }
        ],
    }


def _fighter_game(
    home_team: str = "Max Holloway",
    away_team: str = "Conor McGregor",
    commence: str = "2026-07-11T21:00:00+00:00",
) -> Game:
    return Game(
        id=f"{away_team}-{home_team}",
        sport_key="mma_mixed_martial_arts",
        sport_title="UFC",
        commence_time=datetime.fromisoformat(commence),
        home_team=home_team,
        away_team=away_team,
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(Outcome(away_team, 1.90), Outcome(home_team, 1.95)),
                    )
                },
            )
        },
    )


def _ufc_event(slug: str, fighter_a: str, fighter_b: str, start: str) -> dict:
    return {
        "slug": slug,
        "gameId": abs(hash(slug)) % 10_000_000,
        "startTime": start,
        "markets": [
            {
                "sportsMarketType": "moneyline",
                "outcomes": f'["{fighter_a}", "{fighter_b}"]',
                "clobTokenIds": '["a", "b"]',
                "outcomePrices": '["0.55", "0.45"]',
            }
        ],
    }


def _wnba_game(
    home_team: str = "Las Vegas Aces",
    away_team: str = "New York Liberty",
    commence: str = "2026-07-15T00:00:00+00:00",
) -> Game:
    return Game(
        id=f"{away_team}-{home_team}",
        sport_key="basketball_wnba",
        sport_title="WNBA",
        commence_time=datetime.fromisoformat(commence),
        home_team=home_team,
        away_team=away_team,
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(Outcome(away_team, 2.1), Outcome(home_team, 1.75)),
                    )
                },
            )
        },
    )


def _wnba_event(slug: str, away: str, home: str, start: str) -> dict:
    return {
        "slug": slug,
        "gameId": abs(hash(slug)) % 10_000_000,
        "startTime": start,
        "markets": [
            {
                "sportsMarketType": "moneyline",
                "outcomes": f'["{away}", "{home}"]',
                "clobTokenIds": '["a", "b"]',
                "outcomePrices": '["0.45", "0.55"]',
            }
        ],
    }


def test_sport_tag_ids_cover_major_us_sports():
    assert SPORT_TAG_IDS["baseball_mlb"] == 100381
    assert "basketball_nba" in SPORT_TAG_IDS
    assert SPORT_TAG_IDS["basketball_wnba"] == 100254
    assert SPORT_TAG_IDS["mma_mixed_martial_arts"] == 279


def test_discover_matches_wnba_game_by_team_names():
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _wnba_event(
                "wnba-liberty-aces-2026-07-15",
                "New York Liberty",
                "Las Vegas Aces",
                "2026-07-15T00:00:00Z",
            ),
            tag_id=100254,
        ),
    )
    mappings, warnings = discover_event_mappings(
        "basketball_wnba", [_wnba_game()], client, allow_fetch=False
    )

    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.polymarket_event_slug == "wnba-liberty-aces-2026-07-15"
    assert mapping.verified is False
    assert {mapping.home_team, mapping.away_team} == {"Las Vegas Aces", "New York Liberty"}
    assert any("Auto-matched" in w for w in warnings)


def test_discover_matches_when_polymarket_spelling_differs():
    # The Odds API spells the expansion team "Portland Fire"; Polymarket lists
    # it as "PortlandFire" (no space). Discovery must still pair them by
    # normalized name and record the Polymarket spelling on the outcome so the
    # merge step can relabel hedge odds back to the sportsbook name.
    game = Game(
        id="Las Vegas Aces-Portland Fire",
        sport_key="basketball_wnba",
        sport_title="WNBA",
        commence_time=datetime.fromisoformat("2026-07-10T02:00:00+00:00"),
        home_team="Portland Fire",
        away_team="Las Vegas Aces",
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(Outcome("Las Vegas Aces", 1.7), Outcome("Portland Fire", 2.2)),
                    )
                },
            )
        },
    )
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _wnba_event(
                "wnba-las-por-2026-07-09",
                "Las Vegas Aces",
                "PortlandFire",
                "2026-07-10T02:00:00Z",
            ),
            tag_id=100254,
        ),
    )
    mappings, _ = discover_event_mappings(
        "basketball_wnba", [game], client, allow_fetch=False
    )

    assert len(mappings) == 1
    mapping = mappings[0]
    assert {mapping.home_team, mapping.away_team} == {"Portland Fire", "Las Vegas Aces"}
    outcome_by_team = mapping.outcome_by_team
    assert outcome_by_team["Portland Fire"] == "PortlandFire"
    assert outcome_by_team["Las Vegas Aces"] == "Las Vegas Aces"


def test_discover_matches_ufc_fight_by_fighter_names():
    # UFC events use the same generic "moneyline" 2-way outcome list as team
    # sports; the two fighter names line up exactly like home/away teams do.
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _ufc_event(
                "ufc-max1-con-2026-07-11",
                "Max Holloway",
                "Conor McGregor",
                "2026-07-11T21:00:00Z",
            ),
            tag_id=279,
        ),
    )
    mappings, warnings = discover_event_mappings(
        "mma_mixed_martial_arts", [_fighter_game()], client, allow_fetch=False
    )

    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.polymarket_event_slug == "ufc-max1-con-2026-07-11"
    assert mapping.verified is False
    assert {mapping.home_team, mapping.away_team} == {"Max Holloway", "Conor McGregor"}
    assert any("Auto-matched" in w for w in warnings)


def test_discover_matches_game_by_team_and_time():
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _mlb_event(
                "mlb-bal-cin-2026-07-03",
                "Baltimore Orioles",
                "Cincinnati Reds",
                "2026-07-03T23:10:00Z",
            )
        ),
    )
    mappings, warnings = discover_event_mappings(
        "baseball_mlb", [_mlb_game()], client, allow_fetch=False
    )

    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.polymarket_event_slug == "mlb-bal-cin-2026-07-03"
    assert mapping.verified is False
    assert {mapping.home_team, mapping.away_team} == {"Cincinnati Reds", "Baltimore Orioles"}
    assert any("Auto-matched" in w for w in warnings)


def test_discover_disambiguates_rematch_by_closest_start_time():
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _mlb_event(
                "mlb-bal-cin-2026-07-10",
                "Baltimore Orioles",
                "Cincinnati Reds",
                "2026-07-10T23:10:00Z",
            ),
            _mlb_event(
                "mlb-bal-cin-2026-07-03",
                "Baltimore Orioles",
                "Cincinnati Reds",
                "2026-07-03T23:10:00Z",
            ),
        ),
    )
    mappings, _warnings = discover_event_mappings(
        "baseball_mlb", [_mlb_game()], client, allow_fetch=False
    )

    assert len(mappings) == 1
    assert mappings[0].polymarket_event_slug == "mlb-bal-cin-2026-07-03"


def test_discover_skips_when_start_time_outside_window():
    far = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _mlb_event("mlb-bal-cin-far", "Baltimore Orioles", "Cincinnati Reds", far)
        ),
    )
    mappings, _warnings = discover_event_mappings(
        "baseball_mlb", [_mlb_game()], client, allow_fetch=False
    )
    assert mappings == []


def test_discover_unknown_sport_returns_empty():
    client = PolymarketClient(dry_run=True, sample_markets={})
    mappings, warnings = discover_event_mappings(
        "cricket_test_matches", [_mlb_game()], client, allow_fetch=False
    )
    assert mappings == []
    assert warnings == []


def test_list_discovered_events_orders_away_first_for_us_sports():
    client = PolymarketClient(
        dry_run=True,
        sample_markets=_tag_events_sample(
            _mlb_event(
                "mlb-bal-cin-2026-07-03",
                "Baltimore Orioles",
                "Cincinnati Reds",
                "2026-07-03T23:10:00Z",
            )
        ),
    )
    events, _warnings = list_discovered_events("baseball_mlb", client, allow_fetch=False)
    assert len(events) == 1
    assert events[0].away_team == "Baltimore Orioles"
    assert events[0].home_team == "Cincinnati Reds"


def test_get_game_markets_parses_mlb_moneyline_as_h2h():
    samples = {
        "event:mlb-bal-cin-2026-07-03": [
            {"slug": "mlb-bal-cin-2026-07-03", "gameId": 10078607, "closed": False}
        ],
        "game_events:10078607": [
            {
                "slug": "mlb-bal-cin-2026-07-03",
                "closed": False,
                "markets": [
                    {
                        "sportsMarketType": "moneyline",
                        "outcomes": '["Baltimore Orioles", "Cincinnati Reds"]',
                        "clobTokenIds": '["a", "b"]',
                        "outcomePrices": '["0.52", "0.48"]',
                    }
                ],
            }
        ],
        "price:a": {"price": "0.52"},
        "price:b": {"price": "0.48"},
    }
    client = PolymarketClient(dry_run=True, sample_markets=samples)

    parsed = client.get_game_markets(
        "mlb-bal-cin-2026-07-03",
        "Cincinnati Reds",
        "Baltimore Orioles",
        allow_fetch=False,
    )

    assert parsed is not None
    assert "h2h" in parsed.markets
    assert {o.name for o in parsed.markets["h2h"]} == {"Cincinnati Reds", "Baltimore Orioles"}
