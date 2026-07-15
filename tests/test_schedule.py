"""Tests for schedule feasibility."""

from datetime import datetime

from bonusarb.models import Game, Leg
from bonusarb.schedule import combo_has_unique_matchups, filter_upcoming_games, is_schedule_feasible, matchup_key


def _leg(game_id: str, commence: str) -> Leg:
    return Leg(
        game_id=game_id,
        event_label=game_id,
        commence_time=datetime.fromisoformat(commence.replace("Z", "+00:00")),
        selection="Team A",
        opposite_selection="Team B",
        token_book="fanduel",
        token_odds=2.0,
        hedge_book="draftkings",
        hedge_odds=2.0,
    )


def test_schedule_feasible_for_spaced_games():
    legs = (
        _leg("g1", "2026-07-03T00:00:00Z"),
        _leg("g2", "2026-07-03T05:00:00Z"),
    )
    ok, issues = is_schedule_feasible(legs, "basketball_nba", min_gap_minutes=30)
    assert ok
    assert issues == []


def test_schedule_rejects_overlapping_games():
    legs = (
        _leg("g1", "2026-07-03T00:00:00Z"),
        _leg("g2", "2026-07-03T01:00:00Z"),
    )
    ok, issues = is_schedule_feasible(legs, "basketball_nba", min_gap_minutes=30)
    assert not ok
    assert issues


def _brewers_cardinals_game(game_id: str) -> Game:
    commence = datetime.fromisoformat("2026-07-07T23:00:00+00:00")
    from bonusarb.models import BookmakerOdds, Market, Outcome

    bookmakers = {
        "fanduel": BookmakerOdds(
            key="fanduel",
            title="FanDuel",
            markets={
                "h2h": Market(
                    key="h2h",
                    outcomes=(
                        Outcome("St. Louis Cardinals", 2.1),
                        Outcome("Milwaukee Brewers", 1.77),
                    ),
                ),
                "spreads": Market(
                    key="spreads",
                    outcomes=(
                        Outcome("St. Louis Cardinals", 1.91, 1.5),
                        Outcome("Milwaukee Brewers", 1.93, -1.5),
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
                        Outcome("St. Louis Cardinals", 2.05),
                        Outcome("Milwaukee Brewers", 1.8),
                    ),
                ),
                "spreads": Market(
                    key="spreads",
                    outcomes=(
                        Outcome("St. Louis Cardinals", 1.89, 1.5),
                        Outcome("Milwaukee Brewers", 1.95, -1.5),
                    ),
                ),
            },
        ),
    }
    return Game(
        id=game_id,
        sport_key="baseball_mlb",
        sport_title="MLB",
        commence_time=commence,
        home_team="St. Louis Cardinals",
        away_team="Milwaukee Brewers",
        bookmakers=bookmakers,
    )


def test_matchup_key_ignores_home_away_order():
    game = _brewers_cardinals_game("g1")
    swapped = Game(
        id="g2",
        sport_key=game.sport_key,
        sport_title=game.sport_title,
        commence_time=game.commence_time,
        home_team=game.away_team,
        away_team=game.home_team,
        bookmakers=game.bookmakers,
    )
    assert matchup_key(game) == matchup_key(swapped)


def test_combo_has_unique_matchups_rejects_same_matchup():
    game_a = _brewers_cardinals_game("api-id-1")
    game_b = _brewers_cardinals_game("api-id-2")
    game_by_id = {game_a.id: game_a, game_b.id: game_b}
    legs = (
        _leg("api-id-1", "2026-07-07T23:00:00Z"),
        _leg("api-id-2", "2026-07-07T23:00:00Z"),
    )
    assert not combo_has_unique_matchups(legs, game_by_id)


def test_combo_has_unique_matchups_allows_doubleheader():
    game_a = _brewers_cardinals_game("dh-1")
    game_b = _brewers_cardinals_game("dh-2")
    game_b = Game(
        id=game_b.id,
        sport_key=game_b.sport_key,
        sport_title=game_b.sport_title,
        commence_time=datetime.fromisoformat("2026-07-08T23:00:00+00:00"),
        home_team=game_b.home_team,
        away_team=game_b.away_team,
        bookmakers=game_b.bookmakers,
    )
    game_by_id = {game_a.id: game_a, game_b.id: game_b}
    legs = (
        _leg("dh-1", "2026-07-07T23:00:00Z"),
        _leg("dh-2", "2026-07-08T23:00:00Z"),
    )
    assert combo_has_unique_matchups(legs, game_by_id)


def test_filter_upcoming_games_drops_started_games():
    started = _brewers_cardinals_game("started")
    future = Game(
        id="future",
        sport_key=started.sport_key,
        sport_title=started.sport_title,
        commence_time=datetime.fromisoformat("2026-07-09T23:00:00+00:00"),
        home_team=started.home_team,
        away_team=started.away_team,
        bookmakers=started.bookmakers,
    )
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    upcoming, filtered_count = filter_upcoming_games([started, future], now=now)
    assert filtered_count == 1
    assert len(upcoming) == 1
    assert upcoming[0].id == "future"


def test_filter_upcoming_games_keeps_future_games():
    future_a = Game(
        id="future-a",
        sport_key="baseball_mlb",
        sport_title="MLB",
        commence_time=datetime.fromisoformat("2026-07-09T23:00:00+00:00"),
        home_team="St. Louis Cardinals",
        away_team="Milwaukee Brewers",
        bookmakers=_brewers_cardinals_game("fixture").bookmakers,
    )
    future_b = Game(
        id="future-b",
        sport_key="baseball_mlb",
        sport_title="MLB",
        commence_time=datetime.fromisoformat("2026-07-10T23:00:00+00:00"),
        home_team="St. Louis Cardinals",
        away_team="Milwaukee Brewers",
        bookmakers=_brewers_cardinals_game("fixture").bookmakers,
    )
    now = datetime.fromisoformat("2026-07-08T12:00:00+00:00")
    upcoming, filtered_count = filter_upcoming_games([future_a, future_b], now=now)
    assert filtered_count == 0
    assert len(upcoming) == 2
