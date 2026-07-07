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


def _tag_events_sample(*events: dict) -> dict:
    return {"tag_events:100381": list(events)}


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


def test_sport_tag_ids_cover_major_us_sports():
    assert SPORT_TAG_IDS["baseball_mlb"] == 100381
    assert "basketball_nba" in SPORT_TAG_IDS


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
