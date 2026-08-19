"""Classic two-way (1-leg) arbitrage sizing with natural sportsbook stakes."""

from __future__ import annotations

from dataclasses import dataclass

POLYMARKET_BOOK = "polymarket"


@dataclass(frozen=True)
class TwoWaySize:
    stake_a: float
    stake_b: float
    locked_profit: float
    roi: float
    capital_used: float


def arb_edge(odds_a: float, odds_b: float) -> float:
    """Return the arb edge ``1 - 1/o_a - 1/o_b`` (positive => arb exists)."""
    if odds_a <= 1.0 or odds_b <= 1.0:
        return -1.0
    return 1.0 - (1.0 / odds_a) - (1.0 / odds_b)


def path_profits(
    stake_a: float,
    odds_a: float,
    stake_b: float,
    odds_b: float,
) -> tuple[float, float]:
    """Return (profit if A wins, profit if B wins) for total outlay stake_a+stake_b."""
    capital = stake_a + stake_b
    return stake_a * odds_a - capital, stake_b * odds_b - capital


def locked_profit_for_stakes(
    stake_a: float,
    odds_a: float,
    stake_b: float,
    odds_b: float,
) -> float:
    """Worst-case profit across the two win paths."""
    profit_a, profit_b = path_profits(stake_a, odds_a, stake_b, odds_b)
    return min(profit_a, profit_b)


def size_two_way_arb_continuous(
    odds_a: float,
    odds_b: float,
    total_capital: float,
) -> TwoWaySize | None:
    """Equalize returns under ``stake_a + stake_b = total_capital``."""
    if total_capital <= 0 or odds_a <= 1.0 or odds_b <= 1.0:
        return None
    edge = arb_edge(odds_a, odds_b)
    if edge <= 0:
        return None
    inv_a = 1.0 / odds_a
    inv_b = 1.0 / odds_b
    inv_sum = inv_a + inv_b
    stake_a = total_capital * inv_a / inv_sum
    stake_b = total_capital - stake_a
    locked = locked_profit_for_stakes(stake_a, odds_a, stake_b, odds_b)
    if locked <= 0:
        return None
    return TwoWaySize(
        stake_a=stake_a,
        stake_b=stake_b,
        locked_profit=locked,
        roi=locked / total_capital,
        capital_used=total_capital,
    )


def is_natural_stake(amount: int) -> bool:
    """True if ``amount`` looks like a normal sportsbook stake.

    Natural stakes end in 0 or 5 (multiples of 5), or are repeated-digit
    amounts such as 11, 22, 33, 111.
    """
    if amount <= 0:
        return False
    if amount % 5 == 0:
        return True
    digits = str(amount)
    return len(digits) >= 2 and len(set(digits)) == 1


def iter_natural_stakes(max_inclusive: int) -> list[int]:
    """All natural stakes in ``1 .. max_inclusive``."""
    if max_inclusive < 1:
        return []
    return [n for n in range(1, max_inclusive + 1) if is_natural_stake(n)]


def nearest_natural_stakes(target: float, max_exclusive: float) -> list[int]:
    """Natural stakes near ``target`` that are strictly less than ``max_exclusive``."""
    upper = int(max_exclusive) - 1 if max_exclusive == int(max_exclusive) else int(max_exclusive)
    if upper < 1:
        return []
    candidates = iter_natural_stakes(upper)
    if not candidates:
        return []
    # Prefer closest to target; include a few neighbors for profit search.
    ordered = sorted(candidates, key=lambda n: (abs(n - target), n))
    # Keep a focused window so sizing stays near the continuous optimum.
    return ordered[:12]


def _size_book_vs_book(
    odds_a: float,
    odds_b: float,
    total_capital: float,
) -> TwoWaySize | None:
    """Both sides are sportsbooks: natural stakes summing to capital when possible."""
    capital_int = int(round(total_capital))
    # Prefer exact integer capital (typical user input like 100).
    if abs(total_capital - capital_int) < 1e-9 and capital_int >= 2:
        best: TwoWaySize | None = None
        naturals = set(iter_natural_stakes(capital_int - 1))
        for stake_a in sorted(naturals):
            stake_b = capital_int - stake_a
            if stake_b not in naturals:
                continue
            locked = locked_profit_for_stakes(float(stake_a), odds_a, float(stake_b), odds_b)
            if locked <= 0:
                continue
            candidate = TwoWaySize(
                stake_a=float(stake_a),
                stake_b=float(stake_b),
                locked_profit=locked,
                roi=locked / float(capital_int),
                capital_used=float(capital_int),
            )
            if best is None or candidate.locked_profit > best.locked_profit:
                best = candidate
        if best is not None:
            return best

    # Fallback: natural pair with sum <= capital, leftover unallocated.
    continuous = size_two_way_arb_continuous(odds_a, odds_b, total_capital)
    if continuous is None:
        return None
    best_fallback: TwoWaySize | None = None
    for stake_a in nearest_natural_stakes(continuous.stake_a, total_capital):
        remaining = total_capital - stake_a
        for stake_b in nearest_natural_stakes(continuous.stake_b, remaining + 1e-9):
            if stake_a + stake_b > total_capital + 1e-9:
                continue
            locked = locked_profit_for_stakes(float(stake_a), odds_a, float(stake_b), odds_b)
            if locked <= 0:
                continue
            used = float(stake_a + stake_b)
            candidate = TwoWaySize(
                stake_a=float(stake_a),
                stake_b=float(stake_b),
                locked_profit=locked,
                roi=locked / used,
                capital_used=used,
            )
            if best_fallback is None or candidate.locked_profit > best_fallback.locked_profit:
                best_fallback = candidate
    return best_fallback


def _size_book_vs_polymarket(
    odds_book: float,
    odds_poly: float,
    total_capital: float,
    *,
    book_is_a: bool,
) -> TwoWaySize | None:
    """Sportsbook stake natural; Polymarket stake may be fractional."""
    continuous = size_two_way_arb_continuous(
        odds_book if book_is_a else odds_poly,
        odds_poly if book_is_a else odds_book,
        total_capital,
    )
    if continuous is None:
        return None

    book_target = continuous.stake_a if book_is_a else continuous.stake_b
    best: TwoWaySize | None = None
    for book_stake in nearest_natural_stakes(book_target, total_capital):
        poly_stake = total_capital - book_stake
        if poly_stake <= 0:
            continue
        if book_is_a:
            locked = locked_profit_for_stakes(
                float(book_stake), odds_book, poly_stake, odds_poly
            )
            stake_a, stake_b = float(book_stake), poly_stake
        else:
            locked = locked_profit_for_stakes(
                poly_stake, odds_poly, float(book_stake), odds_book
            )
            stake_a, stake_b = poly_stake, float(book_stake)
        if locked <= 0:
            continue
        candidate = TwoWaySize(
            stake_a=stake_a,
            stake_b=stake_b,
            locked_profit=locked,
            roi=locked / total_capital,
            capital_used=total_capital,
        )
        if best is None or candidate.locked_profit > best.locked_profit:
            best = candidate
    return best


def size_two_way_arb(
    odds_a: float,
    odds_b: float,
    total_capital: float,
    *,
    book_a: str,
    book_b: str,
) -> TwoWaySize | None:
    """Size a two-way arb with natural rounding on sportsbook sides."""
    if arb_edge(odds_a, odds_b) <= 0:
        return None

    a_is_poly = book_a == POLYMARKET_BOOK
    b_is_poly = book_b == POLYMARKET_BOOK

    if a_is_poly and b_is_poly:
        # Both sides Polymarket: continuous fractional stakes are fine.
        return size_two_way_arb_continuous(odds_a, odds_b, total_capital)

    if not a_is_poly and not b_is_poly:
        return _size_book_vs_book(odds_a, odds_b, total_capital)

    if a_is_poly:
        return _size_book_vs_polymarket(odds_b, odds_a, total_capital, book_is_a=False)
    return _size_book_vs_polymarket(odds_a, odds_b, total_capital, book_is_a=True)
