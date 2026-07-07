"""Tests for the bundled sports sample list."""

from bonusarb.config import LEAGUES
from bonusarb.oddsapi.client import OddsApiClient, OddsApiError


def test_dry_run_with_api_key_falls_back_to_sample_when_live_fails(monkeypatch):
    client = OddsApiClient(api_key="test-key", dry_run=True)

    def fail_live(*args, **kwargs):
        raise OddsApiError("network unavailable")

    monkeypatch.setattr(client, "_request", fail_live)
    sports = client.list_sports(allow_fetch=True)
    keys = {sport["key"] for sport in sports}
    assert keys == {key for key, _ in LEAGUES}


def test_dry_run_without_api_key_uses_sample_sports():
    client = OddsApiClient(api_key="", dry_run=True)
    # Force the no-live-fetch branch so we exercise the bundled sample list,
    # not whatever happens to be active on the live API today.
    client.api_key = ""
    sports = client.list_sports(allow_fetch=True)
    keys = {sport["key"] for sport in sports}
    assert keys == {key for key, _ in LEAGUES}
