"""HTTP client for The Odds API."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from bonusarb.models import BookmakerKey, BookmakerOdds, Game, Market, Outcome, QuotaInfo, TOKEN_BOOKS
from bonusarb.odds_utils import normalize_price
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.config import (
    DEFAULT_BOOKMAKERS,
    DEFAULT_MARKET,
    DEFAULT_ODDS_FORMAT,
    DEFAULT_REGION,
    ODDS_API_BASE_URL,
    ODDS_API_KEY,
    QUOTA_STATE_PATH,
    SAMPLE_DATA_PATH,
)


class OddsApiError(RuntimeError):
    pass


class OddsApiClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache: OddsCache | None = None,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key or ODDS_API_KEY
        self.cache = cache or OddsCache()
        self.dry_run = dry_run
        self.quota = self._load_quota_state()
        self.session = requests.Session()

    def _load_quota_state(self) -> QuotaInfo:
        if not QUOTA_STATE_PATH.exists():
            return QuotaInfo()
        try:
            # utf-8-sig tolerates a BOM that some external editors/shells write.
            payload = json.loads(QUOTA_STATE_PATH.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return QuotaInfo()
        return QuotaInfo(
            remaining=payload.get("remaining"),
            used=payload.get("used"),
            last_cost=payload.get("last_cost"),
        )

    def _save_quota_state(self) -> None:
        QUOTA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "remaining": self.quota.remaining,
            "used": self.quota.used,
            "last_cost": self.quota.last_cost,
        }
        QUOTA_STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")

    def would_exceed(self, estimated_cost: int) -> bool:
        """Return True if a fetch of ``estimated_cost`` credits is known to be unaffordable."""
        if self.dry_run or self.quota.remaining is None:
            return False
        return estimated_cost > self.quota.remaining

    def _parse_quota(self, response: requests.Response) -> QuotaInfo:
        remaining = response.headers.get("x-requests-remaining")
        used = response.headers.get("x-requests-used")
        last = response.headers.get("x-requests-last")
        return QuotaInfo(
            remaining=int(remaining) if remaining is not None else None,
            used=int(used) if used is not None else None,
            last_cost=int(last) if last is not None else None,
        )

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        namespace: str,
        cache_key: str,
        allow_fetch: bool = True,
        force_fetch: bool = False,
        ignore_dry_run: bool = False,
    ) -> Any:
        if not force_fetch:
            cached = self.cache.get(namespace, cache_key)
            if cached is not None:
                return cached

        if not allow_fetch:
            raise OddsApiError(
                f"No cached data available for {namespace}:{cache_key}. "
                "Run without --no-fetch or use --dry-run."
            )

        if self.dry_run and not ignore_dry_run:
            return self._load_sample_data(namespace, cache_key)

        if not self.api_key:
            raise OddsApiError("ODDS_API_KEY is not set. Add it to .env or pass --api-key.")

        url = f"{ODDS_API_BASE_URL}{path}"
        query = dict(params or {})
        query["apiKey"] = self.api_key
        response = self.session.get(url, params=query, timeout=30)
        quota = self._parse_quota(response)
        self.quota = self.quota.merge(quota)

        if response.status_code != 200:
            raise OddsApiError(f"Odds API error {response.status_code}: {response.text}")

        data = response.json()
        self.cache.set(namespace, cache_key, data)
        self._save_quota_state()
        return data

    def _load_sample_data(self, namespace: str, cache_key: str) -> Any:
        if not SAMPLE_DATA_PATH.exists():
            raise OddsApiError(f"Sample data not found at {SAMPLE_DATA_PATH}")
        payload = json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        if namespace == "sports":
            return payload["sports"]
        if namespace == "odds":
            return payload["odds"].get(cache_key, [])
        raise OddsApiError(f"No dry-run sample available for {namespace}:{cache_key}")

    def list_sports(self, *, allow_fetch: bool = True, force_fetch: bool = False) -> list[dict[str, Any]]:
        # The /sports endpoint is free; use the live list when an API key is available,
        # even in dry-run mode, so users can pick any active sport.
        if allow_fetch and self.api_key:
            try:
                return self._request(
                    "/sports",
                    params={"all": "false"},
                    namespace="sports",
                    cache_key="active",
                    allow_fetch=True,
                    force_fetch=force_fetch,
                    ignore_dry_run=True,
                )
            except (OddsApiError, requests.RequestException):
                if not self.dry_run:
                    raise

        if not allow_fetch:
            cached = self.cache.get("sports", "active")
            if cached is not None:
                return cached
            raise OddsApiError("No cached sports list available. Run without --no-fetch.")

        return self._load_sample_data("sports", "active")

    def get_odds(
        self,
        sport_key: str,
        *,
        markets: str = DEFAULT_MARKET,
        regions: str = DEFAULT_REGION,
        bookmakers: tuple[BookmakerKey, ...] = DEFAULT_BOOKMAKERS,
        odds_format: str = DEFAULT_ODDS_FORMAT,
        allow_fetch: bool = True,
        force_fetch: bool = False,
    ) -> list[Game]:
        cache_key = "|".join(
            [
                sport_key,
                markets,
                regions,
                ",".join(bookmakers),
                odds_format,
            ]
        )
        raw = self._request(
            f"/sports/{sport_key}/odds",
            params={
                "regions": regions,
                "markets": markets,
                "bookmakers": ",".join(bookmakers),
                "oddsFormat": odds_format,
            },
            namespace="odds",
            cache_key=cache_key,
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
        )
        return [self._parse_game(item) for item in raw]

    def _parse_game(self, item: dict[str, Any]) -> Game:
        commence = datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00"))
        bookmakers: dict[BookmakerKey, BookmakerOdds] = {}
        for raw_book in item.get("bookmakers", []):
            key = raw_book["key"]
            if key not in ("fanduel", "draftkings"):
                continue
            markets: dict[str, Market] = {}
            for raw_market in raw_book.get("markets", []):
                outcomes = tuple(
                    Outcome(
                        name=outcome["name"],
                        price=float(outcome["price"]),
                        point=outcome.get("point"),
                    )
                    for outcome in raw_market.get("outcomes", [])
                )
                last_update = None
                if raw_market.get("last_update"):
                    last_update = datetime.fromisoformat(
                        raw_market["last_update"].replace("Z", "+00:00")
                    )
                markets[raw_market["key"]] = Market(
                    key=raw_market["key"],
                    outcomes=outcomes,
                    last_update=last_update,
                )
            bookmakers[key] = BookmakerOdds(
                key=key,
                title=raw_book.get("title", key),
                markets=markets,
            )

        return Game(
            id=item["id"],
            sport_key=item["sport_key"],
            sport_title=item.get("sport_title", item["sport_key"]),
            commence_time=commence,
            home_team=item["home_team"],
            away_team=item["away_team"],
            bookmakers=bookmakers,
        )

    def estimated_request_cost(self, markets: str, regions: str = DEFAULT_REGION) -> int:
        market_count = len([m for m in markets.split(",") if m.strip()])
        region_count = len([r for r in regions.split(",") if r.strip()])
        return max(market_count, 1) * max(region_count, 1)
