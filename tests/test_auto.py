"""Tests for the auto Polymarket hedger.

Covers: sequential-hedge tail recompute under line movement, watcher status
mapping, resumable state idempotency, executor paper/skip/floor behavior with a
mocked CLOB, the Polymarket-only automatable filter, and token_id threading
through the merge -> leg path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bonusarb.arb.sequential import (
    recompute_remaining_hedges,
    solve_sequential_hedge,
    verify_plan,
)
from bonusarb.auto.config import AutoConfig
from bonusarb.auto.executor import (
    ClobExecutor,
    FAILED,
    FILLED,
    HedgeContext,
    OrderResponse,
    OrderStatus,
    PARTIAL,
    SKIPPED,
)
from bonusarb.polymarket.client import effective_taker_share_price, share_price_to_decimal_odds
from bonusarb.auto.select import filter_automatable, is_automatable
from bonusarb.auto.state import (
    ActiveParlay,
    LEG_HEDGE_PLACED,
    LEG_LOST,
    LEG_PENDING,
    LEG_WON,
    load_parlay,
)
from bonusarb.auto.watcher import LOST, PENDING, ResolutionWatcher, WON
from bonusarb.models import (
    BookmakerOdds,
    Game,
    HedgePlan,
    Leg,
    Market,
    Outcome,
    TokenConstraint,
    TokenType,
)
from bonusarb.odds_utils import enumerate_game_legs
from bonusarb.polymarket.merge import merge_polymarket_odds
from bonusarb.polymarket.mappings import EventMapping, OutcomeMapping


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _polymarket_leg(
    game_id: str,
    commence: str,
    token_odds: float,
    hedge_odds: float,
    hedge_token_id: str,
    slug: str = "slug-x",
    selection: str = "Team A",
    opposite: str = "Team B",
) -> Leg:
    return Leg(
        game_id=game_id,
        event_label=f"{opposite} @ {selection}",
        commence_time=_dt(commence),
        selection=selection,
        opposite_selection=opposite,
        token_book="fanduel",
        token_odds=token_odds,
        hedge_book="polymarket",
        hedge_odds=hedge_odds,
        market_key="h2h",
        polymarket_event_slug=slug,
        hedge_token_id=hedge_token_id,
        hedge_price_source="clob",
    )


def _game_with_polymarket() -> Game:
    """A game whose synthetic polymarket book carries token ids on outcomes."""
    fanduel = BookmakerOdds(
        key="fanduel",
        title="FanDuel",
        markets={
            "h2h": Market(
                key="h2h",
                outcomes=(
                    Outcome("Team A", 2.10),
                    Outcome("Team B", 1.75),
                ),
            )
        },
    )
    polymarket = BookmakerOdds(
        key="polymarket",
        title="Polymarket",
        markets={
            "h2h": Market(
                key="h2h",
                outcomes=(
                    Outcome("Team A", 1.90, token_id="tokA"),
                    Outcome("Team B", 1.90, token_id="tokB"),
                ),
            )
        },
    )
    return Game(
        id="g1",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=_dt("2026-07-03T00:00:00Z"),
        home_team="Team A",
        away_team="Team B",
        bookmakers={"fanduel": fanduel, "polymarket": polymarket},
    )


# ---------------------------------------------------------------------------
# 1. Tail recompute under line movement
# ---------------------------------------------------------------------------


def test_recompute_remaining_keeps_paths_equal_after_line_movement():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=2,
        max_stake=100,
        boost_pct=0.0,
    )
    legs = (
        _polymarket_leg("g1", "2026-07-03T00:00:00Z", 2.10, 1.95, "t1", selection="Team A", opposite="Team B"),
        _polymarket_leg("g2", "2026-07-03T06:00:00Z", 2.05, 1.95, "t2", selection="Team C", opposite="Team D"),
    )
    plan = solve_sequential_hedge(legs, token, stake=100, sport_key="basketball_nba")
    assert plan is not None
    placed_hedge_1 = plan.hedge_steps[0].stake
    sum_placed = placed_hedge_1

    # Leg 1 won; leg 2's hedge price moved in our favor (odds 2.05 vs 1.95).
    live_odds_leg2 = 2.05
    locked_after, remaining_stakes = recompute_remaining_hedges(
        stake_cost=plan.stake_cost,
        win_profit=plan.effective_win_profit,
        remaining_hedge_odds=[live_odds_leg2],
        already_placed_stakes=[placed_hedge_1],
    )
    assert len(remaining_stakes) == 1
    hedge_2 = remaining_stakes[0]
    # Lose path: bet lost, hedge 1 lost, hedge 2 pays.
    profit_leg2_loses = hedge_2 * (live_odds_leg2 - 1.0) - sum_placed - plan.stake_cost
    # Win path: bet wins (stake returned) -> win_profit net, both hedges lost.
    profit_all_win = plan.effective_win_profit - hedge_2 - sum_placed
    assert abs(profit_leg2_loses - locked_after) < 0.05
    assert abs(profit_all_win - locked_after) < 0.05
    assert locked_after > plan.locked_profit  # favorable move raised the lock

    # Unfavorable move (worse hedge) must reduce locked profit, possibly negative.
    bad_locked, _ = recompute_remaining_hedges(
        stake_cost=plan.stake_cost,
        win_profit=plan.effective_win_profit,
        remaining_hedge_odds=[1.80],
        already_placed_stakes=[placed_hedge_1],
    )
    assert bad_locked < locked_after


def test_recompute_single_leg_tail_matches_solver():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=50,
        boost_pct=0.0,
    )
    legs = (_polymarket_leg("g1", "2026-07-03T00:00:00Z", 2.20, 2.10, "t1"),)
    plan = solve_sequential_hedge(legs, token, stake=50, sport_key="basketball_nba")
    assert plan is not None

    locked, stakes = recompute_remaining_hedges(
        stake_cost=plan.stake_cost,
        win_profit=plan.effective_win_profit,
        remaining_hedge_odds=[plan.hedge_steps[0].odds],
        already_placed_stakes=[],
    )
    assert abs(locked - plan.locked_profit) < 0.05
    assert abs(stakes[0] - plan.hedge_steps[0].stake) < 0.05


# ---------------------------------------------------------------------------
# 2. Watcher status mapping
# ---------------------------------------------------------------------------


class _StubPolyClient:
    def __init__(self, price: float | None, *, resolution: str | None = None) -> None:
        self.price = price
        self.resolution = resolution

    def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
        return self.price

    def get_token_resolution(self, token_id, *, event_slug=None, force_fetch=True):
        return self.resolution


def test_watcher_classifies_won_lost_pending():
    # confirm_polls=1 preserves the single-poll classification contract for edges.
    watcher = ResolutionWatcher(
        _StubPolyClient(0.99), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == LOST  # hedge side won -> leg lost

    watcher = ResolutionWatcher(
        _StubPolyClient(0.01), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == WON  # hedge side lost -> leg won

    watcher = ResolutionWatcher(
        _StubPolyClient(0.50), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == PENDING

    watcher = ResolutionWatcher(
        _StubPolyClient(None), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == PENDING
    assert watcher.resolve(None).status == PENDING


def test_watcher_requires_consecutive_confirm_polls():
    watcher = ResolutionWatcher(
        _StubPolyClient(0.01), won_threshold=0.99, lost_threshold=0.97, confirm_polls=3
    )
    assert watcher.resolve("t1").status == PENDING
    assert watcher.resolve("t1").status == PENDING
    assert watcher.resolve("t1").status == WON


def test_watcher_gamma_closed_accelerates_without_waiting():
    watcher = ResolutionWatcher(
        _StubPolyClient(0.50, resolution="won"),
        won_threshold=0.99,
        lost_threshold=0.97,
        confirm_polls=3,
    )
    result = watcher.resolve("t1")
    assert result.status == WON
    assert result.source == "gamma"


def test_watcher_asymmetric_threshold_edges():
    watcher = ResolutionWatcher(
        _StubPolyClient(0.97), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == LOST
    watcher = ResolutionWatcher(
        _StubPolyClient(0.98), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == LOST

    watcher = ResolutionWatcher(
        _StubPolyClient(0.011), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == PENDING
    watcher = ResolutionWatcher(
        _StubPolyClient(0.01), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == WON
    watcher = ResolutionWatcher(
        _StubPolyClient(0.005), won_threshold=0.99, lost_threshold=0.97, confirm_polls=1
    )
    assert watcher.resolve("t1").status == WON


# ---------------------------------------------------------------------------
# 3. State persistence + resume idempotency
# ---------------------------------------------------------------------------


def _two_leg_plan() -> HedgePlan:
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=2,
        max_stake=100,
        boost_pct=0.0,
    )
    legs = (
        _polymarket_leg("g1", "2026-07-03T00:00:00Z", 2.10, 1.95, "t1", slug="s1", selection="A", opposite="B"),
        _polymarket_leg("g2", "2026-07-03T06:00:00Z", 2.05, 1.95, "t2", slug="s2", selection="C", opposite="D"),
    )
    plan = solve_sequential_hedge(legs, token, stake=100, sport_key="basketball_nba")
    assert plan is not None
    return plan


def test_active_parlay_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba", usd_cad_rate=1.42)
    saved = parlay.save()
    assert saved.exists()

    reloaded = load_parlay(saved)
    assert reloaded.id == parlay.id
    assert reloaded.usd_cad_rate == pytest.approx(1.42)
    assert reloaded.stake_cost == plan.stake_cost
    assert len(reloaded.legs) == 2
    assert reloaded.legs[0].hedge_token_id == "t1"
    assert reloaded.next_pending_leg().leg_index == 1

    # Simulate hedge 1 placed + leg 1 won, then reload.
    reloaded.legs[0].status = LEG_HEDGE_PLACED
    reloaded.legs[0].order_id = "ord-1"
    reloaded.legs[0].filled_shares = 100.0
    reloaded.legs[0].filled_price = 0.51
    reloaded.legs[0].filled_cost = 51.0
    reloaded.legs[0].status = LEG_WON
    reloaded.legs[0].resolved_won = True
    reloaded.save()
    reloaded2 = load_parlay(saved)
    assert reloaded2.legs[0].status == LEG_WON
    assert reloaded2.next_pending_leg().leg_index == 2


def test_resume_never_double_places(tmp_path: Path, monkeypatch):
    """If an order id is already recorded, the leg is not re-placed."""
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto import monitor as monitor_mod

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba")
    parlay.save()

    # Pre-mark leg 1 as placed so resume must skip it.
    parlay.legs[0].order_id = "already-placed"
    parlay.legs[0].filled_shares = 10.0
    parlay.legs[0].filled_price = 0.5
    parlay.legs[0].filled_cost = 5.0
    parlay.legs[0].status = LEG_HEDGE_PLACED
    parlay.save()

    class _FailingExecutor:
        def place_hedge(self, leg, context):
            raise AssertionError("resume must not re-place an already-filled leg")

    class _PendingWatcher:
        def resolve(self, token_id, *, event_slug=None):
            from bonusarb.auto.watcher import LegResolution
            return LegResolution(PENDING, None)

    class _NoopNotifier:
        def send(self, *a, **k): return True
        def alert(self, *a, **k): return True
        def hedge_placed(self, *a, **k): return True
        def leg_resolved(self, *a, **k): return True
        def complete(self, *a, **k): return True

    cfg = AutoConfig.from_env()
    # Leg 1 is HEDGE_PLACED; watcher is pending, so monitor should return without
    # touching the executor and without advancing leg 2.
    result = monitor_mod.run_monitor(
        parlay, _FailingExecutor(), _PendingWatcher(), _NoopNotifier(), cfg
    )
    assert result.legs[0].status == LEG_HEDGE_PLACED
    assert result.legs[1].status == LEG_PENDING


def test_run_monitor_does_not_send_started_notification(tmp_path: Path, monkeypatch):
    """run_monitor must not emit the one-time 'started' message on each poll."""
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto import monitor as monitor_mod

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba")
    parlay.legs[0].order_id = "ord-1"
    parlay.legs[0].filled_shares = 10.0
    parlay.legs[0].filled_price = 0.5
    parlay.legs[0].filled_cost = 5.0
    parlay.legs[0].status = LEG_HEDGE_PLACED
    parlay.save()

    class _PendingWatcher:
        def resolve(self, token_id, *, event_slug=None):
            from bonusarb.auto.watcher import LegResolution
            return LegResolution(PENDING, None)

    class _RecordingNotifier:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def send(self, text: str) -> bool:
            self.messages.append(text)
            return True

        def alert(self, message: str) -> None:
            self.messages.append(message)

        def hedge_placed(self, *a, **k) -> None:
            return None

        def leg_resolved(self, *a, **k) -> None:
            return None

        def complete(self, *a, **k) -> None:
            return None

    notifier = _RecordingNotifier()
    cfg = AutoConfig.from_env()

    for _ in range(2):
        monitor_mod.run_monitor(
            parlay, _FailingExecutor(), _PendingWatcher(), notifier, cfg
        )

    started = [m for m in notifier.messages if "Auto-hedger started" in m]
    assert started == []


class _FailingExecutor:
    def place_hedge(self, leg, context):
        raise AssertionError("should not place hedge in this test")


# ---------------------------------------------------------------------------
# 4. Executor: paper fill, profit-floor skip, mocked live fill
# ---------------------------------------------------------------------------


def _auto_config() -> AutoConfig:
    return AutoConfig(
        private_key="0x" + "1" * 64,
        funder_address="0x" + "2" * 40,
        signature_type=2,
        chain_id=137,
        clob_host="https://clob.polymarket.com",
        telegram_bot_token="",
        telegram_chat_id="",
        min_locked_profit_floor=0.50,
        max_slippage=0.02,
        poll_interval_seconds=1,
        won_threshold=0.99,
        lost_threshold=0.97,
        min_order_shares=1.0,
        order_fill_seconds=2,
        resolution_confirm_polls=3,
    )


def _leg_state(token_id: str, planned_odds: float, planned_stake: float):
    from bonusarb.auto.state import LegState

    return LegState(
        leg_index=1,
        event_label="B @ A",
        selection="B",
        commence_time_iso="2026-07-03T00:00:00+00:00",
        place_by_iso="2026-07-03T02:30:00+00:00",
        market_key="h2h",
        polymarket_event_slug="s1",
        hedge_token_id=token_id,
        planned_stake=planned_stake,
        planned_odds=planned_odds,
    )


def _context(plan: HedgePlan) -> HedgeContext:
    return HedgeContext(
        stake_cost=plan.stake_cost,
        win_profit=plan.effective_win_profit,
        already_placed_stakes=[],
        remaining_planned_odds=[step.odds for step in plan.hedge_steps],
    )


def test_executor_paper_fills_at_live_price():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()
    live_price = 0.50
    p_eff = effective_taker_share_price(live_price)

    class _PriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            return live_price

        def executable_buy_for_shares(self, token_id, shares, *, max_slippage, force_fetch=True):
            from bonusarb.polymarket.client import BuyVwapResult
            return BuyVwapResult(
                vwap=live_price,
                worst_price=live_price,
                filled_shares=shares,
                notional=shares * live_price,
            )

    executor = ClobExecutor(cfg, _PriceClient(), paper=True)
    result = executor.place_hedge(leg, _context(plan))
    assert result.status == FILLED
    assert result.order_id and result.order_id.startswith("paper-")
    assert result.filled_price == pytest.approx(live_price)
    assert result.filled_shares == pytest.approx(result.stake_usd / p_eff)
    assert result.filled_cost == pytest.approx(
        result.filled_shares * live_price
        + result.filled_shares * 0.05 * live_price * (1.0 - live_price)
    )
    assert result.locked_profit_after >= cfg.min_locked_profit_floor


def test_executor_sizes_shares_using_post_fee_price():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()
    live_price = 0.50
    p_eff = effective_taker_share_price(live_price)
    ctx = _context(plan)
    remaining_odds = list(ctx.remaining_planned_odds)
    remaining_odds[0] = share_price_to_decimal_odds(live_price)
    _, stakes = recompute_remaining_hedges(
        stake_cost=ctx.stake_cost,
        win_profit=ctx.win_profit,
        remaining_hedge_odds=remaining_odds,
        already_placed_stakes=ctx.already_placed_stakes,
    )
    intended = stakes[0] / p_eff

    class _PriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            return live_price

        def executable_buy_for_shares(self, token_id, shares, *, max_slippage, force_fetch=True):
            from bonusarb.polymarket.client import BuyVwapResult
            return BuyVwapResult(
                vwap=live_price,
                worst_price=live_price,
                filled_shares=shares,
                notional=shares * live_price,
            )

    mock_api = _MockOrderApi(fill_shares=intended, fill_price=live_price)
    executor = ClobExecutor(cfg, _PriceClient(), paper=False, order_api=mock_api)
    result = executor.place_hedge(leg, ctx)
    assert result.status == FILLED
    assert mock_api.posted
    assert mock_api.posted[0][1] == pytest.approx(intended)


def test_executor_skips_when_profit_below_floor():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()
    cfg.min_locked_profit_floor = 1_000_000.0  # impossible to satisfy

    class _BadPriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            # Worse hedge price (lower odds) shrinks locked profit.
            return 0.97  # decimal odds ~1.03, terrible hedge

        def executable_buy_for_shares(self, token_id, shares, *, max_slippage, force_fetch=True):
            from bonusarb.polymarket.client import BuyVwapResult
            return BuyVwapResult(0.97, 0.97, shares, shares * 0.97)

    executor = ClobExecutor(cfg, _BadPriceClient(), paper=True)
    result = executor.place_hedge(leg, _context(plan))
    assert result.status == SKIPPED
    assert result.reason is not None
    assert "below floor" in result.reason


def test_executor_fails_without_live_price():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()

    class _NoPriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            return None

        def executable_buy_for_shares(self, *a, **k):
            return None

    executor = ClobExecutor(cfg, _NoPriceClient(), paper=True)
    result = executor.place_hedge(leg, _context(plan))
    assert result.status == FAILED


class _MockOrderApi:
    def __init__(self, *, fill_shares: float, fill_price: float, status: str = "matched"):
        self._fill_shares = fill_shares
        self._fill_price = fill_price
        self._status = status
        self.posted = []
        self.cancelled = []

    def post_marketable_order(self, token_id, shares, limit_price):
        self.posted.append((token_id, shares, limit_price))
        return OrderResponse(order_id="ord-123", status="live")

    def get_order_status(self, order_id):
        return OrderStatus(status=self._status, filled_shares=self._fill_shares, avg_price=self._fill_price)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)


def test_executor_live_full_fill_via_mock():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()
    live_price = 0.50
    p_eff = effective_taker_share_price(live_price)
    ctx = _context(plan)
    remaining_odds = list(ctx.remaining_planned_odds)
    remaining_odds[0] = share_price_to_decimal_odds(live_price)
    _, stakes = recompute_remaining_hedges(
        stake_cost=ctx.stake_cost,
        win_profit=ctx.win_profit,
        remaining_hedge_odds=remaining_odds,
        already_placed_stakes=ctx.already_placed_stakes,
    )
    intended = stakes[0] / p_eff

    class _PriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            return live_price

        def executable_buy_for_shares(self, token_id, shares, *, max_slippage, force_fetch=True):
            from bonusarb.polymarket.client import BuyVwapResult
            return BuyVwapResult(live_price, live_price, shares, shares * live_price)

    mock_api = _MockOrderApi(fill_shares=intended, fill_price=live_price)
    executor = ClobExecutor(cfg, _PriceClient(), paper=False, order_api=mock_api)
    result = executor.place_hedge(leg, ctx)
    assert result.status == FILLED
    assert result.order_id == "ord-123"
    assert mock_api.posted and mock_api.cancelled == ["ord-123"]


def test_executor_live_partial_fill_flags_partial():
    plan = _two_leg_plan()
    leg = _leg_state("t1", plan.hedge_steps[0].odds, plan.hedge_steps[0].stake)
    cfg = _auto_config()
    live_price = 0.50
    p_eff = effective_taker_share_price(live_price)
    ctx = _context(plan)
    remaining_odds = list(ctx.remaining_planned_odds)
    remaining_odds[0] = share_price_to_decimal_odds(live_price)
    _, stakes = recompute_remaining_hedges(
        stake_cost=ctx.stake_cost,
        win_profit=ctx.win_profit,
        remaining_hedge_odds=remaining_odds,
        already_placed_stakes=ctx.already_placed_stakes,
    )
    intended = stakes[0] / p_eff

    class _PriceClient:
        def get_token_price(self, token_id, *, side="BUY", force_fetch=True):
            return live_price

        def executable_buy_for_shares(self, token_id, shares, *, max_slippage, force_fetch=True):
            from bonusarb.polymarket.client import BuyVwapResult
            return BuyVwapResult(live_price, live_price, shares, shares * live_price)

    mock_api = _MockOrderApi(fill_shares=intended * 0.5, fill_price=live_price)
    executor = ClobExecutor(cfg, _PriceClient(), paper=False, order_api=mock_api)
    result = executor.place_hedge(leg, ctx)
    assert result.status == PARTIAL


# ---------------------------------------------------------------------------
# 5. Polymarket-only automatable filter
# ---------------------------------------------------------------------------


def _plan_with_hedge_books(books: tuple[str, ...]) -> HedgePlan:
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=50,
        boost_pct=0.0,
        sport_key="basketball_nba",
    )
    legs = tuple(
        Leg(
            game_id=f"g{i}",
            event_label=f"g{i}",
            commence_time=_dt("2026-07-03T00:00:00Z"),
            selection="A",
            opposite_selection="B",
            token_book="fanduel",
            token_odds=2.10,
            hedge_book=book,
            hedge_odds=1.95,
            hedge_token_id=("tok" if book == "polymarket" else None),
            polymarket_event_slug=("slug" if book == "polymarket" else None),
            hedge_price_source=("clob" if book == "polymarket" else None),
        )
        for i, book in enumerate(books)
    )
    plan = solve_sequential_hedge(legs, token, stake=50, sport_key="basketball_nba")
    assert plan is not None
    return plan


def test_is_automatable_requires_all_polymarket_with_token_id():
    assert is_automatable(_plan_with_hedge_books(("polymarket", "polymarket")))
    assert not is_automatable(_plan_with_hedge_books(("polymarket", "draftkings")))
    assert not is_automatable(_plan_with_hedge_books(("draftkings", "draftkings")))


def test_is_automatable_rejects_gamma_fallback_and_nfl():
    from dataclasses import replace

    plan = _plan_with_hedge_books(("polymarket", "polymarket"))
    bad = replace(
        plan,
        legs=tuple(replace(leg, hedge_price_source="gamma_fallback") for leg in plan.legs),
        hedge_steps=tuple(
            replace(step, hedge_price_source="gamma_fallback") for step in plan.hedge_steps
        ),
    )
    assert not is_automatable(bad)
    assert not is_automatable(plan, sport_key="americanfootball_nfl")


def test_filter_automatable_keeps_only_polymarket_plans():
    plans = [
        _plan_with_hedge_books(("polymarket", "polymarket")),
        _plan_with_hedge_books(("polymarket", "draftkings")),
    ]
    kept = filter_automatable(plans)
    assert len(kept) == 1
    assert all(step.book == "polymarket" for step in kept[0].hedge_steps)


# ---------------------------------------------------------------------------
# 6. token_id threading through merge -> enumerate_game_legs
# ---------------------------------------------------------------------------


def test_merge_threads_token_id_and_legs_pick_it_up():
    game = _game_with_polymarket()
    mapping = EventMapping(
        sport_key="basketball_nba",
        home_team="Team A",
        away_team="Team B",
        polymarket_event_slug="slug-x",
        settlement="test",
        outcomes=(
            OutcomeMapping("Team A", "Team A"),
            OutcomeMapping("Team B", "Team B"),
        ),
        commence_time=_dt("2026-07-03T00:00:00Z"),
        verified=True,
    )

    class _StubPolyClient:
        def get_game_markets(self, slug, home, away, **kw):
            from bonusarb.polymarket.client import PolymarketGameMarkets, PolymarketOutcomeOdds

            return PolymarketGameMarkets(
                event_slug=slug,
                markets={
                    "h2h": (
                        PolymarketOutcomeOdds("Team A", 1.90, "tokA"),
                        PolymarketOutcomeOdds("Team B", 1.90, "tokB"),
                    )
                },
            )

    merged, warnings = merge_polymarket_odds([game], [mapping], _StubPolyClient())
    poly = merged[0].bookmakers["polymarket"]
    ids = {o.name: o.token_id for o in poly.markets["h2h"].outcomes}
    assert ids == {"Team A": "tokA", "Team B": "tokB"}

    legs = enumerate_game_legs(
        merged[0],
        "fanduel",
        ("draftkings", "polymarket"),
        ("h2h",),
        slug_by_game_id={"g1": "slug-x"},
    )
    poly_legs = [leg for leg in legs if leg.hedge_book == "polymarket"]
    assert poly_legs, "expected at least one Polymarket-hedged leg"
    for leg in poly_legs:
        assert leg.hedge_token_id in {"tokA", "tokB"}
        assert leg.polymarket_event_slug == "slug-x"


# ---------------------------------------------------------------------------
# 7. P0 production blockers
# ---------------------------------------------------------------------------


def test_partial_fill_pauses_and_does_not_advance(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto import monitor as monitor_mod
    from bonusarb.auto.executor import FillResult, PARTIAL

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba")
    parlay.save()

    class _PartialExecutor:
        def place_hedge(self, leg, context):
            return FillResult(
                PARTIAL,
                order_id="ord-partial",
                filled_shares=5.0,
                filled_price=0.5,
                filled_cost=2.5,
                live_price=0.5,
                stake_usd=10.0,
                locked_profit_after=1.0,
                reason="partial fill",
            )

    class _PendingWatcher:
        def resolve(self, token_id, *, event_slug=None):
            from bonusarb.auto.watcher import LegResolution
            return LegResolution(PENDING, None)

    class _Notifier:
        def __init__(self) -> None:
            self.alerts: list[str] = []

        def alert(self, message: str) -> None:
            self.alerts.append(message)

        def hedge_placed(self, *a, **k) -> None:
            raise AssertionError("partial must not count as hedge_placed success path")

        def send(self, *a, **k):
            return True

        def leg_resolved(self, *a, **k) -> None:
            return None

        def complete(self, *a, **k) -> None:
            return None

    notifier = _Notifier()
    result = monitor_mod.run_monitor(
        parlay, _PartialExecutor(), _PendingWatcher(), notifier, _auto_config()
    )
    assert result.status == "paused"
    assert result.legs[0].order_id == "ord-partial"
    assert result.legs[0].status == LEG_PENDING
    assert result.legs[1].status == LEG_PENDING
    assert any("PARTIALLY" in a for a in notifier.alerts)


def test_execution_mode_persisted_and_loaded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto.state import MODE_LIVE

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(
        plan, sport_key="basketball_nba", execution_mode=MODE_LIVE
    )
    path = parlay.save()
    reloaded = load_parlay(path)
    assert reloaded.execution_mode == MODE_LIVE


def test_atomic_save_replaces_final_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba")
    path = parlay.save()
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    # Second save still leaves a single final file.
    parlay.summary = "updated"
    parlay.save()
    assert path.read_text(encoding="utf-8").count("updated") == 1


def test_parlay_lock_rejects_second_acquire(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto.state import ParlayLock

    first = ParlayLock("abc")
    first.acquire()
    second = ParlayLock("abc")
    with pytest.raises(RuntimeError, match="already locked"):
        second.acquire()
    first.release()
    # After release, another process can lock.
    second.acquire()
    second.release()


def test_post_order_uses_orderType_keyword():
    """Contract: installed py-clob-client expects ``orderType``, not ``order_type``."""
    import inspect

    from py_clob_client.client import ClobClient

    params = list(inspect.signature(ClobClient.post_order).parameters)
    assert "orderType" in params
    assert "order_type" not in params


def test_resolve_resume_mode_mismatch_refuses():
    from types import SimpleNamespace

    from bonusarb.auto.cli import _resolve_resume_mode
    from bonusarb.auto.state import MODE_LIVE

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(
        plan, sport_key="basketball_nba", execution_mode=MODE_LIVE
    )
    args = SimpleNamespace(live=False, paper=True)
    cfg = _auto_config()
    with pytest.raises(SystemExit) as exc:
        _resolve_resume_mode(args, cfg, parlay)
    assert exc.value.code == 2


def test_placing_intent_saved_before_failed_post(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("bonusarb.auto.state.STATE_DIR", tmp_path)
    from bonusarb.auto import monitor as monitor_mod
    from bonusarb.auto.executor import FillResult, FAILED
    from bonusarb.auto.state import LEG_PENDING

    plan = _two_leg_plan()
    parlay = ActiveParlay.from_plan(plan, sport_key="basketball_nba")
    parlay.save()

    seen_client_ids: list[str] = []

    class _FailExecutor:
        def place_hedge(self, leg, context):
            seen_client_ids.append(leg.client_order_id or "")
            return FillResult(
                FAILED, None, 0.0, 0.0, 0.0, 0.5, 10.0, 0.0, reason="boom"
            )

    class _PendingWatcher:
        def resolve(self, token_id, *, event_slug=None):
            from bonusarb.auto.watcher import LegResolution
            return LegResolution(PENDING, None)

    class _Notifier:
        def alert(self, message: str) -> None:
            return None

        def hedge_placed(self, *a, **k) -> None:
            return None

        def send(self, *a, **k):
            return True

        def leg_resolved(self, *a, **k) -> None:
            return None

        def complete(self, *a, **k) -> None:
            return None

    result = monitor_mod.run_monitor(
        parlay, _FailExecutor(), _PendingWatcher(), _Notifier(), _auto_config()
    )
    assert result.status == "paused"
    assert result.legs[0].status == LEG_PENDING
    assert result.legs[0].client_order_id
    assert seen_client_ids and seen_client_ids[0] == result.legs[0].client_order_id


# ---------------------------------------------------------------------------
# 8. P1: VWAP depth, slip reconcile, fee-inclusive cost
# ---------------------------------------------------------------------------


def test_executable_buy_vwap_walks_asks():
    from bonusarb.polymarket.client import BookLevel, executable_buy_vwap

    asks = [BookLevel(0.40, 10), BookLevel(0.42, 10), BookLevel(0.50, 100)]
    result = executable_buy_vwap(asks, 15, max_price=0.45)
    assert result is not None
    assert result.filled_shares == pytest.approx(15)
    assert result.vwap == pytest.approx((10 * 0.40 + 5 * 0.42) / 15)
    assert result.worst_price == pytest.approx(0.42)
    assert executable_buy_vwap(asks, 25, max_price=0.41) is None


def test_rebuild_plan_from_accepted_slip_resizes_hedges():
    from bonusarb.auto.select import rebuild_plan_from_accepted_slip

    plan = _two_leg_plan()
    rebuilt = rebuild_plan_from_accepted_slip(
        plan,
        accepted_stake=80.0,
        accepted_boost_pct=0.0,
        accepted_combined_odds=plan.combined_odds,
        sport_key="basketball_nba",
        min_locked_profit_floor=0.01,
    )
    assert rebuilt is not None
    assert rebuilt.stake == pytest.approx(80.0)
    assert rebuilt.stake_cost == pytest.approx(80.0)
    assert sum(s.stake for s in rebuilt.hedge_steps) != pytest.approx(
        sum(s.stake for s in plan.hedge_steps)
    )


def test_all_in_buy_cost_includes_taker_fee():
    from bonusarb.polymarket.client import all_in_buy_cost, modeled_taker_fee

    shares, price = 10.0, 0.5
    fee = modeled_taker_fee(shares, price)
    assert fee == pytest.approx(10.0 * 0.05 * 0.5 * 0.5)
    assert all_in_buy_cost(shares, price) == pytest.approx(shares * price + fee)


def test_settlement_blocks_nfl_and_spreads():
    from bonusarb.settlement import settlement_block_reason

    assert settlement_block_reason("americanfootball_nfl", ["h2h"]) is not None
    assert settlement_block_reason("mma_mixed_martial_arts", ["h2h"]) is not None
    assert settlement_block_reason("basketball_nba", ["spreads"]) is not None
    assert settlement_block_reason("basketball_nba", ["h2h"]) is None
