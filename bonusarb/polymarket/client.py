"""Read-only Polymarket client for hedge odds.

Uses the public Gamma API (https://gamma-api.polymarket.com) for market
discovery/metadata and the public CLOB API (https://clob.polymarket.com) for
live prices. No authentication is required for read endpoints.

Polymarket prices are share prices in the range (0, 1). A share pays out 1 unit
if the outcome wins, so buying a share at price ``p`` is equivalent to decimal
odds of ``1 / p``. We convert the buy price of each outcome into decimal odds
for the existing hedge solver.

Sports fixtures are split across multiple Gamma events that share a ``gameId``.
The main event slug is used to discover sibling events (e.g. ``*-more-markets``),
which contain moneyline, spreads, and totals markets.

Gamma returns several fields (``outcomes``, ``outcomePrices``, ``clobTokenIds``)
as JSON-encoded strings, so they must be parsed with ``json.loads`` before use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from bonusarb.oddsapi.cache import OddsCache


POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"


class PolymarketError(RuntimeError):
    pass


def share_price_to_decimal_odds(price: float) -> float:
    """Convert a Polymarket share price (0, 1) to decimal odds (>= 1)."""
    if not 0.0 < price < 1.0:
        raise ValueError(f"Polymarket share price must be in (0, 1), got {price}")
    return 1.0 / price


def _parse_stringified_array(value: Any) -> list[Any]:
    """Gamma ships some fields as JSON-encoded strings; parse them transparently."""
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, list):
        return value
    return []


def _parse_last_update(market: dict[str, Any]) -> datetime | None:
    if not market.get("lastUpdate"):
        return None
    try:
        return datetime.fromisoformat(str(market["lastUpdate"]).replace("Z", "+00:00"))
    except ValueError:
        return None


def _spread_team_from_title(group_item_title: str | None) -> str | None:
    if not group_item_title:
        return None
    if "(" in group_item_title:
        return group_item_title.split(" (", 1)[0].strip()
    return group_item_title.strip()


@dataclass(frozen=True)
class PolymarketOutcomeOdds:
    name: str
    decimal_odds: float
    token_id: str
    point: float | None = None


@dataclass(frozen=True)
class PolymarketGameMarkets:
    event_slug: str
    markets: dict[str, tuple[PolymarketOutcomeOdds, ...]]
    last_update: datetime | None = None


class PolymarketMarket:
    """A resolved Polymarket binary market with team-labelled decimal odds."""

    def __init__(
        self,
        slug: str,
        outcomes: tuple[tuple[str, float, str], ...],
        last_update: datetime | None = None,
    ) -> None:
        # outcomes: (team_name, decimal_odds, token_id)
        self.slug = slug
        self.outcomes = outcomes
        self.last_update = last_update

    def decimal_odds_for(self, team: str) -> float | None:
        for name, odds, _token_id in self.outcomes:
            if name == team:
                return odds
        return None


class PolymarketClient:
    def __init__(
        self,
        cache: OddsCache | None = None,
        dry_run: bool = False,
        sample_markets: dict[str, dict[str, Any]] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.cache = cache or OddsCache()
        self.dry_run = dry_run
        self._sample_markets = sample_markets or {}
        self.session = session or requests.Session()

    def get_game_markets(
        self,
        event_slug: str,
        home_team: str,
        away_team: str,
        *,
        allow_fetch: bool = True,
        force_fetch: bool = False,
    ) -> PolymarketGameMarkets | None:
        """Fetch moneyline, spreads, and totals odds for a mapped fixture.

        Resolves the main event slug to a ``gameId``, loads all sibling Gamma
        events for that game, and parses hedge-relevant sports markets.
        """
        event = self._fetch_event_by_slug(
            event_slug,
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
        )
        if event is None or event.get("closed"):
            return None

        game_id = event.get("gameId")
        if game_id is None:
            return None

        sibling_events = self._fetch_events_for_game(
            int(game_id),
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
        )
        if not sibling_events:
            sibling_events = [event]

        raw_markets: list[dict[str, Any]] = []
        for sibling in sibling_events:
            if sibling.get("closed"):
                continue
            raw_markets.extend(sibling.get("markets") or [])

        if not raw_markets:
            return None

        teams = {home_team, away_team}
        parsed = self._parse_game_markets(
            raw_markets,
            teams,
            allow_fetch=allow_fetch,
        )
        if not parsed:
            return None

        last_update = max(
            (ts for ts in (_parse_last_update(m) for m in raw_markets) if ts is not None),
            default=None,
        )
        return PolymarketGameMarkets(
            event_slug=event_slug,
            markets=parsed,
            last_update=last_update,
        )

    def get_market(
        self,
        slug: str,
        outcome_by_team: dict[str, str],
        *,
        allow_fetch: bool = True,
        force_fetch: bool = False,
    ) -> PolymarketMarket | None:
        """Fetch a single Polymarket market by slug and resolve decimal odds per team.

        ``outcome_by_team`` maps sportsbook team name -> Polymarket outcome name
        (e.g. {"Portugal": "Portugal", "Croatia": "Croatia"}). Returns None if
        the market is missing, closed, or does not contain both required
        outcomes with usable prices.
        """
        cache_key = f"market:{slug}"
        raw = self._request(
            "markets",
            cache_key,
            params={"slug": slug},
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
        )

        if not raw:
            return None
        market = raw[0] if isinstance(raw, list) else raw
        if not market or market.get("closed"):
            return None

        outcomes = _parse_stringified_array(market.get("outcomes"))
        clob_token_ids = _parse_stringified_array(market.get("clobTokenIds"))
        if len(outcomes) != 2 or len(clob_token_ids) != 2:
            return None

        prices = self._resolve_prices(market, clob_token_ids, allow_fetch=allow_fetch)
        if prices is None:
            return None

        outcome_prices = dict(zip(outcomes, prices))
        team_labelled: list[tuple[str, float, str]] = []
        for team, polymarket_outcome in outcome_by_team.items():
            price = outcome_prices.get(polymarket_outcome)
            if price is None or not 0.0 < price < 1.0:
                return None
            token_id = clob_token_ids[outcomes.index(polymarket_outcome)]
            team_labelled.append((team, share_price_to_decimal_odds(price), token_id))

        return PolymarketMarket(slug, tuple(team_labelled), last_update=_parse_last_update(market))

    def _fetch_event_by_slug(
        self,
        event_slug: str,
        *,
        allow_fetch: bool,
        force_fetch: bool,
    ) -> dict[str, Any] | None:
        cache_key = f"event:{event_slug}"
        raw = self._request(
            "events",
            cache_key,
            path="/events",
            params={"slug": event_slug},
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
            optional=True,
        )
        if not raw:
            return None
        return raw[0] if isinstance(raw, list) else raw

    def list_events_for_tag(
        self,
        tag_id: int,
        *,
        allow_fetch: bool = True,
        force_fetch: bool = False,
        page_size: int = 100,
        max_pages: int = 12,
    ) -> list[dict[str, Any]]:
        """List open Gamma events for a sport tag (used for auto-discovery).

        Ordered by ``startTime`` (actual game time) and paginated: futures and
        player-prop events are created long before games, so ordering by
        creation date buries the real fixtures past the page cap. Results are
        cached as one combined list under the tag key.
        """
        cache_key = f"tag_events:{tag_id}"

        if not force_fetch and not self.dry_run:
            cached = self.cache.get("events", cache_key)
            if cached is not None:
                return cached

        if self.dry_run:
            sample = self._sample_markets.get(cache_key)
            return list(sample) if sample else []

        if not allow_fetch:
            cached = self.cache.get("events", cache_key)
            return cached or []

        url = f"{POLYMARKET_GAMMA_BASE_URL}/events"
        events: list[dict[str, Any]] = []
        for page in range(max_pages):
            params = {
                "tag_id": tag_id,
                "closed": "false",
                "limit": page_size,
                "offset": page * page_size,
                "order": "startTime",
                "ascending": "true",
            }
            try:
                response = self.session.get(url, params=params, timeout=30)
            except requests.RequestException:
                break
            if response.status_code != 200:
                break
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break
            events.extend(batch)
            if len(batch) < page_size:
                break

        self.cache.set("events", cache_key, events)
        return events

    def _fetch_events_for_game(
        self,
        game_id: int,
        *,
        allow_fetch: bool,
        force_fetch: bool,
    ) -> list[dict[str, Any]]:
        cache_key = f"game_events:{game_id}"
        raw = self._request(
            "events",
            cache_key,
            path="/events",
            params={"game_id": game_id, "limit": 50},
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
            optional=True,
        )
        if not raw:
            return []
        return raw if isinstance(raw, list) else [raw]

    def _parse_game_markets(
        self,
        raw_markets: list[dict[str, Any]],
        teams: set[str],
        *,
        allow_fetch: bool,
    ) -> dict[str, tuple[PolymarketOutcomeOdds, ...]]:
        parsed: dict[str, tuple[PolymarketOutcomeOdds, ...]] = {}
        spread_sides: dict[tuple[str, float], PolymarketOutcomeOdds] = {}
        total_sides: dict[tuple[str, float], PolymarketOutcomeOdds] = {}

        for market in raw_markets:
            if market.get("closed"):
                continue

            sports_type = market.get("sportsMarketType")
            if sports_type == "moneyline":
                h2h = self._parse_binary_team_market(market, teams, allow_fetch=allow_fetch)
                if h2h is not None:
                    parsed["h2h"] = h2h
                continue

            if sports_type == "spreads":
                self._collect_spread_sides(market, teams, spread_sides, allow_fetch=allow_fetch)
                continue

            if sports_type == "totals":
                self._collect_total_sides(market, total_sides, allow_fetch=allow_fetch)

        if spread_sides:
            parsed["spreads"] = tuple(spread_sides[key] for key in sorted(spread_sides))
        if total_sides:
            parsed["totals"] = tuple(total_sides[key] for key in sorted(total_sides))

        return parsed

    def _parse_binary_team_market(
        self,
        market: dict[str, Any],
        teams: set[str],
        *,
        allow_fetch: bool,
    ) -> tuple[PolymarketOutcomeOdds, ...] | None:
        outcomes = _parse_stringified_array(market.get("outcomes"))
        clob_token_ids = _parse_stringified_array(market.get("clobTokenIds"))
        if len(outcomes) != 2 or len(clob_token_ids) != 2:
            return None
        if set(outcomes) != teams:
            return None

        prices = self._resolve_prices(market, clob_token_ids, allow_fetch=allow_fetch)
        if prices is None:
            return None

        resolved: list[PolymarketOutcomeOdds] = []
        for name, price, token_id in zip(outcomes, prices, clob_token_ids):
            if not 0.0 < price < 1.0:
                return None
            resolved.append(
                PolymarketOutcomeOdds(
                    name=name,
                    decimal_odds=share_price_to_decimal_odds(price),
                    token_id=token_id,
                )
            )
        return tuple(resolved)

    def _collect_spread_sides(
        self,
        market: dict[str, Any],
        teams: set[str],
        spread_sides: dict[tuple[str, float], PolymarketOutcomeOdds],
        *,
        allow_fetch: bool,
    ) -> None:
        line = market.get("line")
        if line is None:
            return
        try:
            spread_line = float(line)
        except (TypeError, ValueError):
            return

        outcomes = _parse_stringified_array(market.get("outcomes"))
        clob_token_ids = _parse_stringified_array(market.get("clobTokenIds"))
        if len(outcomes) != 2 or len(clob_token_ids) != 2:
            return
        if set(outcomes) != teams:
            return

        prices = self._resolve_prices(market, clob_token_ids, allow_fetch=allow_fetch)
        if prices is None:
            return

        anchor_team = _spread_team_from_title(market.get("groupItemTitle"))
        if anchor_team not in teams:
            # Some feeds (e.g. MLB) label the group "Spread -1.5" with no team name.
            # Fall back to outcome order: the first outcome carries the listed line.
            anchor_team = outcomes[0] if outcomes and outcomes[0] in teams else None
        if anchor_team not in teams:
            return

        other_team = next(team for team in teams if team != anchor_team)
        price_by_name = dict(zip(outcomes, prices))
        token_by_name = dict(zip(outcomes, clob_token_ids))

        for team, point in (
            (anchor_team, spread_line),
            (other_team, -spread_line),
        ):
            price = price_by_name.get(team)
            token_id = token_by_name.get(team)
            if price is None or token_id is None or not 0.0 < price < 1.0:
                return
            key = (team, round(point, 4))
            candidate = PolymarketOutcomeOdds(
                name=team,
                decimal_odds=share_price_to_decimal_odds(price),
                token_id=token_id,
                point=point,
            )
            existing = spread_sides.get(key)
            if existing is None or candidate.decimal_odds > existing.decimal_odds:
                spread_sides[key] = candidate

    def _collect_total_sides(
        self,
        market: dict[str, Any],
        total_sides: dict[tuple[str, float], PolymarketOutcomeOdds],
        *,
        allow_fetch: bool,
    ) -> None:
        line = market.get("line")
        if line is None:
            return
        try:
            total_line = float(line)
        except (TypeError, ValueError):
            return

        outcomes = _parse_stringified_array(market.get("outcomes"))
        clob_token_ids = _parse_stringified_array(market.get("clobTokenIds"))
        if len(outcomes) != 2 or len(clob_token_ids) != 2:
            return
        if set(outcomes) != {"Over", "Under"}:
            return

        prices = self._resolve_prices(market, clob_token_ids, allow_fetch=allow_fetch)
        if prices is None:
            return

        price_by_name = dict(zip(outcomes, prices))
        token_by_name = dict(zip(outcomes, clob_token_ids))
        for side in ("Over", "Under"):
            price = price_by_name.get(side)
            token_id = token_by_name.get(side)
            if price is None or token_id is None or not 0.0 < price < 1.0:
                return
            key = (side, round(total_line, 4))
            candidate = PolymarketOutcomeOdds(
                name=side,
                decimal_odds=share_price_to_decimal_odds(price),
                token_id=token_id,
                point=total_line,
            )
            existing = total_sides.get(key)
            if existing is None or candidate.decimal_odds > existing.decimal_odds:
                total_sides[key] = candidate

    def _resolve_prices(
        self,
        market: dict[str, Any],
        clob_token_ids: list[str],
        *,
        allow_fetch: bool,
    ) -> list[float] | None:
        live = self._live_prices(clob_token_ids, allow_fetch=allow_fetch)
        if live is not None:
            return live
        outcome_prices = _parse_stringified_array(market.get("outcomePrices"))
        if len(outcome_prices) != len(clob_token_ids):
            return None
        try:
            return [float(p) for p in outcome_prices]
        except (TypeError, ValueError):
            return None

    def _live_prices(self, token_ids: list[str], *, allow_fetch: bool) -> list[float] | None:
        prices: list[float] = []
        for token_id in token_ids:
            cache_key = f"price:{token_id}"
            raw = self._request(
                "price",
                cache_key,
                base_url=POLYMARKET_CLOB_BASE_URL,
                path="/price",
                params={"token_id": token_id, "side": "BUY"},
                allow_fetch=allow_fetch,
                optional=True,
            )
            if not raw or "price" not in raw:
                return None
            try:
                prices.append(float(raw["price"]))
            except (TypeError, ValueError):
                return None
        return prices if len(prices) == len(token_ids) else None

    def _request(
        self,
        namespace: str,
        cache_key: str,
        *,
        params: dict[str, Any] | None = None,
        base_url: str = POLYMARKET_GAMMA_BASE_URL,
        path: str = "/markets",
        allow_fetch: bool = True,
        force_fetch: bool = False,
        optional: bool = False,
    ) -> Any:
        if not force_fetch and not self.dry_run:
            cached = self.cache.get(namespace, cache_key)
            if cached is not None:
                return cached

        if self.dry_run:
            sample = self._sample_markets.get(cache_key)
            if sample is None and optional:
                return None
            if sample is None:
                if optional:
                    return None
                raise PolymarketError(f"No Polymarket sample data for {cache_key}.")
            return sample

        if not allow_fetch:
            raise PolymarketError(
                f"No cached Polymarket data available for {namespace}:{cache_key}. "
                "Run without --no-fetch or provide sample data."
            )

        url = f"{base_url}{path}"
        try:
            response = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            if optional:
                return None
            raise PolymarketError(f"Polymarket request failed: {exc}") from exc

        if response.status_code != 200:
            if optional:
                return None
            raise PolymarketError(
                f"Polymarket error {response.status_code}: {response.text}"
            )

        data = response.json()
        self.cache.set(namespace, cache_key, data)
        return data
